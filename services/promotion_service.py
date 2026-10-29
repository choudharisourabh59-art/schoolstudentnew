"""Year-end class promotion. Always an explicit, authorised decision per student; always transactional."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from database.connection import session_scope
from database.models import (AcademicYear, Attendance, Enrollment, Examination, ExaminationSubject, SchoolClass,
                             Section, Student, StudentResult, utcnow)
from services import audit
from services.academic_service import get_policy
from services.attendance_service import attendance_percentage
from services.errors import NotFound, ValidationFailed
from services.grade_service import grade_for
from services.permissions import ADMIN, CurrentUser, require_role

OUTCOMES = ("promoted", "retained", "transferred", "graduated", "withdrawn")
STUDENT_STATUS = {"transferred": "transferred", "graduated": "graduated", "withdrawn": "withdrawn"}


@dataclass
class Decision:
    enrollment_id: int
    outcome: str
    target_class_id: int | None = None
    target_section_id: int | None = None
    roll_no: int | None = None


def _years(s, from_year_id: int, to_year_id: int):
    from_year, to_year = s.get(AcademicYear, from_year_id), s.get(AcademicYear, to_year_id)
    if from_year is None or to_year is None:
        raise NotFound("Academic year not found.")
    if from_year.id == to_year.id or to_year.start_date <= from_year.start_date:
        raise ValidationFailed("The target academic year must come after the current academic year.")
    return from_year, to_year


def eligible_students(actor: CurrentUser, from_year_id: int, to_year_id: int, class_id: int | None = None,
                      section_id: int | None = None) -> list[dict]:
    """Students still awaiting a decision, with performance data and a *suggested* outcome (never automatic)."""
    require_role(actor, ADMIN)
    with session_scope() as s:
        _years(s, from_year_id, to_year_id)
        policy = get_policy(s)
        max_level = s.scalar(select(func.max(SchoolClass.level))) or 0
        already = select(Enrollment.student_id).where(Enrollment.academic_year_id == to_year_id)
        stmt = (select(Enrollment.id, Student.student_code, Student.full_name, SchoolClass.name, SchoolClass.level,
                       Section.name, Enrollment.roll_no, Enrollment.class_id, Enrollment.section_id)
                .join(Student, Student.id == Enrollment.student_id)
                .join(SchoolClass, SchoolClass.id == Enrollment.class_id)
                .join(Section, Section.id == Enrollment.section_id)
                .where(Enrollment.academic_year_id == from_year_id, Enrollment.status == "active",
                       Student.is_archived.is_(False), Enrollment.student_id.not_in(already)))
        if class_id:
            stmt = stmt.where(Enrollment.class_id == class_id)
        if section_id:
            stmt = stmt.where(Enrollment.section_id == section_id)
        rows = s.execute(stmt.order_by(SchoolClass.level, Section.name, Enrollment.roll_no, Student.full_name)).all()
        ids = [r[0] for r in rows]
        marks: dict[int, tuple] = {}
        att: dict[int, dict] = {}
        if ids:
            for eid, got, mx in s.execute(
                    select(StudentResult.enrollment_id, func.sum(StudentResult.marks), func.sum(ExaminationSubject.max_marks))
                    .join(ExaminationSubject, ExaminationSubject.id == StudentResult.exam_subject_id)
                    .join(Examination, Examination.id == ExaminationSubject.examination_id)
                    .where(StudentResult.enrollment_id.in_(ids), Examination.status == "published",
                           StudentResult.marks.is_not(None)).group_by(StudentResult.enrollment_id)):
                marks[eid] = (got, mx)
            for eid, status, n in s.execute(select(Attendance.enrollment_id, Attendance.status, func.count())
                                            .where(Attendance.enrollment_id.in_(ids))
                                            .group_by(Attendance.enrollment_id, Attendance.status)):
                att.setdefault(eid, {"P": 0, "A": 0, "L": 0, "E": 0})[status] = n
        out = []
        for eid, code, name, cname, level, sname, roll, cid, sid in rows:
            pct = None
            if eid in marks and marks[eid][1]:
                pct = Decimal(str(marks[eid][0])) * 100 / Decimal(str(marks[eid][1]))
            a = att.get(eid)
            apct = attendance_percentage(a["P"], a["A"], a["L"], a["E"], policy["late_counts_as_present"]) if a else None
            if level >= max_level:
                suggestion = "graduated"
            elif pct is not None and pct >= Decimal(str(policy["promotion_min_percentage"])) and (
                    apct is None or apct >= Decimal(str(policy["promotion_min_attendance"]))):
                suggestion = "promoted"
            else:
                suggestion = "retained"
            out.append({"enrollment_id": eid, "student_code": code, "name": name, "class": cname, "level": level,
                        "class_id": cid, "section": sname, "section_id": sid, "roll_no": roll,
                        "percentage": pct, "attendance": apct, "suggested": suggestion})
        return out


def _validate(s, actor, from_year_id, to_year_id, decisions: list[Decision]):
    _years(s, from_year_id, to_year_id)
    policy = get_policy(s)
    if not decisions:
        raise ValidationFailed("No promotion decisions were supplied.")
    errors: list[str] = []
    seen: set[int] = set()
    rolls: set[tuple] = set()
    plan = []
    classes = {c.id: c for c in s.scalars(select(SchoolClass))}
    sections = {x.id for x in s.scalars(select(Section))}
    for d in decisions:
        if d.enrollment_id in seen:
            errors.append(f"Enrollment {d.enrollment_id} appears twice.")
            continue
        seen.add(d.enrollment_id)
        enr = s.get(Enrollment, d.enrollment_id)
        student = s.get(Student, enr.student_id) if enr else None
        label = student.full_name if student else f"enrollment {d.enrollment_id}"
        if enr is None or enr.academic_year_id != from_year_id:
            errors.append(f"{label}: not enrolled in the selected current year.")
            continue
        if enr.status != "active":
            errors.append(f"{label}: a decision has already been recorded ({enr.status}).")
            continue
        if s.scalar(select(Enrollment.id).where(Enrollment.student_id == enr.student_id,
                                                Enrollment.academic_year_id == to_year_id)):
            errors.append(f"{label}: already enrolled in the target year (duplicate promotion).")
            continue
        if d.outcome not in OUTCOMES:
            errors.append(f"{label}: unknown outcome '{d.outcome}'.")
            continue
        if d.outcome in ("promoted", "retained"):
            target = classes.get(d.target_class_id)
            current = classes[enr.class_id]
            if target is None or d.target_section_id not in sections:
                errors.append(f"{label}: choose a target class and section.")
                continue
            wanted = current.level + (int(policy["promotion_level_step"]) if d.outcome == "promoted" else 0)
            if target.level != wanted:
                errors.append(f"{label}: '{d.outcome}' requires a class at level {wanted}, not {target.name}.")
                continue
            if d.roll_no is not None:
                key = (d.target_class_id, d.target_section_id, d.roll_no)
                clash = s.scalar(select(Enrollment.id).where(
                    Enrollment.academic_year_id == to_year_id, Enrollment.class_id == d.target_class_id,
                    Enrollment.section_id == d.target_section_id, Enrollment.roll_no == d.roll_no))
                if d.roll_no < 1 or key in rolls or clash:
                    errors.append(f"{label}: roll number {d.roll_no} is invalid or already taken.")
                    continue
                rolls.add(key)
        plan.append((d, enr, student))
    return plan, errors


def preview_promotion(actor: CurrentUser, from_year_id: int, to_year_id: int, decisions: list[Decision]) -> dict:
    """Dry run: validates every decision and reports problems without writing anything."""
    require_role(actor, ADMIN)
    with session_scope() as s:
        plan, errors = _validate(s, actor, from_year_id, to_year_id, decisions)
        counts: dict[str, int] = {}
        for d, _e, _s in plan:
            counts[d.outcome] = counts.get(d.outcome, 0) + 1
        return {"ok": not errors, "errors": errors, "counts": counts}


def apply_promotion(actor: CurrentUser, from_year_id: int, to_year_id: int, decisions: list[Decision]) -> dict:
    """All decisions are applied or none are: any problem aborts and rolls back the whole operation."""
    require_role(actor, ADMIN)
    with session_scope() as s:
        plan, errors = _validate(s, actor, from_year_id, to_year_id, decisions)
        if errors:
            more = f" (+{len(errors) - 8} more)" if len(errors) > 8 else ""
            raise ValidationFailed("Promotion not applied. " + " | ".join(errors[:8]) + more)
        counts: dict[str, int] = {}
        for d, enr, student in plan:
            previous = {"year": from_year_id, "class": enr.class_id, "section": enr.section_id,
                        "roll_no": enr.roll_no, "status": enr.status}
            enr.status, enr.decided_by, enr.decided_at = d.outcome, actor.id, utcnow()
            new = {"outcome": d.outcome}
            if d.outcome in ("promoted", "retained"):
                s.add(Enrollment(student_id=enr.student_id, academic_year_id=to_year_id, class_id=d.target_class_id,
                                 section_id=d.target_section_id, roll_no=d.roll_no, status="active"))
                new.update({"year": to_year_id, "class": d.target_class_id, "section": d.target_section_id,
                            "roll_no": d.roll_no})
            if d.outcome in STUDENT_STATUS:
                student.status = STUDENT_STATUS[d.outcome]
            audit.log(s, actor, f"promotion_{d.outcome}", "Student", enr.student_id, previous, new)
            counts[d.outcome] = counts.get(d.outcome, 0) + 1
        try:
            s.flush()  # surfaces any unique-constraint violation inside the transaction (=> full rollback)
        except IntegrityError:
            raise ValidationFailed("Promotion not applied: a database constraint (duplicate enrollment or roll number) was violated.")
        audit.log(s, actor, "promotion_batch", "AcademicYear", to_year_id, {"from_year": from_year_id}, counts)
        return counts
