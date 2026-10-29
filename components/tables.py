from __future__ import annotations

import math
from typing import Callable

import pandas as pd
import streamlit as st

from components.dashboard_cards import empty_state
from services import report_service


def paginated_table(fetch: Callable[[int, int], tuple[list[dict], int]], key: str, page_size: int = 25,
                    empty: str = "No records match your filters.", columns: dict | None = None) -> list[dict]:
    """Server-side pagination: only one page is ever loaded from the database."""
    page_key = f"{key}_page"
    page = int(st.session_state.get(page_key, 1))
    rows, total = fetch(page, page_size)
    pages = max(1, math.ceil(total / page_size))
    if page > pages:
        page = st.session_state[page_key] = pages
        rows, total = fetch(page, page_size)
    if not rows:
        empty_state(empty)
        return []
    df = pd.DataFrame(rows)
    if columns:
        df = df[[c for c in columns if c in df.columns]].rename(columns=columns)
    st.dataframe(df, use_container_width=True, hide_index=True)
    left, mid, right = st.columns([1, 2, 1])
    left.button("◀ Previous", key=f"{key}_prev", disabled=page <= 1,
                on_click=lambda: st.session_state.__setitem__(page_key, page - 1))
    mid.caption(f"Page {page} of {pages} · {total:,} records")
    right.button("Next ▶", key=f"{key}_next", disabled=page >= pages,
                 on_click=lambda: st.session_state.__setitem__(page_key, page + 1))
    return rows


def export_buttons(df: pd.DataFrame, filename: str, title: str, subtitle: str = "") -> None:
    """CSV / Excel / PDF downloads of exactly what is on screen (the active filters)."""
    if df.empty:
        return
    c1, c2, c3 = st.columns(3)
    c1.download_button("⬇ CSV", report_service.to_csv_bytes(df), f"{filename}.csv", "text/csv", key=f"csv_{filename}")
    c2.download_button("⬇ Excel", report_service.to_excel_bytes(df), f"{filename}.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key=f"xlsx_{filename}")
    try:
        pdf = report_service.to_pdf_bytes(title, df, subtitle)
        c3.download_button("⬇ PDF", pdf, f"{filename}.pdf", "application/pdf", key=f"pdf_{filename}")
    except Exception:
        c3.caption("PDF unavailable")
