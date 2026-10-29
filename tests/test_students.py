import datetime as dt

import pytest

from services import student_service as ss
from services.errors import PermissionDenied, ValidationFailed
from tests.conftest import audit_rows, sdata


def test_create_student_generates_unique_ids(world):
    ids = {ss.get_student(world.admin, sid)["student_code"] for sid in world.a + world.b}
    assert len(ids) == len(world.a + world.b)


def test_duplicate_student_id_rejected(world):
    ss.create_student(world.admin, sdata("Has Code", student_code="X-001"))
    with pytest.raises(ValidationFailed):
        ss.create_student(world.admin, sdata("Other Child", student_code="X-001"))


@pytest.mark.parametrize("bad", [
    {"date_of_birth": dt.date.today() + dt.timedelta(days=1)},
    {"full_name": "A"},
    {"email": "not-an-email"},
    {"admission_date": dt.date(2000, 1, 1)},
])
def test_invalid_student_data_rejected(world, bad):
    with pytest.raises(ValidationFailed):
        ss.create_student(world.admin, sdata("Valid Name", **bad))


def test_roll_number_must_be_unique_in_class(world):
    with pytest.raises(ValidationFailed):
        ss.create_student(world.admin, sdata("Roll Clash"), world.cy, world.c9, world.secA, 1)


def test_only_admin_can_create_edit_archive(world):
    for actor in (world.t1, world.principal, world.s1, world.p1):
        with pytest.raises(PermissionDenied):
            ss.create_student(actor, sdata("Nope Nope"))
        with pytest.raises(PermissionDenied):
            ss.set_archived(actor, world.b[0], True)


def test_update_records_previous_and_new_values(world):
    ss.update_student(world.admin, world.b[0], {"guardian_name": "New Guardian"})
    entry = audit_rows("student_update")[-1]
    assert entry.previous_value["guardian_name"].startswith("Guardian")
    assert entry.new_value["guardian_name"] == "New Guardian"


def test_student_sees_only_self_and_parent_only_own_child(world):
    rows, total = ss.search_students(world.s1)
    assert total == 1 and rows[0]["id"] == world.b[0]
    rows, total = ss.search_students(world.p2)
    assert [r["id"] for r in rows] == [world.b[1]]
    with pytest.raises(PermissionDenied):
        ss.get_student(world.s1, world.b[1])
    with pytest.raises(PermissionDenied):
        ss.get_student(world.p1, world.b[1])


def test_teacher_sees_only_assigned_class(world):
    ids = {r["id"] for r in ss.search_students(world.t1)[0]}
    assert ids == set(world.b)
    assert ss.search_students(world.t2)[1] == 0  # assigned to 9-B, which is empty
    with pytest.raises(PermissionDenied):
        ss.get_student(world.t2, world.b[0])


def test_unverified_parent_link_grants_nothing(world):
    from database.connection import session_scope
    from database.models import ParentStudent
    with session_scope() as s:
        for link in s.query(ParentStudent).all():
            link.verified = False
    assert ss.search_students(world.p1)[1] == 0


def test_search_filters_and_pagination(world):
    rows, total = ss.search_students(world.admin, query="Curr", page=1, page_size=2)
    assert total == 3 and len(rows) == 2
    rows2, _ = ss.search_students(world.admin, query="Curr", page=2, page_size=2)
    assert len(rows2) == 1
    assert ss.search_students(world.admin, class_id=world.c9, section_id=world.secB)[1] == 0
    assert ss.search_students(world.admin, query="100%")[1] == 0  # wildcard characters are escaped


def test_archive_hides_and_restore_returns(world):
    ss.set_archived(world.admin, world.b[2], True, "left the area")
    assert ss.search_students(world.admin, query="Curr")[1] == 2
    assert ss.search_students(world.admin, only_archived=True)[1] == 1
    ss.set_archived(world.admin, world.b[2], False)
    assert ss.search_students(world.admin, query="Curr")[1] == 3


CSV_OK = ("full_name,date_of_birth,admission_date,class,section,roll_no\n"
          "Imported One,2011-03-04,2021-04-01,Class 8,B,1\n"
          "Imported Two,05/06/2011,01/04/2021,Class 8,B,2\n")


def test_csv_import_success(world):
    result = ss.import_students_csv(world.admin, CSV_OK.encode(), world.cy)
    assert result["imported"] == 2 and not result["errors"]
    assert ss.search_students(world.admin, class_id=world.c8, section_id=world.secB)[1] == 2


def test_csv_import_is_all_or_nothing(world):
    bad = CSV_OK + "Bad Row,not-a-date,2021-04-01,Class 8,B,3\nDup Roll,2011-03-04,2021-04-01,Class 8,B,1\n"
    result = ss.import_students_csv(world.admin, bad.encode(), world.cy)
    assert result["imported"] == 0 and result["error_count"] == 2
    assert ss.search_students(world.admin, class_id=world.c8, section_id=world.secB)[1] == 0


def test_csv_import_rejects_missing_columns_and_non_admin(world):
    with pytest.raises(ValidationFailed):
        ss.import_students_csv(world.admin, b"full_name\nX Y\n", world.cy)
    with pytest.raises(PermissionDenied):
        ss.import_students_csv(world.t1, CSV_OK.encode(), world.cy)
