import datetime as dt

import streamlit as st

from components.dashboard_cards import empty_state
from components.forms import class_select, run_action, section_select, student_picker, year_select
from components.sidebar import require_login
from components.tables import export_buttons
from config.settings import today
from services import academic_service as ac, grade_service as gs, report_service as rs

user = require_login("Reports")
st.title("Reports")
ref = ac.reference_data()
STUDENT_REPORTS = ["Student report card", "Individual attendance report", "Student academic history"]
STAFF_REPORTS = ["Class attendance report", "Subject result report", "Class performance report", "Enrollment report"]
kind = st.selectbox("Report", STUDENT_REPORTS + (STAFF_REPORTS if user.role in ("admin", "principal", "teacher") else []))
st.caption("Exports use exactly the filters chosen here, and only records you are allowed to see.")
df, title, sub = None, kind, ""

if kind in STUDENT_REPORTS:
    pick = student_picker(user, "rp_student")
    if pick:
        years = ["All years"] + [y["name"] for y in ref["years"]]
        if kind == "Student report card":
            y = st.selectbox("Academic year", years, key="rp_card_year")
            ok, df = run_action(rs.report_card, user, pick["id"], None if y == "All years" else y)
            sub = f"{pick['full_name']} ({pick['student_code']}) | {y}"
        elif kind == "Individual attendance report":
            y = st.selectbox("Academic year", years, key="rp_att_year")
            yid = next((x["id"] for x in ref["years"] if x["name"] == y), None)
            ok, df = run_action(rs.student_attendance_report, user, pick["id"], yid)
            sub = f"{pick['full_name']} ({pick['student_code']}) | {y}"
        else:
            ok, df = run_action(rs.academic_history, user, pick["id"])
            sub = f"{pick['full_name']} ({pick['student_code']})"
elif kind == "Enrollment report":
    year_id = year_select(ref, "rp_year")
    ok, df = run_action(rs.enrollment_report, user, year_id)
    sub = f"Academic year {next(y['name'] for y in ref['years'] if y['id'] == year_id)}" if year_id else ""
elif kind == "Class attendance report":
    c = st.columns(4)
    with c[0]:
        year_id = year_select(ref, "rp_year")
    with c[1]:
        cid = class_select(ref, "rp_class", allow_all=False)
    with c[2]:
        sid = section_select(ref, "rp_section", allow_all=False)
    since = c[3].date_input("From", today() - dt.timedelta(days=30), key="rp_from")
    until = st.date_input("To", today(), key="rp_to")
    ok, df = run_action(rs.class_attendance_report, user, year_id, cid, sid, since, until)
    sub = f"{since} to {until}"
else:  # subject result report / class performance report
    c = st.columns(4)
    with c[0]:
        year_id = year_select(ref, "rp_year")
    exams = gs.list_examinations(year_id)
    if not exams:
        empty_state("No examinations for this year.")
    else:
        with c[1]:
            labels = {f"{e['term']} - {e['name']}": e for e in exams}
            exam = labels[st.selectbox("Examination", list(labels), key="rp_exam")]
        with c[2]:
            cid = class_select(ref, "rp_class", allow_all=False)
        with c[3]:
            sid = section_select(ref, "rp_section")
        subject = None
        if kind == "Subject result report":
            subject = st.selectbox("Subject", [s["name"] for s in ref["subjects"]], key="rp_subject")
        ok, df = run_action(rs.exam_report, user, exam["id"], cid, sid, subject)
        sub = f"{exam['term']} {exam['name']} ({exam['status']})" + (f" | {subject}" if subject else "")

if df is not None:
    if df.empty:
        empty_state("There are no records for these filters.")
    else:
        st.dataframe(df, use_container_width=True, hide_index=True)
        export_buttons(df, kind.lower().replace(" ", "_"), title, sub)
