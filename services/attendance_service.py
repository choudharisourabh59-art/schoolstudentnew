"""Attendance capture and calculations. Future dates are never counted; Excused is excluded from the rate."""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import func, select

from config.settings import today
from database.connection import session_scope
from database.models import (ATTENDANCE_STATUSES, AcademicYear, Attendance, Enrollment, SchoolClass, Section, Student)
from services import audit
from services.academic_service import get_policy
from services.errors import NotFound, PermissionDenied, ValidationFailed
from services.permissions import (TEACHER, CurrentUser, enrollment_scope, require_class_access, require_student_access)


def attendance_percentage(present: int, absent: int, late: int, excused: int,
                          late_counts_as_present: bool = True) -> Decimal | None:
    """(present [+ late]) / (present + late + absent) * 100. Excused days are left out. None if no countable days."""
    counted = present + late + absent
    if counted == 0:
        return None
    attended = present + (late if late_counts_as_present else 0)
    return Decimal(attended) * Decimal(100) / Decimal(counted)


def _empty() -> dict:
    return {"P": 0, "A": 0, "L": 0, "E": 0}


def _with_pct(counts: dict, late_counts: bool = True) -> dict:
    out = dict(counts)
    out["recorded_days"] = sum(counts.values())
    out["percentage"] = attendance_percentage(counts["P"], counts["A"], counts["L"], counts["E"], late_counts)
    return out


def mark_attendance(actor: CurrentUser, year_id: int, class_id: int, section_id: int, day: dt.date,
                    entries: dict[int, str]) -> dict:
    """entries maps enrollment_id -> P/A/L/E. Creates or edits records in one transaction."""
    if not entries:
        raise ValidationFailed("No attendance entries were supplied.")
    if set(entries.values()) - set(ATTENDANCE_STATUSES):
        raise ValidationFailed("Attendance status must be P, A, L or E.")
    if day > today():
        raise ValidationFailed("Attendance cannot be recorded for a future date.")
    with session_scope() as s:
        year = s.get(AcademicYear, year_id)
        if year is None:
            raise NotFound("Academic year not found.")
        if not (year.start_date <= day <= year.end_date):
            raise ValidationFailed("That date is outside the selected academic year.")
        require_class_access(s, actor, year_id, class_id, section_id, write=True)
        ids = list(entries)
        valid = set(s.scalars(select(Enrollment.id).where(
            Enrollment.id.in_(ids), Enrollment.academic_year_id == year_id, Enrollment.class_id == class_id,
            Enrollment.section_id == section_id)))
        if valid != set(ids):
            raise ValidationFailed("One or more students are not enrolled in this class and section.")
        existing = {a.enrollment_id: a for a in s.scalars(
            select(Attendance).where(Attendance.date == day, Attendance.enrollment_id.in_(ids)))}
        if existing and actor.role == TEACHER:
            if (today() - day).days > int(get_policy(s)["attendance_edit_days"]):
                raise PermissionDenied("Teachers can only edit recent attendance; ask an administrator.")
        created = updated = unchanged = 0
        for eid, status in entries.items():
            row = existing.get(eid)
            if row is None:
                s.add(Attendance(enrollment_id=eid, date=day, status=status, marked_by=actor.id))
                created += 1
            elif row.status != status:
                audit.log(s, actor, "attendance_edit", "Attendance", row.id,
                          {"date": day, "status": row.status}, {"date": day, "status": status})
                row.status, row.marked_by = status, actor.id
                updated += 1
            else:
                unchanged += 1
        audit.log(s, actor, "attendance_mark", "ClassSection", f"{class_id}-{section_id}", None,
                  {"date": day, "created": created, "updated": updated})
        return {"created": created, "updated": updated, "unchanged": unchanged}


def roster(actor: CurrentUser, year_id: int, class_id: int, section_id: int, day: dt.date) -> list[dict]:
    """Students of a class with the status already saved for `day` (None = not yet marked)."""
    with session_scope() as s:
        require_class_access(s, actor, year_id, class_id, section_id)
        rows = s.execute(
            select(Enrollment.id, Enrollment.roll_no, Student.student_code, Student.full_name, Attendance.status)
            .join(Student, Student.id == Enrollment.student_id)
            .outerjoin(Attendance, (Attendance.enrollment_id == Enrollment.id) & (Attendance.date == day))
            .where(Enrollment.academic_year_id == year_id, Enrollment.class_id == class_id,
                   Enrollment.section_id == section_id, Student.is_archived.is_(False),
                   Enrollment.status.in_(("active", "promoted", "retained")))
            .order_by(Enrollment.roll_no, Student.full_name))
        return [{"enrollment_id": r[0], "roll_no": r[1], "student_code": r[2], "name": r[3], "status": r[4]}
                for r in rows]


def student_summary(actor: CurrentUser, student_id: int, year_id: int | None = None) -> dict:
    with session_scope() as s:
        require_student_access(s, actor, student_id)
        stmt = (select(Attendance.status, func.count()).join(Enrollment, Enrollment.id == Attendance.enrollment_id)
                .where(Enrollment.student_id == student_id, Attendance.date <= today()))
        if year_id:
            stmt = stmt.where(Enrollment.academic_year_id == year_id)
        counts = _empty()
        for status, n in s.execute(stmt.group_by(Attendance.status)):
            counts[status] = n
        return _with_pct(counts, bool(get_policy(s)["late_counts_as_present"]))


def student_history(actor: CurrentUser, student_id: int, year_id: int | None = None, limit: int = 400) -> list[dict]:
    with session_scope() as s:
        require_student_access(s, actor, student_id)
        stmt = (select(Attendance.date, Attendance.status, AcademicYear.name, SchoolClass.name, Section.name)
                .join(Enrollment, Enrollment.id == Attendance.enrollment_id)
                .join(AcademicYear, AcademicYear.id == Enrollment.academic_year_id)
                .join(SchoolClass, SchoolClass.id == Enrollment.class_id)
                .join(Section, Section.id == Enrollment.section_id)
                .where(Enrollment.student_id == student_id, Attendance.date <= today()))
        if year_id:
            stmt = stmt.where(Enrollment.academic_year_id == year_id)
        stmt = stmt.order_by(Attendance.date.desc()).limit(limit)
        return [{"date": r[0], "status": r[1], "year": r[2], "class": r[3], "section": r[4]} for r in s.execute(stmt)]


def _scoped(actor, stmt):
    scope = enrollment_scope(actor)
    return stmt if scope is None else stmt.where(scope)


def breakdown(actor: CurrentUser, year_id: int, class_id: int | None = None, section_id: int | None = None,
              since: dt.date | None = None, until: dt.date | None = None) -> dict:
    """Totals of P/A/L/E for the filter (used by the donut chart and the dashboard rate)."""
    until = min(until or today(), today())
    with session_scope() as s:
        stmt = (select(Attendance.status, func.count()).join(Enrollment, Enrollment.id == Attendance.enrollment_id)
                .where(Enrollment.academic_year_id == year_id, Attendance.date <= until))
        if class_id:
            stmt = stmt.where(Enrollment.class_id == class_id)
        if section_id:
            stmt = stmt.where(Enrollment.section_id == section_id)
        if since:
            stmt = stmt.where(Attendance.date >= since)
        counts = _empty()
        for status, n in s.execute(_scoped(actor, stmt).group_by(Attendance.status)):
            counts[status] = n
        return _with_pct(counts, bool(get_policy(s)["late_counts_as_present"]))


def monthly_trend(actor: CurrentUser, year_id: int, class_id: int | None = None,
                  section_id: int | None = None) -> list[dict]:
    with session_scope() as s:
        stmt = (select(Attendance.date, Attendance.status, func.count())
                .join(Enrollment, Enrollment.id == Attendance.enrollment_id)
                .where(Enrollment.academic_year_id == year_id, Attendance.date <= today()))
        if class_id:
            stmt = stmt.where(Enrollment.class_id == class_id)
        if section_id:
            stmt = stmt.where(Enrollment.section_id == section_id)
        late_counts = bool(get_policy(s)["late_counts_as_present"])
        months: dict[str, dict] = defaultdict(_empty)
        for day, status, n in s.execute(_scoped(actor, stmt).group_by(Attendance.date, Attendance.status)):
            months[day.strftime("%Y-%m")][status] += n
        out = []
        for month in sorted(months):
            row = _with_pct(months[month], late_counts)
            out.append({"month": month, "present": row["P"], "absent": row["A"], "late": row["L"],
                        "excused": row["E"], "percentage": row["percentage"]})
        return out


def daily_trend(actor: CurrentUser, year_id: int, class_id=None, section_id=None, days: int = 30) -> list[dict]:
    since = today() - dt.timedelta(days=days)
    with session_scope() as s:
        stmt = (select(Attendance.date, Attendance.status, func.count())
                .join(Enrollment, Enrollment.id == Attendance.enrollment_id)
                .where(Enrollment.academic_year_id == year_id, Attendance.date >= since, Attendance.date <= today()))
        if class_id:
            stmt = stmt.where(Enrollment.class_id == class_id)
        if section_id:
            stmt = stmt.where(Enrollment.section_id == section_id)
        late_counts = bool(get_policy(s)["late_counts_as_present"])
        per_day: dict[dt.date, dict] = defaultdict(_empty)
        for day, status, n in s.execute(_scoped(actor, stmt).group_by(Attendance.date, Attendance.status)):
            per_day[day][status] += n
        return [{"date": d, "percentage": _with_pct(per_day[d], late_counts)["percentage"], **per_day[d]}
                for d in sorted(per_day)]


def class_percentages(actor: CurrentUser, year_id: int, class_id: int | None = None) -> list[dict]:
    with session_scope() as s:
        stmt = (select(SchoolClass.name, SchoolClass.level, Section.name, Attendance.status, func.count())
                .select_from(Attendance).join(Enrollment, Enrollment.id == Attendance.enrollment_id)
                .join(SchoolClass, SchoolClass.id == Enrollment.class_id)
                .join(Section, Section.id == Enrollment.section_id)
                .where(Enrollment.academic_year_id == year_id, Attendance.date <= today()))
        if class_id:
            stmt = stmt.where(Enrollment.class_id == class_id)
        late_counts = bool(get_policy(s)["late_counts_as_present"])
        groups: dict[tuple, dict] = defaultdict(_empty)
        for cname, level, sname, status, n in s.execute(
                _scoped(actor, stmt).group_by(SchoolClass.name, SchoolClass.level, Section.name, Attendance.status)):
            groups[(level, cname, sname)][status] += n
        return [{"class": c, "section": sec, **_with_pct(groups[(lvl, c, sec)], late_counts)}
                for (lvl, c, sec) in sorted(groups)]


def top_absentees(actor: CurrentUser, year_id: int, class_id: int | None = None, section_id: int | None = None,
                  limit: int = 20) -> list[dict]:
    with session_scope() as s:
        stmt = (select(Student.student_code, Student.full_name, func.count().label("n"))
                .select_from(Attendance).join(Enrollment, Enrollment.id == Attendance.enrollment_id)
                .join(Student, Student.id == Enrollment.student_id)
                .where(Enrollment.academic_year_id == year_id, Attendance.status == "A", Attendance.date <= today()))
        if class_id:
            stmt = stmt.where(Enrollment.class_id == class_id)
        if section_id:
            stmt = stmt.where(Enrollment.section_id == section_id)
        stmt = _scoped(actor, stmt).group_by(Student.id, Student.student_code, Student.full_name) \
            .order_by(func.count().desc()).limit(limit)
        return [{"student_code": r[0], "name": r[1], "absences": r[2]} for r in s.execute(stmt)]
