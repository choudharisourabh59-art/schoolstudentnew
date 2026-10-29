"""Examinations, marks entry, derived results. Results are computed from raw marks with Decimal arithmetic,
so they can never be stale: any change to a mark is reflected the next time anything is read."""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy import exists, func, or_, select
from sqlalchemy.exc import IntegrityError

from database.connection import session_scope
from database.models import (AcademicYear, Enrollment, Examination, ExaminationSubject, SchoolClass, Section, Student,
                             StudentResult, Subject, utcnow)
from services import audit
from services.academic_service import get_policy
from services.errors import NotFound, PermissionDenied, ValidationFailed
from services.permissions import (ADMIN, OVERSIGHT_ROLES, PARENT, STUDENT, TEACHER, CurrentUser, enrollment_scope,
                                  require_class_access, require_role, require_student_access)

TWO_DP = Decimal("0.01")


def to_decimal(value) -> Decimal | None:
    """Blank/NaN -> None (missing). Rejects text, infinities and more than 2 decimal places."""
    if value is None:
        return None
    if isinstance(value, float):
        if math.isnan(value):
            return None
        value = repr(value)
    text = str(value).strip()
    if text == "" or text.lower() in ("nan", "none"):
        return None
    try:
        number = Decimal(text)
        if not number.is_finite():
            raise InvalidOperation
        quantised = number.quantize(TWO_DP)
    except InvalidOperation:
        raise ValidationFailed(f"'{text}' is not a valid number.")
    if quantised != number:
        raise ValidationFailed(f"'{text}' has more than 2 decimal places.")
    return quantised


def fmt(value: Decimal | None, places: str = "0.01") -> str:
    """Display only. Never feed the rounded value back into calculations."""
    return "-" if value is None else str(Decimal(value).quantize(Decimal(places), rounding=ROUND_HALF_UP))


def grade_for(percentage: Decimal, bands) -> str:
    for minimum, grade in sorted(((Decimal(str(m)), g) for m, g in bands), key=lambda x: -x[0]):
        if percentage >= minimum:
            return grade
    return bands[-1][1]


def calculate_result(rows: list[dict], policy: dict) -> dict:
    """rows: [{subject, max, passing, marks (Decimal|None), absent (bool)}]. Missing marks are never zero."""
    missing = [r["subject"] for r in rows if r["marks"] is None and not r["absent"]]
    total = sum((r["marks"] for r in rows if r["marks"] is not None), Decimal(0))
    max_total = sum((r["max"] for r in rows), Decimal(0))
    pct = (total * 100 / max_total) if max_total else None
    failed: list[str] = []
    if not rows:
        result = "NO RESULT"
    elif missing:
        result = "INCOMPLETE"
    else:
        for r in rows:
            if r["absent"]:
                if policy["absent_is_fail"]:
                    failed.append(r["subject"])
            elif policy["require_subject_pass"] and r["marks"] < r["passing"]:
                failed.append(r["subject"])
        result = "FAIL" if failed or pct < Decimal(str(policy["pass_percentage"])) else "PASS"
    grade = grade_for(pct, policy["grade_bands"]) if result in ("PASS", "FAIL") else None
    return {"total": total, "max_total": max_total, "percentage": pct, "grade": grade, "result": result,
            "missing_subjects": missing, "failed_subjects": failed}


# ---------- setup ----------
def create_examination(actor: CurrentUser, year_id: int, term: str, name: str) -> int:
    require_role(actor, ADMIN)
    if not term.strip() or not name.strip():
        raise ValidationFailed("Term and examination name are required.")
    try:
        with session_scope() as s:
            if s.get(AcademicYear, year_id) is None:
                raise NotFound("Academic year not found.")
            exam = Examination(academic_year_id=year_id, term=term.strip(), name=name.strip(), created_by=actor.id)
            s.add(exam)
            s.flush()
            audit.log(s, actor, "exam_create", "Examination", exam.id, None, {"term": term, "name": name})
            return exam.id
    except IntegrityError:
        raise ValidationFailed("An examination with that term and name already exists in this year.")


def add_exam_subject(actor: CurrentUser, exam_id: int, subject_id: int, class_id: int, max_marks, passing_marks) -> int:
    require_role(actor, ADMIN)
    mx, ps = to_decimal(max_marks), to_decimal(passing_marks)
    if mx is None or ps is None or mx <= 0 or ps < 0 or ps > mx:
        raise ValidationFailed("Maximum marks must be above 0 and passing marks between 0 and the maximum.")
    try:
        with session_scope() as s:
            exam = s.get(Examination, exam_id)
            if exam is None:
                raise NotFound("Examination not found.")
            if exam.status == "published":
                raise PermissionDenied("Published examinations cannot be restructured; reopen it first.")
            es = ExaminationSubject(examination_id=exam_id, subject_id=subject_id, class_id=class_id,
                                    max_marks=mx, passing_marks=ps)
            s.add(es)
            s.flush()
            audit.log(s, actor, "exam_subject_add", "ExaminationSubject", es.id, None,
                      {"exam": exam_id, "subject": subject_id, "class": class_id, "max": mx, "pass": ps})
            return es.id
    except IntegrityError:
        raise ValidationFailed("That subject is already part of this examination for the class (or the subject/class is invalid).")


def list_examinations(year_id: int) -> list[dict]:
    with session_scope() as s:
        return [{"id": e.id, "term": e.term, "name": e.name, "status": e.status}
                for e in s.scalars(select(Examination).where(Examination.academic_year_id == year_id)
                                   .order_by(Examination.term, Examination.name))]


def list_exam_subjects(exam_id: int, class_id: int | None = None) -> list[dict]:
    with session_scope() as s:
        stmt = (select(ExaminationSubject.id, Subject.id, Subject.name, ExaminationSubject.class_id, SchoolClass.name,
                       ExaminationSubject.max_marks, ExaminationSubject.passing_marks)
                .join(Subject, Subject.id == ExaminationSubject.subject_id)
                .join(SchoolClass, SchoolClass.id == ExaminationSubject.class_id)
                .where(ExaminationSubject.examination_id == exam_id).order_by(SchoolClass.level, Subject.name))
        if class_id:
            stmt = stmt.where(ExaminationSubject.class_id == class_id)
        return [{"id": r[0], "subject_id": r[1], "subject": r[2], "class_id": r[3], "class": r[4],
                 "max_marks": r[5], "passing_marks": r[6]} for r in s.execute(stmt)]


# ---------- marks entry ----------
@dataclass
class MarkEntry:
    enrollment_id: int
    marks: object = None  # Decimal | str | float | None (None/blank = not entered)
    is_absent: bool = False


def marks_roster(actor: CurrentUser, exam_subject_id: int, section_id: int) -> list[dict]:
    with session_scope() as s:
        es = s.get(ExaminationSubject, exam_subject_id)
        if es is None:
            raise NotFound("Examination subject not found.")
        exam = s.get(Examination, es.examination_id)
        require_class_access(s, actor, exam.academic_year_id, es.class_id, section_id, es.subject_id)
        rows = s.execute(
            select(Enrollment.id, Enrollment.roll_no, Student.student_code, Student.full_name, StudentResult.marks,
                   StudentResult.is_absent)
            .join(Student, Student.id == Enrollment.student_id)
            .outerjoin(StudentResult, (StudentResult.enrollment_id == Enrollment.id) &
                       (StudentResult.exam_subject_id == exam_subject_id))
            .where(Enrollment.academic_year_id == exam.academic_year_id, Enrollment.class_id == es.class_id,
                   Enrollment.section_id == section_id, Student.is_archived.is_(False),
                   Enrollment.status.in_(("active", "promoted", "retained")))
            .order_by(Enrollment.roll_no, Student.full_name))
        return [{"enrollment_id": r[0], "roll_no": r[1], "student_code": r[2], "name": r[3], "marks": r[4],
                 "absent": bool(r[5]) if r[5] is not None else False} for r in rows]


def enter_marks(actor: CurrentUser, exam_subject_id: int, entries: list[MarkEntry], reason: str = "") -> dict:
    """Validate everything first, then save all rows (and their audit entries) in one transaction."""
    require_role(actor, ADMIN, TEACHER)
    if not entries:
        raise ValidationFailed("No marks were supplied.")
    ids = [e.enrollment_id for e in entries]
    if len(set(ids)) != len(ids):
        raise ValidationFailed("A student appears more than once in the submitted marks.")
    with session_scope() as s:
        es = s.get(ExaminationSubject, exam_subject_id)
        if es is None:
            raise NotFound("Examination subject not found.")
        exam = s.get(Examination, es.examination_id)
        policy = get_policy(s)
        if exam.status == "published":
            if actor.role not in policy["amend_roles"]:
                raise PermissionDenied("Published results can only be amended by an authorised user.")
            if not reason.strip():
                raise ValidationFailed("A reason is required to amend published results.")
        enrolments = {e.id: e for e in s.scalars(select(Enrollment).where(
            Enrollment.id.in_(ids), Enrollment.academic_year_id == exam.academic_year_id,
            Enrollment.class_id == es.class_id))}
        if set(enrolments) != set(ids):
            raise ValidationFailed("One or more students are not enrolled in this examination's class.")
        for section_id in {e.section_id for e in enrolments.values()}:
            require_class_access(s, actor, exam.academic_year_id, es.class_id, section_id, es.subject_id, write=True)
        parsed: list[tuple[int, Decimal | None, bool]] = []
        for e in entries:
            marks, absent = to_decimal(e.marks), bool(e.is_absent)
            if absent and marks is not None:
                raise ValidationFailed("A student marked absent cannot also have marks.")
            if marks is not None:
                if marks < 0 and not policy["allow_negative_marks"]:
                    raise ValidationFailed("Negative marks are not allowed.")
                if marks > es.max_marks:
                    raise ValidationFailed(f"Marks cannot exceed the maximum of {fmt(es.max_marks)}.")
            parsed.append((e.enrollment_id, marks, absent))
        existing = {r.enrollment_id: r for r in s.scalars(
            select(StudentResult).where(StudentResult.exam_subject_id == exam_subject_id,
                                        StudentResult.enrollment_id.in_(ids)))}
        changed = 0
        for eid, marks, absent in parsed:
            row = existing.get(eid)
            if row is None:
                if marks is None and not absent:
                    continue
                s.add(StudentResult(exam_subject_id=exam_subject_id, enrollment_id=eid, marks=marks,
                                    is_absent=absent, entered_by=actor.id))
                audit.log(s, actor, "marks_enter", "StudentResult", f"{exam_subject_id}:{eid}", None,
                          {"marks": marks, "absent": absent, "reason": reason or None})
                changed += 1
            elif (row.marks, row.is_absent) != (marks, absent):
                audit.log(s, actor, "marks_change", "StudentResult", row.id,
                          {"marks": row.marks, "absent": row.is_absent},
                          {"marks": marks, "absent": absent, "reason": reason or None,
                           "exam_status": exam.status})
                row.marks, row.is_absent, row.entered_by, row.updated_at = marks, absent, actor.id, utcnow()
                changed += 1
        return {"changed": changed, "exam_status": exam.status}


def missing_entries(s, exam_id: int) -> int:
    """Number of (student, subject) cells still empty for an examination."""
    subjects = s.execute(select(ExaminationSubject.id, ExaminationSubject.class_id)
                         .where(ExaminationSubject.examination_id == exam_id)).all()
    exam = s.get(Examination, exam_id)
    missing = 0
    for es_id, class_id in subjects:
        expected = s.scalar(select(func.count(Enrollment.id)).where(
            Enrollment.academic_year_id == exam.academic_year_id, Enrollment.class_id == class_id,
            Enrollment.status == "active"))
        entered = s.scalar(select(func.count(StudentResult.id)).join(
            Enrollment, Enrollment.id == StudentResult.enrollment_id).where(
            StudentResult.exam_subject_id == es_id, Enrollment.status == "active",
            or_(StudentResult.marks.is_not(None), StudentResult.is_absent.is_(True))))
        missing += max(0, (expected or 0) - (entered or 0))
    return missing


def publish_results(actor: CurrentUser, exam_id: int, allow_incomplete: bool = False) -> None:
    require_role(actor, *OVERSIGHT_ROLES)
    with session_scope() as s:
        exam = s.get(Examination, exam_id)
        if exam is None:
            raise NotFound("Examination not found.")
        if exam.status == "published":
            raise ValidationFailed("These results are already published.")
        if s.scalar(select(func.count(ExaminationSubject.id)).where(ExaminationSubject.examination_id == exam_id)) == 0:
            raise ValidationFailed("Add at least one subject before publishing.")
        gaps = missing_entries(s, exam_id)
        if gaps and not allow_incomplete:
            raise ValidationFailed(f"{gaps} mark(s) are still missing. Complete them or publish as incomplete.")
        exam.status, exam.published_by, exam.published_at = "published", actor.id, utcnow()
        audit.log(s, actor, "results_publish", "Examination", exam_id, {"status": "draft"},
                  {"status": "published", "missing_entries": gaps})


def reopen_examination(actor: CurrentUser, exam_id: int, reason: str) -> None:
    require_role(actor, ADMIN)
    if not reason.strip():
        raise ValidationFailed("A reason is required to reopen published results.")
    with session_scope() as s:
        exam = s.get(Examination, exam_id)
        if exam is None:
            raise NotFound("Examination not found.")
        audit.log(s, actor, "results_reopen", "Examination", exam_id, {"status": exam.status},
                  {"status": "draft", "reason": reason})
        exam.status, exam.published_at, exam.published_by = "draft", None, None


# ---------- reading results ----------
def exam_results(actor: CurrentUser, exam_id: int, class_id: int, section_id: int | None = None) -> list[dict]:
    with session_scope() as s:
        exam = s.get(Examination, exam_id)
        if exam is None:
            raise NotFound("Examination not found.")
        if actor.role in (STUDENT, PARENT) and exam.status != "published":
            raise PermissionDenied("These results have not been published yet.")
        policy = get_policy(s)
        subjects = s.execute(
            select(ExaminationSubject.id, Subject.name, ExaminationSubject.max_marks, ExaminationSubject.passing_marks)
            .join(Subject, Subject.id == ExaminationSubject.subject_id)
            .where(ExaminationSubject.examination_id == exam_id, ExaminationSubject.class_id == class_id)
            .order_by(Subject.name)).all()
        stmt = (select(Enrollment.id, Enrollment.roll_no, Student.student_code, Student.full_name, Section.name)
                .join(Student, Student.id == Enrollment.student_id).join(Section, Section.id == Enrollment.section_id)
                .where(Enrollment.academic_year_id == exam.academic_year_id, Enrollment.class_id == class_id))
        if section_id:
            stmt = stmt.where(Enrollment.section_id == section_id)
        scope = enrollment_scope(actor)
        if scope is not None:
            stmt = stmt.where(scope)
        enrolments = s.execute(stmt.order_by(Section.name, Enrollment.roll_no, Student.full_name)).all()
        ids = [e[0] for e in enrolments]
        marks = {}
        if ids and subjects:
            for r in s.execute(select(StudentResult.exam_subject_id, StudentResult.enrollment_id, StudentResult.marks,
                                      StudentResult.is_absent).where(
                    StudentResult.exam_subject_id.in_([x[0] for x in subjects]), StudentResult.enrollment_id.in_(ids))):
                marks[(r[0], r[1])] = (r[2], r[3])
        out = []
        for eid, roll, code, name, sec in enrolments:
            rows = []
            for es_id, subject, mx, ps in subjects:
                m, a = marks.get((es_id, eid), (None, False))
                rows.append({"subject": subject, "max": mx, "passing": ps, "marks": m, "absent": a})
            out.append({"enrollment_id": eid, "roll_no": roll, "student_code": code, "name": name, "section": sec,
                        "subjects": rows, **calculate_result(rows, policy)})
        return out


def student_results(actor: CurrentUser, student_id: int) -> list[dict]:
    """Every examination across every year for one student (published only for students/parents)."""
    with session_scope() as s:
        require_student_access(s, actor, student_id)
        policy = get_policy(s)
        published_only = actor.role in (STUDENT, PARENT)
        enrolments = s.execute(
            select(Enrollment.id, Enrollment.academic_year_id, Enrollment.class_id, AcademicYear.name,
                   SchoolClass.name, AcademicYear.start_date)
            .join(AcademicYear, AcademicYear.id == Enrollment.academic_year_id)
            .join(SchoolClass, SchoolClass.id == Enrollment.class_id)
            .where(Enrollment.student_id == student_id).order_by(AcademicYear.start_date)).all()
        out = []
        for eid, year_id, class_id, year_name, class_name, _ in enrolments:
            stmt = (select(Examination.id, Examination.name, Examination.term, Examination.status,
                           ExaminationSubject.id, Subject.name, ExaminationSubject.max_marks,
                           ExaminationSubject.passing_marks, StudentResult.marks, StudentResult.is_absent)
                    .select_from(ExaminationSubject)
                    .join(Examination, Examination.id == ExaminationSubject.examination_id)
                    .join(Subject, Subject.id == ExaminationSubject.subject_id)
                    .outerjoin(StudentResult, (StudentResult.exam_subject_id == ExaminationSubject.id) &
                               (StudentResult.enrollment_id == eid))
                    .where(Examination.academic_year_id == year_id, ExaminationSubject.class_id == class_id)
                    .order_by(Examination.id, Subject.name))
            if published_only:
                stmt = stmt.where(Examination.status == "published")
            grouped: dict[int, dict] = {}
            for ex_id, ex_name, term, status, _es, subj, mx, ps, m, a in s.execute(stmt):
                g = grouped.setdefault(ex_id, {"exam_id": ex_id, "exam": ex_name, "term": term, "status": status,
                                               "year": year_name, "class": class_name, "subjects": []})
                g["subjects"].append({"subject": subj, "max": mx, "passing": ps, "marks": m, "absent": bool(a)})
            for g in grouped.values():
                out.append({**g, **calculate_result(g["subjects"], policy)})
        return out


def _published_filters(stmt, year_id, class_id, section_id, term, subject_id):
    stmt = stmt.where(Examination.status == "published", Examination.academic_year_id == year_id,
                      StudentResult.marks.is_not(None))
    if class_id:
        stmt = stmt.where(Enrollment.class_id == class_id)
    if section_id:
        stmt = stmt.where(Enrollment.section_id == section_id)
    if term:
        stmt = stmt.where(Examination.term == term)
    if subject_id:
        stmt = stmt.where(ExaminationSubject.subject_id == subject_id)
    return stmt


def _result_joins(stmt):
    return (stmt.select_from(StudentResult)
            .join(ExaminationSubject, ExaminationSubject.id == StudentResult.exam_subject_id)
            .join(Examination, Examination.id == ExaminationSubject.examination_id)
            .join(Enrollment, Enrollment.id == StudentResult.enrollment_id))


def class_performance(actor: CurrentUser, year_id: int, term: str | None = None, subject_id: int | None = None,
                      section_id: int | None = None) -> list[dict]:
    """Average of (marks / max) per class and term over published results (dashboard aggregate)."""
    with session_scope() as s:
        pct = StudentResult.marks * 100 / ExaminationSubject.max_marks
        stmt = _result_joins(select(SchoolClass.name, SchoolClass.level, Examination.term, func.avg(pct))
                             .join(SchoolClass, SchoolClass.id == Enrollment.class_id))
        stmt = _published_filters(stmt, year_id, None, section_id, term, subject_id)
        scope = enrollment_scope(actor)
        if scope is not None:
            stmt = stmt.where(scope)
        stmt = stmt.group_by(SchoolClass.name, SchoolClass.level, Examination.term).order_by(SchoolClass.level)
        return [{"class": r[0], "term": r[2], "average": float(r[3])} for r in s.execute(stmt) if r[3] is not None]


def subject_averages(actor: CurrentUser, year_id: int, class_id=None, section_id=None, term=None) -> list[dict]:
    with session_scope() as s:
        pct = StudentResult.marks * 100 / ExaminationSubject.max_marks
        stmt = _result_joins(select(Subject.name, func.avg(pct)).join(Subject, Subject.id == ExaminationSubject.subject_id))
        stmt = _published_filters(stmt, year_id, class_id, section_id, term, None)
        scope = enrollment_scope(actor)
        if scope is not None:
            stmt = stmt.where(scope)
        return [{"subject": r[0], "average": float(r[1])}
                for r in s.execute(stmt.group_by(Subject.name).order_by(Subject.name)) if r[1] is not None]


def grade_distribution(actor: CurrentUser, year_id: int, class_id=None, section_id=None, term=None,
                       subject_id=None) -> list[dict]:
    with session_scope() as s:
        policy = get_policy(s)
        stmt = _result_joins(select(Enrollment.id, func.sum(StudentResult.marks), func.sum(ExaminationSubject.max_marks)))
        stmt = _published_filters(stmt, year_id, class_id, section_id, term, subject_id)
        scope = enrollment_scope(actor)
        if scope is not None:
            stmt = stmt.where(scope)
        counts: Counter = Counter()
        for _eid, got, mx in s.execute(stmt.group_by(Enrollment.id)):
            if mx and Decimal(str(mx)) > 0:
                counts[grade_for(Decimal(str(got)) * 100 / Decimal(str(mx)), policy["grade_bands"])] += 1
        return [{"grade": g, "students": counts.get(g, 0)} for _m, g in policy["grade_bands"]]


def students_with_missing_marks(actor: CurrentUser, year_id: int, class_id=None, section_id=None,
                                exam_id: int | None = None) -> int:
    """Active students who lack at least one required mark in any examination of the year."""
    with session_scope() as s:
        entered = exists().where(
            StudentResult.exam_subject_id == ExaminationSubject.id, StudentResult.enrollment_id == Enrollment.id,
            or_(StudentResult.marks.is_not(None), StudentResult.is_absent.is_(True))
        ).correlate(ExaminationSubject, Enrollment)
        needed = exists().where(
            ExaminationSubject.class_id == Enrollment.class_id,
            ExaminationSubject.examination_id == Examination.id,
            Examination.academic_year_id == Enrollment.academic_year_id, ~entered).correlate(Enrollment)
        if exam_id:
            needed = needed.where(Examination.id == exam_id)
        stmt = select(func.count(Enrollment.id)).where(Enrollment.academic_year_id == year_id,
                                                       Enrollment.status == "active", needed)
        if class_id:
            stmt = stmt.where(Enrollment.class_id == class_id)
        if section_id:
            stmt = stmt.where(Enrollment.section_id == section_id)
        scope = enrollment_scope(actor)
        if scope is not None:
            stmt = stmt.where(scope)
        return int(s.scalar(stmt) or 0)


def entry_progress(actor: CurrentUser, year_id: int) -> list[dict]:
    """Per examination: how many mark cells are filled vs required (for the dashboard progress bars)."""
    with session_scope() as s:
        out = []
        for exam in s.scalars(select(Examination).where(Examination.academic_year_id == year_id)
                              .order_by(Examination.term, Examination.name)):
            expected = 0
            for _es, class_id in s.execute(select(ExaminationSubject.id, ExaminationSubject.class_id)
                                           .where(ExaminationSubject.examination_id == exam.id)):
                expected += s.scalar(select(func.count(Enrollment.id)).where(
                    Enrollment.academic_year_id == year_id, Enrollment.class_id == class_id,
                    Enrollment.status == "active")) or 0
            missing = missing_entries(s, exam.id)
            out.append({"exam": f"{exam.term} - {exam.name}", "status": exam.status,
                        "expected": expected, "filled": max(0, expected - missing)})
        return out
