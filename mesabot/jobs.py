"""The four scheduled messages. Each job builds a fresh World, sends it (Telegram or CallMeBot) and
records what it sent in the JSON log so nothing is sent twice."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from . import messages
from .items import Item, World, grade_key
from .services import Services
from .timeutil import iso, parse_iso

log = logging.getLogger(__name__)

RESEND_HOURS = 12      # do not repeat an alert for the same item within 12h
DUE_WINDOW_HOURS = 24
PREP_WINDOW_HOURS = 48


def run_morning_brief(svc: Services, now: datetime | None = None) -> str:
    world = World(svc, now)
    last = parse_iso(svc.store.meta("last_morning_brief"))
    since = last if last and world.now - last < timedelta(days=3) else world.now - timedelta(hours=24)
    text = messages.morning_brief(world, grades_since=since)
    svc.sender.send(text)
    svc.store.set_meta("last_morning_brief", iso(world.now))
    return text


def run_deadline_check(svc: Services, now: datetime | None = None) -> str | None:
    world = World(svc, now)
    t = world.now
    horizon = t + timedelta(hours=DUE_WINDOW_HOURS)

    # Primary feed: the Nexus reminder-automation skill (assignment deadlines).
    feed_ids = {f"asg:{f['id']}" for f in world.reminder_feed(DUE_WINDOW_HOURS) if f.get("kind") == "deadline"}
    items = world.pending_items()
    due_soon = [i for i in items if i.due and t <= i.due <= horizon]
    missing = feed_ids - {i.key for i in due_soon}
    if missing:
        log.info("feed deadlines not in pending list (already submitted/graded/done): %s", missing)

    # Pre-work / one-pager / case study / opt-in items: one extra reminder 48h before the
    # session they prepare for (mandatory sessions themselves are covered by the 24h alert).
    prep: list[Item] = [
        i for i in items
        if i.kind == "announcement" and i.session_at
        and t < i.session_at <= t + timedelta(hours=PREP_WINDOW_HOURS)
        and not svc.store.last_alert(f"pre48:{i.key}")
    ]

    send_due = [i for i in due_soon if not svc.store.alerted_within(f"due:{i.key}", RESEND_HOURS, t)]
    cal = [e for e in (world.calendar(t, horizon) or [])
           if not svc.store.alerted_within(f"cal:{e['id']}:{iso(e['start'])}", RESEND_HOURS, t)]

    if not (send_due or prep or cal):
        return None
    text = messages.deadline_alert(send_due, prep, cal, t)
    svc.sender.send(text)
    for i in send_due:
        svc.store.record_alert(f"due:{i.key}", i.title, t)
    for i in prep:
        svc.store.record_alert(f"pre48:{i.key}", i.title, t)
    for e in cal:
        svc.store.record_alert(f"cal:{e['id']}:{iso(e['start'])}", e["title"], t)
    svc.store.prune_alerts(older_than_days=60, now=t)
    return text


def run_grade_check(svc: Services, now: datetime | None = None) -> str | None:
    world = World(svc, now)
    t = world.now
    grades = world.all_grades()
    cards = world.report_cards()
    if not svc.store.meta("grades_seeded"):
        # First run: remember what is already out instead of pinging about old marks.
        for g in grades + cards:
            svc.store.record_grade(grade_key(g), g, channel="seed", now=t)
        svc.store.set_meta("grades_seeded", True)
        svc.store.set_meta("last_grade_check", iso(t))
        log.info("seeded %d existing grades", len(grades) + len(cards))
        return None
    new_grades = [g for g in grades if not svc.store.grade_alerted(grade_key(g))]
    new_cards = [c for c in cards if not svc.store.grade_alerted(grade_key(c))]
    svc.store.set_meta("last_grade_check", iso(t))
    if not (new_grades or new_cards):
        return None
    text = messages.grade_alert(new_grades, new_cards)
    svc.sender.send(text)
    for g in new_grades + new_cards:
        svc.store.record_grade(grade_key(g), g, channel="6pm", now=t)
    return text


def run_weekly_summary(svc: Services, now: datetime | None = None) -> str:
    world = World(svc, now)
    text = messages.weekly_summary(world)
    svc.sender.send(text)
    svc.store.set_meta("last_weekly_summary", iso(world.now))
    return text


JOBS = {
    "morning": run_morning_brief,
    "deadlines": run_deadline_check,
    "grades": run_grade_check,
    "weekly": run_weekly_summary,
}


def run_safely(name: str, svc: Services) -> None:
    """Scheduler entry point: a failing job logs and tells you, it never kills the process."""
    try:
        JOBS[name](svc)
        svc.store.set_meta(f"last_run_{name}", iso(datetime.now().astimezone()))
    except Exception as exc:  # noqa: BLE001
        log.exception("job %s failed", name)
        try:
            svc.sender.send(f"⚠️ Mesa bot: the {name} job failed ({type(exc).__name__}: {str(exc)[:200]}).")
        except Exception:  # noqa: BLE001
            log.exception("could not report the failure")
