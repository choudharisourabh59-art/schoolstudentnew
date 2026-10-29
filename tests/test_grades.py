from decimal import Decimal

import pytest
from sqlalchemy import func, select

from config.settings import DEFAULT_POLICY
from database.connection import session_scope
from database.models import StudentResult
from services import grade_service as gs
from services.errors import PermissionDenied, ValidationFailed
from tests.conftest import audit_rows, enter_all

POLICY = dict(DEFAULT_POLICY)


def row(subject, mx, ps, marks, absent=False):
    return {"subject": subject, "max": Decimal(mx), "passing": Decimal(ps),
            "marks": None if marks is None else Decimal(marks), "absent": absent}


def result_rows() -> int:
    with session_scope() as s:
        return s.scalar(select(func.count(StudentResult.id)))


# ---------- pure calculation ----------
def test_to_decimal_rules():
    assert gs.to_decimal("85.5") == Decimal("85.50")
    assert gs.to_decimal(None) is None and gs.to_decimal("") is None and gs.to_decimal(float("nan")) is None
    assert gs.to_decimal(72.25) == Decimal("72.25")
    for bad in ("abc", "1.234", "inf"):
        with pytest.raises(ValidationFailed):
            gs.to_decimal(bad)


def test_result_example_from_specification():
    r = gs.calculate_result([row("Mathematics", 100, 35, 85), row("Science", 100, 35, 90), row("English", 100, 35, 80)], POLICY)
    assert r["total"] == 255 and r["max_total"] == 300
    assert r["percentage"] == Decimal(85) and r["grade"] == "A" and r["result"] == "PASS"


def test_subject_failure_fails_the_result_even_with_good_percentage():
    r = gs.calculate_result([row("Maths", 100, 35, 95), row("Science", 100, 35, 30)], POLICY)
    assert r["percentage"] > 40 and r["result"] == "FAIL" and r["failed_subjects"] == ["Science"]
    relaxed = {**POLICY, "require_subject_pass": False}
    assert gs.calculate_result([row("Maths", 100, 35, 95), row("Science", 100, 35, 30)], relaxed)["result"] == "PASS"


def test_missing_marks_are_incomplete_not_zero():
    r = gs.calculate_result([row("Maths", 100, 35, 80), row("Science", 100, 35, None)], POLICY)
    assert r["result"] == "INCOMPLETE" and r["grade"] is None and r["missing_subjects"] == ["Science"]
    assert r["total"] == 80  # the missing subject contributes nothing, and is not a silent 0 pass/fail


def test_absent_fails_by_default_and_follows_policy():
    rows = [row("Maths", 100, 35, 90), row("Science", 100, 35, None, absent=True)]
    assert gs.calculate_result(rows, POLICY)["result"] == "FAIL"
    relaxed = {**POLICY, "absent_is_fail": False}
    assert gs.calculate_result(rows, relaxed)["result"] == "PASS"  # 90/200 = 45% >= 40%: only the percentage rule applies


def test_decimal_precision_is_not_rounded_internally():
    r = gs.calculate_result([row("Only", 3, 1, 1)], POLICY)
    assert abs(r["percentage"] * 3 - 100) < Decimal("1e-20")
    assert gs.fmt(r["percentage"]) == "33.33"


def test_grade_bands_are_configurable():
    bands = [["75", "Distinction"], ["0", "Pass"]]
    assert gs.grade_for(Decimal("80"), bands) == "Distinction" and gs.grade_for(Decimal("10"), bands) == "Pass"


# ---------- entering marks ----------
def test_invalid_marks_are_rejected_and_nothing_is_saved(world):
    enr = world.enr(world.b[0], world.cy)
    for bad in (101, -1, "abc", 12.345):
        with pytest.raises(ValidationFailed):
            gs.enter_marks(world.t1, world.es_math, [gs.MarkEntry(enr, bad)])
    with pytest.raises(ValidationFailed):
        gs.enter_marks(world.t1, world.es_math, [gs.MarkEntry(enr, 50, True)])  # absent AND marks
    with pytest.raises(ValidationFailed):
        gs.enter_marks(world.t1, world.es_math, [gs.MarkEntry(enr, 50), gs.MarkEntry(enr, 60)])  # duplicate student
    assert result_rows() == 0


def test_valid_batch_with_one_bad_row_saves_nothing(world):
    entries = [gs.MarkEntry(world.enr(world.b[0], world.cy), 70), gs.MarkEntry(world.enr(world.b[1], world.cy), 500)]
    with pytest.raises(ValidationFailed):
        gs.enter_marks(world.t1, world.es_math, entries)
    assert result_rows() == 0


def test_blank_mark_stays_missing_and_is_never_zero(world):
    enter_all(world, [80, None, None], [40, None, None])
    assert result_rows() == 2
    roster = gs.marks_roster(world.t1, world.es_math, world.secA)
    assert [r["marks"] for r in roster][1:] == [None, None]
    assert roster[0]["marks"] == Decimal("80.00")


def test_duplicate_results_are_impossible(world):
    enter_all(world, [80, 70, 60], [40, 30, 20])
    enter_all(world, [81, 71, 61], [41, 31, 21])  # re-entering updates, never duplicates
    assert result_rows() == 6


def test_only_assigned_teacher_or_admin_may_enter_marks(world):
    entry = [gs.MarkEntry(world.enr(world.b[0], world.cy), 70)]
    for actor in (world.t2, world.principal, world.s1, world.p1):
        with pytest.raises(PermissionDenied):
            gs.enter_marks(actor, world.es_math, entry)
    gs.enter_marks(world.t1, world.es_math, entry)


def test_changing_a_mark_is_audited_and_result_recalculated(world):
    enter_all(world, [80, 90, 40], [40, 45, 10])
    before = gs.exam_results(world.admin, world.exam, world.c9)
    assert before[0]["percentage"] == Decimal(80) and before[0]["result"] == "PASS" and before[0]["grade"] == "A"
    assert before[1]["grade"] == "A+" and before[2]["result"] == "FAIL"
    gs.enter_marks(world.t1, world.es_math, [gs.MarkEntry(world.enr(world.b[0], world.cy), 100)])
    after = gs.exam_results(world.admin, world.exam, world.c9)
    assert after[0]["percentage"] == Decimal(140) * 100 / Decimal(150) and after[0]["grade"] == "A+"
    entry = audit_rows("marks_change")[-1]
    assert entry.previous_value["marks"] == "80.00" and entry.new_value["marks"] == "100.00"


# ---------- publishing ----------
def test_publish_requires_complete_marks_unless_allowed(world):
    enter_all(world, [80, None, None], [40, None, None])
    with pytest.raises(ValidationFailed):
        gs.publish_results(world.principal, world.exam)
    gs.publish_results(world.principal, world.exam, allow_incomplete=True)
    rows = gs.exam_results(world.admin, world.exam, world.c9)
    assert rows[0]["result"] == "PASS" and rows[1]["result"] == "INCOMPLETE"


def test_teachers_cannot_publish(world):
    enter_all(world, [80, 90, 40], [40, 45, 10])
    with pytest.raises(PermissionDenied):
        gs.publish_results(world.t1, world.exam)


def test_students_and_parents_only_see_published_results_for_themselves(world):
    enter_all(world, [80, 90, 40], [40, 45, 10])
    with pytest.raises(PermissionDenied):
        gs.exam_results(world.s1, world.exam, world.c9)
    assert gs.student_results(world.s1, world.b[0]) == []
    gs.publish_results(world.admin, world.exam)
    rows = gs.exam_results(world.s1, world.exam, world.c9)
    assert len(rows) == 1 and rows[0]["name"] == "Curr Student 1"
    assert len(gs.student_results(world.p1, world.b[0])) == 1
    with pytest.raises(PermissionDenied):
        gs.student_results(world.p2, world.b[0])


def test_published_results_need_authority_and_a_reason_to_amend(world):
    enter_all(world, [80, 90, 40], [40, 45, 10])
    gs.publish_results(world.admin, world.exam)
    entry = [gs.MarkEntry(world.enr(world.b[0], world.cy), 85)]
    with pytest.raises(PermissionDenied):
        gs.enter_marks(world.t1, world.es_math, entry)
    with pytest.raises(ValidationFailed):
        gs.enter_marks(world.admin, world.es_math, entry)
    gs.enter_marks(world.admin, world.es_math, entry, reason="Re-evaluation approved")
    assert audit_rows("marks_change")[-1].new_value["reason"] == "Re-evaluation approved"
    with pytest.raises(ValidationFailed):
        gs.reopen_examination(world.admin, world.exam, " ")
    gs.reopen_examination(world.admin, world.exam, "Moderation")
    assert gs.list_examinations(world.cy)[0]["status"] == "draft"


def test_dashboard_helpers(world):
    enter_all(world, [80, None, None], [40, 45, None])
    assert gs.students_with_missing_marks(world.admin, world.cy) == 2  # b1 lacks Maths, b2 lacks both; b0 is complete
    enter_all(world, [80, 90, 70], [40, 45, 30])
    assert gs.students_with_missing_marks(world.admin, world.cy) == 0
    progress = gs.entry_progress(world.admin, world.cy)[0]
    assert progress["expected"] == 6 and progress["filled"] == 6
    gs.publish_results(world.admin, world.exam)
    assert {d["grade"]: d["students"] for d in gs.grade_distribution(world.admin, world.cy)}["A+"] == 1
    assert gs.class_performance(world.admin, world.cy)[0]["class"] == "Class 9"
