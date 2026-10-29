import datetime as dt

import pytest
from sqlalchemy import select, update

from config import settings as cfg
from database.connection import get_engine, session_scope
from database.models import AuditLog, User
from services import auth_service as auth
from services.errors import AuthError, PermissionDenied, ValidationFailed
from tests.conftest import PASSWORD, audit_rows


def test_password_is_hashed_never_plain(world):
    with session_scope() as s:
        stored = s.scalar(select(User.password_hash).where(User.username == "teacher1"))
    assert PASSWORD not in stored
    assert stored.startswith(("$argon2", "scrypt$"))
    assert auth.verify_password(PASSWORD, stored)
    assert not auth.verify_password("wrong-password", stored)


def test_login_by_username_and_email_case_insensitive(world):
    assert auth.authenticate("ADMIN", PASSWORD).role == "admin"
    assert auth.authenticate("admin@example.org", PASSWORD).role == "admin"


def test_bad_credentials_give_identical_generic_error(world):
    with pytest.raises(AuthError) as unknown:
        auth.authenticate("nobody", PASSWORD)
    with pytest.raises(AuthError) as wrong:
        auth.authenticate("admin", "wrong-password-123")
    assert str(unknown.value) == str(wrong.value)


def test_account_locks_after_repeated_failures(world, monkeypatch):
    monkeypatch.setattr(cfg.settings, "max_failed_logins", 3)
    for _ in range(3):
        with pytest.raises(AuthError):
            auth.authenticate("teacher1", "bad-password-000")
    with pytest.raises(AuthError):  # even the correct password is refused while locked
        auth.authenticate("teacher1", PASSWORD)
    assert audit_rows("account_locked")


def test_inactive_account_cannot_log_in(world):
    teacher_user = next(u for u in auth.list_users(world.admin) if u["username"] == "teacher1")
    auth.set_user_active(world.admin, teacher_user["id"], False)
    with pytest.raises(AuthError):
        auth.authenticate("teacher1", PASSWORD)
    assert auth.refresh_identity(teacher_user["id"]) is None


def test_admin_cannot_deactivate_self(world):
    with pytest.raises(ValidationFailed):
        auth.set_user_active(world.admin, world.admin.id, False)


def test_bootstrap_only_works_once(world):
    with pytest.raises(PermissionDenied):
        auth.bootstrap_admin("second", None, "Second", PASSWORD)


@pytest.mark.parametrize("bad", ["short", "1234567890123", "admin-admin-admin"])
def test_weak_passwords_rejected(world, bad):
    with pytest.raises(ValidationFailed):
        auth.validate_password(bad, "admin")


def test_only_admin_can_create_users(world):
    for actor in (world.t1, world.principal, world.s1, world.p1):
        with pytest.raises(PermissionDenied):
            auth.create_user(actor, "intruder", "Intruder", "admin", PASSWORD)


def test_reset_token_is_single_use_and_expires(world):
    uid = next(u["id"] for u in auth.list_users(world.admin) if u["username"] == "teacher1")
    token = auth.issue_reset_token(world.admin, uid)
    auth.reset_password_with_token(token, "An0ther-Str0ng-Pass")
    assert auth.authenticate("teacher1", "An0ther-Str0ng-Pass")
    with pytest.raises(AuthError):
        auth.reset_password_with_token(token, "Yet-An0ther-Pass1")
    token2 = auth.issue_reset_token(world.admin, uid)
    with session_scope() as s:
        s.execute(update(User).where(User.id == uid).values(reset_expires=dt.datetime(2000, 1, 1)))
    with pytest.raises(AuthError):
        auth.reset_password_with_token(token2, "Yet-An0ther-Pass1")


def test_change_password_requires_current_password(world):
    with pytest.raises(AuthError):
        auth.change_password(world.t1, "not-my-password", "N3w-Str0ng-Password")
    auth.change_password(world.t1, PASSWORD, "N3w-Str0ng-Password")
    assert auth.authenticate("teacher1", "N3w-Str0ng-Password")


def test_login_and_logout_are_audited(world):
    auth.logout(world.t1)
    assert audit_rows("login") and audit_rows("logout")


def test_audit_log_is_append_only(world):
    with pytest.raises(PermissionError):
        with session_scope() as s:
            s.scalar(select(AuditLog)).action = "tampered"
    with pytest.raises(Exception):  # database trigger blocks raw SQL as well
        with get_engine().begin() as conn:
            conn.exec_driver_sql("UPDATE audit_logs SET action='tampered'")
    with pytest.raises(Exception):
        with get_engine().begin() as conn:
            conn.exec_driver_sql("DELETE FROM audit_logs")
