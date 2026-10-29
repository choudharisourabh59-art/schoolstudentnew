"""Dashboard metrics, report data (pandas DataFrames, already scoped to what the actor may see) and exports."""
from __future__ import annotations

import datetime as dt
import io

import pandas as pd
from sqlalchemy import func, select

from config.settings import today
from database.connection import session_scope
from database.models import (AcademicYear, Attendance, AuditLog, Enrollment, SchoolClass, Section, Student, Teacher,
                             User)
from services import grade_service as gs
from services.attendance_service import attendance_percentage
from services.errors import NotFound, ValidationFailed
from services.permissions import (OVERSIGHT_ROLES, CurrentUser, enrollment_scope, require_class_access, require_role,
                                  require_student_access)
from services.student_service import enrollment_history


def dashboard_metrics(actor: CurrentUser, year_id: int, class_id: int | None = None,
                      section_id: int | None = None) -> dict:
    with session_scope() as s:
        def scoped(stmt):
            stmt = stmt.where(Enrollment.academic_year_id == year_id)
            if class_id:
                stmt = stmt.where(Enrollment.class_id == class_id)
            if section_id:
                stmt = stmt.where(Enrollment.section_id == section_id)
            scope = enrollment_scope(actor)
            return stmt if scope is None else stmt.where(scope)

        active = s.scalar(scoped(select(func.count(Enrollment.id)).join(Student, Student.id == Enrollment.student_id)
                                 .where(Student.is_archived.is_(False), Enrollment.status == "active")))
        classes = s.scalar(scoped(select(func.count(func.distinct(Enrollment.class_id)))))
        pairs = select(Enrollment.class_id, Enrollment.section_id).distinct()
        sections = s.scalar(select(func.count()).select_from(scoped(pairs).subquery()))
        by_status = dict(s.execute(scoped(select(Enrollment.status, func.count(Enrollment.id)))
                                   .group_by(Enrollment.status)).all())
        by_class = [{"class": r[0], "students": r[1]} for r in s.execute(
            scoped(select(SchoolClass.name, func.count(Enrollment.id)).join(SchoolClass, SchoolClass.id == Enrollment.class_id)
                   .where(Enrollment.status == "active")).group_by(SchoolClass.name, SchoolClass.level)
            .order_by(SchoolClass.level))]
        teachers = s.scalar(select(func.count(Teacher.id)).join(User, User.id == Teacher.user_id)
                            .where(User.is_active.is_(True)))
        year = s.get(AcademicYear, year_id)
        return {"active_students": int(active or 0), "teachers": int(teachers or 0), "classes": int(classes or 0),
                "sections": int(sections or 0), "enrollment_status": by_status, "students_by_class": by_class,
                "year": year.name if year else ""}


def recent_activity(actor: CurrentUser, limit: int = 10) -> list[dict]:
    require_role(actor, *OVERSIGHT_ROLES)
    with session_scope() as s:
        rows = s.execute(select(AuditLog.created_at, User.full_name, AuditLog.action, AuditLog.record_type,
                                AuditLog.record_id).outerjoin(User, User.id == AuditLog.user_id)
                         .order_by(AuditLog.id.desc()).limit(limit))
        return [{"when": r[0], "user": r[1] or "system", "action": r[2], "record": f"{r[3]} {r[4]}".strip()} for r in rows]


def audit_page(actor: CurrentUser, page: int = 1, page_size: int = 50, action: str | None = None) -> tuple[list[dict], int]:
    require_role(actor, *OVERSIGHT_ROLES)
    with session_scope() as s:
        base = select(AuditLog.id)
        if action:
            base = base.where(AuditLog.action == action)
        total = s.scalar(select(func.count()).select_from(base.subquery()))
        stmt = (select(AuditLog.id, AuditLog.created_at, User.username, AuditLog.action, AuditLog.record_type,
                       AuditLog.record_id, AuditLog.previous_value, AuditLog.new_value)
                .outerjoin(User, User.id == AuditLog.user_id).order_by(AuditLog.id.desc())
                .limit(page_size).offset((max(1, page) - 1) * page_size))
        if action:
            stmt = stmt.where(AuditLog.action == action)
        return [{"id": r[0], "when": r[1], "user": r[2] or "system", "action": r[3], "record_type": r[4],
                 "record_id": r[5], "previous": r[6], "new": r[7]} for r in s.execute(stmt)], int(total or 0)


# ---------- report data ----------
def report_card(actor: CurrentUser, student_id: int, year_name: str | None = None) -> pd.DataFrame:
    rows = []
    for exam in gs.student_results(actor, student_id):
        if year_name and exam["year"] != year_name:
            continue
        for sub in exam["subjects"]:
            rows.append({"Year": exam["year"], "Class": exam["class"], "Term": exam["term"], "Exam": exam["exam"],
                         "Subject": sub["subject"],
                         "Marks": "ABSENT" if sub["absent"] else ("MISSING" if sub["marks"] is None else gs.fmt(sub["marks"])),
                         "Max": gs.fmt(sub["max"]), "Pass mark": gs.fmt(sub["passing"])})
        rows.append({"Year": exam["year"], "Class": exam["class"], "Term": exam["term"], "Exam": exam["exam"],
                     "Subject": "TOTAL", "Marks": gs.fmt(exam["total"]), "Max": gs.fmt(exam["max_total"]),
                     "Pass mark": f"{gs.fmt(exam['percentage'])}% | {exam['grade'] or '-'} | {exam['result']}"})
    return pd.DataFrame(rows)


def student_attendance_report(actor: CurrentUser, student_id: int, year_id: int | None = None) -> pd.DataFrame:
    from services.attendance_service import student_history

    names = {"P": "Present", "A": "Absent", "L": "Late", "E": "Excused"}
    return pd.DataFrame([{"Date": r["date"], "Status": names[r["status"]], "Year": r["year"], "Class": r["class"],
                          "Section": r["section"]} for r in student_history(actor, student_id, year_id)])


def class_attendance_report(actor: CurrentUser, year_id: int, class_id: int, section_id: int,
                            since: dt.date, until: dt.date) -> pd.DataFrame:
    if since > until:
        raise ValidationFailed("The start date is after the end date.")
    until = min(until, today())
    with session_scope() as s:
        require_class_access(s, actor, year_id, class_id, section_id)
        rows = s.execute(
            select(Enrollment.roll_no, Student.student_code, Student.full_name, Attendance.status, func.count())
            .select_from(Enrollment).join(Student, Student.id == Enrollment.student_id)
            .outerjoin(Attendance, (Attendance.enrollment_id == Enrollment.id) & (Attendance.date >= since) &
                       (Attendance.date <= until))
            .where(Enrollment.academic_year_id == year_id, Enrollment.class_id == class_id,
                   Enrollment.section_id == section_id)
            .group_by(Enrollment.id, Enrollment.roll_no, Student.student_code, Student.full_name, Attendance.status))
        table: dict[str, dict] = {}
        for roll, code, name, status, n in rows:
            rec = table.setdefault(code, {"Roll": roll, "Student ID": code, "Name": name,
                                          "Present": 0, "Absent": 0, "Late": 0, "Excused": 0})
            if status:
                rec[{"P": "Present", "A": "Absent", "L": "Late", "E": "Excused"}[status]] += n
        for rec in table.values():
            pct = attendance_percentage(rec["Present"], rec["Absent"], rec["Late"], rec["Excused"])
            rec["Attendance %"] = float(round(pct, 2)) if pct is not None else None
        return pd.DataFrame(sorted(table.values(), key=lambda r: (r["Roll"] is None, r["Roll"] or 0, r["Name"])))


def exam_report(actor: CurrentUser, exam_id: int, class_id: int, section_id: int | None = None,
                subject: str | None = None) -> pd.DataFrame:
    rows = []
    for r in gs.exam_results(actor, exam_id, class_id, section_id):
        rec = {"Roll": r["roll_no"], "Student ID": r["student_code"], "Name": r["name"], "Section": r["section"]}
        for sub in r["subjects"]:
            if subject and sub["subject"] != subject:
                continue
            rec[sub["subject"]] = "ABSENT" if sub["absent"] else ("" if sub["marks"] is None else gs.fmt(sub["marks"]))
        if not subject:
            rec.update({"Total": gs.fmt(r["total"]), "Max": gs.fmt(r["max_total"]),
                        "Percentage": gs.fmt(r["percentage"]), "Grade": r["grade"] or "-", "Result": r["result"]})
        rows.append(rec)
    return pd.DataFrame(rows)


def academic_history(actor: CurrentUser, student_id: int) -> pd.DataFrame:
    per_year: dict[str, list] = {}
    for exam in gs.student_results(actor, student_id):
        if exam["percentage"] is not None and exam["result"] in ("PASS", "FAIL"):
            per_year.setdefault(exam["year"], []).append(exam["percentage"])
    rows = []
    for h in enrollment_history(actor, student_id):
        pcts = per_year.get(h["year_name"])
        avg = sum(pcts) / len(pcts) if pcts else None
        rows.append({"Year": h["year_name"], "Class": h["class_name"], "Section": h["section_name"],
                     "Roll": h["roll_no"], "Outcome": h["status"],
                     "Average %": gs.fmt(avg) if avg is not None else "-"})
    return pd.DataFrame(rows)


def enrollment_report(actor: CurrentUser, year_id: int) -> pd.DataFrame:
    require_role(actor, *OVERSIGHT_ROLES)
    with session_scope() as s:
        rows = s.execute(
            select(SchoolClass.name, Section.name, Enrollment.status, func.count(Enrollment.id))
            .join(SchoolClass, SchoolClass.id == Enrollment.class_id).join(Section, Section.id == Enrollment.section_id)
            .where(Enrollment.academic_year_id == year_id)
            .group_by(SchoolClass.name, SchoolClass.level, Section.name, Enrollment.status)
            .order_by(SchoolClass.level, Section.name))
        return pd.DataFrame([{"Class": r[0], "Section": r[1], "Status": r[2], "Students": r[3]} for r in rows])


# ---------- exports ----------
def _safe_cell(value):
    """Neutralise spreadsheet formula injection (cells starting with = + - @)."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@") and not _looks_numeric(value):
        return "'" + value
    return value


def _looks_numeric(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    return df.apply(lambda col: col.map(_safe_cell)) if not df.empty else df


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    return _clean(df).to_csv(index=False).encode("utf-8-sig")


def to_excel_bytes(df: pd.DataFrame, sheet: str = "Report") -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        _clean(df).to_excel(writer, index=False, sheet_name=sheet[:30])
    return buf.getvalue()


def to_pdf_bytes(title: str, df: pd.DataFrame, subtitle: str = "") -> bytes:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError:
        raise ValidationFailed("PDF export needs the 'reportlab' package (see requirements.txt).")
    buf = io.BytesIO()
    page = landscape(A4) if len(df.columns) > 6 else A4
    doc = SimpleDocTemplate(buf, pagesize=page, leftMargin=24, rightMargin=24, topMargin=28, bottomMargin=28)
    styles = getSampleStyleSheet()
    story = [Paragraph(title, styles["Title"])]
    if subtitle:
        story.append(Paragraph(subtitle, styles["Normal"]))
    story.append(Spacer(1, 10))
    if df.empty:
        story.append(Paragraph("No records for the selected filters.", styles["Normal"]))
    else:
        data = [list(map(str, df.columns))] + [["" if pd.isna(v) else str(v)[:60] for v in row]
                                                for row in df.head(3000).itertuples(index=False)]
        table = Table(data, repeatRows=1)
        table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#6FBF73")),
                                   ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("FONTSIZE", (0, 0), (-1, -1), 8),
                                   ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CCCCCC")),
                                   ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5FAF5")])]))
        story.append(table)
    doc.build(story)
    return buf.getvalue()
