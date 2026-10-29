"""HTML cards (KPI with ring, mini stat, progress list, titles). All dynamic text is HTML-escaped."""
from html import escape

import streamlit as st

from components.theme import GREEN


def _ring(pct: float | None, color: str) -> str:
    if pct is None:
        return ""
    p = max(0.0, min(100.0, pct))
    return f'<div class="ring" style="--p:{p:.1f};--c:{color}"><span>{p:.1f}%</span></div>'


def kpi_card(title: str, value: str, total: str = "", icon: str = "📊", tint: str = "green",
             ring: float | None = None, ring_color: str = GREEN) -> None:
    tail = f" <small>/ {escape(total)}</small>" if total else ""
    st.markdown(
        f'<div class="card t-{escape(tint)}"><div class="kpi"><div><div class="icon-tile">{icon}</div>'
        f'<div class="label">{escape(title)}</div><div class="value">{escape(value)}{tail}</div></div>'
        f'{_ring(ring, ring_color)}</div></div>', unsafe_allow_html=True)


def mini_card(title: str, value: str, icon: str = "👥", tint: str = "purple") -> None:
    st.markdown(
        f'<div class="card t-{escape(tint)}"><div class="mini"><div class="icon-tile" style="margin:0">{icon}</div>'
        f'<div><div class="label">{escape(title)}</div><div class="value">{escape(value)}</div></div></div></div>',
        unsafe_allow_html=True)


def card_title(title: str, subtitle: str = "", icon: str = "📈") -> None:
    st.markdown(f'<div class="card-title"><div class="icon-tile" style="margin:0;width:34px;height:34px">{icon}</div>'
                f'<div><div class="t">{escape(title)}</div><div class="s">{escape(subtitle)}</div></div></div>',
                unsafe_allow_html=True)


def progress_list(items: list[tuple[str, str, float, str]], empty: str = "Nothing to show yet.") -> None:
    """items: (label, right-hand text, percent 0-100, colour)."""
    if not items:
        empty_state(empty)
        return
    html = "".join(
        f'<div class="prow"><div class="top"><span>{escape(label)}</span><span>{escape(right)}</span></div>'
        f'<div class="bar"><i style="width:{max(0.0, min(100.0, pct)):.1f}%;background:{escape(color)}"></i></div></div>'
        for label, right, pct, color in items)
    st.markdown(f'<div class="card">{html}</div>', unsafe_allow_html=True)


def empty_state(message: str, icon: str = "🗂️") -> None:
    st.markdown(f'<div class="empty"><div class="big">{icon}</div>{escape(message)}</div>', unsafe_allow_html=True)
