"""Term detection from `list_courses`.

Each course carries a `term` label and (sometimes) start/end dates. A term's window
is the earliest start to the latest end of its dated courses. The active term is
the one whose window contains today; between terms, a term starting within
UPCOMING_GRACE_DAYS becomes active early so its courses appear from day one.
The previous term is the latest one that ended before the active term began:
its unresolved items are carried forward. A new term in Nexus is picked up
automatically; nothing here names a term.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from .timeutil import IST, parse_iso

UPCOMING_GRACE_DAYS = 14
_STOP = {"with", "prof", "and", "the", "of", "a", "an", "for", "to", "in", "its", "dr"}


def normalize(name: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def tokens(text: str | None) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if t not in _STOP and len(t) > 1}


@dataclass
class Term:
    name: str
    start: date | None = None
    end: date | None = None
    courses: list[dict[str, Any]] = field(default_factory=list)
    order: int = 0

    @property
    def dated(self) -> bool:
        return self.start is not None and self.end is not None

    def contains(self, day: date) -> bool:
        return self.dated and self.start <= day <= self.end  # type: ignore[operator]

    def day_number(self, day: date) -> int | None:
        return (day - self.start).days + 1 if self.start and day >= self.start else None


@dataclass
class TermContext:
    terms: list[Term]
    active: Term | None
    previous: Term | None
    course_term: dict[str, str]  # normalized course title -> term name

    @property
    def active_name(self) -> str | None:
        return self.active.name if self.active else None

    @property
    def previous_name(self) -> str | None:
        return self.previous.name if self.previous else None

    def term_of_course(self, course: str | None) -> str | None:
        if not course:
            return None
        key = normalize(course)
        if key in self.course_term:
            return self.course_term[key]
        # Attendance/schedule names can differ slightly from list_courses titles.
        for title, term in self.course_term.items():
            if key and (key in title or title in key):
                return term
        return None

    def term_of_date(self, when: datetime | None) -> str | None:
        if when is None:
            return None
        day = when.astimezone(IST).date()
        for term in self.terms:
            if term.contains(day):
                return term.name
        return None

    def tracked_terms(self) -> set[str]:
        """Terms whose items are in scope: the active one plus the previous (carry-forward)."""
        return {t for t in (self.active_name, self.previous_name) if t}

    def courses_in(self, term_name: str | None) -> list[dict[str, Any]]:
        for term in self.terms:
            if term.name == term_name:
                return term.courses
        return []

    def all_courses(self) -> list[dict[str, Any]]:
        return [c for t in self.terms for c in t.courses]


def build_terms(courses: list[dict[str, Any]]) -> list[Term]:
    terms: dict[str, Term] = {}
    for index, course in enumerate(courses):
        name = course.get("term") or "Unassigned"
        term = terms.setdefault(name, Term(name=name, order=index))
        term.courses.append(course)
        start = parse_iso(course.get("startDate"))
        end = parse_iso(course.get("endDate"))
        if start:
            term.start = min(term.start, start.date()) if term.start else start.date()
        if end:
            term.end = max(term.end, end.date()) if term.end else end.date()
    return sorted(terms.values(), key=lambda t: (t.start or date.min, t.order))


def detect_terms(courses: list[dict[str, Any]], today: date) -> TermContext:
    terms = build_terms(courses)
    dated = [t for t in terms if t.dated]
    active: Term | None = None

    current = [t for t in dated if t.contains(today)]
    if current:
        active = max(current, key=lambda t: t.start)  # overlapping terms: the newer one
    else:
        upcoming = [t for t in dated if t.start > today]  # type: ignore[operator]
        soon = [t for t in upcoming if (t.start - today).days <= UPCOMING_GRACE_DAYS]  # type: ignore[operator]
        ended = [t for t in dated if t.end < today]  # type: ignore[operator]
        if soon:
            active = min(soon, key=lambda t: t.start)
        elif ended:
            active = max(ended, key=lambda t: t.end)
        elif upcoming:
            active = min(upcoming, key=lambda t: t.start)
        elif terms:
            active = terms[-1]

    previous: Term | None = None
    if active and active.start:
        before = [t for t in dated if t is not active and t.end < active.start]  # type: ignore[operator]
        if not before:
            before = [t for t in dated if t is not active and t.start < active.start]  # type: ignore[operator]
        if before:
            previous = max(before, key=lambda t: t.end)

    course_term = {normalize(c.get("title")): (c.get("term") or "Unassigned") for c in courses}
    return TermContext(terms=terms, active=active, previous=previous, course_term=course_term)


# Everyday words that also appear in course names ("Orientation Week", "Business Sprint Lab"):
# they must never pick a course on their own.
_QUESTION_WORDS = {
    "week", "weeks", "weekly", "day", "days", "month", "today", "tomorrow", "yesterday", "next", "last", "this",
    "class", "classes", "session", "sessions", "lecture", "lectures", "how", "many", "much", "what", "when",
    "where", "who", "which", "have", "do", "does", "did", "my", "me", "can", "could", "should", "will", "is",
    "are", "am", "skip", "bunk", "miss", "attend", "attendance", "score", "scored", "marks", "mark", "grade",
    "grades", "got", "get", "teaches", "teach", "time", "total", "left", "remaining", "any", "all", "so", "no",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "morning", "evening",
}


def match_course(question: str, courses: list[dict[str, Any]],
                 prefer_term: str | None = None) -> dict[str, Any] | None:
    """Best course for free text like 'my next Finance class' or 'Art of Selling'."""
    asked = tokens(question) - _QUESTION_WORDS
    best: tuple[float, dict[str, Any] | None] = (0.0, None)
    for course in courses:
        title = course.get("title") or ""
        subject = title.split(" with ")[0]
        subject_tokens = tokens(subject)
        all_tokens = subject_tokens | tokens(course.get("code")) | tokens(course.get("instructor"))
        hits = asked & all_tokens
        if not hits:
            continue
        score = len(hits & subject_tokens) * 2 + len(hits - subject_tokens)
        score += len(hits) / max(1, len(subject_tokens)) * 0.5
        if prefer_term and course.get("term") == prefer_term:
            score += 0.25
        if normalize(subject) and normalize(subject) in normalize(question):
            score += 3
        if score > best[0]:
            best = (score, course)
    return best[1]
