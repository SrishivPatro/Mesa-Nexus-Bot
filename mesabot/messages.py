"""WhatsApp message text. WhatsApp markdown: *bold*, _italic_. All times IST."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterable

from .attendance import CourseAttendance
from .items import Item, World
from .timeutil import (end_of_day, fmt_day, fmt_dt, fmt_relative, fmt_time, parse_iso,
                       start_of_day, week_start)

DONE_HINT = '_Reply "done <keyword>" to clear an item, e.g. "done mrs"._'
MAX_FEEDBACK = 280

KIND_LABEL = {"assignment": "Assignment", "announcement": "Action", "session": "Mandatory",
              "mrs": "MRS", "attendance": "Attendance"}


# ---- shared sections -------------------------------------------------------------------

def session_line(event: dict[str, Any]) -> str:
    start, end = parse_iso(event.get("start")), parse_iso(event.get("end"))
    when = f"{fmt_time(start)}–{fmt_time(end)}" if start and end else (event.get("time") or "")
    where = event.get("room") or ("online" if event.get("meetingLink") else "")
    att = "Y" if event.get("attendanceRequired") else "N"
    title = event.get("title") or event.get("course") or "Session"
    if event.get("cancelled"):
        return f"• ~{when} · {title}~ _cancelled_"
    if event.get("type") == "holiday":
        return f"• 🏖️ {title} (holiday)"
    line = f"• {when} · {title}"
    if where:
        line += f" · {where}"
    line += f" · Attendance: {att}"
    if event.get("meetingLink"):
        line += f"\n   {event['meetingLink']}"
    for name in event.get("attachments") or []:
        line += f"\n   📎 {name if isinstance(name, str) else name.get('name', 'attachment')}"
    return line


def timetable_section(events: list[dict[str, Any]], empty: str = "No classes scheduled.") -> list[str]:
    if not events:
        return [f"_{empty}_"]
    return [session_line(e) for e in events]


def timetable_by_day(events: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    current = None
    for e in events:
        start = parse_iso(e.get("start"))
        day = fmt_day(start) if start else e.get("day", "")
        if day != current:
            lines.append(f"\n*{day}*")
            current = day
        lines.append(session_line(e))
    return lines or ["_No classes scheduled._"]


def item_line(item: Item, now: datetime) -> str:
    label = KIND_LABEL.get(item.kind, "Item")
    tags = [t for t in item.tags if t not in {"submission", "form", "mrs"}]
    if tags and item.kind == "announcement":
        label = " · ".join(t.title() for t in tags[:3])
    if item.carried and item.term:
        label += f" · {item.term}"
    line = f"• [{label}] {item.title}"
    if item.course and item.course not in item.title and item.kind == "assignment":
        line += f" — {short_course(item.course)}"
    if item.due:
        state = "⚠️ " if item.overdue(now) else ""
        line += f"\n   {state}due {fmt_dt(item.due)} ({fmt_relative(item.due, now)})"
    elif item.kind == "assignment":
        line += "\n   no deadline set"
    if item.session_at and item.session_at != item.due:
        line += f" · session {session_when(item)}"
    if item.detail:
        line += f"\n   _{item.detail}_"
    for _, url in item.links:
        line += f"\n   🔗 {url}"
    for _, name in item.files:
        line += f"\n   📎 {name}"
    return line


def session_when(item: Item) -> str:
    return fmt_dt(item.session_at) if item.session_timed else fmt_day(item.session_at)


def items_section(items: list[Item], now: datetime) -> list[str]:
    if not items:
        return ["✅ Nothing pending."]
    current = [i for i in items if not i.carried]
    carried = [i for i in items if i.carried]
    lines = [item_line(i, now) for i in current]
    if carried:
        lines.append(f"\n_Carried forward from {carried[0].term}:_")
        lines += [item_line(i, now) for i in carried]
    return lines


def short_course(course: str | None) -> str:
    return (course or "").split(" with ")[0]


def attendance_line(row: CourseAttendance, threshold: float) -> str:
    name = short_course(row.course)
    suffix = f" _({row.term}, carried forward)_" if row.carried else ""
    if row.status == "untracked":
        return f"• {name}: attendance not tracked"
    if row.status == "no_data":
        return f"• {name}: no attendance taken yet"
    base = f"{name}: *{row.percentage:.1f}%* ({row.attended}/{row.total})"
    if row.status == "below":
        return f"• 🔴 {base} — below {threshold:g}%, attend the next *{row.need}* in a row to recover{suffix}"
    if row.status == "at_risk":
        miss = "can't miss any more" if row.can_miss == 0 else f"can miss only *{row.can_miss}* more"
        return f"• 🟠 {base} — {miss}{suffix}"
    return f"• 🟢 {base} — can miss {row.can_miss} more{suffix}"


def attendance_alert_section(world: World) -> list[str]:
    alerts = world.attendance_alerts()
    if not alerts:
        return [f"✅ All tracked courses at or above {world.cfg.attendance_threshold:g}%."]
    return [attendance_line(r, world.cfg.attendance_threshold) for r in alerts]


def attendance_full_section(world: World) -> list[str]:
    rows = world.attendance
    if not rows:
        return ["_No attendance data yet._"]
    return [attendance_line(r, world.cfg.attendance_threshold) for r in rows]


def grade_line(g: dict[str, Any], with_feedback: bool = True) -> str:
    marks, max_marks = g.get("marks"), g.get("maxMarks")
    score = f"{_num(marks)}/{_num(max_marks)}" if max_marks not in (None, "0.00") else f"{_num(marks)}"
    line = f"• *{g.get('assignment')}* — {short_course(g.get('course'))}: *{score}*"
    released = parse_iso(g.get("releasedAt"))
    if released:
        line += f" _(released {fmt_day(released)})_"
    feedback = (g.get("feedback") or "").strip()
    if with_feedback and feedback:
        text = " ".join(feedback.split())
        line += f"\n   Feedback: {text[:MAX_FEEDBACK]}{'…' if len(text) > MAX_FEEDBACK else ''}"
    return line


def _num(value: Any) -> str:
    try:
        f = float(value)
        return f"{f:g}"
    except (TypeError, ValueError):
        return str(value) if value is not None else "–"


def calendar_section(events: list[dict[str, Any]] | None, now: datetime) -> list[str]:
    if events is None:
        return ["_Google Calendar not connected (set GOOGLE_CALENDAR_CREDENTIALS_JSON)._"]
    if not events:
        return ["_Nothing on your calendar._"]
    lines = []
    for e in events:
        start: datetime = e["start"]
        when = f"{fmt_day(start)} (all day)" if e.get("all_day") else fmt_dt(start)
        line = f"• {when} · {e['title']}"
        if e.get("location"):
            line += f" · {e['location']}"
        lines.append(line)
    return lines


def header(title: str, subtitle: str | None = None) -> list[str]:
    lines = [title]
    if subtitle:
        lines.append(f"_{subtitle}_")
    return lines


def term_subtitle(world: World) -> str:
    term = world.ctx.active
    if not term:
        return "Term not detected"
    day = term.day_number(world.now.date())
    if day and term.end and world.now.date() <= term.end:
        return f"{term.name} · day {day}, ends {fmt_day(term.end)}"
    if term.start and world.now.date() < term.start:
        return f"{term.name} starts {fmt_day(term.start)}"
    return term.name


def join(sections: Iterable[list[str] | str]) -> str:
    out: list[str] = []
    for s in sections:
        out.append(s if isinstance(s, str) else "\n".join(s))
    return "\n\n".join(x for x in out if x.strip())


# ---- Message 1: morning brief ------------------------------------------------------------

def morning_brief(world: World, grades_since: datetime) -> str:
    now = world.now
    today_events = world.timetable(start_of_day(now), end_of_day(now))
    items = world.pending_items()
    new_grades = world.grades_since(grades_since)
    cal = world.calendar(start_of_day(now), now + timedelta(hours=48))
    name = world.first_name
    return join([
        header(f"☀️ *Good morning{', ' + name if name else ''}* — {fmt_day(now)}", term_subtitle(world)),
        ["📅 *Today's timetable*"] + timetable_section(today_events, "No classes today."),
        ["📋 *Pending action items*"] + items_section(items, now) + ([DONE_HINT] if items else []),
        ["🚨 *Attendance alerts*"] + attendance_alert_section(world),
        ["📊 *New grades & feedback*"] + ([grade_line(g) for g in new_grades] or ["_Nothing new since yesterday._"]),
        ["📆 *Google Calendar — today + next 48h*"] + calendar_section(cal, now),
    ])


# ---- Message 2: deadline alert -----------------------------------------------------------

def deadline_alert(due_soon: list[Item], prep: list[Item], calendar: list[dict[str, Any]], now: datetime) -> str:
    sections: list[list[str] | str] = []
    if due_soon:
        sections.append(["⏰ *Due within 24 hours*"] + [item_line(i, now) for i in due_soon])
    if prep:
        lines = ["🗓️ *48h heads-up: prep for an upcoming session*"]
        for i in prep:
            line = f"• {i.title} — session {session_when(i)} ({fmt_relative(i.session_at, now)})"
            if i.due and i.due > now:
                line += f"\n   prep due {fmt_dt(i.due)}"
            for _, url in i.links:
                line += f"\n   🔗 {url}"
            lines.append(line)
        sections.append(lines)
    if calendar:
        sections.append(["📆 *Calendar in the next 24h*"] + calendar_section(calendar, now))
    if sections:
        sections.append(DONE_HINT)
    return join(sections)


# ---- Message 3: grade alert ---------------------------------------------------------------

def grade_alert(grades: list[dict[str, Any]], cards: list[dict[str, Any]]) -> str:
    lines = ["📊 *New grade released*" if len(grades) + len(cards) == 1 else "📊 *New grades released*"]
    lines += [grade_line(g) for g in grades]
    for card in cards:
        term = card.get("term") or card.get("name") or "Term"
        gpa = card.get("gpa") or card.get("sgpa")
        lines.append(f"• Report card published: *{term}*" + (f" — GPA {gpa}" if gpa else ""))
    return "\n".join(lines)


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
    return join([
        header(f"🗓️ *Week ahead* — {fmt_day(start)} to {fmt_day(end - timedelta(days=1))}", term_subtitle(world)),
        ["📝 *Due in the next 7 days*"] + ([item_line(i, now) for i in due_week] or ["✅ Nothing due."]),
        (["⚠️ *Still overdue*"] + [item_line(i, now) for i in overdue]) if overdue else [],
        ["📣 *Flagged from announcements*"] + ([item_line(i, now) for i in flagged] or ["_Nothing flagged._"]),
        ["📅 *Timetable*"] + timetable_by_day(events),
        ["🎯 *Attendance*"] + attendance_full_section(world),
        ["📆 *Google Calendar*"] + calendar_section(cal, now),
    ])
