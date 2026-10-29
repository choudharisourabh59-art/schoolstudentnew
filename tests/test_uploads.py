import io
import zipfile

import pytest

from config import settings as cfg
from database import connection
from services import backup_service as bk
from services import upload_service as up
from services.errors import NotFound, PermissionDenied, ValidationFailed

PDF = b"%PDF-1.4\n%test document\n" + b"0" * 200


def make_zip(*names: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name in names:
            z.writestr(name, "<x/>")
    return buf.getvalue()


def stored_files():
    return [p for p in cfg.settings.storage_dir.rglob("*") if p.is_file()]


def test_valid_upload_roundtrip_and_metadata(world):
    doc = up.upload_document(world.admin, "Certificate.pdf", PDF, "certificate", world.b[0])
    meta, data = up.get_document(world.s1, doc)
    assert data == PDF and meta["name"] == "Certificate.pdf" and meta["content_type"] == "application/pdf"
    listed = up.list_documents(world.p1, world.b[0])[0]
    assert listed["size"] == len(PDF) and listed["approved"]


def test_files_persist_after_restart(world, db):
    doc = up.upload_document(world.admin, "a.pdf", PDF, "certificate", world.b[0])
    connection.configure(db)  # simulate a server restart: new engine, same database file and storage folder
    assert up.get_document(world.s1, doc)[1] == PDF


@pytest.mark.parametrize("name,data", [
    ("virus.pdf", b"MZ\x90\x00 this is an executable"),   # content does not match extension
    ("photo.png", PDF),
    ("run.exe", b"MZ\x90\x00"),                           # type not allowed
    ("script.php", b"<?php ?>"),
    ("empty.pdf", b""),
    ("fake.docx", make_zip("not/word.xml")),              # zip without a Word document inside
    ("fake.xlsx", make_zip("word/document.xml")),         # wrong kind of Office file
])
def test_unsafe_uploads_rejected(world, name, data):
    with pytest.raises(ValidationFailed):
        up.upload_document(world.admin, name, data, "other", world.b[0])
    assert stored_files() == []


def test_real_office_files_accepted(world):
    up.upload_document(world.admin, "notes.docx", make_zip("word/document.xml"), "other", world.b[0])
    up.upload_document(world.admin, "marks.xlsx", make_zip("xl/workbook.xml"), "other", world.b[0])
    assert len(stored_files()) == 2


def test_size_limit_enforced(world, monkeypatch):
    monkeypatch.setattr(cfg.settings, "max_upload_mb", 1)
    with pytest.raises(ValidationFailed):
        up.upload_document(world.admin, "big.pdf", b"%PDF-" + b"0" * (1024 * 1024 + 1), "other", world.b[0])


def test_path_traversal_filename_is_neutralised(world):
    doc = up.upload_document(world.admin, "../../etc/passwd.pdf", PDF, "other", world.b[0])
    assert up.list_documents(world.admin, world.b[0])[0]["name"] == "passwd.pdf"
    root = cfg.settings.storage_dir.resolve()
    assert all(root in p.resolve().parents and ".." not in p.name for p in stored_files())
    with pytest.raises(PermissionDenied):
        up._local_path("../../outside.pdf")
    assert doc


def test_never_overwrites_and_rejects_identical_duplicates(world):
    first = up.upload_document(world.admin, "one.pdf", PDF, "other", world.b[0])
    with pytest.raises(ValidationFailed):
        up.upload_document(world.admin, "again.pdf", PDF, "other", world.b[0])
    second = up.upload_document(world.admin, "two.pdf", PDF + b"x", "other", world.b[0])
    assert first != second and len(stored_files()) == 2


def test_unauthorised_users_cannot_download(world):
    doc = up.upload_document(world.admin, "private.pdf", PDF, "medical", world.b[0])
    for actor in (world.s2, world.p2, world.t2):
        with pytest.raises(PermissionDenied):
            up.get_document(actor, doc)
        with pytest.raises(PermissionDenied):
            up.list_documents(actor, world.b[0])
    assert up.get_document(world.t1, doc)[1] == PDF and up.get_document(world.p1, doc)[1] == PDF


def test_student_uploads_need_approval_before_family_sees_them(world):
    doc = up.upload_document(world.s1, "award.pdf", PDF, "certificate", world.b[0])
    assert up.list_documents(world.s1, world.b[0])           # the uploader sees it
    assert up.list_documents(world.p1, world.b[0]) == []     # the parent does not, yet
    with pytest.raises(PermissionDenied):
        up.get_document(world.p1, doc)
    up.approve_document(world.t1, doc)
    assert up.get_document(world.p1, doc)[1] == PDF
    with pytest.raises(PermissionDenied):
        up.upload_document(world.s1, "me.png", b"\x89PNG\r\n\x1a\n" + b"0" * 20, "photo", world.b[0])


def test_tampered_file_fails_integrity_check(world):
    doc = up.upload_document(world.admin, "x.pdf", PDF, "other", world.b[0])
    stored_files()[0].write_bytes(PDF + b"tampered")
    with pytest.raises(NotFound):
        up.get_document(world.admin, doc)


def test_delete_needs_admin_and_reason_and_removes_access(world):
    doc = up.upload_document(world.admin, "x.pdf", PDF, "other", world.b[0])
    with pytest.raises(PermissionDenied):
        up.delete_document(world.t1, doc, "no")
    with pytest.raises(ValidationFailed):
        up.delete_document(world.admin, doc, "")
    up.delete_document(world.admin, doc, "uploaded by mistake")
    with pytest.raises(NotFound):
        up.get_document(world.admin, doc)
    assert up.list_documents(world.admin, world.b[0]) == []


def test_missing_and_orphaned_files_are_detected(world):
    up.upload_document(world.admin, "x.pdf", PDF, "other", world.b[0])
    assert bk.storage_consistency(world.admin)["ok"]
    stored_files()[0].unlink()
    (cfg.settings.storage_dir / "stray.pdf").write_bytes(PDF)
    report = bk.storage_consistency(world.admin)
    assert report["missing_files"] == ["x.pdf"] and report["orphaned_files"] == ["stray.pdf"] and not report["ok"]


def test_database_storage_backend(world, monkeypatch):
    monkeypatch.setattr(cfg.settings, "storage_backend", "db")
    doc = up.upload_document(world.admin, "db.pdf", PDF, "other", world.b[0])
    assert up.get_document(world.s1, doc)[1] == PDF and stored_files() == []
    assert bk.storage_consistency(world.admin)["ok"]


def test_school_wide_documents_are_admin_only(world):
    doc = up.upload_document(world.admin, "policy.pdf", PDF, "report", None)
    with pytest.raises(PermissionDenied):
        up.upload_document(world.t1, "x.pdf", PDF, "report", None)
    with pytest.raises(PermissionDenied):
        up.get_document(world.t1, doc)
    assert up.get_document(world.principal, doc)[1] == PDF
