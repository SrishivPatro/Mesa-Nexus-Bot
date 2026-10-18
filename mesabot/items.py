"""Gathers everything for one run into `World`: term context, pending items, MRS,
attendance, grades, timetable and calendar. Messages and the bot read from here."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import cached_property
from typing import Any

from .attendance import CourseAttendance, analyze
from .nexus import Nexus, NexusError
from .parsing import MRS_RE, ParsedText, extract_dates, mrs_module, parse_text
from .services import Services
from .store import short_hash
from .terms import TermContext, detect_terms, normalize, tokens
from .timeutil import iso, now_ist, parse_iso, start_of_day, week_end, week_start

log = logging.getLogger(__name__)

ANNOUNCEMENT_DAYS = 90
NOTIFICATION_DAYS = 30
MAX_OTHER_LINKS = 2


@dataclass
class Item:
    key: str                         # stable id: dedup of alerts + "done" tracking
    kind: str                        # assignment | announcement | session | mrs | attendance
    title: str
    due: datetime | None = None
    course: str | None = None
    detail: str | None = None
    links: list[tuple[str, str]] = field(default_factory=list)   # (url key, url), not done
    files: list[tuple[str, str]] = field(default_factory=list)   # (attachment key, filename), unread
    tags: list[str] = field(default_factory=list)
    session_at: datetime | None = None   # prep items: the session they prepare for
    session_timed: bool = True           # False when only the session's date is known
    term: str | None = None
    carried: bool = False
    source_id: str | None = None

    def overdue(self, now: datetime) -> bool:
        return self.due is not None and self.due < now

    def match_text(self) -> str:
        return " ".join(filter(None, [self.title, self.course, self.detail, " ".join(self.tags)]))


def grade_key(grade: dict[str, Any]) -> str:
    """Same assignment + same marks + same release = same alert. A re-release alerts again."""
    if "assignmentId" in grade or "assignment" in grade:
        return "g:" + short_hash(str(grade.get("assignmentId") or grade.get("assignment")),
                                 str(grade.get("marks")), str(grade.get("feedback") or "")[:200],
                                 str(grade.get("releasedAt") or ""))
    return "rc:" + short_hash(json.dumps(grade, sort_keys=True, default=str))


class World:
    def __init__(self, svc: Services, now: datetime | None = None):
        self.svc = svc
        self.cfg = svc.config
        self.store = svc.store
        self.nexus: Nexus = svc.nexus.fresh()
        self.now = now or svc.fixed_now or self.nexus.now() or now_ist()

    # ---- raw data ----------------------------------------------------------------------

    @cached_property
    def me(self) -> dict[str, Any]:
        try:
            return self.nexus.get_me()
        except NexusError:
            return {}

    @property
    def first_name(self) -> str:
        return (self.me.get("name") or "").split(" ")[0]

    @cached_property
    def courses(self) -> list[dict[str, Any]]:
        return self.nexus.list_courses()

    @cached_property
    def ctx(self) -> TermContext:
        ctx = detect_terms(self.courses, self.now.date())
        if ctx.active_name and self.store.meta("active_term") != ctx.active_name:
            log.info("Active term is now %s (previous: %s)", ctx.active_name, ctx.previous_name)
            self.store.set_meta("active_term", ctx.active_name)
            self.store.set_meta("active_term_since", iso(self.now))
        return ctx

    @cached_property
    def grades(self) -> dict[str, Any]:
        return self.nexus.get_grades()

    @cached_property
    def graded_ids(self) -> set[str]:
        return {g.get("assignmentId") for g in self.grades.get("assignmentMarks", []) if g.get("assignmentId")}

    @cached_property
    def announcements(self) -> list[dict[str, Any]]:
        return self.nexus.list_announcements(since_days=ANNOUNCEMENT_DAYS)

    @cached_property
    def notifications(self) -> list[dict[str, Any]]:
        try:
            return self.nexus.list_notifications(since_days=NOTIFICATION_DAYS, limit=100)
        except NexusError as exc:
            log.info("notifications unavailable: %s", exc)
            return []

    @cached_property
    def parsed(self) -> list[ParsedText]:
        out: list[ParsedText] = []
        for a in self.announcements:
            out.append(parse_text("announcement", a["id"], a.get("title", ""), a.get("body", ""),
                                  parse_iso(a.get("postedAt")) or self.now, course=a.get("course"),
                                  listed_attachments=a.get("attachments") or [], pinned=bool(a.get("pinned"))))
        for n in self.notifications:
            links = n.get("links") or {}
            if links.get("announcementId") or links.get("assignmentId"):
                continue  # covered by the full announcement / list_assignments; notification text is truncated
            out.append(parse_text("notification", n["id"], n.get("title", ""), n.get("body", ""),
                                  parse_iso(n.get("createdAt")) or self.now))
        return out

    # ---- assignments -------------------------------------------------------------------

    @cached_property
    def assignments(self) -> list[dict[str, Any]]:
        seen, out = set(), []
        for status in ("due", "overdue"):
            for a in self.nexus.list_assignments(status):
                if a["id"] not in seen:
                    seen.add(a["id"])
                    out.append(a)
        return out

    def assignment_term(self, a: dict[str, Any]) -> str | None:
        return (self.ctx.term_of_course(a.get("course"))
                or self.ctx.term_of_date(parse_iso(a.get("dueAt")))
                or (self.ctx.active_name if not a.get("dueAt") else None))

    def assignment_items(self) -> list[Item]:
        items = []
        scope = self.ctx.tracked_terms() if self.cfg.carry_forward else {self.ctx.active_name} - {None}
        for a in self.assignments:
            if a.get("submissionStatus") == "submitted" or a["id"] in self.graded_ids:
                continue  # graded in-class work shows as "not submitted" but is resolved
            key = f"asg:{a['id']}"
            if self.store.is_done(key):
                continue
            term = self.assignment_term(a)
            if scope and term and term not in scope:
                continue
            due = parse_iso(a.get("dueAt"))
            detail = "group" if a.get("isGroup") else None
            if due and due < self.now:
                cutoff = parse_iso(a.get("cutoffAt"))
                if a.get("allowLate") and cutoff and cutoff > self.now:
                    detail = f"late submission open until {cutoff.strftime('%d %b %H:%M')}"
            items.append(Item(key=key, kind="assignment", title=a.get("title") or "Assignment",
                              due=due, course=a.get("course"), detail=detail, term=term,
                              carried=bool(term and term == self.ctx.previous_name),
                              source_id=a["id"]))
        return items

    # ---- announcements and notifications -----------------------------------------------

    def _register_links(self, p: ParsedText, deadline: datetime | None) -> tuple[list, list]:
        links, files = [], []
        for url in p.forms:
            key = self.store.upsert_url(url, label=_form_label(p, url), kind="google_form",
                                        source=p.source, source_id=p.source_id,
                                        source_title=p.title, deadline=deadline)
            if not self.store.url_done(key):
                links.append((key, url))
        if p.actionable:
            for url in [u for u in p.urls if u not in p.forms][:MAX_OTHER_LINKS]:
                key = self.store.upsert_url(url, label=p.title, kind="url", source=p.source,
                                            source_id=p.source_id, source_title=p.title,
                                            deadline=deadline)
                if not self.store.url_done(key):
                    links.append((key, url))
        for name in p.attachments:
            key = self.store.upsert_attachment(name, p.source, p.source_id, p.title)
            if not self.store.attachment_read(key):
                files.append((key, name))
        return links, files

    def announcement_items(self) -> list[Item]:
        lookback = self.now - timedelta(days=self.cfg.action_lookback_days)
        today = start_of_day(self.now)
        candidates: list[tuple[ParsedText, Item]] = []
        for p in self.parsed:
            if MRS_RE.search(f"{p.title} {p.body}") and mrs_module(f"{p.title}\n{p.body}"):
                self._register_links(p, None)
                continue  # handled by the MRS tracker
            future_deadlines = [d.when for d in p.deadlines if d.when >= self.now - timedelta(hours=1)]
            future_events = [d.when for d in p.event_dates if d.when >= today]
            due = min(future_deadlines) if future_deadlines else None
            links, files = self._register_links(p, due)
            if not p.actionable:
                continue
            if p.deadlines:
                live = bool(future_deadlines)
            elif p.dates and not future_events:
                live = False  # only talks about days that are over ("tomorrow's exam" from last week)
            elif "mandatory" in p.tags:
                live = bool(future_events)
            else:
                live = p.posted >= lookback or bool(future_events and p.posted >= self.now - timedelta(days=30))
            key = f"ann:{p.source_id}"
            if not live or self.store.is_done(key):
                continue
            if p.forms and not any(k for k, u in links if u in p.forms):
                continue  # every form in it is marked done
            session_at, session_timed = None, True
            if future_events:
                after_due = [d for d in p.event_dates if d.when >= today and (not due or d.when >= due)]
                mention = min(after_due or [d for d in p.event_dates if d.when >= today], key=lambda d: d.when)
                session_at, session_timed = mention.when, mention.has_time
                if not session_timed:
                    found = self._session_on(mention.when, p)
                    if found:
                        session_at, session_timed = found, True
            kind = "announcement"
            if "mandatory" in p.tags and not due and session_at:
                kind, due = "session", session_at
            candidates.append((p, Item(
                key=key, kind=kind, title=p.title, due=due, course=p.course,
                links=links, files=files, tags=[t for t in p.tags if t not in {"action required"}],
                session_at=session_at if p.is_prep or kind == "session" else None,
                session_timed=session_timed,
                term=self.ctx.active_name, source_id=p.source_id)))

        # Staff often re-post the same announcement: keep the newest per title and per form.
        candidates.sort(key=lambda c: c[0].posted, reverse=True)
        items, seen_titles, seen_forms = [], set(), set()
        for p, item in candidates:
            title_key = normalize(re.sub(r"(?i)\b(reminder|update|deadline extended.*)\b", "", p.title))
            forms = {u for u in p.forms}
            if title_key in seen_titles or (forms and forms <= seen_forms):
                continue
            seen_titles.add(title_key)
            seen_forms |= forms
            items.append(item)
        return items

    @cached_property
    def upcoming_sessions(self) -> list[dict[str, Any]]:
        try:
            return self.timetable(start_of_day(self.now), start_of_day(self.now) + timedelta(days=30))
        except NexusError:
            return []

    def _session_on(self, day: datetime, p: ParsedText) -> datetime | None:
        """Start time of the timetable session an announcement refers to, if one matches."""
        words = tokens(f"{p.title} {p.course or ''}") - {"session", "term", "kick", "off", "pre", "read"}
        best, best_hits = None, 0
        for e in self.upcoming_sessions:
            start = parse_iso(e.get("start"))
            if not start or start.date() != day.date() or e.get("cancelled"):
                continue
            hits = len(words & tokens(f"{e.get('title')} {e.get('course')}"))
            if hits > best_hits:
                best, best_hits = start, hits
        return best

    # ---- MRS Practice Lab --------------------------------------------------------------

    @cached_property
    def mrs(self) -> dict[str, Any]:
        """This week's MRS module, deadline (Sunday 11:59 pm IST) and done flag."""
        week = week_start(self.now)
        week_key = week.date().isoformat()
        deadline = week_end(self.now).replace(second=0)
        best: tuple[datetime, str, str] | None = None
        for p in self.parsed:
            text = f"{p.title}\n{p.body}"
            module = mrs_module(text)
            if not module or not (MRS_RE.search(text) or "session yet this week" in text):
                continue
            dates = [d.when for d in extract_dates(p.body, p.posted, p.title)]
            in_week = [d for d in dates if week <= d <= deadline]
            if dates and not in_week:
                continue  # talks about another week
            if not dates and not (week <= p.posted <= deadline):
                continue
            if best is None or p.posted > best[0]:
                best = (p.posted, module, f"{p.source}:{p.source_id}")
        module, source = (best[1], best[2]) if best else (None, None)
        entry = self.store.set_mrs_week(week_key, module, deadline, source)
        return {"week": week_key, "module": entry.get("module"), "deadline": deadline,
                "done": bool(entry.get("done")), "done_at": entry.get("done_at")}

    def mrs_item(self) -> Item | None:
        mrs = self.mrs
        if mrs["done"]:
            return None
        module = mrs["module"] or "module not announced yet (check MRS Practice Lab → Modules)"
        return Item(key=f"mrs:{mrs['week']}", kind="mrs", title=f"MRS weekly session: {module}",
                    due=mrs["deadline"], detail='reply "done mrs" when finished', tags=["mrs"],
                    term=self.ctx.active_name)

    # ---- attendance --------------------------------------------------------------------

    @cached_property
    def attendance(self) -> list[CourseAttendance]:
        summary = self.nexus.get_attendance_summary()
        rows = analyze(summary, self.ctx, self.cfg.attendance_threshold, self.cfg.at_risk_margin,
                       self.cfg.untracked_courses, include_previous=self.cfg.carry_forward)
        return [r for r in rows if not (r.carried and self.store.is_done(attendance_key(r.course)))]

    def attendance_alerts(self) -> list[CourseAttendance]:
        return [r for r in self.attendance if r.alert]

    @cached_property
    def missed_sessions(self) -> list[dict[str, Any]]:
        return self.nexus.get_attendance_summary().get("missedSessions", [])

    # ---- everything pending --------------------------------------------------------------

    def pending_items(self) -> list[Item]:
        items = self.assignment_items() + self.announcement_items()
        mrs = self.mrs_item()
        if mrs:
            items.append(mrs)
        far = self.now + timedelta(days=3650)
        return sorted(items, key=lambda i: (i.carried, i.due or far, i.title))

    def due_between(self, start: datetime, end: datetime) -> list[Item]:
        return [i for i in self.pending_items() if i.due and start <= i.due <= end]

    # ---- grades --------------------------------------------------------------------------

    def all_grades(self) -> list[dict[str, Any]]:
        marks = sorted(self.grades.get("assignmentMarks", []),
                       key=lambda g: g.get("releasedAt") or "", reverse=True)
        return marks

    def grades_since(self, since: datetime) -> list[dict[str, Any]]:
        return [g for g in self.all_grades() if (parse_iso(g.get("releasedAt")) or since) > since]

    def report_cards(self) -> list[dict[str, Any]]:
        return self.grades.get("reportCards", [])

    # ---- timetable and calendar -------------------------------------------------------------

    def timetable(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        return self.nexus.get_schedule(start, end)

    def calendar(self, start: datetime, end: datetime) -> list[dict[str, Any]] | None:
        """Google Calendar events, or None when the calendar is not connected."""
        if not self.svc.gcal:
            return None
        try:
            return self.svc.gcal.events(start, end)
        except Exception as exc:
            log.error("Google Calendar request failed: %s", exc)
            return None

    def reminder_feed(self, hours: int) -> list[dict[str, Any]]:
        try:
            return self.nexus.reminder_feed(hours)
        except NexusError as exc:
            log.error("reminder feed failed: %s", exc)
            return []


def attendance_key(course: str) -> str:
    return f"att:{normalize(course)}"


def _form_label(p: ParsedText, url: str) -> str:
    """'ILS/BSL opt-in form' style label: the text just before the link, else the title."""
    idx = p.body.find(url)
    if idx > 0:
        line = p.body[: idx].rsplit("\n", 1)[-1].strip(" :-–—•👉🔗")
        line = re.sub(r"(?i)\b(fill (?:out )?the form here|link|here|share it with us)\b.*$", "", line).strip(" :-(")
        if len(line) >= 6:
            return f"{p.title} · {line[:60]}"
    return p.title
