"""Relational schema. Historical data lives in Enrollment-scoped rows and is never overwritten."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import (JSON, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, LargeBinary,
                        Numeric, String, Text, UniqueConstraint, event)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

ROLES = ("admin", "principal", "teacher", "student", "parent")
ENROLLMENT_STATUSES = ("active", "promoted", "retained", "transferred", "graduated", "withdrawn")
ATTENDANCE_STATUSES = ("P", "A", "L", "E")


def utcnow() -> dt.datetime:
    """Naive UTC timestamps everywhere (portable between SQLite and PostgreSQL)."""
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    full_name: Mapped[str] = mapped_column(String(150))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    reset_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reset_expires: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    last_login: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (CheckConstraint("role IN ('admin','principal','teacher','student','parent')", name="ck_user_role"),)


class Teacher(Base):
    __tablename__ = "teachers"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    employee_no: Mapped[str] = mapped_column(String(40), unique=True)
    phone: Mapped[str] = mapped_column(String(30), default="")


class Parent(Base):
    __tablename__ = "parents"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    phone: Mapped[str] = mapped_column(String(30), default="")


class AcademicYear(Base):
    __tablename__ = "academic_years"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(20), unique=True)
    start_date: Mapped[dt.date] = mapped_column(Date)
    end_date: Mapped[dt.date] = mapped_column(Date)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (CheckConstraint("end_date > start_date", name="ck_year_dates"),)


class SchoolClass(Base):
    __tablename__ = "classes"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(40), unique=True)
    level: Mapped[int] = mapped_column(Integer, unique=True)  # 1..12; promotion goes level -> level+1


class Section(Base):
    __tablename__ = "sections"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(10), unique=True)


class Subject(Base):
    __tablename__ = "subjects"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)


class Student(Base):
    __tablename__ = "students"
    id: Mapped[int] = mapped_column(primary_key=True)
    student_code: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(150))
    date_of_birth: Mapped[dt.date] = mapped_column(Date)
    gender: Mapped[str | None] = mapped_column(String(10), nullable=True)
    admission_date: Mapped[dt.date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="active")
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    address: Mapped[str] = mapped_column(Text, default="")
    phone: Mapped[str] = mapped_column(String(30), default="")
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    guardian_name: Mapped[str] = mapped_column(String(150), default="")
    guardian_phone: Mapped[str] = mapped_column(String(30), default="")
    emergency_contact_name: Mapped[str] = mapped_column(String(150), default="")
    emergency_contact_phone: Mapped[str] = mapped_column(String(30), default="")
    photo_document_id: Mapped[int | None] = mapped_column(ForeignKey("uploaded_documents.id", use_alter=True,
                                                                        name="fk_student_photo"), nullable=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), unique=True, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (Index("ix_students_name", "full_name"),)


class ParentStudent(Base):
    __tablename__ = "parent_students"
    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int] = mapped_column(ForeignKey("parents.id"))
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), index=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verified_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    __table_args__ = (UniqueConstraint("parent_id", "student_id", name="uq_parent_student"),)


class Enrollment(Base):
    """A student's place in one academic year. One row per student per year => history is preserved."""
    __tablename__ = "enrollments"
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), index=True)
    academic_year_id: Mapped[int] = mapped_column(ForeignKey("academic_years.id"))
    class_id: Mapped[int] = mapped_column(ForeignKey("classes.id"))
    section_id: Mapped[int] = mapped_column(ForeignKey("sections.id"))
    roll_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")  # outcome once the year is decided
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (
        UniqueConstraint("student_id", "academic_year_id", name="uq_enrollment_student_year"),
        UniqueConstraint("academic_year_id", "class_id", "section_id", "roll_no", name="uq_enrollment_roll"),
        Index("ix_enrollment_group", "academic_year_id", "class_id", "section_id"),
        CheckConstraint("status IN ('active','promoted','retained','transferred','graduated','withdrawn')",
                        name="ck_enrollment_status"),
    )


class TeacherAssignment(Base):
    __tablename__ = "teacher_assignments"
    id: Mapped[int] = mapped_column(primary_key=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teachers.id"), index=True)
    academic_year_id: Mapped[int] = mapped_column(ForeignKey("academic_years.id"))
    class_id: Mapped[int] = mapped_column(ForeignKey("classes.id"))
    section_id: Mapped[int] = mapped_column(ForeignKey("sections.id"))
    subject_id: Mapped[int | None] = mapped_column(ForeignKey("subjects.id"), nullable=True)  # NULL = all subjects
    is_class_teacher: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (UniqueConstraint("teacher_id", "academic_year_id", "class_id", "section_id", "subject_id",
                                       name="uq_assignment"),)


class Attendance(Base):
    __tablename__ = "attendance"
    id: Mapped[int] = mapped_column(primary_key=True)
    enrollment_id: Mapped[int] = mapped_column(ForeignKey("enrollments.id"), index=True)
    date: Mapped[dt.date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(1))
    marked_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    __table_args__ = (
        UniqueConstraint("enrollment_id", "date", name="uq_attendance_day"),
        Index("ix_attendance_date_status", "date", "status"),
        CheckConstraint("status IN ('P','A','L','E')", name="ck_attendance_status"),
    )


class Examination(Base):
    __tablename__ = "examinations"
    id: Mapped[int] = mapped_column(primary_key=True)
    academic_year_id: Mapped[int] = mapped_column(ForeignKey("academic_years.id"))
    term: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(12), default="draft")  # draft | published
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    published_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    __table_args__ = (UniqueConstraint("academic_year_id", "term", "name", name="uq_exam"),
                      CheckConstraint("status IN ('draft','published')", name="ck_exam_status"))


class ExaminationSubject(Base):
    __tablename__ = "examination_subjects"
    id: Mapped[int] = mapped_column(primary_key=True)
    examination_id: Mapped[int] = mapped_column(ForeignKey("examinations.id"), index=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id"))
    class_id: Mapped[int] = mapped_column(ForeignKey("classes.id"))
    max_marks: Mapped[Decimal] = mapped_column(Numeric(7, 2))
    passing_marks: Mapped[Decimal] = mapped_column(Numeric(7, 2))
    __table_args__ = (UniqueConstraint("examination_id", "subject_id", "class_id", name="uq_exam_subject"),
                      CheckConstraint("max_marks > 0 AND passing_marks >= 0 AND passing_marks <= max_marks",
                                      name="ck_exam_subject_marks"))


class StudentResult(Base):
    """Raw marks only. Totals, percentages, grades and PASS/FAIL are derived on read (never stale)."""
    __tablename__ = "student_results"
    id: Mapped[int] = mapped_column(primary_key=True)
    exam_subject_id: Mapped[int] = mapped_column(ForeignKey("examination_subjects.id"))
    enrollment_id: Mapped[int] = mapped_column(ForeignKey("enrollments.id"), index=True)
    marks: Mapped[Decimal | None] = mapped_column(Numeric(7, 2), nullable=True)  # NULL = not entered
    is_absent: Mapped[bool] = mapped_column(Boolean, default=False)
    entered_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    __table_args__ = (UniqueConstraint("exam_subject_id", "enrollment_id", name="uq_result"),
                      CheckConstraint("NOT (is_absent AND marks IS NOT NULL)", name="ck_result_absent"))


class UploadedDocument(Base):
    __tablename__ = "uploaded_documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int | None] = mapped_column(ForeignKey("students.id", use_alter=True, name="fk_doc_student"),
                                                   nullable=True, index=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(120), unique=True)
    storage_backend: Mapped[str] = mapped_column(String(10))
    content_type: Mapped[str] = mapped_column(String(100))
    file_size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(40))
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    uploaded_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    is_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class DocumentBlob(Base):
    """File bytes when STORAGE_BACKEND=db."""
    __tablename__ = "document_blobs"
    document_id: Mapped[int] = mapped_column(ForeignKey("uploaded_documents.id"), primary_key=True)
    data: Mapped[bytes] = mapped_column(LargeBinary)


class Award(Base):
    __tablename__ = "awards"
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), index=True)
    title: Mapped[str] = mapped_column(String(150))
    description: Mapped[str] = mapped_column(Text, default="")
    awarded_on: Mapped[dt.date] = mapped_column(Date)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class Remark(Base):
    __tablename__ = "remarks"
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), index=True)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(60), index=True)
    record_type: Mapped[str] = mapped_column(String(60))
    record_id: Mapped[str] = mapped_column(String(40), default="")
    previous_value: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    new_value: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)


@event.listens_for(AuditLog, "before_update")
def _audit_no_update(*_args):
    raise PermissionError("Audit log entries cannot be modified.")


@event.listens_for(AuditLog, "before_delete")
def _audit_no_delete(*_args):
    raise PermissionError("Audit log entries cannot be deleted.")


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(150))
    body: Mapped[str] = mapped_column(Text, default="")
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class SchoolSetting(Base):
    __tablename__ = "school_settings"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
