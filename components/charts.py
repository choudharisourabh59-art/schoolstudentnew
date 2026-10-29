"""Plotly figures. Each returns None for empty data so the page can show an empty state instead of a blank chart."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from components.theme import BLUE, GREEN, LAVENDER, PALETTE, PEACH, PINK, SALMON


def _style(fig: go.Figure, title: str, height: int = 340, yaxis: str | None = None, xaxis: str | None = None) -> go.Figure:
    fig.update_layout(
        title=dict(text=title, x=0.01, font=dict(size=15)), height=height, margin=dict(l=10, r=10, t=50, b=10),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(family="Inter, sans-serif", size=12),
        legend=dict(orientation="h", y=-0.2), hoverlabel=dict(bgcolor="white"))
    fig.update_xaxes(showgrid=False, title=xaxis, linecolor="#e5e7eb")
    fig.update_yaxes(gridcolor="#eef1f4", title=yaxis, zeroline=False)
    return fig


def performance_line(df: pd.DataFrame) -> go.Figure | None:
    """columns: label, percentage (one student's result per examination, in time order)."""
    if df.empty:
        return None
    fig = go.Figure(go.Scatter(x=df["label"], y=df["percentage"], mode="lines+markers", line=dict(color=GREEN, width=3),
                               marker=dict(size=9, color=SALMON), hovertemplate="%{x}<br>%{y:.2f}%<extra></extra>"))
    fig.update_yaxes(range=[0, 100])
    return _style(fig, "Performance over time", yaxis="Percentage", xaxis="Examination")


def subject_bar(df: pd.DataFrame, title: str = "Subject-wise marks") -> go.Figure | None:
    """columns: subject, value (percentage)."""
    if df.empty:
        return None
    fig = go.Figure(go.Bar(x=df["subject"], y=df["value"], marker_color=GREEN,
                           hovertemplate="%{x}<br>%{y:.2f}%<extra></extra>"))
    fig.update_yaxes(range=[0, 100])
    return _style(fig, title, yaxis="Percentage")


def attendance_donut(counts: dict) -> go.Figure | None:
    labels = {"P": "Present", "A": "Absent", "L": "Late", "E": "Excused"}
    items = [(labels[k], counts.get(k, 0)) for k in "PALE" if counts.get(k, 0)]
    if not items:
        return None
    fig = go.Figure(go.Pie(labels=[i[0] for i in items], values=[i[1] for i in items], hole=0.62,
                           marker=dict(colors=[GREEN, SALMON, PEACH, LAVENDER][:len(items)]),
                           sort=False, textinfo="percent"))
    return _style(fig, "Attendance breakdown", 300)


def status_donut(counts: dict, title: str) -> go.Figure | None:
    items = [(k.title(), v) for k, v in counts.items() if v]
    if not items:
        return None
    fig = go.Figure(go.Pie(labels=[i[0] for i in items], values=[i[1] for i in items], hole=0.62,
                           marker=dict(colors=PALETTE[:len(items)]), sort=False))
    return _style(fig, title, 300)


def class_grouped_bar(df: pd.DataFrame) -> go.Figure | None:
    """columns: class, term, average."""
    if df.empty:
        return None
    fig = go.Figure()
    for i, (term, part) in enumerate(df.groupby("term")):
        fig.add_bar(name=str(term), x=part["class"], y=part["average"], marker_color=PALETTE[i % len(PALETTE)],
                    hovertemplate="%{x}<br>%{y:.1f}%<extra>" + str(term) + "</extra>")
    fig.update_layout(barmode="group")
    fig.update_yaxes(range=[0, 100])
    return _style(fig, "Class-wise performance (average %)", yaxis="Average %", xaxis="Class")


def grade_distribution(df: pd.DataFrame) -> go.Figure | None:
    if df.empty or df["students"].sum() == 0:
        return None
    fig = go.Figure(go.Bar(x=df["grade"], y=df["students"], marker_color=SALMON,
                           hovertemplate="Grade %{x}: %{y} students<extra></extra>"))
    return _style(fig, "Grade distribution", yaxis="Students", xaxis="Grade")


def monthly_attendance(df: pd.DataFrame, title: str = "Monthly attendance trend") -> go.Figure | None:
    """columns: month, percentage, absent, late."""
    if df.empty:
        return None
    fig = go.Figure()
    fig.add_scatter(x=df["month"], y=df["percentage"], name="Attendance %", mode="lines+markers",
                    line=dict(color=GREEN, width=3), hovertemplate="%{x}<br>%{y:.1f}%<extra></extra>")
    if "absent" in df:
        fig.add_bar(x=df["month"], y=df["absent"], name="Absences", marker_color=SALMON, yaxis="y2", opacity=0.55)
    if "late" in df:
        fig.add_bar(x=df["month"], y=df["late"], name="Late arrivals", marker_color=PEACH, yaxis="y2", opacity=0.55)
    fig.update_layout(yaxis2=dict(overlaying="y", side="right", showgrid=False, title="Count"), barmode="group")
    fig.update_yaxes(range=[0, 100], selector=dict(title=None))
    return _style(fig, title, yaxis="Attendance %")


def enrollment_bar(df: pd.DataFrame) -> go.Figure | None:
    if df.empty:
        return None
    fig = go.Figure(go.Bar(x=df["class"], y=df["students"], marker_color=BLUE))
    return _style(fig, "Students per class", yaxis="Students")


def progression_line(df: pd.DataFrame) -> go.Figure | None:
    """columns: year, level, class - the class level reached in each academic year."""
    if df.empty:
        return None
    fig = go.Figure(go.Scatter(x=df["year"], y=df["level"], mode="lines+markers", text=df["class"],
                               line=dict(color=LAVENDER, width=3), marker=dict(size=10, color=GREEN),
                               hovertemplate="%{x}<br>%{text}<extra></extra>"))
    fig.update_yaxes(dtick=1)
    return _style(fig, "Grade progression", yaxis="Class level")
