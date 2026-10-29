"""Authentication, password hashing, account lifecycle and user administration."""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import os
import re
import secrets

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from config.settings import settings
from database.connection import session_scope
from database.models import Parent, ParentStudent, Student, Teacher, User, utcnow
from services import audit
from services.errors import AuthError, NotFound, PermissionDenied, ValidationFailed
from services.permissions import ADMIN, ALL_ROLES, CurrentUser, require_role

_COMMON = {"password123", "1234567890", "qwertyuiop", "administrator", "welcome123", "letmein123"}
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ---------- password hashing (Argon2id, with a stdlib scrypt fallback when argon2-cffi is absent) ----------
def hash_password(password: str) -> str:
    try:
        from argon2 import PasswordHasher

        return PasswordHasher().hash(password)
    except ImportError:
        salt = os.urandom(16)
        digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
        return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        if stored.startswith("scrypt$"):
            _, salt, digest = stored.split("$")
            check = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=32)
            return hmac.compare_digest(check.hex(), digest)
        from argon2 import PasswordHasher

        return PasswordHasher().verify(stored, password)
    except Exception:
        return False


def validate_password(password: str, username: str = "") -> None:
    if len(password) < settings.password_min_length:
        raise ValidationFailed(f"Password must be at least {settings.password_min_length} characters long.")
    if password.isdigit() or password.lower() in _COMMON:
        raise ValidationFailed("That password is too easy to guess.")
    if username and username.lower() in password.lower():
        raise ValidationFailed("Password must not contain the username.")


def _identity(s, user: User) -> CurrentUser:
    teacher_id = s.scalar(select(Teacher.id).where(Teacher.user_id == user.id))
    student_id = s.scalar(select(Student.id).where(Student.user_id == user.id))
    parent_id = s.scalar(select(Parent.id).where(Parent.user_id == user.id))
    return CurrentUser(user.id, user.username, user.full_name, user.role, teacher_id, student_id, parent_id)


# ---------- login / logout ----------
def authenticate(identifier: str, password: str) -> CurrentUser:
    """Username or e-mail + password. Locks the account after repeated failures. Generic error messages."""
    generic = "Invalid username or password, or the account is locked/inactive."
    identifier = (identifier or "").strip().lower()
    if not identifier or not password:
        raise AuthError(generic)
    outcome: CurrentUser | None = None
    with session_scope() as s:
        user = s.scalar(select(User).where(or_(func.lower(User.username) == identifier,
                                               func.lower(User.email) == identifier)))
        now = utcnow()
        if user is None:
            verify_password(password, hash_password("dummy-password-for-timing"))  # equalise timing
        elif user.locked_until and user.locked_until > now:
            audit.log(s, None, "login_blocked_locked", "User", user.id)
        elif not user.is_active:
            audit.log(s, None, "login_blocked_inactive", "User", user.id)
        elif verify_password(password, user.password_hash):
            user.failed_attempts, user.locked_until, user.last_login = 0, None, now
            audit.log(s, user, "login", "User", user.id)
            outcome = _identity(s, user)
        else:
            user.failed_attempts += 1
            if user.failed_attempts >= settings.max_failed_logins:
                user.locked_until = now + dt.timedelta(minutes=settings.lockout_minutes)
                user.failed_attempts = 0
                audit.log(s, None, "account_locked", "User", user.id)
            audit.log(s, None, "login_failed", "User", user.id)
    if outcome is None:
        raise AuthError(generic)
    return outcome


def logout(actor: CurrentUser) -> None:
    with session_scope() as s:
        audit.log(s, actor, "logout", "User", actor.id)


def refresh_identity(user_id: int) -> CurrentUser | None:
    """Re-read role/active state; the UI calls this so deactivation takes effect immediately."""
    with session_scope() as s:
        user = s.get(User, user_id)
        return _identity(s, user) if user and user.is_active else None


# ---------- bootstrap and user administration ----------
def has_users() -> bool:
    with session_scope() as s:
        return s.scalar(select(func.count(User.id))) > 0


def bootstrap_admin(username: str, email: str | None, full_name: str, password: str) -> int:
    """Creates the first administrator. Refuses to run once any user exists."""
    validate_password(password, username)
    with session_scope() as s:
        if s.scalar(select(func.count(User.id))) > 0:
            raise PermissionDenied("Setup is already complete.")
        user = User(username=username.strip(), email=(email or "").strip() or None, full_name=full_name.strip(),
                    password_hash=hash_password(password), role=ADMIN)
        s.add(user)
        s.flush()
        audit.log(s, user, "bootstrap_admin", "User", user.id)
        return user.id


def create_user(actor: CurrentUser, username: str, full_name: str, role: str, password: str,
                email: str | None = None, employee_no: str | None = None, phone: str = "") -> int:
    require_role(actor, ADMIN)
    username = username.strip()
    if role not in ALL_ROLES:
        raise ValidationFailed("Unknown role.")
    if len(username) < 3 or not full_name.strip():
        raise ValidationFailed("Username (3+ characters) and full name are required.")
    if email and not _EMAIL.match(email):
        raise ValidationFailed("That e-mail address is not valid.")
    validate_password(password, username)
    if role == "teacher" and not (employee_no or "").strip():
        raise ValidationFailed("An employee number is required for teachers.")
    try:
        with session_scope() as s:
            user = User(username=username, email=(email or "").strip() or None, full_name=full_name.strip(),
                        password_hash=hash_password(password), role=role)
            s.add(user)
            s.flush()
            if role == "teacher":
                s.add(Teacher(user_id=user.id, employee_no=employee_no.strip(), phone=phone))
            elif role == "parent":
                s.add(Parent(user_id=user.id, phone=phone))
            audit.log(s, actor, "user_create", "User", user.id, None, {"username": username, "role": role})
            return user.id
    except IntegrityError:
        raise ValidationFailed("That username, e-mail or employee number is already in use.")


def set_user_active(actor: CurrentUser, user_id: int, active: bool) -> None:
    require_role(actor, ADMIN)
    if user_id == actor.id and not active:
        raise ValidationFailed("You cannot deactivate your own account.")
    with session_scope() as s:
        user = s.get(User, user_id)
        if user is None:
            raise NotFound("User not found.")
        previous = user.is_active
        user.is_active = active
        audit.log(s, actor, "user_activate" if active else "user_deactivate", "User", user_id,
                  {"is_active": previous}, {"is_active": active})


def change_role(actor: CurrentUser, user_id: int, new_role: str) -> None:
    require_role(actor, ADMIN)
    if new_role not in ALL_ROLES:
        raise ValidationFailed("Unknown role.")
    if user_id == actor.id:
        raise ValidationFailed("You cannot change your own role.")
    with session_scope() as s:
        user = s.get(User, user_id)
        if user is None:
            raise NotFound("User not found.")
        if user.role in ("teacher", "parent") or new_role in ("teacher", "parent"):
            raise ValidationFailed("Changing to/from teacher or parent needs a new account (profile data differs).")
        audit.log(s, actor, "user_role_change", "User", user_id, {"role": user.role}, {"role": new_role})
        user.role = new_role


def change_password(actor: CurrentUser, old_password: str, new_password: str) -> None:
    with session_scope() as s:
        user = s.get(User, actor.id)
        if user is None or not verify_password(old_password, user.password_hash):
            raise AuthError("Current password is incorrect.")
        validate_password(new_password, user.username)
        user.password_hash = hash_password(new_password)
        audit.log(s, actor, "password_change", "User", user.id)


def issue_reset_token(actor: CurrentUser, user_id: int) -> str:
    """Administrator generates a one-time token and hands it to the user (no e-mail server is assumed)."""
    require_role(actor, ADMIN)
    token = secrets.token_urlsafe(24)
    with session_scope() as s:
        user = s.get(User, user_id)
        if user is None:
            raise NotFound("User not found.")
        user.reset_token_hash = hashlib.sha256(token.encode()).hexdigest()
        user.reset_expires = utcnow() + dt.timedelta(minutes=settings.reset_token_minutes)
        audit.log(s, actor, "reset_token_issued", "User", user_id)
    return token


def reset_password_with_token(token: str, new_password: str) -> None:
    digest = hashlib.sha256((token or "").strip().encode()).hexdigest()
    with session_scope() as s:
        user = s.scalar(select(User).where(User.reset_token_hash == digest))
        if user is None or user.reset_expires is None or user.reset_expires < utcnow():
            raise AuthError("This reset code is invalid or has expired.")
        validate_password(new_password, user.username)
        user.password_hash = hash_password(new_password)
        user.reset_token_hash, user.reset_expires = None, None
        user.failed_attempts, user.locked_until = 0, None
        audit.log(s, user, "password_reset", "User", user.id)


def link_student_account(actor: CurrentUser, student_id: int, user_id: int) -> None:
    require_role(actor, ADMIN)
    with session_scope() as s:
        user, student = s.get(User, user_id), s.get(Student, student_id)
        if user is None or student is None or user.role != "student":
            raise ValidationFailed("Choose an existing user with the 'student' role and a valid student.")
        try:
            student.user_id = user_id
            s.flush()
        except IntegrityError:
            raise ValidationFailed("That account is already linked to another student.")
        audit.log(s, actor, "student_account_link", "Student", student_id, None, {"user_id": user_id})


def link_parent(actor: CurrentUser, parent_user_id: int, student_id: int) -> None:
    """Administrator-verified parent/child link; unverified links grant nothing."""
    require_role(actor, ADMIN)
    with session_scope() as s:
        parent = s.scalar(select(Parent).where(Parent.user_id == parent_user_id))
        if parent is None or s.get(Student, student_id) is None:
            raise ValidationFailed("Choose an existing parent account and student.")
        if s.scalar(select(ParentStudent.id).where(ParentStudent.parent_id == parent.id,
                                                   ParentStudent.student_id == student_id)):
            raise ValidationFailed("That parent is already linked to this student.")
        s.add(ParentStudent(parent_id=parent.id, student_id=student_id, verified=True, verified_by=actor.id))
        audit.log(s, actor, "parent_link", "Student", student_id, None, {"parent_user_id": parent_user_id})


def list_users(actor: CurrentUser, role: str | None = None) -> list[dict]:
    require_role(actor, ADMIN)
    with session_scope() as s:
        stmt = select(User).order_by(User.role, User.username)
        if role:
            stmt = stmt.where(User.role == role)
        return [{"id": u.id, "username": u.username, "full_name": u.full_name, "email": u.email, "role": u.role,
                 "is_active": u.is_active, "last_login": u.last_login} for u in s.scalars(stmt)]
