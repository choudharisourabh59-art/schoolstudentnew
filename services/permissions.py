"""All authorisation rules live here. Services call these; the UI never decides access on its own."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import exists, false, select
from sqlalchemy.orm import Session

from database.models import Enrollment, ParentStudent, Student, TeacherAssignment
from services.errors import PermissionDenied

ADMIN, PRINCIPAL, TEACHER, STUDENT, PARENT = "admin", "principal", "teacher", "student", "parent"
ALL_ROLES = (ADMIN, PRINCIPAL, TEACHER, STUDENT, PARENT)
STAFF_ROLES = (ADMIN, PRINCIPAL, TEACHER)
OVERSIGHT_ROLES = (ADMIN, PRINCIPAL)


@dataclass(frozen=True)
class CurrentUser:
    id: int
    username: str
    full_name: str
    role: str
    teacher_id: int | None = None
    student_id: int | None = None
    parent_id: int | None = None


def require_role(actor: CurrentUser | None, *roles: str) -> CurrentUser:
    if actor is None or actor.role not in roles:
        raise PermissionDenied("You do not have permission to perform this action.")
    return actor


def _teacher_assignment_clause(actor: CurrentUser):
    return exists().where(
        TeacherAssignment.teacher_id == actor.teacher_id,
        TeacherAssignment.academic_year_id == Enrollment.academic_year_id,
        TeacherAssignment.class_id == Enrollment.class_id,
        TeacherAssignment.section_id == Enrollment.section_id,
    ).correlate(Enrollment)


def _linked_children(actor: CurrentUser):
    return select(ParentStudent.student_id).where(ParentStudent.parent_id == (actor.parent_id or -1),
                                                  ParentStudent.verified.is_(True))


def enrollment_scope(actor: CurrentUser):
    """SQL clause restricting Enrollment rows the actor may read; None means unrestricted."""
    if actor.role in OVERSIGHT_ROLES:
        return None
    if actor.role == TEACHER:
        return _teacher_assignment_clause(actor) if actor.teacher_id else false()
    if actor.role == STUDENT:
        return Enrollment.student_id == (actor.student_id or -1)
    if actor.role == PARENT:
        return Enrollment.student_id.in_(_linked_children(actor))
    return false()


def student_scope(actor: CurrentUser):
    """A SELECT of student ids the actor may see; None means all students."""
    if actor.role in OVERSIGHT_ROLES:
        return None
    if actor.role == TEACHER:
        if not actor.teacher_id:
            return select(Student.id).where(false())
        return select(Enrollment.student_id).where(_teacher_assignment_clause(actor))
    if actor.role == STUDENT:
        return select(Student.id).where(Student.id == (actor.student_id or -1))
    if actor.role == PARENT:
        return _linked_children(actor)
    return select(Student.id).where(false())


def require_student_access(session: Session, actor: CurrentUser, student_id: int) -> None:
    scope = student_scope(actor)
    stmt = select(Student.id).where(Student.id == student_id)
    if scope is not None:
        stmt = stmt.where(Student.id.in_(scope))
    if session.scalar(stmt) is None:
        raise PermissionDenied("You do not have access to this student record.")


def can_access_class(session: Session, actor: CurrentUser, year_id: int, class_id: int, section_id: int,
                     subject_id: int | None = None, write: bool = False) -> bool:
    if actor.role == ADMIN:
        return True
    if actor.role == PRINCIPAL:
        return not write
    if actor.role != TEACHER or not actor.teacher_id:
        return False
    stmt = select(TeacherAssignment.id).where(
        TeacherAssignment.teacher_id == actor.teacher_id, TeacherAssignment.academic_year_id == year_id,
        TeacherAssignment.class_id == class_id, TeacherAssignment.section_id == section_id)
    if subject_id is not None:  # a NULL-subject assignment covers every subject
        stmt = stmt.where((TeacherAssignment.subject_id == subject_id) | TeacherAssignment.subject_id.is_(None))
    return session.scalar(stmt.limit(1)) is not None


def require_class_access(session, actor, year_id, class_id, section_id, subject_id=None, write=False) -> None:
    if not can_access_class(session, actor, year_id, class_id, section_id, subject_id, write):
        raise PermissionDenied("You are not assigned to this class.")
