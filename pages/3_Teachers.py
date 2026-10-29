import pandas as pd
import streamlit as st

from components.dashboard_cards import empty_state
from components.forms import class_select, run_action, section_select, subject_select, year_select
from components.sidebar import require_login
from services import academic_service as ac, auth_service

user = require_login("Teachers", ("admin", "principal", "teacher"))
st.title("Teachers")
ref = ac.reference_data()
if not ref["years"]:
    empty_state("Create an academic year first (Settings > Academic structure).")
    st.stop()
year_id = year_select(ref, "t_year")

if user.role in ("admin", "principal"):
    teachers = ac.list_teachers(user)
    st.subheader("Teaching staff")
    if teachers:
        st.dataframe(pd.DataFrame(teachers)[["employee_no", "name", "username", "phone", "is_active"]],
                     use_container_width=True, hide_index=True)
    else:
        empty_state("No teachers yet.")

st.subheader("Class assignments" if user.role != "teacher" else "My classes")
assignments = ac.list_assignments(user, year_id)
if assignments:
    st.dataframe(pd.DataFrame(assignments).drop(columns="id"), use_container_width=True, hide_index=True)
else:
    empty_state("No assignments for this year.")

if user.role == "admin":
    with st.expander("Add a teacher account"):
        with st.form("new_teacher", clear_on_submit=True):
            a, b = st.columns(2)
            full_name, username = a.text_input("Full name"), b.text_input("Username")
            emp, email = a.text_input("Employee number"), b.text_input("E-mail (optional)")
            phone, pw = a.text_input("Phone"), b.text_input("Temporary password", type="password")
            go = st.form_submit_button("Create teacher")
        if go:
            run_action(auth_service.create_user, user, username, full_name, "teacher", pw, email or None, emp, phone,
                       success="Teacher account created. Ask them to change the password after first sign-in.")
    with st.expander("Assign a teacher to a class"):
        if teachers:
            names = {f"{t['name']} ({t['employee_no']})": t["teacher_id"] for t in teachers}
            who = names[st.selectbox("Teacher", list(names), key="as_teacher")]
            c1, c2, c3 = st.columns(3)
            with c1:
                cid = class_select(ref, "as_class", allow_all=False)
            with c2:
                sid = section_select(ref, "as_section", allow_all=False)
            with c3:
                sub = subject_select(ref, "as_subject")
            lead = st.checkbox("Class teacher")
            if st.button("Assign", key="as_btn") and cid and sid:
                run_action(ac.assign_teacher, user, who, year_id, cid, sid, sub, lead, success="Assignment saved.")
        else:
            st.info("Add a teacher first.")
    if assignments:
        with st.expander("Remove an assignment"):
            labels = {f"{a['teacher']} - {a['class']}-{a['section']} - {a['subject']}": a["id"] for a in assignments}
            pick = labels[st.selectbox("Assignment", list(labels))]
            if st.button("Remove", key="rm_as") and run_action(ac.remove_assignment, user, pick, success="Removed.")[0]:
                st.rerun()
