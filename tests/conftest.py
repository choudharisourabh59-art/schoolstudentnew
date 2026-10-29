"""Shared fixtures: an isolated SQLite database per test and a small but realistic school ('world')."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from config import settings as cfg
from database import connection
from database.migrate import run_migrations
from database.models import AuditLog, Enrollment
from services import academic_service as ac
from services import auth_service as auth
from services import grade_service as gs
from services import student_service as ss

PASSWORD = "Str0ng-Passw0rd!"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    monkeypatch.setattr(cfg.settings, "database_url", url)
    monkeypatch.setattr(cfg.settings, "storage_dir", tmp_path / "uploads")
    monkeypatch.setattr(cfg.settings, "storage_backend", "local")
    monkeypatch.setattr(cfg.settings, "backup_dir", tmp_path / "backups")
    connection.configure(url)
    run_migrations()
    yield url


def sdata(name: str, **extra) -> dict:
    return {"full_name": name, "date_of_birth": dt.date(2010, 1, 1), "admission_date": dt.date(2020, 4, 1),
            "guardian_name": "Guardian " + name, **extra}


def audit_rows(action: str | None = None) -> list[AuditLog]:
    with connection.session_scope() as s:
        stmt = select(AuditLog)
        if action:
            stmt = stmt.where(AuditLog.action == action)
        return list(s.scalars(stmt.order_by(AuditLog.id)))


@pytest.fixture()
def world(db):
    """Previous year: Class 8-A with a1..a4.  Current year: Class 9-A with b1..b3, one exam (Maths/100, Science/50)."""
    w = SimpleNamespace()
    today = cfg.today()
    auth.bootstrap_admin("admin", "admin@example.org", "Admin User", PASSWORD)
    w.admin = auth.authenticate("admin", PASSWORD)
    a = w.admin
    for name, role, extra in [("principal1", "principal", {}), ("teacher1", "teacher", {"employee_no": "T1"}),
                              ("teacher2", "teacher", {"employee_no": "T2"}), ("student1", "student", {}),
                              ("student2", "student", {}), ("parent1", "parent", {}), ("parent2", "parent", {})]:
        auth.create_user(a, name, name.title(), role, PASSWORD, **extra)
    cy_start = today - dt.timedelta(days=100)
    w.today, w.cy_start = today, cy_start
    w.py = ac.create_year(a, "PY", cy_start - dt.timedelta(days=365), cy_start - dt.timedelta(days=1))
    w.cy = ac.create_year(a, "CY", cy_start, cy_start + dt.timedelta(days=364), make_current=True)
    w.c8, w.c9, w.c10 = ac.create_class(a, "Class 8", 8), ac.create_class(a, "Class 9", 9), ac.create_class(a, "Class 10", 10)
    w.secA, w.secB = ac.create_section(a, "A"), ac.create_section(a, "B")
    w.math, w.sci = ac.create_subject(a, "MATH", "Mathematics"), ac.create_subject(a, "SCI", "Science")
    w.a = [ss.create_student(a, sdata(f"Prev Student {i}"), w.py, w.c8, w.secA, i) for i in range(1, 5)]
    w.b = [ss.create_student(a, sdata(f"Curr Student {i}"), w.cy, w.c9, w.secA, i) for i in range(1, 4)]
    auth_users = {u["username"]: u["id"] for u in auth.list_users(a)}
    auth.link_student_account(a, w.b[0], auth_users["student1"])
    auth.link_student_account(a, w.b[1], auth_users["student2"])
    auth.link_parent(a, auth_users["parent1"], w.b[0])
    auth.link_parent(a, auth_users["parent2"], w.b[1])
    login = lambda u: auth.authenticate(u, PASSWORD)  # noqa: E731
    w.principal, w.t1, w.t2 = login("principal1"), login("teacher1"), login("teacher2")
    w.s1, w.s2, w.p1, w.p2 = login("student1"), login("student2"), login("parent1"), login("parent2")
    ac.assign_teacher(a, w.t1.teacher_id, w.cy, w.c9, w.secA, None, True)
    ac.assign_teacher(a, w.t2.teacher_id, w.cy, w.c9, w.secB, None, True)  # a different class: no access to 9-A
    w.exam = gs.create_examination(a, w.cy, "Term 1", "Mid-term")
    w.es_math = gs.add_exam_subject(a, w.exam, w.math, w.c9, 100, 35)
    w.es_sci = gs.add_exam_subject(a, w.exam, w.sci, w.c9, 50, 20)
    w.enr = lambda student_id, year_id: _enrollment_id(student_id, year_id)
    w.PASSWORD = PASSWORD
    return w


def _enrollment_id(student_id: int, year_id: int) -> int:
    with connection.session_scope() as s:
        return s.scalar(select(Enrollment.id).where(Enrollment.student_id == student_id,
                                                    Enrollment.academic_year_id == year_id))


def enter_all(world, math_marks, sci_marks, actor=None):
    """Helper: enter marks for b1..b3 (None = leave empty)."""
    actor = actor or world.admin
    for es, marks in ((world.es_math, math_marks), (world.es_sci, sci_marks)):
        entries = [gs.MarkEntry(world.enr(sid, world.cy), m) for sid, m in zip(world.b, marks)]
        gs.enter_marks(actor, es, entries)
