"""Inbound WhatsApp commands and natural-language questions, answered live from Nexus."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable

from . import messages as m
from .attendance import is_untracked, margin
from .items import Item, World, attendance_key
from .nexus import NexusError
from .parsing import MONTHS
from .services import Services
from .terms import match_course, normalize, tokens
from .timeutil import (IST, WEEKDAYS, end_of_day, fmt_day, fmt_dt, fmt_relative, next_weekday,
                       parse_iso, start_of_day, week_end, week_start)

log = logging.getLogger(__name__)

HELP = """*🤖 MESA BOT*

*Commands*
`due` · due today and this week
`today` · today's classes and to-dos
`week` · the week ahead
`attendance` · % per course, with danger flags
`grades` · released marks
`pending` · everything still to do
`mrs` · this week's MRS module
`schedule monday` · classes on a day
`done mrs` · tick something off

*Or just ask*
_how many Data 102 classes next week?_
_can I skip SQL tomorrow?_
_what did I score in Art of Selling?_
_what announcements came this week?_"""


def handle_message(svc: Services, text: str, now: datetime | None = None) -> str:
    text = (text or "").strip()
    if not text or text.lower() in {"help", "hi", "hello", "menu", "commands", "?", "start"}:
        return HELP  # no Nexus call needed
    world = World(svc, now)
    try:
        return route(world, text)
    except NexusError as exc:
        log.exception("Nexus error answering %r", text)
        return f"⚠️ Couldn't reach Nexus right now ({exc}). Try again in a minute."


def route(world: World, text: str) -> str:
    low = re.sub(r"\s+", " ", text.lower()).strip(" ?!.")
    if low in {"help", "hi", "hello", "menu", "commands", "?", "start"}:
        return HELP
    if low.startswith("done"):
        return cmd_done(world, text[4:].strip())
    if low.startswith("schedule") or low.startswith("timetable"):
        return cmd_schedule(world, low.split(" ", 1)[1] if " " in low else "today")
    simple: dict[str, Callable[[World], str]] = {
        "due": cmd_due, "today": cmd_today, "week": cmd_week, "attendance": cmd_attendance,
        "grades": cmd_grades, "marks": cmd_grades, "pending": cmd_pending, "mrs": cmd_mrs,
    }
    if low in simple:
        return simple[low](world)
    return answer_question(world, low, original=text)


# ---- commands ----------------------------------------------------------------------------

def cmd_due(world: World) -> str:
    now = world.now
    items = world.pending_items()
    today = [i for i in items if i.due and start_of_day(now) <= i.due <= end_of_day(now)]
    week = [i for i in items if i.due and end_of_day(now) < i.due <= max(week_end(now), now + timedelta(days=7))]
    overdue = [i for i in items if i.overdue(now) and i.kind == "assignment" and i not in today]
    return m.join([
        m.section("📝 DUE TODAY", [m.item_line(i, now) for i in today] or ["✅ Nothing due today"]),
        m.section("🗓️ DUE THIS WEEK", [m.item_line(i, now) for i in week] or ["✅ Nothing else this week"]),
        m.section("⚠️ OVERDUE", [m.item_line(i, now) for i in overdue]) if overdue else [],
    ])


def cmd_today(world: World) -> str:
    now = world.now
    events = world.timetable(start_of_day(now), end_of_day(now))
    items = world.pending_items()
    soon = [i for i in items if not i.due or i.due <= now + timedelta(days=2)]
    return m.join([
        m.section(f"📅 TODAY · {fmt_day(now)}", m.timetable_section(events)),
        m.section("📌 TO DO", m.items_section(soon, now)),
    ])


def cmd_week(world: World) -> str:
    return m.weekly_summary(world, start=start_of_day(world.now))


def cmd_attendance(world: World) -> str:
    rows = world.attendance
    lines = [f"*🎯 ATTENDANCE* _(need {world.cfg.attendance_threshold:g}%)_"]
    lines += m.attendance_full_section(world)
    missed = world.missed_sessions[:3]
    if missed:
        lines.append("\n_Last missed: " + "; ".join(f"{m.short_course(s.get('session'))} ({s.get('day')})" for s in missed) + "_")
    if not any(r.alert for r in rows):
        lines.append("\n✅ No course is in danger.")
    return "\n".join(lines)


def cmd_grades(world: World) -> str:
    grades = world.all_grades()
    lines = [m.grade_line(g, with_feedback=False) for g in grades] or ["_No marks released yet_"]
    for card in world.report_cards():
        lines.append(f"🧾 Report card: {card.get('term') or card.get('name')}")
    note = '_Send "feedback <assignment>" for the full feedback_' if grades else None
    return "\n".join(m.section("📊 GRADES", ["\n\n".join(lines)], note))


def cmd_pending(world: World) -> str:
    items = world.pending_items()
    lines = [f"*📌 PENDING ({len(items)})*"] + m.items_section(items, world.now)
    if items:
        lines.append(m.DONE_HINT)
    carried_att = [r for r in world.attendance if r.carried and r.alert]
    if carried_att:
        lines.append("\n*🎯 ATTENDANCE, CARRIED FORWARD*")
        lines += [m.attendance_line(r, world.cfg.attendance_threshold) for r in carried_att]
    return "\n".join(lines)


def cmd_mrs(world: World) -> str:
    mrs = world.mrs
    module = mrs["module"] or "not announced yet (check MRS Practice Lab → Modules)"
    if mrs["done"]:
        done_at = parse_iso(mrs.get("done_at"))
        status = f"✅ marked done{(' ' + fmt_dt(done_at)) if done_at else ''}"
    else:
        status = f"⏳ not done — due {fmt_dt(mrs['deadline'])} ({fmt_relative(mrs['deadline'], world.now)})"
    return (f"*🎙️ MRS WEEKLY SESSION*\n_week of {fmt_day(parse_iso(mrs['week'] + 'T00:00:00+05:30'))}_\n\n"
            f"Module: *{module}*\n{status}"
            + ("" if mrs["done"] else '\n\n_Reply "done mrs" once you finish a complete session_'))


def parse_day(text: str, now: datetime) -> date | None:
    text = text.strip().lower()
    today = now.date()
    if text in {"", "today", "now"}:
        return today
    if text in {"tomorrow", "tmrw", "tmr"}:
        return today + timedelta(days=1)
    if text == "yesterday":
        return today - timedelta(days=1)
    nxt = text.startswith("next ")
    word = text.replace("next ", "").strip()
    for i, day in enumerate(WEEKDAYS):
        if word in {day, day[:3], day[:4]}:
            return next_weekday(today, i, include_today=not nxt)
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})$", word)
    if match:
        return date(int(match[1]), int(match[2]), int(match[3]))
    match = re.match(r"(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)", word) or re.match(r"([a-z]+)\s+(\d{1,2})", word)
    if match:
        a, b = match.groups()
        day_s, month_s = (a, b) if a.isdigit() else (b, a)
        month = MONTHS.get(month_s) or MONTHS.get(month_s[:3])
        if month:
            try:
                d = date(today.year, month, int(day_s))
                return d if d >= today - timedelta(days=60) else date(today.year + 1, month, int(day_s))
            except ValueError:
                return None
    return None


def cmd_schedule(world: World, day_text: str) -> str:
    day = parse_day(day_text, world.now)
    if not day:
        return 'Which day? Try "schedule monday", "schedule tomorrow" or "schedule 24 oct".'
    start = datetime.combine(day, datetime.min.time(), IST)
    events = world.timetable(start, start + timedelta(days=1))
    return "\n".join([f"📅 *{fmt_day(day)}*"] + m.timetable_section(events, "No classes that day."))


# ---- done <keyword> -------------------------------------------------------------------------

@dataclass
class Candidate:
    key: str
    kind: str          # url | attachment | item | attendance
    label: str
    group: str         # announcement/assignment id: ties inside one group are marked together
    text: str


def _candidates(world: World) -> list[Candidate]:
    items = world.pending_items()  # also registers new URLs and attachments
    snap = world.store.snapshot()
    out: list[Candidate] = []
    for key, u in snap["flagged_urls"].items():
        if not u.get("done"):
            out.append(Candidate(key, "url", u.get("label") or u["url"], u.get("source_id", ""),
                                 f"{u.get('label', '')} {u.get('source_title', '')} form link {u['url']}"))
    for key, a in snap["attachments"].items():
        if not a.get("read"):
            out.append(Candidate(key, "attachment", a["filename"], a.get("source_id", ""),
                                 f"{a['filename']} {a.get('source_title', '')} attachment file"))
    for i in items:
        if i.kind == "mrs":
            continue
        out.append(Candidate(i.key, "item", i.title, i.source_id or i.key, i.match_text()))
    for r in world.attendance:
        if r.carried and r.alert:
            out.append(Candidate(attendance_key(r.course), "attendance", f"{r.course} attendance ({r.term})",
                                 attendance_key(r.course), f"{r.course} attendance"))
    return out


def _score(keyword: str, cand: Candidate) -> float:
    kw_norm = normalize(keyword)
    label_norm, text_norm = normalize(cand.label), normalize(cand.text)
    kw_tokens = tokens(keyword)
    if not kw_tokens and not kw_norm:
        return 0
    score = len(kw_tokens & tokens(cand.text)) / max(1, len(kw_tokens))
    if kw_norm and kw_norm == label_norm:
        score += 3
    elif kw_norm and kw_norm in label_norm:
        score += 2
    elif kw_norm and kw_norm in text_norm:
        score += 1
    return score


def cmd_done(world: World, keyword: str) -> str:
    keyword = keyword.strip(" :-")
    if not keyword:
        return 'Done with what? e.g. "done mrs", "done opt-in form", "done case.pdf".'
    if normalize(keyword) in {"mrs", "mrsweekly", "mrssession", "mrspracticelab"}:
        mrs = world.mrs
        world.store.mark_mrs_done(mrs["week"], mrs["deadline"])
        return f"✅ MRS marked done for the week of {fmt_day(parse_iso(mrs['week'] + 'T00:00:00+05:30'))}" + (
            f" ({mrs['module']})." if mrs["module"] else ".") + " I'll stop reminding you until next Monday."

    scored = sorted(((c, _score(keyword, c)) for c in _candidates(world)), key=lambda x: -x[1])
    scored = [(c, s) for c, s in scored if s >= 0.5]
    if not scored:
        return f'🤔 Nothing pending matches "{keyword}". Send "pending" to see what is open.'
    best = scored[0][1]
    top = [c for c, s in scored if s >= best - 1e-9]
    groups = {c.group for c in top}
    if len(groups) > 1:
        options = "\n".join(f"• {c.label}" for c in top[:5])
        return f'Several things match "{keyword}":\n{options}\nReply "done" + a few more words from the one you mean.'
    for c in top:
        if c.kind == "url":
            world.store.mark_url_done(c.key)
        elif c.kind == "attachment":
            world.store.mark_attachment_read(c.key)
        else:
            world.store.mark_done(c.key, c.label, c.kind)
    names = ", ".join(c.label for c in top)
    return f"✅ Marked done: {names}. It won't appear in future messages."


# ---- natural language ---------------------------------------------------------------------

STOPWORDS = {"what", "when", "is", "my", "the", "a", "an", "did", "i", "in", "of", "for", "to", "do",
             "have", "any", "me", "show", "tell", "about", "next", "class", "classes", "session",
             "this", "week", "today", "am", "at", "how", "much", "many", "are", "there", "get",
             "got", "score", "scored", "marks", "grade", "grades", "please", "on", "can", "you", "came"}


def _since_days(q: str) -> int:
    if "today" in q:
        return 1
    if "yesterday" in q:
        return 2
    if "week" in q:
        return 7
    if "month" in q:
        return 30
    return 14


def answer_question(world: World, q: str, original: str | None = None) -> str:
    """Every free-text question goes to Gemini, which understands context and follow-ups.
    The pattern rules are only a backup for when Gemini isn't configured or fails."""
    if world.svc.ai:
        try:
            from .ai import NexusTools, system_prompt
            return world.svc.ai.answer(original or q, NexusTools(world), system_prompt(world))
        except Exception as exc:  # noqa: BLE001 - quota, network, model changes: fall back to rules
            log.warning("Gemini failed, using rules: %s", exc)
    course = match_course(q, world.courses, prefer_term=world.ctx.active_name)
    return quick_answer(world, q, course) or rule_answer(world, q, course)


COUNT_RE = re.compile(r"\b(how many|number of|count)\b.*\b(class|classes|sessions?|lectures?)\b")
SKIP_RE = re.compile(r"\b(skip|bunk|miss|leave)\b")


def quick_answer(world: World, q: str, course: dict[str, Any] | None) -> str | None:
    """High-confidence patterns only; anything else goes to Gemini."""
    if re.search(r"\bfeedback\b", q):
        return answer_feedback(world, q)
    if COUNT_RE.search(q):
        return answer_count(world, q, course)
    if course and SKIP_RE.search(q):
        return answer_skip(world, course)
    if course and re.search(r"\b(next|when)\b", q) and re.search(r"\b(class|session|lecture)\b", q):
        return answer_next_class(world, course)
    if course and re.search(r"\b(score|scored|marks?|grades?|result|got)\b", q):
        return answer_scores(world, course)
    if course and re.search(r"\b(who teaches|teacher|instructor|professor|linkedin|taught)\b", q):
        return answer_instructor(world, course)
    return None


def rule_answer(world: World, q: str, course: dict[str, Any] | None) -> str:
    if re.search(r"\bfeedback\b", q):
        return answer_feedback(world, q)
    if re.search(r"\b(score|scored|marks?|grades?|result|gpa|cgpa|got in)\b", q):
        return answer_scores(world, course)
    if re.search(r"\b(attendance|at risk|bunk|skip|miss(?:ed)?|absent)\b", q):
        return answer_attendance(world, course)
    if re.search(r"\b(announce\w*|posted|notice|news|updates?)\b", q):
        return answer_announcements(world, q)
    if re.search(r"\b(who teaches|teacher|instructor|professor|prof|linkedin|taught)\b", q):
        return answer_instructor(world, course)
    if re.search(r"\bmrs\b|practice lab", q):
        return cmd_mrs(world)
    if re.search(r"\b(due|deadline|submit|assignment|homework|pending)\b", q):
        return cmd_due(world)
    if re.search(r"\bnotification", q):
        return answer_notifications(world)
    day_match = re.search(r"\b(today|tomorrow|yesterday|(?:next )?(?:mon|tues|wednes|thurs|fri|satur|sun)day)\b", q)
    if re.search(r"\b(class|classes|session|lecture|when|where|timetable|schedule)\b", q):
        if course:
            return answer_next_class(world, course)
        if day_match:
            return cmd_schedule(world, day_match.group(1))
        return answer_next_class(world, None)
    if day_match:
        return cmd_schedule(world, day_match.group(1))
    if course:
        return answer_course(world, course)
    return answer_search(world, q)


def course_events(events: list[dict[str, Any]], course: dict[str, Any] | None) -> list[dict[str, Any]]:
    events = [e for e in events if not e.get("cancelled") and e.get("type") != "holiday"]
    if not course:
        return events
    title_norm = normalize(course.get("title"))
    subject = tokens((course.get("title") or "").split(" with ")[0])
    return [e for e in events if normalize(e.get("course")) == title_norm
            or (subject and subject <= tokens(f"{e.get('title')} {e.get('course')}"))]


def parse_period(q: str, now: datetime) -> tuple[datetime, datetime, str]:
    """(start, end, label) for 'today', 'tomorrow', 'this/next week', 'this/next month',
    'next N days', a weekday; default: the next 7 days."""
    today = start_of_day(now)
    if "tomorrow" in q:
        d = today + timedelta(days=1)
        return d, d + timedelta(days=1), f"tomorrow ({fmt_day(d)})"
    if "today" in q:
        return today, today + timedelta(days=1), "today"
    if "next week" in q:
        start = week_start(now) + timedelta(days=7)
        return start, start + timedelta(days=7), f"next week ({fmt_day(start)} to {fmt_day(start + timedelta(days=6))})"
    if re.search(r"\b(this|the) week\b|\bweek\b", q) and now.weekday() == 6:
        start = today + timedelta(days=1)  # on a Sunday, "this week" means the coming one
        return start, start + timedelta(days=7), f"this week ({fmt_day(start)} to {fmt_day(start + timedelta(days=6))})"
    if re.search(r"\b(this|the) week\b|\bweek\b", q):
        return today, week_end(now), f"this week (till {fmt_day(week_end(now))})"
    if "next month" in q:
        first = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
        nxt = (first + timedelta(days=32)).replace(day=1)
        return first, nxt, f"in {first.strftime('%B')}"
    if "month" in q:
        nxt = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
        return today, nxt, f"for the rest of {today.strftime('%B')}"
    m = re.search(r"next (\d{1,2}) days", q)
    if m:
        n = int(m.group(1))
        return today, today + timedelta(days=n), f"in the next {n} days"
    for i, day in enumerate(WEEKDAYS):
        if re.search(rf"\b{day}\b", q):
            d = datetime.combine(next_weekday(now.date(), i), datetime.min.time(), IST)
            return d, d + timedelta(days=1), f"on {fmt_day(d)}"
    return today, today + timedelta(days=7), "in the next 7 days"


def _course_attendance(world: World, course: dict[str, Any]) -> dict[str, Any] | None:
    key = normalize(course.get("title"))
    for r in world.nexus.get_attendance_summary().get("courses", []):
        if normalize(r.get("course")) == key:
            return r
    return None


def answer_count(world: World, q: str, course: dict[str, Any] | None) -> str:
    start, end, label = parse_period(q, world.now)
    events = course_events(world.timetable(start, end), course)
    counted = [e for e in events if e.get("attendanceRequired")] if not course else events
    name = m.short_course(course.get("title")) if course else "classes with attendance"
    noun = "class" if len(counted) == 1 else "classes"
    if not counted:
        return f"*📅 No {name} {'classes ' if course else ''}{label}*"
    title = (f"*📅 {len(counted)} {name} {noun} {label}*" if course
             else f"*📅 {len(counted)} {name} {label}*")
    lines = [title]
    for e in counted:
        s = parse_iso(e.get("start"))
        lines.append(f"{fmt_day(s)} " + m.session_line(e) if s else m.session_line(e))
    if course and counted:
        rec = _course_attendance(world, course)
        if rec and rec.get("total"):
            a, t, n = int(rec["attended"]), int(rec["total"]), len([e for e in counted if e.get("attendanceRequired")])
            can_miss, need = margin(a, t, world.cfg.attendance_threshold)
            lines.append("")
            lines.append(f"You're at *{a / t * 100:.0f}%* ({a}/{t}). "
                         + (f"Attend the next *{need}* in a row to get back to {world.cfg.attendance_threshold:g}%."
                            if need else ("You can't miss any." if can_miss == 0 else f"You can miss *{can_miss}*."))
                         + (f" Attend {'it' if n == 1 else 'all ' + str(n)} → *{(a + n) / (t + n) * 100:.0f}%*." if n else ""))
    return "\n".join(lines)


def answer_skip(world: World, course: dict[str, Any]) -> str:
    name = m.short_course(course.get("title"))
    if is_untracked(course.get("title") or "", world.cfg.untracked_courses):
        return f"*{name}* doesn't track attendance, so skipping won't affect your %."
    now = world.now
    upcoming = [e for e in course_events(world.timetable(now, now + timedelta(days=14)), course)
                if e.get("attendanceRequired")]
    rec = _course_attendance(world, course)
    if not rec or not rec.get("total"):
        return (f"*{name}*: no attendance taken yet, so every class counts from the start. "
                f"{len(upcoming)} {'class' if len(upcoming) == 1 else 'classes'} with attendance in the next 2 weeks.")
    a, t = int(rec["attended"]), int(rec["total"])
    can_miss, need = margin(a, t, world.cfg.attendance_threshold)
    thr = f"{world.cfg.attendance_threshold:g}%"
    if need:
        head = f"🔴 *Don't skip {name}.* You're at *{a / t * 100:.0f}%* ({a}/{t}), below {thr}. Attend the next *{need}* in a row to recover."
    elif can_miss == 0:
        head = f"🟠 *Better not.* {name} is at *{a / t * 100:.0f}%* ({a}/{t}). Missing one drops you to *{a / (t + 1) * 100:.0f}%*, below {thr}."
    else:
        head = f"🟢 *Yes, you can skip {can_miss}* {name} {'class' if can_miss == 1 else 'classes'} and stay at or above {thr}. Now *{a / t * 100:.0f}%* ({a}/{t})."
    nxt = parse_iso(upcoming[0]["start"]) if upcoming else None
    n = len(upcoming)
    tail = f"\n{n} {name} {'class' if n == 1 else 'classes'} in the next 2 weeks" + (f", next {fmt_dt(nxt)}." if nxt else ".")
    return head + tail


def answer_next_class(world: World, course: dict[str, Any] | None) -> str:
    now = world.now
    events = course_events(world.timetable(now, now + timedelta(days=21)), course)
    events = [e for e in events if (parse_iso(e.get("end")) or now) > now]
    name = m.short_course(course.get("title")) if course else "class"
    if not events:
        extra = ""
        if course and course.get("term") != world.ctx.active_name:
            extra = f" ({m.short_course(course.get('title'))} was a {course.get('term')} course)"
        return f"No upcoming {name} sessions in the next 3 weeks{extra}."
    e = events[0]
    start = parse_iso(e["start"])
    line = m.session_line(e)
    return f"📅 Next {name}: *{fmt_dt(start)}* ({fmt_relative(start, now)})\n{line}" + (
        f"\nInstructor: {e['instructor']}" if e.get("instructor") else "")


def answer_scores(world: World, course: dict[str, Any] | None) -> str:
    grades = world.all_grades()
    if course:
        key = normalize(course.get("title"))
        grades = [g for g in grades if normalize(g.get("course")) == key]
        if not grades:
            return f"No marks released yet for {m.short_course(course.get('title'))}."
        return "\n\n".join([f"*📊 {m.short_course(course.get('title')).upper()}*"] + [m.grade_line(g) for g in grades])
    return cmd_grades(world)


def answer_feedback(world: World, q: str) -> str:
    q_tokens = tokens(q) - {"feedback"}
    best, best_score = None, 0
    for g in world.all_grades():
        score = len(q_tokens & tokens(f"{g.get('assignment')} {g.get('course')}"))
        if score > best_score:
            best, best_score = g, score
    if not best:
        return 'Which one? e.g. "feedback finance mid-term".'
    fb = (best.get("feedback") or "").strip() or "_No written feedback was released._"
    return f"📝 *{best.get('assignment')}* — {m.short_course(best.get('course'))}: {best.get('marks')}\n{fb[:3000]}"


def answer_attendance(world: World, course: dict[str, Any] | None) -> str:
    if course:
        key = normalize(course.get("title"))
        rows = [r for r in world.attendance if normalize(r.course) == key]
        if not rows:
            summary = world.nexus.get_attendance_summary()
            rec = next((r for r in summary.get("courses", []) if normalize(r.get("course")) == key), None)
            if not rec:
                return f"No attendance recorded for {m.short_course(course.get('title'))} yet."
            can_miss, need = margin(rec["attended"], rec["total"], world.cfg.attendance_threshold)
            return (f"{m.short_course(rec['course'])}: {rec['percentage']}% ({rec['attended']}/{rec['total']}) — "
                    + (f"need {need} in a row to recover" if need else f"can miss {can_miss} more"))
        return "\n".join(m.attendance_line(r, world.cfg.attendance_threshold) for r in rows)
    alerts = world.attendance_alerts()
    if not alerts:
        return f"✅ You're not at risk: every tracked course is at or above {world.cfg.attendance_threshold:g}% with room to spare.\n\n" + cmd_attendance(world)
    return "\n".join(["*🚨 Yes, these need attention*"] + [m.attendance_line(r, world.cfg.attendance_threshold) for r in alerts])


def answer_announcements(world: World, q: str) -> str:
    days = _since_days(q)
    words = [w for w in re.findall(r"[a-z0-9-]+", q) if w not in STOPWORDS and w not in
             {"announcement", "announcements", "announced", "posted", "notice", "news", "update", "updates",
              "week", "month", "yesterday", "last", "recent", "latest", "new"}]
    query = " ".join(words[:3]) or None
    anns = world.nexus.list_announcements(since_days=days if not query else 30, query=query)
    if not anns and query:
        anns = world.nexus.list_announcements(since_days=30, query=words[0])
    if not anns:
        return f"No announcements{' about ' + query if query else ''} in the last {days} day(s)."
    anns = sorted(anns, key=lambda a: a.get("postedAt") or "", reverse=True)
    lines = [f"📣 *Announcements{' about ' + query if query else ''}* (last {days if not query else 30} days)"]
    for a in anns[:8]:
        posted = parse_iso(a.get("postedAt"))
        lines.append(f"• {fmt_day(posted) if posted else ''} · *{a.get('title')}*")
    if len(anns) > 8:
        lines.append(f"_…and {len(anns) - 8} more._")
    return "\n".join(lines)


def answer_instructor(world: World, course: dict[str, Any] | None) -> str:
    if not course:
        return 'Which course? e.g. "who teaches SQL".'
    who = course.get("instructor") or "Nexus doesn't list an instructor yet"
    line = f"👩‍🏫 *{course.get('title')}* ({course.get('term')}): {who}"
    if course.get("instructorLinkedIn"):
        line += f"\n{course['instructorLinkedIn']}"
    return line


def answer_course(world: World, course: dict[str, Any]) -> str:
    parts = [answer_instructor(world, course), answer_next_class(world, course)]
    if course.get("startDate"):
        parts.append(f"Runs {course.get('startDate')} → {course.get('endDate')}")
    return "\n\n".join(parts)


def answer_notifications(world: World) -> str:
    notes = world.nexus.list_notifications(since_days=7, limit=10, unread_only=True)
    if not notes:
        return "✅ No unread notifications this week."
    return "\n".join(["🔔 *Unread notifications*"] + [f"• {n.get('title')}: {n.get('body', '')[:120]}" for n in notes])


def answer_search(world: World, q: str) -> str:
    words = [w for w in re.findall(r"[a-z0-9-]+", q) if w not in STOPWORDS and len(w) > 2]
    for word in words[:3]:
        anns = world.nexus.list_announcements(since_days=30, query=word)
        if anns:
            lines = [f'🔎 Announcements mentioning "{word}":']
            lines += [f"• {fmt_day(parse_iso(a['postedAt']))} · *{a.get('title')}*" for a in anns[:5]]
            return "\n".join(lines)
    return "I couldn't find that in Nexus. " + HELP
