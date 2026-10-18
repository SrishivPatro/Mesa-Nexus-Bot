"""Message text. Markup is WhatsApp-style (*bold*, _italic_, ~strike~, `code`), which
telegram.to_html converts to Telegram HTML. All times IST.

Layout rules: one bold CAPS header per section, one urgency dot per item
(⚠️ overdue · 🔴 < 24h · 🟠 < 3 days · 🟢 later · ⚪ no deadline), short titles on the
first line and details indented on the next, times in `code` so they line up.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Iterable

from .attendance import CourseAttendance
from .items import Item, World
from .timeutil import (end_of_day, fmt_day, fmt_dt, fmt_relative, fmt_time, parse_iso,
                       start_of_day, week_start)

DONE_HINT = '_Reply "done <word>" to tick something off, e.g. done mrs_'
MAX_FEEDBACK = 280
INDENT = "      "

TAG_LABEL = {"opt-in": "opt-in", "pre-work": "pre-work", "one-pager": "one-pager",
             "pre-read": "pre-read", "case study": "case study", "rsvp": "RSVP",
             "mandatory": "mandatory", "apply": "apply"}
_TITLE_NOISE = re.compile(r"^\s*(?:very important|important|action required|reminder|deadline reminder)\s*[:\-–—]\s*", re.I)


# ---- small helpers -----------------------------------------------------------------------

def short_course(course: str | None) -> str:
    return (course or "").split(" with ")[0].strip()


def clean_title(title: str | None) -> str:
    title = (title or "").strip().strip("<>").strip()
    return _TITLE_NOISE.sub("", title) or title


def urgency(due: datetime | None, now: datetime) -> str:
    if due is None:
        return "⚪"
    hours = (due - now).total_seconds() / 3600
    if hours < 0:
        return "⚠️"
    if hours <= 24:
        return "🔴"
    if hours <= 72:
        return "🟠"
    return "🟢"


def _hhmm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def _num(value: Any) -> str:
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return str(value) if value is not None else "–"


def section(title: str, lines: list[str], note: str | None = None) -> list[str]:
    out = [f"*{title}*"] + lines
    if note:
        out.append(note)
    return out


def join(sections: Iterable[list[str] | str]) -> str:
    out = [s if isinstance(s, str) else "\n".join(s) for s in sections]
    return "\n\n".join(x for x in out if x.strip())


# ---- timetable ---------------------------------------------------------------------------

def session_line(event: dict[str, Any]) -> str:
    """`09:30` *Data 102* ✅ · Delta Classroom"""
    start, end = parse_iso(event.get("start")), parse_iso(event.get("end"))
    title = short_course(event.get("title") or event.get("course") or "Session")
    if event.get("type") == "holiday":
        return f"🏖️ *{title}*: holiday"
    when = f"`{_hhmm(start)}`" if start else ""
    if event.get("cancelled"):
        return f"{when} ~{title}~ _cancelled_"
    line = f"{when} *{title}*" + (" ✅" if event.get("attendanceRequired") else "")
    where = event.get("room") or ("online" if event.get("meetingLink") else "")
    if where:
        line += f" · {where}"
    if start and end and (end - start) >= timedelta(hours=3):
        line += f" · till {fmt_time(end)}"
    if event.get("meetingLink"):
        line += f"\n{INDENT}🔗 {event['meetingLink']}"
    for name in event.get("attachments") or []:
        line += f"\n{INDENT}📎 {name if isinstance(name, str) else name.get('name', 'attachment')}"
    return line


ATTENDANCE_LEGEND = "_✅ = attendance taken_"


def timetable_section(events: list[dict[str, Any]], empty: str = "No classes 🎉") -> list[str]:
    if not events:
        return [f"_{empty}_"]
    lines = [session_line(e) for e in events]
    if any(e.get("attendanceRequired") for e in events):
        lines.append(ATTENDANCE_LEGEND)
    return lines


def timetable_by_day(events: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    current = None
    for e in events:
        start = parse_iso(e.get("start"))
        day = fmt_day(start) if start else e.get("day", "")
        if day != current:
            lines.append(f"\n_{day}_" if lines else f"_{day}_")
            current = day
        lines.append(session_line(e))
    if not lines:
        return ["_No classes scheduled._"]
    if any(e.get("attendanceRequired") for e in events):
        lines.append(ATTENDANCE_LEGEND)
    return lines


# ---- to-do items -------------------------------------------------------------------------

def session_when(item: Item) -> str:
    return fmt_dt(item.session_at) if item.session_timed else fmt_day(item.session_at)


def item_line(item: Item, now: datetime) -> str:
    """🔴 *Start-up Leader Session* · opt-in, one-pager
          Tue 20 Oct, 5:00 pm · in 33h · session Wed 21 Oct"""
    title = clean_title(item.title)
    extra = ""
    if item.kind == "assignment" and item.course and short_course(item.course).lower() not in title.lower():
        extra = short_course(item.course)
    elif item.kind == "mrs":
        title, _, module = title.partition(": ")
        title, extra = "MRS weekly session", module
    else:
        tags = [TAG_LABEL[t] for t in item.tags if t in TAG_LABEL]
        extra = ", ".join(tags[:3])
    line = f"{urgency(item.due or item.session_at, now)} *{title}*" + (f" · {extra}" if extra else "")

    details = []
    if item.due:
        if item.overdue(now):
            details.append(f"was due {fmt_dt(item.due)} · {fmt_relative(item.due, now)}")
        else:
            details.append(f"{fmt_dt(item.due)} · {fmt_relative(item.due, now)}")
    elif item.kind == "assignment":
        details.append("no deadline set")
    if item.session_at and item.session_at != item.due:
        details.append(f"session {session_when(item)}")
    if item.carried and item.term:
        details.append(item.term)
    if details:
        line += f"\n{INDENT}" + " · ".join(details)
    if item.detail and item.kind != "mrs":
        line += f"\n{INDENT}_{item.detail}_"
    for _, url in item.links:
        line += f"\n{INDENT}🔗 {url}"
    for _, name in item.files:
        line += f"\n{INDENT}📎 {name}"
    return line


def items_section(items: list[Item], now: datetime) -> list[str]:
    if not items:
        return ["✅ All clear. Nothing pending."]
    current = [i for i in items if not i.carried]
    carried = [i for i in items if i.carried]
    lines = [item_line(i, now) for i in current]
    if carried:
        lines.append(f"\n_Carried forward from {carried[0].term}_")
        lines += [item_line(i, now) for i in carried]
    return lines


# ---- attendance --------------------------------------------------------------------------

def attendance_line(row: CourseAttendance, threshold: float) -> str:
    name = short_course(row.course)
    suffix = f" _({row.term})_" if row.carried else ""
    if row.status == "untracked":
        return f"⚪ {name} · not tracked"
    if row.status == "no_data":
        return f"⚪ {name} · no classes yet"
    base = f"*{name}* {row.percentage:.0f}% ({row.attended}/{row.total})"
    if row.status == "below":
        return f"🔴 {base}\n{INDENT}below {threshold:g}%, attend the next *{row.need}* to recover{suffix}"
    if row.status == "at_risk":
        miss = "can't miss any more" if row.can_miss == 0 else f"can miss only *{row.can_miss}* more"
        return f"🟠 {base}\n{INDENT}{miss}{suffix}"
    return f"🟢 {base} · can miss {row.can_miss}{suffix}"


def attendance_alert_section(world: World) -> list[str]:
    alerts = world.attendance_alerts()
    if not alerts:
        return [f"✅ All courses safe (≥ {world.cfg.attendance_threshold:g}%)"]
    return [attendance_line(r, world.cfg.attendance_threshold) for r in alerts]


def attendance_full_section(world: World) -> list[str]:
    rows = world.attendance
    if not rows:
        return ["_No attendance data yet._"]
    return [attendance_line(r, world.cfg.attendance_threshold) for r in rows]


# ---- grades ------------------------------------------------------------------------------

def grade_line(g: dict[str, Any], with_feedback: bool = True) -> str:
    marks, max_marks = g.get("marks"), g.get("maxMarks")
    score = f"{_num(marks)}/{_num(max_marks)}" if max_marks not in (None, "0.00") else _num(marks)
    line = f"📝 *{g.get('assignment')}* · {short_course(g.get('course'))}\n{INDENT}*{score}*"
    released = parse_iso(g.get("releasedAt"))
    if released:
        line += f" · released {fmt_day(released)}"
    feedback = " ".join((g.get("feedback") or "").split())
    if with_feedback and feedback:
        line += f"\n{INDENT}_{feedback[:MAX_FEEDBACK]}{'…' if len(feedback) > MAX_FEEDBACK else ''}_"
    return line


# ---- calendar ----------------------------------------------------------------------------

def calendar_section(events: list[dict[str, Any]] | None, now: datetime) -> list[str]:
    if events is None:
        return ["_Google Calendar not connected_"]
    if not events:
        return ["_Nothing on your calendar_"]
    lines = []
    for e in events:
        start: datetime = e["start"]
        when = f"{fmt_day(start)} · all day" if e.get("all_day") else f"{fmt_day(start)} `{_hhmm(start)}`"
        lines.append(f"{when} *{e['title']}*" + (f" · {e['location']}" if e.get("location") else ""))
    return lines


# ---- headers -----------------------------------------------------------------------------

def term_subtitle(world: World) -> str:
    term = world.ctx.active
    if not term:
        return ""
    day = term.day_number(world.now.date())
    if day and term.end and world.now.date() <= term.end:
        return f"{term.name} · day {day}"
    if term.start and world.now.date() < term.start:
        return f"{term.name} starts {fmt_day(term.start)}"
    return term.name


def header(title: str, subtitle: str | None = None) -> list[str]:
    return [title] + ([f"_{subtitle}_"] if subtitle else [])


# ---- Message 1: morning brief --------------------------------------------------------------

def morning_brief(world: World, grades_since: datetime) -> str:
    now = world.now
    today_events = world.timetable(start_of_day(now), end_of_day(now))
    items = world.pending_items()
    new_grades = world.grades_since(grades_since)
    cal = world.calendar(start_of_day(now), now + timedelta(hours=48))
    name = world.first_name
    subtitle = " · ".join(x for x in (fmt_day(now), term_subtitle(world)) if x)
    sections: list[list[str] | str] = [
        header(f"☀️ *Good morning{', ' + name if name else ''}*", subtitle),
        section("📅 TODAY", timetable_section(today_events)),
        section(f"📌 TO DO ({len(items)})" if items else "📌 TO DO", items_section(items, now),
                DONE_HINT if items else None),
        section("🎯 ATTENDANCE", attendance_alert_section(world)),
        section("📊 NEW GRADES", [grade_line(g) for g in new_grades] or ["_Nothing new_"]),
    ]
    if cal:  # hidden when not connected or empty
        sections.append(section("🗓️ CALENDAR · next 48h", calendar_section(cal, now)))
    return join(sections)


# ---- Message 2: deadline alert -------------------------------------------------------------

def deadline_alert(due_soon: list[Item], prep: list[Item], calendar: list[dict[str, Any]], now: datetime) -> str:
    sections: list[list[str] | str] = []
    if due_soon:
        sections.append(section("⏰ DUE IN THE NEXT 24 HOURS", [item_line(i, now) for i in due_soon]))
    if prep:
        lines = []
        for i in prep:
            line = f"🗓️ *{clean_title(i.title)}*\n{INDENT}session {session_when(i)} · {fmt_relative(i.session_at, now)}"
            if i.due and i.due > now:
                line += f"\n{INDENT}prep due {fmt_dt(i.due)}"
            for _, url in i.links:
                line += f"\n{INDENT}🔗 {url}"
            lines.append(line)
        sections.append(section("📚 PREP AHEAD · 48h heads-up", lines))
    if calendar:
        sections.append(section("🗓️ CALENDAR · next 24h", calendar_section(calendar, now)))
    if sections:
        sections.append(DONE_HINT)
    return join(sections)


# ---- Message 3: grade alert ----------------------------------------------------------------

def grade_alert(grades: list[dict[str, Any]], cards: list[dict[str, Any]]) -> str:
    title = "📊 *New grade released*" if len(grades) + len(cards) == 1 else "📊 *New grades released*"
    lines = [grade_line(g) for g in grades]
    for card in cards:
        term = card.get("term") or card.get("name") or "Term"
        gpa = card.get("gpa") or card.get("sgpa")
        lines.append(f"🧾 Report card published: *{term}*" + (f" · GPA {gpa}" if gpa else ""))
    return join([[title], ["\n\n".join(lines)]])


# ---- Message 4: weekly summary -------------------------------------------------------------

def weekly_summary(world: World, start: datetime | None = None) -> str:
    now = world.now
    start = start or (week_start(now) + timedelta(days=7) if now.weekday() == 6 else start_of_day(now))
    end = start + timedelta(days=7)
    items = world.pending_items()
    due_week = [i for i in items if i.due and now <= i.due < end and i.kind in {"assignment", "mrs"}]
    flagged = [i for i in items if i.kind in {"announcement", "session"}
               and ((i.due and now <= i.due < end) or (i.session_at and now <= i.session_at < end))]
    overdue = [i for i in items if i.overdue(now) and i.kind == "assignment"]
    events = world.timetable(start, end)
    cal = world.calendar(start, end)
    sections: list[list[str] | str] = [
        header(f"🗓️ *Your week* · {fmt_day(start)} to {fmt_day(end - timedelta(days=1))}", term_subtitle(world)),
        section("📝 DUE", [item_line(i, now) for i in due_week] or ["✅ Nothing due"]),
    ]
    if overdue:
        sections.append(section("⚠️ OVERDUE", [item_line(i, now) for i in overdue]))
    sections += [
        section("📣 FROM ANNOUNCEMENTS", [item_line(i, now) for i in flagged] or ["_Nothing flagged_"]),
        section("📅 TIMETABLE", timetable_by_day(events)),
        section("🎯 ATTENDANCE", attendance_full_section(world)),
    ]
    if cal:
        sections.append(section("🗓️ CALENDAR", calendar_section(cal, now)))
    return join(sections)
