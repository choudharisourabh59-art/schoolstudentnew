import datetime as dt

import pandas as pd
import streamlit as st

from components import charts
from components.dashboard_cards import card_title, empty_state
from components.forms import run_action, student_picker, year_select
from components.sidebar import require_login
from components.tables import export_buttons
from config.settings import today
from services import academic_service as ac, attendance_service as att, report_service as rs

user = require_login("Attendance")
st.title("Attendance")
ref = ac.reference_data()
LABEL = {"P": "Present", "A": "Absent", "L": "Late", "E": "Excused"}

if user.role in ("student", "parent"):
    pick = student_picker(user, "att_student")
    if pick:
        summary = att.student_summary(user, pick["id"])
        c = st.columns(5)
        c[0].metric("Attendance", f"{float(summary['percentage']):.1f}%" if summary["percentage"] is not None else "No data")
        for col, key in zip(c[1:], "PALE"):
            col.metric(LABEL[key], summary[key])
        left, right = st.columns([1, 2])
        fig = charts.attendance_donut(summary)
        if fig:
            left.plotly_chart(fig, use_container_width=True)
        history = att.student_history(user, pick["id"])
        if history:
            df = pd.DataFrame(history)
            df["status"] = df["status"].map(LABEL)
            right.dataframe(df, use_container_width=True, hide_index=True)
        else:
            right.info("No attendance has been recorded yet.")
    st.stop()

if not ref["years"]:
    empty_state("Create an academic year first (Settings > Academic structure).")
    st.stop()
year_id = year_select(ref, "att_year")
year = next(y for y in ref["years"] if y["id"] == year_id)
pairs = ac.my_assigned_classes(user, year_id)
if not pairs:
    empty_state("No classes are available for you in this year.")
    st.stop()
labels = {f"{p['class']} - {p['section']}": p for p in pairs}
can_mark = user.role in ("admin", "teacher")
tab_names = (["Mark attendance"] if can_mark else []) + ["Class report", "Trends"]
tabs = st.tabs(tab_names)
i = 0
if can_mark:
    with tabs[0]:
        sel = labels[st.selectbox("Class and section", list(labels), key="att_cls")]
        day = st.date_input("Date", min(today(), year["end"]), min_value=year["start"],
                            max_value=min(today(), year["end"]), key="att_day")
        roster = att.roster(user, year_id, sel["class_id"], sel["section_id"], day)
        if not roster:
            empty_state("No students are enrolled in this class and section.")
        else:
            saved = sum(1 for r in roster if r["status"])
            st.caption(f"{saved} of {len(roster)} already saved for {day:%d %b %Y}. Unsaved students default to Present - "
                       "change them, then press Save. Future dates cannot be marked.")
            df = pd.DataFrame([{"enrollment_id": r["enrollment_id"], "Roll": r["roll_no"], "Student ID": r["student_code"],
                                "Name": r["name"], "Status": r["status"] or "P"} for r in roster]).set_index("enrollment_id")
            edited = st.data_editor(df, hide_index=True, use_container_width=True, disabled=["Roll", "Student ID", "Name"],
                                    column_config={"Status": st.column_config.SelectboxColumn(
                                        "Status", options=["P", "A", "L", "E"], required=True,
                                        help="P=Present, A=Absent, L=Late, E=Excused")}, key=f"att_edit_{sel['class_id']}_{sel['section_id']}_{day}")
            if st.button("Save attendance", type="primary", key="att_save"):
                ok, res = run_action(att.mark_attendance, user, year_id, sel["class_id"], sel["section_id"], day,
                                     {int(k): v for k, v in edited["Status"].items()})
                if ok:
                    st.success(f"Saved: {res['created']} new, {res['updated']} changed, {res['unchanged']} unchanged.")
    i = 1
with tabs[i]:
    sel = labels[st.selectbox("Class and section", list(labels), key="rep_cls")]
    c = st.columns(2)
    since = c[0].date_input("From", max(year["start"], today() - dt.timedelta(days=30)), key="rep_from")
    until = c[1].date_input("To", today(), key="rep_to")
    ok, df = run_action(rs.class_attendance_report, user, year_id, sel["class_id"], sel["section_id"], since, until)
    if ok and df is not None and not df.empty:
        st.dataframe(df, use_container_width=True, hide_index=True)
        export_buttons(df, f"class_attendance_{sel['class']}_{sel['section']}", "Class attendance report",
                       f"{sel['class']} {sel['section']} | {since} to {until}")
    elif ok:
        empty_state("No students or attendance for this selection.")
with tabs[i + 1]:
    scope = st.selectbox("Scope", ["Whole school"] + list(labels), key="tr_scope")
    cid = sid = None if scope == "Whole school" else labels[scope]["class_id"]
    sid = None if scope == "Whole school" else labels[scope]["section_id"]
    trend = pd.DataFrame(att.monthly_trend(user, year_id, cid, sid))
    fig = charts.monthly_attendance(trend) if not trend.empty else None
    st.plotly_chart(fig, use_container_width=True) if fig else empty_state("No attendance recorded yet.")
    card_title("Class attendance percentages", "Excused days are not counted against students", "🏫")
    cp = att.class_percentages(user, year_id, cid)
    if cp:
        out = pd.DataFrame(cp)
        out["percentage"] = out["percentage"].map(lambda v: None if v is None else round(float(v), 2))
        st.dataframe(out.rename(columns={"P": "Present", "A": "Absent", "L": "Late", "E": "Excused"}),
                     use_container_width=True, hide_index=True)
    card_title("Most absences", "Students with the highest absence counts", "⚠️")
    top = att.top_absentees(user, year_id, cid, sid)
    st.dataframe(pd.DataFrame(top), use_container_width=True, hide_index=True) if top else empty_state("No absences recorded.")
