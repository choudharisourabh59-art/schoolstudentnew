"""Login guard + the branded sidebar used by every page."""
from __future__ import annotations

import logging
import time

import streamlit as st
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from components.theme import inject_css
from config.settings import settings
from database.migrate import run_migrations
from services import auth_service
from services.permissions import CurrentUser

# (label, page path, material icon, roles allowed)
MENU = [
    ("School Overview", "pages/1_Admin_Dashboard.py", ":material/dashboard:", ("admin", "principal")),
    ("Students", "pages/2_Students.py", ":material/groups:", ("admin", "principal", "teacher")),
    ("Teachers", "pages/3_Teachers.py", ":material/school:", ("admin", "principal", "teacher")),
    ("Attendance", "pages/4_Attendance.py", ":material/event_available:", ("admin", "principal", "teacher", "student", "parent")),
    ("Examinations", "pages/5_Examinations.py", ":material/edit_note:", ("admin", "principal", "teacher")),
    ("Results", "pages/6_Results.py", ":material/fact_check:", ("admin", "principal", "teacher", "student", "parent")),
    ("Student Portfolio", "pages/7_Student_Portfolio.py", ":material/badge:", ("admin", "principal", "teacher", "student", "parent")),
    ("Documents", "pages/8_Documents.py", ":material/folder:", ("admin", "principal", "teacher", "student", "parent")),
    ("Reports", "pages/9_Reports.py", ":material/bar_chart:", ("admin", "principal", "teacher", "student", "parent")),
    ("Promotion", "pages/11_Promotion.py", ":material/upgrade:", ("admin",)),
    ("Settings", "pages/10_Settings.py", ":material/settings:", ("admin", "principal", "teacher", "student", "parent")),
]
HOME = {"admin": "pages/1_Admin_Dashboard.py", "principal": "pages/1_Admin_Dashboard.py",
        "teacher": "pages/4_Attendance.py", "student": "pages/7_Student_Portfolio.py",
        "parent": "pages/7_Student_Portfolio.py"}


@st.cache_resource(show_spinner="Connecting to the database...")
def _init_database() -> bool:
    run_migrations()
    return True


def init_database() -> bool:
    """Connects and migrates once per server process; shows a readable message (and stops) if that fails."""
    try:
        return _init_database()
    except OperationalError:
        logging.getLogger("sis").exception("Database connection failed")  # full reason: Manage app > Logs
        url = make_url(settings.database_url)
        st.error("The application could not connect to its database.")
        if url.get_backend_name() == "sqlite":
            st.warning("No `DATABASE_URL` secret is set, so it tried a local SQLite file, which does not work on Streamlit "
                       "Cloud. Add `DATABASE_URL` under App settings > Secrets (see `.streamlit/secrets.toml.example`).")
        else:
            st.markdown(f"Tried: `{url.get_backend_name()}` at host `{url.host}`, port `{url.port or 'default'}`, "
                        f"database `{url.database}`.  \nCheck, in this order:\n"
                        "1. The password in the URL is correct. Special characters must be URL-encoded (`@` becomes `%40`, `#` becomes `%23`).\n"
                        "2. The URL ends with `?sslmode=require`.\n"
                        "3. Supabase: use the **Session pooler** connection string (the direct one is IPv6-only and fails on Streamlit Cloud). Neon: use the pooled string.\n"
                        "4. The database project is running (free projects pause after inactivity).\n"
                        "The exact reason is in **Manage app > Logs**.")
        st.stop()


def current_user() -> CurrentUser | None:
    return st.session_state.get("user")


def sign_in(user: CurrentUser) -> None:
    st.session_state["user"] = user
    st.session_state["last_seen"] = time.time()


def sign_out() -> None:
    user = current_user()
    if user:
        try:
            auth_service.logout(user)
        except Exception:  # noqa: BLE001
            pass
    st.session_state.clear()


def render_sidebar(user: CurrentUser) -> None:
    with st.sidebar:
        st.markdown(f'<div class="brand"><span class="logo">🎓</span>{settings.app_name}</div>', unsafe_allow_html=True)
        for label, path, icon, roles in MENU:
            if user.role in roles:
                st.page_link(path, label=label, icon=icon)
        st.divider()
        st.markdown(f'<div class="user-chip"><b>{user.full_name}</b>{user.role.title()}</div>', unsafe_allow_html=True)
        if st.button("Sign out", use_container_width=True, key="signout"):
            sign_out()
            st.switch_page("app.py")


def require_login(title: str, allowed: tuple[str, ...] | None = None) -> CurrentUser:
    """Call first on every page. Stops the page unless a valid session with an allowed role exists."""
    st.set_page_config(page_title=f"{title} - {settings.app_name}", page_icon="🎓", layout="wide")
    init_database()
    inject_css()
    user = current_user()
    if user is None:
        st.warning("Please sign in to continue.")
        st.page_link("app.py", label="Go to sign in", icon=":material/login:")
        st.stop()
    if time.time() - st.session_state.get("last_seen", 0) > settings.session_idle_minutes * 60:
        st.session_state.clear()
        st.warning("You were signed out after a period of inactivity.")
        st.page_link("app.py", label="Sign in again", icon=":material/login:")
        st.stop()
    fresh = auth_service.refresh_identity(user.id)  # picks up deactivation / role changes immediately
    if fresh is None:
        st.session_state.clear()
        st.error("This account is no longer active.")
        st.page_link("app.py", label="Back to sign in")
        st.stop()
    st.session_state["user"], st.session_state["last_seen"] = fresh, time.time()
    render_sidebar(fresh)
    if settings.is_demo:
        st.markdown('<div class="demo-banner">⚠️ <b>DEMO DATA</b> — this database contains sample records only, '
                    'not real students.</div>', unsafe_allow_html=True)
    if allowed and fresh.role not in allowed:
        st.error("You do not have access to this page.")
        st.stop()
    return fresh
