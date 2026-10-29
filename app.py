"""School Information System - entry point.  Run with:  streamlit run app.py"""
import streamlit as st

from components.sidebar import HOME, current_user, init_database, sign_in
from components.theme import inject_css
from config.settings import settings
from services import auth_service
from services.errors import SISError

st.set_page_config(page_title=settings.app_name, page_icon="🎓", layout="wide", initial_sidebar_state="collapsed")
init_database()
inject_css(hide_sidebar=True)

LOGIN_CARD = f"""<div style="text-align:center;margin:3rem 0 1rem">
<div class="brand" style="justify-content:center;font-size:2.1rem"><span class="logo">🎓</span>{settings.app_name}</div>
<div style="color:#6b7280">School Information System</div></div>"""


def first_run_setup() -> None:
    st.markdown(LOGIN_CARD, unsafe_allow_html=True)
    _, mid, _ = st.columns([1, 1.2, 1])
    with mid:
        st.info("First-time setup: create the administrator account. This screen disappears once an account exists.")
        with st.form("setup"):
            full_name = st.text_input("Full name")
            username = st.text_input("Username")
            email = st.text_input("E-mail (optional)")
            pw = st.text_input(f"Password (min {settings.password_min_length} characters)", type="password")
            pw2 = st.text_input("Repeat password", type="password")
            go = st.form_submit_button("Create administrator", use_container_width=True)
        if go:
            if pw != pw2:
                st.error("The passwords do not match.")
            else:
                try:
                    auth_service.bootstrap_admin(username, email, full_name, pw)
                    st.success("Administrator created. Reloading...")
                    st.rerun()
                except SISError as exc:
                    st.error(str(exc))


def login_view() -> None:
    st.markdown(LOGIN_CARD, unsafe_allow_html=True)
    _, mid, _ = st.columns([1, 1.2, 1])
    with mid:
        tab_in, tab_reset = st.tabs(["Sign in", "Reset password"])
        with tab_in:
            with st.form("login"):
                ident = st.text_input("Username or e-mail")
                pw = st.text_input("Password", type="password")
                go = st.form_submit_button("Sign in", use_container_width=True)
            if go:
                try:
                    sign_in(auth_service.authenticate(ident, pw))
                    st.rerun()
                except SISError as exc:
                    st.error(str(exc))
        with tab_reset:
            st.caption("Ask your administrator for a one-time reset code, then enter it here.")
            with st.form("reset"):
                token = st.text_input("Reset code")
                new = st.text_input("New password", type="password")
                go = st.form_submit_button("Set new password", use_container_width=True)
            if go:
                try:
                    auth_service.reset_password_with_token(token, new)
                    st.success("Password updated. You can sign in now.")
                except SISError as exc:
                    st.error(str(exc))


def main() -> None:
    if not auth_service.has_users():
        if settings.bootstrap_username and settings.bootstrap_password:  # optional unattended setup
            try:
                auth_service.bootstrap_admin(settings.bootstrap_username, settings.bootstrap_email or None,
                                             "Administrator", settings.bootstrap_password)
                st.rerun()
            except SISError as exc:
                st.error(f"Automatic setup failed: {exc}")
                return
        first_run_setup()
        return
    user = current_user()
    if user:
        st.switch_page(HOME[user.role])
    login_view()


main()
