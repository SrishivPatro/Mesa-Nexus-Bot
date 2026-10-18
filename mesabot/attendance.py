"""Attendance analysis: margin before the threshold and classes needed to recover."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable

from .terms import TermContext, normalize


@dataclass
class CourseAttendance:
    course: str
    term: str | None
    attended: int
    total: int
    percentage: float | None
    status: str            # ok | at_risk | below | no_data | untracked
    can_miss: int | None   # classes you can still miss and stay at/above threshold
    need: int | None       # consecutive classes needed to get back to the threshold
    carried: bool = False  # from the previous term, still unresolved

    @property
    def alert(self) -> bool:
        return self.status in {"below", "at_risk"}


def margin(attended: int, total: int, threshold: float) -> tuple[int | None, int | None]:
    """(can_miss, need) for a threshold given as a percentage, e.g. 75.

    can_miss = largest k with attended / (total + k) >= t
    need     = smallest x with (attended + x) / (total + x) >= t
    """
    if total <= 0:
        return None, None
    t = threshold / 100.0
    eps = 1e-9
    if attended / total + eps >= t:
        return max(0, math.floor(attended / t - total + eps)), 0
    if t >= 1:
        return 0, None  # 100% can never be recovered once a class is missed
    return 0, max(0, math.ceil((t * total - attended) / (1 - t) - eps))


def is_untracked(course: str, untracked: Iterable[str]) -> bool:
    key = normalize(course)
    return any(key == normalize(name) for name in untracked)


def analyze(summary: dict[str, Any], ctx: TermContext, threshold: float, at_risk_margin: int,
            untracked: Iterable[str], include_previous: bool = True) -> list[CourseAttendance]:
    """Attendance for the active term's courses, plus the previous term's unresolved ones.

    Courses with no attendance record are listed as no_data (new term, nothing taken
    yet) or untracked (AI Workshops, Career Readiness, Workshops, Mesa Startup Lab);
    neither ever raises an alert.
    """
    untracked = list(untracked)
    records = {normalize(r.get("course")): r for r in summary.get("courses", [])}
    results: list[CourseAttendance] = []
    seen: set[str] = set()

    def build(course: str, term: str | None, record: dict[str, Any] | None, carried: bool) -> CourseAttendance:
        if is_untracked(course, untracked):
            return CourseAttendance(course, term, 0, 0, None, "untracked", None, None, carried)
        if not record or not record.get("total"):
            return CourseAttendance(course, term, 0, 0, None, "no_data", None, None, carried)
        attended, total = int(record.get("attended", 0)), int(record.get("total", 0))
        pct = record.get("percentage")
        pct = round(attended / total * 100, 1) if pct is None else float(pct)
        can_miss, need = margin(attended, total, threshold)
        if need:
            status = "below"
        elif can_miss is not None and can_miss <= at_risk_margin:
            status = "at_risk"
        else:
            status = "ok"
        return CourseAttendance(course, term, attended, total, pct, status, can_miss, need, carried)

    active = ctx.active_name
    for course in ctx.courses_in(active):
        title = course.get("title") or ""
        key = normalize(title)
        seen.add(key)
        results.append(build(title, active, records.get(key), carried=False))

    # Records not matched to an active-term course (previous terms, renamed courses).
    for key, record in records.items():
        if key in seen:
            continue
        term = record.get("term") or ctx.term_of_course(record.get("course"))
        if active and term == active:
            results.append(build(record.get("course", ""), term, record, carried=False))
        elif include_previous and term == ctx.previous_name:
            item = build(record.get("course", ""), term, record, carried=True)
            # Carry forward only what is unresolved: below threshold.
            if item.status == "below":
                results.append(item)
    order = {"below": 0, "at_risk": 1, "ok": 2, "no_data": 3, "untracked": 4}
    return sorted(results, key=lambda r: (r.carried, order[r.status], r.percentage or 0))
