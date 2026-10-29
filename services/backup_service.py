"""Backups, integrity checks and storage consistency. A backup only counts once it has been restored and verified."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import zipfile
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.engine import make_url

from config.settings import settings
from database.connection import session_scope
from database.models import DocumentBlob, UploadedDocument
from services import audit
from services.errors import ValidationFailed
from services.permissions import ADMIN, CurrentUser, require_role


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")


def _write_manifest(path: Path, kind: str) -> dict:
    manifest = {"file": path.name, "kind": kind, "sha256": _sha256(path), "size": path.stat().st_size,
                "created_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
    path.with_suffix(path.suffix + ".json").write_text(json.dumps(manifest, indent=2))
    return manifest


def backup_database(actor: CurrentUser) -> dict:
    require_role(actor, ADMIN)
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    url = make_url(settings.database_url)
    if url.get_backend_name() == "sqlite":
        target = settings.backup_dir / f"db_{_stamp()}.sqlite"
        src = sqlite3.connect(url.database)
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)  # consistent online copy
        finally:
            dst.close()
            src.close()
        kind = "sqlite"
    elif url.get_backend_name() == "postgresql":
        if shutil.which("pg_dump") is None:
            raise ValidationFailed("pg_dump is not installed on this server. Use your database provider's backups "
                                   "or run the documented pg_dump command from a machine that has it.")
        target = settings.backup_dir / f"db_{_stamp()}.dump"
        env = {**os.environ, "PGPASSWORD": url.password or ""}
        cmd = ["pg_dump", "--format=custom", "--no-owner", "-h", url.host or "localhost", "-p", str(url.port or 5432),
               "-U", url.username or "", "-d", url.database or "", "-f", str(target)]
        result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=1800)
        if result.returncode != 0:
            target.unlink(missing_ok=True)
            raise ValidationFailed("pg_dump failed: " + result.stderr.strip()[:300])
        kind = "postgres"
    else:
        raise ValidationFailed("Unsupported database for built-in backups.")
    manifest = _write_manifest(target, kind)
    with session_scope() as s:
        audit.log(s, actor, "backup_database", "Backup", target.name, None, {"size": manifest["size"]})
    return manifest


def backup_files(actor: CurrentUser) -> dict:
    """Zips the local upload folder. (With STORAGE_BACKEND=db the files are inside the database backup.)"""
    require_role(actor, ADMIN)
    if settings.storage_backend != "local":
        raise ValidationFailed("File backup applies to local storage only. Files held in the database or in S3 are "
                               "covered by the database backup / bucket versioning.")
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    target = settings.backup_dir / f"files_{_stamp()}.zip"
    root = settings.storage_dir
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        if root.exists():
            for path in root.rglob("*"):
                if path.is_file():
                    z.write(path, path.relative_to(root).as_posix())
    manifest = _write_manifest(target, "files")
    with session_scope() as s:
        audit.log(s, actor, "backup_files", "Backup", target.name, None, {"size": manifest["size"]})
    return manifest


def verify_backup(path: Path) -> dict:
    """Checks the recorded checksum and that the backup can actually be opened."""
    path = Path(path)
    manifest_path = path.with_suffix(path.suffix + ".json")
    problems: list[str] = []
    if not path.exists():
        return {"ok": False, "problems": ["backup file not found"]}
    if manifest_path.exists():
        if json.loads(manifest_path.read_text()).get("sha256") != _sha256(path):
            problems.append("checksum does not match the manifest (file changed or corrupted)")
    else:
        problems.append("manifest file is missing")
    if path.suffix == ".sqlite":
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                problems.append("SQLite integrity_check failed")
            if con.execute("SELECT count(*) FROM sqlite_master WHERE name='students'").fetchone()[0] != 1:
                problems.append("expected tables are missing")
        except sqlite3.DatabaseError as exc:
            problems.append(f"cannot be opened: {exc}")
        finally:
            con.close()
    elif path.suffix == ".zip":
        with zipfile.ZipFile(path) as z:
            if z.testzip() is not None:
                problems.append("zip archive is corrupt")
    return {"ok": not problems, "problems": problems}


def restore_sqlite_to_test(backup: Path, target: Path) -> Path:
    """Restores into a separate file for checking. Never touches the live database."""
    backup, target = Path(backup), Path(target)
    if target.resolve() == Path(make_url(settings.database_url).database or "").resolve():
        raise ValidationFailed("Refusing to overwrite the live database. Restore to a test file instead.")
    check = verify_backup(backup)
    if not check["ok"]:
        raise ValidationFailed("Backup failed verification: " + "; ".join(check["problems"]))
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(backup, target)
    return target


def list_backups() -> list[dict]:
    if not settings.backup_dir.exists():
        return []
    out = []
    for p in sorted(settings.backup_dir.iterdir(), reverse=True):
        if p.suffix in (".sqlite", ".dump", ".zip"):
            out.append({"name": p.name, "size": p.stat().st_size,
                        "created": dt.datetime.fromtimestamp(p.stat().st_mtime, dt.timezone.utc)})
    return out


def storage_consistency(actor: CurrentUser) -> dict:
    """Metadata <-> stored bytes: finds missing files and orphaned files."""
    require_role(actor, ADMIN)
    with session_scope() as s:
        docs = list(s.scalars(select(UploadedDocument)))
        missing: list[str] = []
        db_ids = set(s.scalars(select(DocumentBlob.document_id)))
        for d in docs:
            if d.storage_backend == "local":
                root = settings.storage_dir.resolve()
                if not (root / d.storage_key[:2] / d.storage_key).exists():
                    missing.append(d.original_filename)
            elif d.storage_backend == "db" and d.id not in db_ids:
                missing.append(d.original_filename)
        orphans: list[str] = []
        if settings.storage_dir.exists():
            known = {d.storage_key for d in docs if d.storage_backend == "local"}
            orphans = [p.name for p in settings.storage_dir.rglob("*") if p.is_file() and p.name not in known]
        return {"documents": len(docs), "missing_files": missing, "orphaned_files": orphans,
                "ok": not missing and not orphans}
