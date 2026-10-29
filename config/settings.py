"""Central configuration. Values come from environment variables, then Streamlit secrets."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent.parent

try:  # optional: load a local .env file
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except ImportError:  # pragma: no cover
    pass


def _get(key: str, default: str | None = None) -> str | None:
    value = os.environ.get(key)
    if value not in (None, ""):
        return value
    try:  # Streamlit Cloud secrets
        import streamlit as st

        if key in st.secrets:
            return str(st.secrets[key])
    except Exception:
        pass
    return default


def _normalise_db_url(url: str) -> str:
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


@dataclass
class Settings:
    app_name: str = "Scholar SIS"
    database_url: str = ""
    storage_backend: str = "local"
    storage_dir: Path = BASE_DIR / "storage" / "uploads"
    backup_dir: Path = BASE_DIR / "backups"
    max_upload_mb: int = 10
    allowed_extensions: tuple[str, ...] = ("pdf", "jpg", "jpeg", "png", "docx", "xlsx")
    timezone: str = "Asia/Kolkata"
    session_idle_minutes: int = 30
    max_failed_logins: int = 5
    lockout_minutes: int = 15
    password_min_length: int = 10
    reset_token_minutes: int = 30
    is_demo: bool = False
    s3_bucket: str = ""
    s3_prefix: str = "sis/"
    s3_endpoint_url: str = ""
    bootstrap_username: str = ""
    bootstrap_email: str = ""
    bootstrap_password: str = ""
    extra: dict = field(default_factory=dict)


def load_settings() -> Settings:
    db = _get("DATABASE_URL") or f"sqlite:///{(BASE_DIR / 'storage' / 'sis_dev.db').as_posix()}"
    return Settings(
        database_url=_normalise_db_url(db),
        storage_backend=(_get("STORAGE_BACKEND", "local") or "local").lower(),
        storage_dir=Path(_get("STORAGE_DIR", str(BASE_DIR / "storage" / "uploads"))),
        backup_dir=Path(_get("BACKUP_DIR", str(BASE_DIR / "backups"))),
        max_upload_mb=int(_get("MAX_UPLOAD_MB", "10") or 10),
        timezone=_get("TIMEZONE", "Asia/Kolkata") or "Asia/Kolkata",
        is_demo=(_get("SIS_DEMO_MODE", "0") == "1"),
        s3_bucket=_get("S3_BUCKET", "") or "",
        s3_prefix=_get("S3_PREFIX", "sis/") or "sis/",
        s3_endpoint_url=_get("S3_ENDPOINT_URL", "") or "",
        bootstrap_username=_get("SIS_ADMIN_USERNAME", "") or "",
        bootstrap_email=_get("SIS_ADMIN_EMAIL", "") or "",
        bootstrap_password=_get("SIS_ADMIN_PASSWORD", "") or "",
    )


settings = load_settings()


def today() -> date:
    """School-local 'today' (never the server's UTC date)."""
    return datetime.now(ZoneInfo(settings.timezone)).date()


# Academic policy defaults. Stored in the database (SchoolSetting 'policy') and editable in Settings.
# Decimal values are kept as strings so they survive JSON storage without float rounding.
DEFAULT_POLICY: dict = {
    "grade_bands": [["90", "A+"], ["80", "A"], ["70", "B"], ["60", "C"], ["50", "D"], ["40", "E"], ["0", "F"]],
    "pass_percentage": "40",
    "require_subject_pass": True,
    "absent_is_fail": True,
    "allow_negative_marks": False,
    "late_counts_as_present": True,
    "attendance_edit_days": 7,
    "amend_roles": ["admin"],
    "promotion_min_percentage": "40",
    "promotion_min_attendance": "75",
    "promotion_level_step": 1,
}
