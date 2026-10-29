import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from database.connection import session_scope
from database.models import Attendance
from services import attendance_service as att
from services.errors import PermissionDenied, ValidationFailed
from tests.conftest import audit_rows


def _entries(world, statuses):
    return {world.enr(sid, world.cy): st for sid, st in zip(world.b, statuses)}


def _count():
    with session_scope() as s:
        return s.scalar(select(func.count(Attendance.id)))


def test_percentage_formula():
    assert att.attendance_percentage(8, 1, 1, 5) == Decimal(90)           # excused ignored, late counts
    assert att.attendance_percentage(8, 1, 1, 5, late_counts_as_present=False) == Decimal(80)
    assert att.attendance_percentage(0, 0, 0, 4) is None                   # nothing countable
    assert att.attendance_percentage(0, 0, 0, 0) is None


def test_teacher_marks_and_cannot_duplicate(world):
    day = world.today
    att.mark_attendance(world.t1, world.cy, world.c9, world.secA, day, _entries(world, "PAL"))
    assert _count() == 3
    res = att.mark_attendance(world.t1, world.cy, world.c9, world.secA, day, _entries(world, "PAL"))
    assert res == {"created": 0, "updated": 0, "unchanged": 3}
    assert _count() == 3  # still one row per student per day


def test_edit_is_audited_with_old_and_new_status(world):
    att.mark_attendance(world.t1, world.cy, world.c9, world.secA, world.today, _entries(world, "PPP"))
    att.mark_attendance(world.t1, world.cy, world.c9, world.secA, world.today, _entries(world, "APP"))
    edit = audit_rows("attendance_edit")[-1]
    assert edit.previous_value["status"] == "P" and edit.new_value["status"] == "A"


def test_future_dates_rejected(world):
    with pytest.raises(ValidationFailed):
        att.mark_attendance(world.admin, world.cy, world.c9, world.secA, world.today + dt.timedelta(days=1),
                            _entries(world, "PPP"))


def test_date_outside_academic_year_rejected(world):
    with pytest.raises(ValidationFailed):
        att.mark_attendance(world.admin, world.cy, world.c9, world.secA, world.cy_start - dt.timedelta(days=1),
                            _entries(world, "PPP"))


def test_invalid_status_and_foreign_students_rejected(world):
    with pytest.raises(ValidationFailed):
        att.mark_attendance(world.admin, world.cy, world.c9, world.secA, world.today, _entries(world, "PXP"))
    with pytest.raises(ValidationFailed):  # previous-year student is not in this class
        att.mark_attendance(world.admin, world.cy, world.c9, world.secA, world.today,
                            {world.enr(world.a[0], world.py): "P"})


def test_only_assigned_teacher_or_admin_can_mark(world):
    for actor in (world.t2, world.principal, world.s1, world.p1):
        with pytest.raises(PermissionDenied):
            att.mark_attendance(actor, world.cy, world.c9, world.secA, world.today, _entries(world, "PPP"))
    att.mark_attendance(world.admin, world.cy, world.c9, world.secA, world.today, _entries(world, "PPP"))


def test_teacher_cannot_edit_old_attendance_but_admin_can(world):
    old = world.today - dt.timedelta(days=30)
    att.mark_attendance(world.admin, world.cy, world.c9, world.secA, old, _entries(world, "PPP"))
    with pytest.raises(PermissionDenied):
        att.mark_attendance(world.t1, world.cy, world.c9, world.secA, old, _entries(world, "AAA"))
    att.mark_attendance(world.admin, world.cy, world.c9, world.secA, old, _entries(world, "AAA"))


def test_summary_history_and_scoping(world):
    for offset, status in enumerate("PPALE"):
        att.mark_attendance(world.admin, world.cy, world.c9, world.secA, world.today - dt.timedelta(days=offset),
                            _entries(world, status * 3))
    summary = att.student_summary(world.s1, world.b[0])
    assert (summary["P"], summary["A"], summary["L"], summary["E"]) == (2, 1, 1, 1)
    assert summary["percentage"] == Decimal(3) * 100 / Decimal(4)  # (2 present + 1 late) / 4 counted days
    assert len(att.student_history(world.p1, world.b[0])) == 5
    with pytest.raises(PermissionDenied):
        att.student_summary(world.s1, world.b[1])
    with pytest.raises(PermissionDenied):
        att.student_history(world.p1, world.b[1])


def test_class_reports_and_trends(world):
    att.mark_attendance(world.admin, world.cy, world.c9, world.secA, world.today, _entries(world, "PAL"))
    pct = att.class_percentages(world.admin, world.cy)
    assert pct[0]["class"] == "Class 9" and pct[0]["A"] == 1
    trend = att.monthly_trend(world.admin, world.cy)
    assert sum(t["absent"] for t in trend) == 1 and sum(t["late"] for t in trend) == 1
    assert att.top_absentees(world.admin, world.cy)[0]["absences"] == 1
    assert att.breakdown(world.t2, world.cy)["recorded_days"] == 0  # t2 is not assigned to 9-A
    assert att.breakdown(world.t1, world.cy)["recorded_days"] == 3
