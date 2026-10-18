from datetime import date

from mesabot.attendance import analyze, margin
from mesabot.config import DEFAULT_UNTRACKED_COURSES
from mesabot.terms import detect_terms, match_course

COURSES = [
    {"title": "Start Strong", "term": "Pre-Term", "startDate": None, "endDate": None},
    {"title": "Finance with B", "term": "Term 1", "startDate": "2026-08-17", "endDate": "2026-10-08"},
    {"title": "Data Driven Decision Making", "term": "Term 1", "startDate": "2026-08-20", "endDate": "2026-10-14"},
    {"title": "AI Workshops", "term": "Term 1", "startDate": None, "endDate": None},
    {"title": "Mesa Start-up Lab", "term": "Term 1", "startDate": None, "endDate": None},
    {"title": "Data 102", "term": "Term 2", "startDate": "2026-10-18", "endDate": "2026-12-18"},
    {"title": "SQL with F", "term": "Term 2", "startDate": "2026-10-23", "endDate": "2026-12-12"},
    {"title": "Career Readiness", "term": "Term 2", "startDate": None, "endDate": None},
]


def test_margin_math():
    assert margin(9, 13, 75) == (0, 3)     # 69.2% → 3 in a row: 12/16 = 75%
    assert margin(4, 8, 75) == (0, 8)      # 50%   → 12/16
    assert margin(15, 19, 75) == (1, 0)    # 78.9% → 15/20 = 75%
    assert margin(3, 4, 75) == (0, 0)      # exactly 75%: cannot miss
    assert margin(12, 13, 75) == (3, 0)    # 12/16
    assert margin(0, 0, 75) == (None, None)


def test_active_previous_and_gap_detection():
    ctx = detect_terms(COURSES, date(2026, 10, 19))
    assert (ctx.active_name, ctx.previous_name) == ("Term 2", "Term 1")
    # Between terms, a term starting within 14 days becomes active early.
    ctx = detect_terms(COURSES, date(2026, 10, 16))
    assert (ctx.active_name, ctx.previous_name) == ("Term 2", "Term 1")
    ctx = detect_terms(COURSES, date(2026, 9, 1))
    assert ctx.active_name == "Term 1"


def test_new_term_is_picked_up_without_code_changes():
    courses = COURSES + [{"title": "Strategy", "term": "Term 3", "startDate": "2027-01-04", "endDate": "2027-03-01"}]
    ctx = detect_terms(courses, date(2027, 1, 10))
    assert (ctx.active_name, ctx.previous_name) == ("Term 3", "Term 2")


def test_attendance_untracked_carry_forward_and_no_data():
    ctx = detect_terms(COURSES, date(2026, 10, 19))
    summary = {"courses": [
        {"course": "Data Driven Decision Making", "term": "Term 1", "attended": 9, "total": 13, "percentage": 69.2},
        {"course": "Finance with B", "term": "Term 1", "attended": 12, "total": 13, "percentage": 92.3},
        {"course": "Career Readiness", "term": "Term 2", "attended": 0, "total": 2, "percentage": 0},
        {"course": "Data 102", "term": "Term 2", "attended": 3, "total": 4, "percentage": 75},
    ]}
    rows = {r.course: r for r in analyze(summary, ctx, 75, 1, DEFAULT_UNTRACKED_COURSES)}
    assert rows["Career Readiness"].status == "untracked" and not rows["Career Readiness"].alert
    assert rows["SQL with F"].status == "no_data" and not rows["SQL with F"].alert
    assert rows["Data 102"].status == "at_risk" and rows["Data 102"].can_miss == 0
    assert rows["Data Driven Decision Making"].carried and rows["Data Driven Decision Making"].need == 3
    assert "Finance with B" not in rows  # previous term and fine: nothing to carry


def test_match_course():
    assert match_course("when is my next finance class", COURSES)["title"] == "Finance with B"
    assert match_course("what did i score in sql", COURSES)["title"] == "SQL with F"
    assert match_course("hello there", COURSES) is None
