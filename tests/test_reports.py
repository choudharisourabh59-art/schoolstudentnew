import datetime as dt

import pandas as pd
import pytest

from services import attendance_service as att
from services import backup_service as bk
from services import grade_service as gs
from services import report_service as rs
from services.errors import PermissionDenied, ValidationFailed
from tests.conftest import enter_all


def published(world):
    enter_all(world, [80, 90, 40], [40, 45, 10])
    gs.publish_results(world.admin, world.exam)


def test_report_card_contains_only_the_right_student(world):
    published(world)
    df = rs.report_card(world.s1, world.b[0])
    assert set(df["Year"]) == {"CY"} and set(df["Subject"]) == {"Mathematics", "Science", "TOTAL"}
    assert df[df["Subject"] == "Mathematics"]["Marks"].iloc[0] == "80.00"
    total = df[df["Subject"] == "TOTAL"].iloc[0]
    assert total["Marks"] == "120.00" and "80.00% | A | PASS" in total["Pass mark"]
    with pytest.raises(PermissionDenied):
        rs.report_card(world.s1, world.b[1])
    with pytest.raises(PermissionDenied):
        rs.report_card(world.p2, world.b[0])


def test_report_card_year_filter(world):
    published(world)
    assert rs.report_card(world.admin, world.b[0], "PY").empty


def test_exam_report_and_scoping(world):
    published(world)
    df = rs.exam_report(world.admin, world.exam, world.c9)
    assert list(df["Name"]) == ["Curr Student 1", "Curr Student 2", "Curr Student 3"]
    assert list(df["Result"]) == ["PASS", "PASS", "FAIL"]
    assert len(rs.exam_report(world.s2, world.exam, world.c9)) == 1  # a student only receives their own row
    assert list(rs.exam_report(world.admin, world.exam, world.c9, subject="Science").columns)[-1] == "Science"


def test_class_attendance_report_values_and_permissions(world):
    day = world.today
    att.mark_attendance(world.admin, world.cy, world.c9, world.secA, day,
                        {world.enr(sid, world.cy): st for sid, st in zip(world.b, "PAL")})
    df = rs.class_attendance_report(world.t1, world.cy, world.c9, world.secA, day, day)
    assert len(df) == 3 and df["Attendance %"].tolist() == [100.0, 0.0, 100.0]
    with pytest.raises(PermissionDenied):
        rs.class_attendance_report(world.t2, world.cy, world.c9, world.secA, day, day)
    with pytest.raises(ValidationFailed):
        rs.class_attendance_report(world.admin, world.cy, world.c9, world.secA, day, day - dt.timedelta(days=1))


def test_student_attendance_report_and_history(world):
    att.mark_attendance(world.admin, world.cy, world.c9, world.secA, world.today,
                        {world.enr(sid, world.cy): "P" for sid in world.b})
    assert rs.student_attendance_report(world.p1, world.b[0])["Status"].tolist() == ["Present"]
    assert len(rs.academic_history(world.s1, world.b[0])) == 1
    with pytest.raises(PermissionDenied):
        rs.student_attendance_report(world.p1, world.b[1])


def test_enrollment_report_and_dashboard_metrics(world):
    df = rs.enrollment_report(world.admin, world.cy)
    assert df["Students"].sum() == 3
    with pytest.raises(PermissionDenied):
        rs.enrollment_report(world.t1, world.cy)
    m = rs.dashboard_metrics(world.admin, world.cy)
    assert m["active_students"] == 3 and m["teachers"] == 2 and m["classes"] == 1
    assert rs.dashboard_metrics(world.t2, world.cy)["active_students"] == 0  # t2's class is empty
    assert rs.dashboard_metrics(world.s1, world.cy)["active_students"] == 1


def test_exports_are_valid_and_neutralise_formulas():
    df = pd.DataFrame({"Name": ["=HYPERLINK(\"http://evil\")", "+cmd", "-5", "Normal"], "Marks": [1, 2, 3, 4]})
    text = rs.to_csv_bytes(df).decode("utf-8-sig")
    assert "'=HYPERLINK" in text and "'+cmd" in text and ",1" in text and "\n-5," in text
    assert rs.to_excel_bytes(df)[:2] == b"PK"
    assert rs.to_pdf_bytes("Title", df, "subtitle")[:4] == b"%PDF"
    assert rs.to_pdf_bytes("Empty", pd.DataFrame())[:4] == b"%PDF"


def test_audit_viewer_is_oversight_only(world):
    rows, total = rs.audit_page(world.principal, 1, 10)
    assert total > 0 and len(rows) <= 10
    for actor in (world.t1, world.s1, world.p1):
        with pytest.raises(PermissionDenied):
            rs.audit_page(actor)
        with pytest.raises(PermissionDenied):
            rs.recent_activity(actor)


def test_backup_verify_and_restore_to_test_file(world, tmp_path):
    manifest = bk.backup_database(world.admin)
    path = tmp_path / "backups" / manifest["file"]
    assert bk.verify_backup(path)["ok"]
    restored = bk.restore_sqlite_to_test(path, tmp_path / "restore_check" / "copy.sqlite")
    import sqlite3
    con = sqlite3.connect(restored)
    assert con.execute("SELECT count(*) FROM students").fetchone()[0] == 7
    con.close()
    path.write_bytes(path.read_bytes() + b"corrupt")  # tampering is detected
    assert not bk.verify_backup(path)["ok"]
    with pytest.raises(ValidationFailed):
        bk.restore_sqlite_to_test(path, tmp_path / "x.sqlite")
    for actor in (world.t1, world.principal):
        with pytest.raises(PermissionDenied):
            bk.backup_database(actor)
