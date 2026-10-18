from datetime import timedelta

from mesabot.bot import handle_message
from mesabot.jobs import run_deadline_check, run_grade_check, run_morning_brief, run_weekly_summary
from mesabot.timeutil import parse_iso


def test_morning_brief_sections(make_svc):
    svc = make_svc("2026-10-19T08:00:00+05:30")
    text = run_morning_brief(svc)
    for header in ("TODAY", "TO DO", "ATTENDANCE", "NEW GRADES"):
        assert header in text
    assert "`09:30` *Data 102* ✅" in text           # attendance taken
    assert "*Building Time* ·" in text and "*Building Time* ✅" not in text
    assert "*MRS weekly session* · Resume Stress Test" in text
    assert "https://docs.google.com/forms/d/e/1FAIpQLSdFAKEFORM/viewform" in text
    assert "Super_Case.pdf" in text
    assert "Carried forward" not in text             # previous term hidden by default
    assert "Session 4 Workbook" not in text
    assert "Career Readiness" not in text            # untracked: never an alert
    assert "Mid-Term Assessment" in text             # released in the last 24h
    assert "Mid-Term Assignment" not in text         # released 9 days ago
    assert "ILS/BSL opt-in form" not in text         # its deadline passed
    assert "Update on Water Quality" not in text     # not actionable
    assert "End-Term Examination" not in text        # only mentions a day that is over
    assert "New assignment" not in text              # assignment notifications duplicate list_assignments


def test_graded_in_class_assessment_is_not_pending(make_svc):
    text = handle_message(make_svc(), "pending")
    assert "Mid-Term Assessment" not in text         # has marks, so it is resolved


def test_deadline_alert_dedup_and_48h_prep(make_svc):
    svc = make_svc("2026-10-19T19:00:00+05:30")
    first = run_deadline_check(svc)
    assert "Session 1 SQL Warm-up" in first and "48h heads-up" in first
    # One hour later: nothing new to say.
    assert run_deadline_check(svc, now=parse_iso("2026-10-19T20:00:00+05:30")) is None
    # 13 hours later the opt-in is still due within 24h: alerted again, prep reminder not repeated.
    later = run_deadline_check(svc, now=parse_iso("2026-10-20T08:00:00+05:30"))
    assert "Start-up Leader" in later and "48h heads-up" not in later
    history = svc.store.snapshot()["alert_history"]
    assert any(k.startswith("pre48:") for k in history)


def test_mrs_sunday_alert_and_done(make_svc):
    svc = make_svc("2026-10-25T09:00:00+05:30")
    assert "MRS weekly session" in run_deadline_check(svc)
    reply = handle_message(svc, "done mrs")
    assert "MRS marked done" in reply
    assert svc.store.mrs_week("2026-10-19")["done"] is True
    assert "MRS weekly session" not in run_morning_brief(svc)


def test_grade_alert_seeds_then_alerts_once(make_svc):
    svc = make_svc("2026-10-19T18:00:00+05:30")
    assert run_grade_check(svc) is None              # first run: seed, no flood
    assert run_grade_check(svc) is None
    grades = svc.nexus.client._read("get_grades")
    grades["assignmentMarks"].append({"assignmentId": "a6", "assignment": "Data 102 Workbook 1",
                                      "course": "Data 102 with Prof. D", "marks": "9", "maxMarks": "10",
                                      "feedback": "Neat.", "releasedAt": "2026-10-19T10:00:00Z"})
    svc.nexus.client._read = lambda name, orig=svc.nexus.client._read: grades if name == "get_grades" else orig(name)
    text = run_grade_check(svc)
    assert "Data 102 Workbook 1" in text and "9/10" in text
    assert run_grade_check(svc) is None              # never twice
    assert any(v["channel"] == "6pm" for v in svc.store.snapshot()["grade_alerts"].values())


def test_weekly_summary_sunday_covers_next_week(make_svc):
    text = run_weekly_summary(make_svc("2026-10-18T21:00:00+05:30"))
    assert "Mon 19 Oct to Sun 25 Oct" in text
    assert "ATTENDANCE" in text and "TIMETABLE" in text


def test_done_form_and_attachment(make_svc):
    svc = make_svc()
    assert "Marked done" in handle_message(svc, "done track opt-in")
    assert "FAKEFORM" not in run_morning_brief(svc)
    assert "Marked done: Super_Case.pdf" in handle_message(svc, "done super_case.pdf")
    assert "Super_Case.pdf" not in run_morning_brief(svc)
    assert "Several things match" in handle_message(make_svc(carry_forward=True), "done workbook")


def test_carry_forward_when_enabled(make_svc):
    svc = make_svc(carry_forward=True)
    text = run_morning_brief(svc)
    assert "Carried forward from Term 1" in text and "Session 4 Workbook" in text
    assert "Business Frameworks" in text


def test_done_carried_attendance(make_svc):
    svc = make_svc(carry_forward=True)
    assert "Marked done" in handle_message(svc, "done business frameworks attendance")
    assert "Business Frameworks" not in handle_message(svc, "attendance")


def test_commands_and_questions(make_svc):
    svc = make_svc("2026-10-19T19:00:00+05:30")
    assert "Fri 23 Oct" in handle_message(svc, "schedule friday")
    assert "Next SQL: *Fri 23 Oct, 11:30 am*" in handle_message(svc, "when is my next SQL class")
    assert "92/100" in handle_message(svc, "what did I score in Art of Selling")
    assert "Yes" in handle_message(svc, "am I at risk of low attendance")
    assert "Track opt-in" in handle_message(svc, "what announcements came this week")
    assert "Prof. D" in handle_message(svc, "who teaches data 102")
    assert "Mesa bot commands" in handle_message(svc, "help")
    assert "DUE TODAY" in handle_message(svc, "due")
    assert "Resume Stress Test" in handle_message(svc, "mrs")
