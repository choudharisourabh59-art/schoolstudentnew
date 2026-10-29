"""Student records, enrollments, CSV import, awards and remarks."""
from __future__ import annotations

import csv
import datetime as dt
import io
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError

from config.settings import today
from database.connection import session_scope
from database.models import (AcademicYear, Award, Enrollment, Remark, SchoolClass, Section, Student)
from services import audit
from services.academic_service import get_current_year_id
from services.errors import NotFound, ValidationFailed
from services.permissions import (ADMIN, TEACHER, CurrentUser, require_role, require_student_access, student_scope)

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
EDITABLE = ("full_name", "date_of_birth", "gender", "admission_date", "address", "phone", "email", "guardian_name",
            "guardian_phone", "emergency_contact_name", "emergency_contact_phone")


class StudentIn(BaseModel):
    full_name: str = Field(min_length=2, max_length=150)
    date_of_birth: dt.date
    admission_date: dt.date
    gender: str | None = None
    address: str = ""
    phone: str = Field(default="", max_length=30)
    email: str | None = None
    guardian_name: str = Field(default="", max_length=150)
    guardian_phone: str = Field(default="", max_length=30)
    emergency_contact_name: str = Field(default="", max_length=150)
    emergency_contact_phone: str = Field(default="", max_length=30)
    student_code: str | None = Field(default=None, max_length=30)

    @field_validator("full_name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = " ".join(v.split())
        if len(v) < 2:
            raise ValueError("name is too short")
        return v

    @model_validator(mode="after")
    def _check(self):
        if self.date_of_birth >= today():
            raise ValueError("date of birth must be in the past")
        if self.admission_date < self.date_of_birth:
            raise ValueError("admission date cannot be before the date of birth")
        if self.gender not in (None, "", "Male", "Female", "Other"):
            raise ValueError("gender must be Male, Female or Other")
        if self.email and not _EMAIL.match(self.email):
            raise ValueError("e-mail address is not valid")
        return self


def parse_student(data: dict) -> StudentIn:
    try:
        return StudentIn(**data)
    except ValidationError as exc:
        raise ValidationFailed("; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()))


def _like(text: str) -> str:
    return "%" + text.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _check_enrollment_target(s, year_id, class_id, section_id, roll_no, ignore_student=None) -> None:
    if s.get(AcademicYear, year_id) is None or s.get(SchoolClass, class_id) is None or s.get(Section, section_id) is None:
        raise ValidationFailed("Academic year, class or section does not exist.")
    if roll_no is not None:
        if roll_no < 1:
            raise ValidationFailed("Roll number must be 1 or higher.")
        stmt = select(Enrollment.id).where(Enrollment.academic_year_id == year_id, Enrollment.class_id == class_id,
                                           Enrollment.section_id == section_id, Enrollment.roll_no == roll_no)
        if ignore_student:
            stmt = stmt.where(Enrollment.student_id != ignore_student)
        if s.scalar(stmt):
            raise ValidationFailed(f"Roll number {roll_no} is already used in that class and section.")


def _next_code(s, year: int, offset: int = 0) -> str:
    return f"STU-{year}-{(s.scalar(select(func.max(Student.id))) or 0) + 1 + offset:05d}"


def create_student(actor: CurrentUser, data: dict, year_id: int | None = None, class_id: int | None = None,
                   section_id: int | None = None, roll_no: int | None = None) -> int:
    require_role(actor, ADMIN)
    info = parse_student(data)
    for attempt in range(3):
        try:
            with session_scope() as s:
                code = (info.student_code or "").strip() or _next_code(s, info.admission_date.year, attempt)
                if info.student_code and s.scalar(select(Student.id).where(Student.student_code == code)):
                    raise ValidationFailed(f"Student ID {code} already exists.")
                student = Student(**{**info.model_dump(exclude={"student_code"}), "student_code": code,
                                     "gender": info.gender or None})
                s.add(student)
                s.flush()
                if class_id and section_id:
                    year_id = year_id or get_current_year_id(s)
                    if year_id is None:
                        raise ValidationFailed("No current academic year is set.")
                    _check_enrollment_target(s, year_id, class_id, section_id, roll_no)
                    s.add(Enrollment(student_id=student.id, academic_year_id=year_id, class_id=class_id,
                                     section_id=section_id, roll_no=roll_no))
                audit.log(s, actor, "student_create", "Student", student.id, None, {"code": code, **data})
                return student.id
        except IntegrityError:
            if info.student_code:
                raise ValidationFailed("That student ID already exists.")
    raise ValidationFailed("Could not allocate a unique student ID; please try again.")


def update_student(actor: CurrentUser, student_id: int, changes: dict) -> None:
    require_role(actor, ADMIN)
    unknown = set(changes) - set(EDITABLE)
    if unknown:
        raise ValidationFailed(f"These fields cannot be edited: {', '.join(sorted(unknown))}")
    with session_scope() as s:
        student = s.get(Student, student_id)
        if student is None:
            raise NotFound("Student not found.")
        current = {f: getattr(student, f) for f in EDITABLE}
        merged = {**current, **changes}
        info = parse_student({**merged, "student_code": student.student_code})
        previous, new = {}, {}
        for field in changes:
            value = getattr(info, field)
            if field == "gender":
                value = value or None
            if current[field] != value:
                previous[field], new[field] = current[field], value
                setattr(student, field, value)
        if new:
            audit.log(s, actor, "student_update", "Student", student_id, previous, new)


def set_archived(actor: CurrentUser, student_id: int, archived: bool, reason: str = "") -> None:
    """Archive/restore. Records are never physically deleted by the application."""
    require_role(actor, ADMIN)
    with session_scope() as s:
        student = s.get(Student, student_id)
        if student is None:
            raise NotFound("Student not found.")
        audit.log(s, actor, "student_archive" if archived else "student_restore", "Student", student_id,
                  {"is_archived": student.is_archived}, {"is_archived": archived, "reason": reason})
        student.is_archived = archived


def search_students(actor: CurrentUser, query: str | None = None, year_id: int | None = None,
                    class_id: int | None = None, section_id: int | None = None, include_archived: bool = False,
                    only_archived: bool = False, page: int = 1, page_size: int = 25) -> tuple[list[dict], int]:
    page, page_size = max(1, page), max(1, min(page_size, 200))
    with session_scope() as s:
        year_id = year_id or get_current_year_id(s)
        join_cond = and_(Enrollment.student_id == Student.id, Enrollment.academic_year_id == year_id)

        def apply(stmt):
            if only_archived:
                stmt = stmt.where(Student.is_archived.is_(True))
            elif not include_archived:
                stmt = stmt.where(Student.is_archived.is_(False))
            if class_id:
                stmt = stmt.where(Enrollment.class_id == class_id)
            if section_id:
                stmt = stmt.where(Enrollment.section_id == section_id)
            if query and query.strip():
                like = _like(query)
                stmt = stmt.where(or_(Student.full_name.ilike(like, escape="\\"),
                                      Student.student_code.ilike(like, escape="\\"),
                                      Student.guardian_name.ilike(like, escape="\\")))
            scope = student_scope(actor)
            return stmt if scope is None else stmt.where(Student.id.in_(scope))

        total = s.scalar(apply(select(func.count(Student.id)).select_from(Student).outerjoin(Enrollment, join_cond)))
        stmt = apply(
            select(Student.id, Student.student_code, Student.full_name, Student.gender, Student.guardian_name,
                   Student.guardian_phone, Student.status, Student.is_archived, Enrollment.class_id,
                   Enrollment.section_id, Enrollment.roll_no, SchoolClass.name.label("class_name"),
                   Section.name.label("section_name"))
            .select_from(Student).outerjoin(Enrollment, join_cond)
            .outerjoin(SchoolClass, SchoolClass.id == Enrollment.class_id)
            .outerjoin(Section, Section.id == Enrollment.section_id)
        ).order_by(Student.full_name, Student.id).limit(page_size).offset((page - 1) * page_size)
        return [dict(r._mapping) for r in s.execute(stmt)], int(total or 0)


def get_student(actor: CurrentUser, student_id: int) -> dict:
    with session_scope() as s:
        require_student_access(s, actor, student_id)
        st = s.get(Student, student_id)
        if st is None:
            raise NotFound("Student not found.")
        data = {c.name: getattr(st, c.name) for c in Student.__table__.columns}
        year_id = get_current_year_id(s)
        row = s.execute(
            select(Enrollment.roll_no, Enrollment.status, Enrollment.class_id, Enrollment.section_id,
                   SchoolClass.name.label("class_name"), Section.name.label("section_name"),
                   AcademicYear.name.label("year_name"))
            .join(SchoolClass, SchoolClass.id == Enrollment.class_id)
            .join(Section, Section.id == Enrollment.section_id)
            .join(AcademicYear, AcademicYear.id == Enrollment.academic_year_id)
            .where(Enrollment.student_id == student_id, Enrollment.academic_year_id == year_id)).first()
        data["current"] = dict(row._mapping) if row else None
        return data


def enrollment_history(actor: CurrentUser, student_id: int) -> list[dict]:
    with session_scope() as s:
        require_student_access(s, actor, student_id)
        rows = s.execute(
            select(Enrollment.id, Enrollment.academic_year_id, Enrollment.roll_no, Enrollment.status,
                   AcademicYear.name.label("year_name"), SchoolClass.name.label("class_name"),
                   SchoolClass.level, Section.name.label("section_name"))
            .join(AcademicYear, AcademicYear.id == Enrollment.academic_year_id)
            .join(SchoolClass, SchoolClass.id == Enrollment.class_id)
            .join(Section, Section.id == Enrollment.section_id)
            .where(Enrollment.student_id == student_id).order_by(AcademicYear.start_date))
        return [dict(r._mapping) for r in rows]


# ---------- CSV import ----------
REQUIRED_COLUMNS = ("full_name", "date_of_birth", "admission_date", "class", "section")
OPTIONAL_COLUMNS = ("roll_no", "gender", "guardian_name", "guardian_phone", "phone", "email", "address",
                    "emergency_contact_name", "emergency_contact_phone", "student_code")
MAX_IMPORT_ROWS = 20000


def _parse_date(text: str) -> dt.date:
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return dt.datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"'{text}' is not a date (use YYYY-MM-DD or DD/MM/YYYY)")


def import_students_csv(actor: CurrentUser, content: bytes, year_id: int | None = None) -> dict:
    """All-or-nothing: if any row is invalid nothing is imported and every problem is reported."""
    require_role(actor, ADMIN)
    if len(content) > 15 * 1024 * 1024:
        raise ValidationFailed("The CSV file is larger than 15 MB.")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValidationFailed("The file must be a UTF-8 encoded CSV.")
    reader = csv.DictReader(io.StringIO(text))
    headers = [(h or "").strip().lower() for h in (reader.fieldnames or [])]
    missing = [c for c in REQUIRED_COLUMNS if c not in headers]
    if missing:
        raise ValidationFailed(f"Missing required column(s): {', '.join(missing)}")
    reader.fieldnames = headers
    rows = list(reader)
    if not rows:
        raise ValidationFailed("The CSV file has no data rows.")
    if len(rows) > MAX_IMPORT_ROWS:
        raise ValidationFailed(f"Too many rows (maximum {MAX_IMPORT_ROWS} per import).")
    errors: list[tuple[int, str]] = []
    prepared: list[tuple[StudentIn, int, int, int | None]] = []
    with session_scope() as s:
        year_id = year_id or get_current_year_id(s)
        if year_id is None:
            raise ValidationFailed("Choose an academic year first.")
        classes = {c.name.lower(): c.id for c in s.scalars(select(SchoolClass))}
        sections = {x.name.upper(): x.id for x in s.scalars(select(Section))}
        used_rolls = {(r.class_id, r.section_id, r.roll_no) for r in
                      s.execute(select(Enrollment.class_id, Enrollment.section_id, Enrollment.roll_no)
                                .where(Enrollment.academic_year_id == year_id, Enrollment.roll_no.is_not(None)))}
        used_codes = set(s.scalars(select(Student.student_code)))
        seen_codes: set[str] = set()
        for n, row in enumerate(rows, start=2):
            try:
                cid = classes.get((row.get("class") or "").strip().lower())
                sid = sections.get((row.get("section") or "").strip().upper())
                if cid is None or sid is None:
                    raise ValueError("unknown class or section")
                roll = int(row["roll_no"]) if (row.get("roll_no") or "").strip() else None
                if roll is not None and (cid, sid, roll) in used_rolls:
                    raise ValueError(f"roll number {roll} is duplicated in this class/section")
                fields = {k: (row.get(k) or "").strip() for k in OPTIONAL_COLUMNS if k != "roll_no"}
                code = fields.pop("student_code") or None
                if code and (code in used_codes or code in seen_codes):
                    raise ValueError(f"student ID {code} already exists")
                info = parse_student({**{k: v for k, v in fields.items() if v}, "student_code": code,
                                      "full_name": row["full_name"], "date_of_birth": _parse_date(row["date_of_birth"]),
                                      "admission_date": _parse_date(row["admission_date"])})
                if roll is not None:
                    used_rolls.add((cid, sid, roll))
                if code:
                    seen_codes.add(code)
                prepared.append((info, cid, sid, roll))
            except (ValueError, ValidationFailed) as exc:
                errors.append((n, str(exc)))
        if errors:
            return {"imported": 0, "errors": errors[:200], "error_count": len(errors)}
        base = s.scalar(select(func.max(Student.id))) or 0
        students = []
        for i, (info, _, _, _) in enumerate(prepared, start=1):
            code = info.student_code or f"STU-{info.admission_date.year}-{base + i:05d}"
            students.append(Student(**{**info.model_dump(exclude={"student_code"}), "student_code": code,
                                       "gender": info.gender or None}))
        try:
            s.add_all(students)
            s.flush()
            s.add_all([Enrollment(student_id=st.id, academic_year_id=year_id, class_id=c, section_id=sec, roll_no=r)
                       for st, (_, c, sec, r) in zip(students, prepared)])
            audit.log(s, actor, "student_import", "Student", "", None, {"rows": len(students), "year_id": year_id})
            s.flush()
        except IntegrityError:
            raise ValidationFailed("Import failed because of a duplicate student ID; nothing was saved.")
    return {"imported": len(prepared), "errors": [], "error_count": 0}


# ---------- awards and remarks ----------
def add_award(actor: CurrentUser, student_id: int, title: str, description: str, awarded_on: dt.date) -> int:
    require_role(actor, ADMIN, TEACHER)
    if not title.strip():
        raise ValidationFailed("An award needs a title.")
    with session_scope() as s:
        require_student_access(s, actor, student_id)
        award = Award(student_id=student_id, title=title.strip(), description=description.strip(),
                      awarded_on=awarded_on, created_by=actor.id)
        s.add(award)
        s.flush()
        audit.log(s, actor, "award_add", "Student", student_id, None, {"title": title})
        return award.id


def add_remark(actor: CurrentUser, student_id: int, text: str) -> int:
    require_role(actor, ADMIN, TEACHER)
    if not text.strip():
        raise ValidationFailed("The remark is empty.")
    with session_scope() as s:
        require_student_access(s, actor, student_id)
        remark = Remark(student_id=student_id, author_id=actor.id, text=text.strip())
        s.add(remark)
        s.flush()
        audit.log(s, actor, "remark_add", "Student", student_id, None, {"remark_id": remark.id})
        return remark.id


def list_awards(actor: CurrentUser, student_id: int) -> list[dict]:
    with session_scope() as s:
        require_student_access(s, actor, student_id)
        return [{"title": a.title, "description": a.description, "awarded_on": a.awarded_on}
                for a in s.scalars(select(Award).where(Award.student_id == student_id).order_by(Award.awarded_on.desc()))]


def list_remarks(actor: CurrentUser, student_id: int) -> list[dict]:
    with session_scope() as s:
        require_student_access(s, actor, student_id)
        return [{"text": r.text, "created_at": r.created_at}
                for r in s.scalars(select(Remark).where(Remark.student_id == student_id)
                                   .order_by(Remark.created_at.desc()).limit(100))]
