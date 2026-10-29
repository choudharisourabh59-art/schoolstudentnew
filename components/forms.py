"""Shared selectors and a safe wrapper that turns service errors into friendly messages."""
from __future__ import annotations

import logging
from typing import Any, Callable

import streamlit as st

from services.errors import SISError

log = logging.getLogger("sis")


def run_action(fn: Callable[..., Any], *args, success: str | None = None, **kwargs):
    """Returns (ok, result). Shows a spinner, then a success or error message. Never shows tracebacks."""
    try:
        with st.spinner("Working..."):
            result = fn(*args, **kwargs)
        if success:
            st.success(success)
        return True, result
    except SISError as exc:
        st.error(str(exc))
    except PermissionError as exc:
        st.error(str(exc))
    except Exception:  # noqa: BLE001 - unexpected: log server-side, keep the UI message generic
        log.exception("Unexpected error")
        st.error("Something went wrong and nothing was saved. Please try again or contact an administrator.")
    return False, None


def _select(label: str, options: list[dict], key: str, name: str = "name", allow_all: bool = False,
            default_id: int | None = None):
    labels = (["All"] if allow_all else []) + [o[name] for o in options]
    if not labels:
        st.selectbox(label, ["(none available)"], key=key, disabled=True)
        return None
    index = 0
    if default_id is not None:
        for i, o in enumerate(options):
            if o["id"] == default_id:
                index = i + (1 if allow_all else 0)
    choice = st.selectbox(label, labels, index=index, key=key)
    if choice == "All":
        return None
    return next(o["id"] for o in options if o[name] == choice)


def year_select(ref: dict, key: str = "year", label: str = "Academic year") -> int | None:
    current = next((y["id"] for y in ref["years"] if y["is_current"]), None)
    return _select(label, ref["years"], key, default_id=current)


def class_select(ref: dict, key: str = "class", allow_all: bool = True, label: str = "Class"):
    return _select(label, ref["classes"], key, allow_all=allow_all)


def section_select(ref: dict, key: str = "section", allow_all: bool = True, label: str = "Section"):
    return _select(label, ref["sections"], key, allow_all=allow_all)


def subject_select(ref: dict, key: str = "subject", allow_all: bool = True, label: str = "Subject"):
    return _select(label, ref["subjects"], key, allow_all=allow_all)


def student_picker(user, key: str, label: str = "Student") -> dict | None:
    """Scoped student chooser. Staff search by name/ID (never lists everyone); students/parents see their own."""
    from services import student_service

    if user.role in ("student", "parent"):
        rows, _ = student_service.search_students(user, page_size=50)
    else:
        query = st.text_input("Search by name, student ID or guardian", key=f"{key}_q")
        rows, total = student_service.search_students(user, query=query or None, page_size=40)
        if total > len(rows):
            st.caption(f"Showing the first {len(rows)} of {total:,} matches - type more to narrow the list.")
    if not rows:
        st.info("No students found for your account.")
        return None
    options = {f"{r['full_name']} - {r['student_code']} ({r['class_name'] or 'not enrolled'}"
               f"{'-' + r['section_name'] if r['section_name'] else ''})": r for r in rows}
    names = list(options)
    preferred = st.session_state.get("portfolio_student_id")
    index = next((i for i, r in enumerate(options.values()) if r["id"] == preferred), 0)
    return options[st.selectbox(label, names, index=index, key=f"{key}_sel")]
