from datetime import datetime

from mesabot.parsing import (extract_attachments, extract_dates, extract_urls, is_google_form,
                             mrs_module, parse_text)
from mesabot.timeutil import IST, parse_iso

POSTED = parse_iso("2026-10-17T11:21:17.875Z")


def at(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=IST)


def test_urls_and_forms():
    body = ("Fill the form here: https://docs.google.com/forms/d/e/1FAIpQLSd/viewform.\n"
            "Ask: https://forms.gle/8PQpStCJHoVmB7yy5 and see [https://bit.ly/3VxvnHO]")
    urls = extract_urls(body)
    assert urls == ["https://docs.google.com/forms/d/e/1FAIpQLSd/viewform",
                    "https://forms.gle/8PQpStCJHoVmB7yy5", "https://bit.ly/3VxvnHO"]
    assert [u for u in urls if is_google_form(u)] == urls[:2]


def test_attachments_from_field_and_text():
    names = extract_attachments("See Viva_Slots_AI.pdf and https://x.com/a.pdf", ["Report 1.pdf"])
    assert names == ["Report 1.pdf", "Viva_Slots_AI.pdf"]


def test_deadline_with_weekday_ordinal_and_time():
    body = ("first Start-up Leader Session on Wednesday, 21st October.\n"
            "📌 Deadline for both opt-in and pre-work submission: Tuesday, 20th October, 5 PM.")
    dates = extract_dates(body, POSTED, "Kick-off")
    assert [(d.when, d.is_deadline) for d in dates] == [(at(2026, 10, 20, 17, 0), True),
                                                       (at(2026, 10, 21, 9, 0), False)]
    assert dates[1].has_time is False


def test_relative_today_merges_with_explicit_date():
    posted = parse_iso("2026-10-15T08:27:18Z")
    dates = extract_dates("fill out the opt-in form by today (Oct 15), 11:59 PM.", posted)
    assert len(dates) == 1 and dates[0].when == at(2026, 10, 15, 23, 59) and dates[0].is_deadline


def test_tomorrow_far_from_deadline_word():
    body = ("the deadline to complete the Tell Me About Yourself session on MRS Practice Lab "
            "is tomorrow 18th Oct at 11:59 PM.")
    dates = extract_dates(body, parse_iso("2026-10-17T07:45:08Z"), "Deadline Reminder")
    assert dates[0].when == at(2026, 10, 18, 23, 59) and dates[0].is_deadline


def test_two_times_same_day_and_weekday_reference():
    body = ("• Case Study Submission (due by 11:59 PM today): Link\n"
            "• Difficulty Selection (due by 6:00 PM today): Link\n"
            "• Your build submission will be due by 11:59 PM on Monday.")
    dates = extract_dates(body, parse_iso("2026-10-10T07:37:20Z"))  # a Saturday
    assert [d.when for d in dates] == [at(2026, 10, 10, 18, 0), at(2026, 10, 10, 23, 59),
                                      at(2026, 10, 12, 23, 59)]


def test_time_range_and_event_vs_deadline():
    body = ("📅 Date: Tuesday, 13th October 2026\n⏱️ Time: 6:30–8:30 PM\n"
            "Share it with us by 13th October, 12:00 PM: https://forms.gle/x")
    dates = extract_dates(body, parse_iso("2026-10-12T14:23:27Z"))
    assert [(d.when, d.is_deadline) for d in dates] == [(at(2026, 10, 13, 12, 0), True),
                                                       (at(2026, 10, 13, 18, 30), False)]


def test_year_rollover():
    dates = extract_dates("Applications close: 5 January", parse_iso("2026-12-20T10:00:00Z"))
    assert dates[0].when.year == 2027 and dates[0].is_deadline


def test_mrs_module_patterns():
    assert mrs_module("This week (Monday, 12 October to Sunday, 18 October), your assigned module "
                      "is Tell Me About Yourself.\nWhat") == "Tell Me About Yourself"
    assert mrs_module("deadline to complete the Resume Stress Test session on MRS Practice Lab") == "Resume Stress Test"
    assert mrs_module("No Tell Me About Yourself session yet this week. Finish one") == "Tell Me About Yourself"
    assert mrs_module("Garba night") is None


def test_tags_and_prep():
    p = parse_text("announcement", "1", "Pre-Read for Tomorrow's Session",
                   "Prof shared a case study as a pre-read.", POSTED)
    assert {"pre-read", "case study"} <= set(p.tags) and p.is_prep and p.actionable
    p = parse_text("announcement", "2", "Garba Night", "Get your batchmates together", POSTED)
    assert not p.actionable
