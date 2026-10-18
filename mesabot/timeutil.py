"""India-time helpers. Mesa runs on Asia/Kolkata (+05:30); every message uses IST."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def now_ist() -> datetime:
    return datetime.now(IST)


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string (with Z or an offset) into an aware IST datetime."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.combine(date.fromisoformat(value[:10]), time(0, 0), IST)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def iso(dt: datetime) -> str:
    return dt.astimezone(IST).isoformat(timespec="seconds")


def start_of_day(dt: datetime) -> datetime:
    return datetime.combine(dt.astimezone(IST).date(), time(0, 0), IST)


def end_of_day(dt: datetime) -> datetime:
    return datetime.combine(dt.astimezone(IST).date(), time(23, 59, 59), IST)


def week_start(dt: datetime) -> datetime:
    """Monday 00:00 IST of the week containing dt."""
    d = dt.astimezone(IST).date()
    return datetime.combine(d - timedelta(days=d.weekday()), time(0, 0), IST)


def week_end(dt: datetime) -> datetime:
    """Sunday 23:59:59 IST of the week containing dt."""
    return week_start(dt) + timedelta(days=6, hours=23, minutes=59, seconds=59)


def next_weekday(base: date, weekday: int, include_today: bool = True) -> date:
    delta = (weekday - base.weekday()) % 7
    if delta == 0 and not include_today:
        delta = 7
    return base + timedelta(days=delta)


def fmt_time(dt: datetime) -> str:
    dt = dt.astimezone(IST)
    hour = dt.hour % 12 or 12
    return f"{hour}:{dt.minute:02d} {'am' if dt.hour < 12 else 'pm'}"


def fmt_day(dt: datetime | date) -> str:
    if isinstance(dt, datetime):
        dt = dt.astimezone(IST).date()
    return f"{dt.strftime('%a')} {dt.day} {dt.strftime('%b')}"


def fmt_dt(dt: datetime) -> str:
    return f"{fmt_day(dt)}, {fmt_time(dt)}"


def fmt_relative(target: datetime, now: datetime) -> str:
    """'in 3h', 'in 2d', '5h overdue'."""
    seconds = (target - now).total_seconds()
    future = seconds >= 0
    seconds = abs(seconds)
    if seconds < 3600:
        amount = f"{max(1, int(seconds // 60))}m"
    elif seconds < 48 * 3600:
        amount = f"{int(seconds // 3600)}h"
    else:
        amount = f"{int(seconds // 86400)}d"
    return f"in {amount}" if future else f"{amount} overdue"
