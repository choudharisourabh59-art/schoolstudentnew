"""Document uploads: content validation, safe unique storage keys, permission-checked access, audit trail."""
from __future__ import annotations

import hashlib
import io
import os
import re
import uuid
import zipfile
from pathlib import Path

from sqlalchemy import select

from config.settings import settings
from database.connection import session_scope
from database.models import DocumentBlob, Student, UploadedDocument, utcnow
from services import audit
from services.errors import NotFound, PermissionDenied, ValidationFailed
from services.permissions import (ADMIN, OVERSIGHT_ROLES, PARENT, STUDENT, TEACHER, CurrentUser, require_role,
                                  require_student_access)

CONTENT_TYPES = {
    "pdf": "application/pdf", "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
CATEGORIES = ("photo", "certificate", "report", "medical", "identity", "other")
MAX_UNZIPPED = 150 * 1024 * 1024


def safe_display_name(name: str) -> str:
    name = os.path.basename((name or "").replace("\\", "/"))
    name = re.sub(r"[\x00-\x1f\x7f<>:\"|?*]", "", name).strip(". ")
    return name[:150] or "file"


def sniff_ok(ext: str, data: bytes) -> bool:
    """The file's real content must match its extension (not just the name)."""
    if ext == "pdf":
        return data.startswith(b"%PDF-")
    if ext in ("jpg", "jpeg"):
        return data[:3] == b"\xff\xd8\xff"
    if ext == "png":
        return data[:8] == b"\x89PNG\r\n\x1a\n"
    if ext in ("docx", "xlsx"):
        if not data.startswith(b"PK\x03\x04"):
            return False
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                infos = z.infolist()
                if sum(i.file_size for i in infos) > MAX_UNZIPPED:  # zip-bomb guard
                    return False
                names = {i.filename for i in infos}
        except zipfile.BadZipFile:
            return False
        return ("word/document.xml" if ext == "docx" else "xl/workbook.xml") in names
    return False


# ---------- storage back-ends ----------
def _local_path(key: str) -> Path:
    root = settings.storage_dir.resolve()
    path = (root / key[:2] / key).resolve()
    if root not in path.parents:  # path-traversal guard
        raise PermissionDenied("Invalid storage location.")
    return path


def _s3():
    import boto3  # imported lazily: only needed for STORAGE_BACKEND=s3

    return boto3.client("s3", endpoint_url=settings.s3_endpoint_url or None)


def _put(s, doc: UploadedDocument, data: bytes) -> None:
    if doc.storage_backend == "local":
        path = _local_path(doc.storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "xb") as fh:  # 'x' = fail instead of overwrite
            fh.write(data)
    elif doc.storage_backend == "db":
        s.add(DocumentBlob(document_id=doc.id, data=data))
    elif doc.storage_backend == "s3":
        _s3().put_object(Bucket=settings.s3_bucket, Key=settings.s3_prefix + doc.storage_key, Body=data,
                         ServerSideEncryption="AES256")
    else:
        raise ValidationFailed("Unknown storage backend.")


def _load(s, doc: UploadedDocument) -> bytes:
    if doc.storage_backend == "local":
        path = _local_path(doc.storage_key)
        if not path.exists():
            raise NotFound("The stored file is missing. Please contact an administrator.")
        return path.read_bytes()
    if doc.storage_backend == "db":
        blob = s.get(DocumentBlob, doc.id)
        if blob is None:
            raise NotFound("The stored file is missing. Please contact an administrator.")
        return bytes(blob.data)
    if doc.storage_backend == "s3":
        return _s3().get_object(Bucket=settings.s3_bucket, Key=settings.s3_prefix + doc.storage_key)["Body"].read()
    raise NotFound("Unknown storage backend.")


# ---------- operations ----------
def upload_document(actor: CurrentUser, filename: str, data: bytes, category: str,
                    student_id: int | None = None) -> int:
    if category not in CATEGORIES:
        raise ValidationFailed("Unknown document category.")
    if not data:
        raise ValidationFailed("The file is empty.")
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise ValidationFailed(f"The file is larger than the {settings.max_upload_mb} MB limit.")
    display = safe_display_name(filename)
    ext = display.rsplit(".", 1)[-1].lower() if "." in display else ""
    if ext not in settings.allowed_extensions:
        raise ValidationFailed("File type not allowed. Use: " + ", ".join(settings.allowed_extensions).upper() + ".")
    if not sniff_ok(ext, data):
        raise ValidationFailed("The file content does not match its extension, so it was rejected.")
    if category == "photo" and ext not in ("jpg", "jpeg", "png"):
        raise ValidationFailed("Photographs must be JPG or PNG images.")
    digest = hashlib.sha256(data).hexdigest()
    with session_scope() as s:
        if student_id is None:
            require_role(actor, ADMIN)  # school-wide documents
        else:
            require_student_access(s, actor, student_id)
            if actor.role == PARENT or actor.role == STUDENT:
                if category in ("photo", "report", "identity", "medical"):
                    raise PermissionDenied("You can upload certificates or other documents only.")
            elif actor.role not in (ADMIN, TEACHER):
                raise PermissionDenied("You do not have permission to upload documents.")
        if student_id is not None and s.scalar(select(UploadedDocument.id).where(
                UploadedDocument.student_id == student_id, UploadedDocument.sha256 == digest,
                UploadedDocument.is_deleted.is_(False))):
            raise ValidationFailed("This exact file has already been uploaded for this student.")
        doc = UploadedDocument(
            student_id=student_id, original_filename=display, storage_key=f"{uuid.uuid4().hex}.{ext}",
            storage_backend=settings.storage_backend, content_type=CONTENT_TYPES[ext], file_size=len(data),
            sha256=digest, category=category, uploaded_by=actor.id,
            is_approved=actor.role in (ADMIN, TEACHER))
        s.add(doc)
        s.flush()
        _put(s, doc, data)
        if category == "photo" and student_id:
            s.get(Student, student_id).photo_document_id = doc.id
        audit.log(s, actor, "document_upload", "UploadedDocument", doc.id, None,
                  {"student_id": student_id, "category": category, "name": display, "size": len(data)})
        return doc.id


def _visible(actor: CurrentUser, doc: UploadedDocument) -> bool:
    if actor.role in OVERSIGHT_ROLES or actor.role == TEACHER:
        return True
    return doc.is_approved or doc.uploaded_by == actor.id


def list_documents(actor: CurrentUser, student_id: int | None = None) -> list[dict]:
    with session_scope() as s:
        stmt = select(UploadedDocument).where(UploadedDocument.is_deleted.is_(False))
        if student_id is None:
            require_role(actor, *OVERSIGHT_ROLES)
            stmt = stmt.where(UploadedDocument.student_id.is_(None))
        else:
            require_student_access(s, actor, student_id)
            stmt = stmt.where(UploadedDocument.student_id == student_id)
        docs = [d for d in s.scalars(stmt.order_by(UploadedDocument.uploaded_at.desc())) if _visible(actor, d)]
        return [{"id": d.id, "name": d.original_filename, "category": d.category, "size": d.file_size,
                 "uploaded_at": d.uploaded_at, "approved": d.is_approved, "content_type": d.content_type}
                for d in docs]


def get_document(actor: CurrentUser, document_id: int) -> tuple[dict, bytes]:
    """The only way to read a file: every download passes the same access check."""
    with session_scope() as s:
        doc = s.get(UploadedDocument, document_id)
        if doc is None or doc.is_deleted:
            raise NotFound("Document not found.")
        if doc.student_id is None:
            require_role(actor, *OVERSIGHT_ROLES)
        else:
            require_student_access(s, actor, doc.student_id)
        if not _visible(actor, doc):
            raise PermissionDenied("You do not have access to this document.")
        data = _load(s, doc)
        if hashlib.sha256(data).hexdigest() != doc.sha256:
            raise NotFound("The stored file failed its integrity check. Please contact an administrator.")
        audit.log(s, actor, "document_download", "UploadedDocument", doc.id)
        return {"name": doc.original_filename, "content_type": doc.content_type}, data


def approve_document(actor: CurrentUser, document_id: int) -> None:
    require_role(actor, ADMIN, TEACHER)
    with session_scope() as s:
        doc = s.get(UploadedDocument, document_id)
        if doc is None or doc.is_deleted:
            raise NotFound("Document not found.")
        if doc.student_id is not None:
            require_student_access(s, actor, doc.student_id)
        elif actor.role != ADMIN:
            raise PermissionDenied("Only administrators manage school-wide documents.")
        doc.is_approved = True
        audit.log(s, actor, "document_approve", "UploadedDocument", doc.id)


def delete_document(actor: CurrentUser, document_id: int, reason: str) -> None:
    """Soft delete: the record and the stored bytes are retained for the retention policy; access is removed."""
    require_role(actor, ADMIN)
    if not reason.strip():
        raise ValidationFailed("A reason is required to delete a document.")
    with session_scope() as s:
        doc = s.get(UploadedDocument, document_id)
        if doc is None or doc.is_deleted:
            raise NotFound("Document not found.")
        doc.is_deleted, doc.deleted_by, doc.deleted_at = True, actor.id, utcnow()
        audit.log(s, actor, "document_delete", "UploadedDocument", doc.id, {"name": doc.original_filename},
                  {"reason": reason})
