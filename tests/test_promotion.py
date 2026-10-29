import datetime as dt

import pytest
from sqlalchemy import func, select

from database.connection import session_scope
from database.models import Attendance, Enrollment, Student
from services import attendance_service as att
from services import audit, promotion_service as ps
from services.errors import PermissionDenied, ValidationFailed


def status_of(world, student_id, year):
    with session_scope() as s:
        return s.scalar(select(Enrollment.status).where(Enrollment.student_id == student_id,
                                                        Enrollment.academic_year_id == year))


def enrollment_count() -> int:
    with session_scope() as s:
        return s.scalar(select(func.count(Enrollment.id)))


def decisions(world):
    e = lambda i: world.enr(world.a[i], world.py)  # noqa: E731
    return [ps.Decision(e(0), "promoted", world.c9, world.secA, 10),
            ps.Decision(e(1), "retained", world.c8, world.secA, 5),
            ps.Decision(e(2), "transferred"),
            ps.Decision(e(3), "graduated")]


def test_listing_candidates_changes_nothing(world):
    before = enrollment_count()
    rows = ps.eligible_students(world.admin, world.py, world.cy, world.c8)
    assert len(rows) == 4 and all(r["suggested"] in ps.OUTCOMES for r in rows)
    assert enrollment_count() == before  # never automatic: a decision must be applied explicitly


def test_students_already_in_target_year_are_not_listed(world):
    # enrol a[0] into the target year first
    with session_scope() as s:
        s.add(Enrollment(student_id=world.a[0], academic_year_id=world.cy, class_id=world.c9, section_id=world.secB, roll_no=1))
    assert len(ps.eligible_students(world.admin, world.py, world.cy, world.c8)) == 3


def test_promotion_creates_new_enrollments_and_preserves_history(world):
    old = world.enr(world.a[0], world.py)
    att.mark_attendance(world.admin, world.py, world.c8, world.secA, world.cy_start - dt.timedelta(days=5),
                        {world.enr(sid, world.py): "A" for sid in world.a})
    counts = ps.apply_promotion(world.admin, world.py, world.cy, decisions(world))
    assert counts == {"promoted": 1, "retained": 1, "transferred": 1, "graduated": 1}
    # new year
    with session_scope() as s:
        new = s.scalar(select(Enrollment).where(Enrollment.student_id == world.a[0], Enrollment.academic_year_id == world.cy))
        assert (new.class_id, new.section_id, new.roll_no, new.status) == (world.c9, world.secA, 10, "active")
        retained = s.scalar(select(Enrollment).where(Enrollment.student_id == world.a[1], Enrollment.academic_year_id == world.cy))
        assert retained.class_id == world.c8
        for sid in (world.a[2], world.a[3]):
            assert s.scalar(select(Enrollment.id).where(Enrollment.student_id == sid, Enrollment.academic_year_id == world.cy)) is None
        # history untouched
        previous = s.get(Enrollment, old)
        assert (previous.class_id, previous.section_id, previous.roll_no, previous.academic_year_id) == (world.c8, world.secA, 1, world.py)
        assert s.scalar(select(func.count(Attendance.id)).where(Attendance.enrollment_id == old)) == 1
        assert s.get(Student, world.a[2]).status == "transferred" and s.get(Student, world.a[3]).status == "graduated"
    assert [status_of(world, world.a[i], world.py) for i in range(4)] == ["promoted", "retained", "transferred", "graduated"]


def test_duplicate_promotion_is_prevented(world):
    ps.apply_promotion(world.admin, world.py, world.cy, decisions(world))
    total = enrollment_count()
    with pytest.raises(ValidationFailed):
        ps.apply_promotion(world.admin, world.py, world.cy, decisions(world))
    assert enrollment_count() == total


def test_one_invalid_decision_blocks_the_whole_batch(world):
    bad = [ps.Decision(world.enr(world.a[0], world.py), "promoted", world.c9, world.secA, 10),
           ps.Decision(world.enr(world.a[1], world.py), "promoted", world.c10, world.secA, 11)]  # skips a level
    before = enrollment_count()
    with pytest.raises(ValidationFailed):
        ps.apply_promotion(world.admin, world.py, world.cy, bad)
    assert enrollment_count() == before and status_of(world, world.a[0], world.py) == "active"


def test_failure_in_the_middle_rolls_everything_back(world, monkeypatch):
    real, calls = audit.log, {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated crash")
        return real(*args, **kwargs)

    monkeypatch.setattr(audit, "log", flaky)
    before = enrollment_count()
    with pytest.raises(RuntimeError):
        ps.apply_promotion(world.admin, world.py, world.cy, decisions(world))
    assert enrollment_count() == before
    assert all(status_of(world, world.a[i], world.py) == "active" for i in range(4))
    with session_scope() as s:
        assert s.get(Student, world.a[2]).status == "active"


def test_preview_reports_problems_without_writing(world):
    before = enrollment_count()
    ok = ps.preview_promotion(world.admin, world.py, world.cy, decisions(world))
    assert ok["ok"] and ok["counts"]["promoted"] == 1
    clash = decisions(world) + [ps.Decision(world.enr(world.a[0], world.py), "retained", world.c8, world.secA)]
    assert not ps.preview_promotion(world.admin, world.py, world.cy, clash)["ok"]  # same enrollment twice
    assert enrollment_count() == before


def test_roll_number_clash_and_year_order_validated(world):
    clash = [ps.Decision(world.enr(world.a[0], world.py), "promoted", world.c9, world.secA, 1)]  # b1 has roll 1 in 9-A
    with pytest.raises(ValidationFailed):
        ps.apply_promotion(world.admin, world.py, world.cy, clash)
    with pytest.raises(ValidationFailed):  # target year must be later
        ps.apply_promotion(world.admin, world.cy, world.py, [ps.Decision(world.enr(world.b[0], world.cy), "retained", world.c9, world.secA, 7)])


def test_only_administrators_can_promote(world):
    for actor in (world.t1, world.principal, world.s1, world.p1):
        with pytest.raises(PermissionDenied):
            ps.apply_promotion(actor, world.py, world.cy, decisions(world))
        with pytest.raises(PermissionDenied):
            ps.eligible_students(actor, world.py, world.cy)


def test_promotion_is_audited(world):
    ps.apply_promotion(world.admin, world.py, world.cy, decisions(world))
    from tests.conftest import audit_rows
    assert len(audit_rows("promotion_promoted")) == 1 and len(audit_rows("promotion_batch")) == 1
