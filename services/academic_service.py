"""Academic years, classes, sections, subjects, teacher assignments and the grading/promotion policy."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from config.settings import DEFAULT_POLICY
from database.connection import session_scope
from database.models import (AcademicYear, SchoolClass, SchoolSetting, Section, Subject, TeacherAssignment)
from services import audit
from services.errors import NotFound, ValidationFailed
from services.permissions import ADMIN, CurrentUser, require_role


def get_policy(session) -> dict:
    row = session.get(SchoolSetting, "policy")
    policy = dict(DEFAULT_POLICY)
    if row:
        policy.update(row.value)
    return policy


def update_policy(actor: CurrentUser, changes: dict) -> dict:
    require_role(actor, ADMIN)
    with session_scope() as s:
        policy = get_policy(s)
        for key in ("pass_percentage", "promotion_min_percentage", "promotion_min_attendance"):
            if key in changes:
                try:
                    value = Decimal(str(changes[key]))
                except InvalidOperation:
                    raise ValidationFailed(f"{key} must be a number.")
                if not (Decimal(0) <= value <= Decimal(100)):
                    raise ValidationFailed(f"{key} must be between 0 and 100.")
        if "grade_bands" in changes:
            bands = changes["grade_bands"]
            try:
                parsed = [(Decimal(str(m)), str(g).strip()) for m, g in bands]
            except (InvalidOperation, ValueError, TypeError):
                raise ValidationFailed("Grade bands must be pairs of (minimum percentage, grade).")
            if not parsed or min(m for m, _ in parsed) != 0 or any(not g for _, g in parsed):
                raise ValidationFailed("Grade bands need a grade for 0% and no blank grade names.")
            changes["grade_bands"] = [[str(m), g] for m, g in sorted(parsed, key=lambda x: -x[0])]
        previous = dict(policy)
        policy.update(changes)
        row = s.get(SchoolSetting, "policy")
        if row:
            row.value = policy
        else:
            s.add(SchoolSetting(key="policy", value=policy))
        audit.log(s, actor, "policy_update", "SchoolSetting", "policy", previous, policy)
        return policy


def create_year(actor: CurrentUser, name: str, start: dt.date, end: dt.date, make_current: bool = False) -> int:
    require_role(actor, ADMIN)
    if end <= start:
        raise ValidationFailed("The end date must be after the start date.")
    try:
        with session_scope() as s:
            year = AcademicYear(name=name.strip(), start_date=start, end_date=end, is_current=False)
            s.add(year)
            s.flush()
            audit.log(s, actor, "year_create", "AcademicYear", year.id, None, {"name": name})
            if make_current:
                _set_current(s, actor, year.id)
            return year.id
    except IntegrityError:
        raise ValidationFailed("An academic year with that name already exists.")


def _set_current(s, actor, year_id: int) -> None:
    if s.get(AcademicYear, year_id) is None:
        raise NotFound("Academic year not found.")
    s.execute(update(AcademicYear).values(is_current=False))
    s.execute(update(AcademicYear).where(AcademicYear.id == year_id).values(is_current=True))
    audit.log(s, actor, "year_set_current", "AcademicYear", year_id)


def set_current_year(actor: CurrentUser, year_id: int) -> None:
    require_role(actor, ADMIN)
    with session_scope() as s:
        _set_current(s, actor, year_id)


def get_current_year_id(s) -> int | None:
    return s.scalar(select(AcademicYear.id).where(AcademicYear.is_current.is_(True)))


def _simple_create(actor, model, record_type, **fields) -> int:
    require_role(actor, ADMIN)
    try:
        with session_scope() as s:
            obj = model(**fields)
            s.add(obj)
            s.flush()
            audit.log(s, actor, f"{record_type.lower()}_create", record_type, obj.id, None, fields)
            return obj.id
    except IntegrityError:
        raise ValidationFailed(f"That {record_type.lower()} already exists (name/code/level must be unique).")


def create_class(actor, name: str, level: int) -> int:
    if level < 1:
        raise ValidationFailed("Class level must be 1 or higher.")
    return _simple_create(actor, SchoolClass, "SchoolClass", name=name.strip(), level=level)


def create_section(actor, name: str) -> int:
    return _simple_create(actor, Section, "Section", name=name.strip().upper())


def create_subject(actor, code: str, name: str) -> int:
    return _simple_create(actor, Subject, "Subject", code=code.strip().upper(), name=name.strip())


def assign_teacher(actor: CurrentUser, teacher_id: int, year_id: int, class_id: int, section_id: int,
                   subject_id: int | None = None, is_class_teacher: bool = False) -> int:
    require_role(actor, ADMIN)
    with session_scope() as s:
        dup = s.scalar(select(TeacherAssignment.id).where(
            TeacherAssignment.teacher_id == teacher_id, TeacherAssignment.academic_year_id == year_id,
            TeacherAssignment.class_id == class_id, TeacherAssignment.section_id == section_id,
            TeacherAssignment.subject_id.is_(None) if subject_id is None else TeacherAssignment.subject_id == subject_id))
        if dup:
            raise ValidationFailed("This teacher is already assigned to that class and subject.")
        a = TeacherAssignment(teacher_id=teacher_id, academic_year_id=year_id, class_id=class_id,
                              section_id=section_id, subject_id=subject_id, is_class_teacher=is_class_teacher)
        s.add(a)
        s.flush()
        audit.log(s, actor, "teacher_assign", "TeacherAssignment", a.id, None,
                  {"teacher": teacher_id, "class": class_id, "section": section_id, "subject": subject_id})
        return a.id


def remove_assignment(actor: CurrentUser, assignment_id: int) -> None:
    require_role(actor, ADMIN)
    with session_scope() as s:
        a = s.get(TeacherAssignment, assignment_id)
        if a is None:
            raise NotFound("Assignment not found.")
        audit.log(s, actor, "teacher_unassign", "TeacherAssignment", a.id,
                  {"teacher": a.teacher_id, "class": a.class_id, "section": a.section_id}, None)
        s.delete(a)


def reference_data() -> dict:
    """Small lookup tables used by every page's filters."""
    with session_scope() as s:
        years = [{"id": y.id, "name": y.name, "is_current": y.is_current, "start": y.start_date, "end": y.end_date}
                 for y in s.scalars(select(AcademicYear).order_by(AcademicYear.start_date.desc()))]
        classes = [{"id": c.id, "name": c.name, "level": c.level}
                   for c in s.scalars(select(SchoolClass).order_by(SchoolClass.level))]
        sections = [{"id": x.id, "name": x.name} for x in s.scalars(select(Section).order_by(Section.name))]
        subjects = [{"id": x.id, "code": x.code, "name": x.name}
                    for x in s.scalars(select(Subject).order_by(Subject.name))]
        return {"years": years, "classes": classes, "sections": sections, "subjects": subjects,
                "policy": get_policy(s)}


def list_teachers(actor: CurrentUser) -> list[dict]:
    from database.models import Teacher, User
    from services.permissions import OVERSIGHT_ROLES

    require_role(actor, *OVERSIGHT_ROLES)
    with session_scope() as s:
        rows = s.execute(select(Teacher.id, Teacher.employee_no, Teacher.phone, User.id, User.full_name, User.username,
                                User.is_active).join(User, User.id == Teacher.user_id).order_by(User.full_name))
        return [{"teacher_id": r[0], "employee_no": r[1], "phone": r[2], "user_id": r[3], "name": r[4],
                 "username": r[5], "is_active": r[6]} for r in rows]


def list_assignments(actor: CurrentUser, year_id: int) -> list[dict]:
    from database.models import Teacher, User

    with session_scope() as s:
        stmt = (select(TeacherAssignment.id, User.full_name, SchoolClass.name, Section.name, Subject.name,
                       TeacherAssignment.is_class_teacher)
                .join(Teacher, Teacher.id == TeacherAssignment.teacher_id).join(User, User.id == Teacher.user_id)
                .join(SchoolClass, SchoolClass.id == TeacherAssignment.class_id)
                .join(Section, Section.id == TeacherAssignment.section_id)
                .outerjoin(Subject, Subject.id == TeacherAssignment.subject_id)
                .where(TeacherAssignment.academic_year_id == year_id).order_by(SchoolClass.level, Section.name))
        if actor.role == "teacher":
            stmt = stmt.where(TeacherAssignment.teacher_id == (actor.teacher_id or -1))
        elif actor.role not in ("admin", "principal"):
            raise PermissionError("Not allowed")
        return [{"id": r[0], "teacher": r[1], "class": r[2], "section": r[3], "subject": r[4] or "All subjects",
                 "class_teacher": r[5]} for r in s.execute(stmt)]


def my_assigned_classes(actor: CurrentUser, year_id: int) -> list[dict]:
    """Class/section pairs the actor may work with (teachers: their assignments; admin/principal: all in use)."""
    from database.models import Enrollment

    with session_scope() as s:
        if actor.role == "teacher":
            stmt = (select(TeacherAssignment.class_id, TeacherAssignment.section_id)
                    .where(TeacherAssignment.teacher_id == (actor.teacher_id or -1),
                           TeacherAssignment.academic_year_id == year_id).distinct())
        elif actor.role in ("admin", "principal"):
            stmt = select(Enrollment.class_id, Enrollment.section_id).where(Enrollment.academic_year_id == year_id).distinct()
        else:
            return []
        pairs = s.execute(stmt).all()
        names_c = {c.id: (c.name, c.level) for c in s.scalars(select(SchoolClass))}
        names_s = {x.id: x.name for x in s.scalars(select(Section))}
        out = [{"class_id": c, "section_id": sec, "class": names_c[c][0], "section": names_s[sec], "level": names_c[c][1]}
               for c, sec in pairs]
        return sorted(out, key=lambda r: (r["level"], r["section"]))
