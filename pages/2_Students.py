import datetime as dt

import streamlit as st

from components.dashboard_cards import empty_state
from components.forms import class_select, run_action, section_select, year_select
from components.sidebar import require_login
from components.tables import paginated_table
from services import academic_service, student_service as ss

user = require_login("Students", ("admin", "principal", "teacher"))
st.title("Students")
ref = academic_service.reference_data()
is_admin = user.role == "admin"
tabs = st.tabs(["Directory"] + (["Add student", "Import CSV", "Archived"] if is_admin else []))
COLS = {"student_code": "Student ID", "full_name": "Name", "class_name": "Class", "section_name": "Section",
        "roll_no": "Roll", "guardian_name": "Guardian", "guardian_phone": "Guardian phone"}

with tabs[0]:
    c = st.columns([2, 1, 1, 1])
    query = c[0].text_input("Search name, student ID or guardian", key="stu_q")
    with c[1]:
        year_id = year_select(ref, "stu_year")
    with c[2]:
        class_id = class_select(ref, "stu_class")
    with c[3]:
        section_id = section_select(ref, "stu_section")
    fkey = f"stu_{abs(hash((query, year_id, class_id, section_id)))}"  # new filters => back to page 1
    rows = paginated_table(lambda p, n: ss.search_students(user, query, year_id, class_id, section_id, page=p, page_size=n),
                           fkey, columns=COLS)
    if rows:
        labels = {f"{r['full_name']} ({r['student_code']})": r["id"] for r in rows}
        chosen = labels[st.selectbox("Open a student from this page", list(labels))]
        profile = ss.get_student(user, chosen)
        cur = profile["current"]
        st.markdown(f"**{profile['full_name']}** · {profile['student_code']} · "
                    f"{(cur['class_name'] + '-' + cur['section_name'] + ' (' + cur['year_name'] + ')') if cur else 'not enrolled this year'}")
        b1, b2 = st.columns(2)
        if b1.button("Open portfolio", key="open_pf"):
            st.session_state["portfolio_student_id"] = chosen
            st.switch_page("pages/7_Student_Portfolio.py")
        if is_admin:
            with st.expander("Edit student"):
                with st.form("edit_student"):
                    g = ["", "Male", "Female", "Other"]
                    name = st.text_input("Full name", profile["full_name"])
                    dob = st.date_input("Date of birth", profile["date_of_birth"], min_value=dt.date(1990, 1, 1))
                    adm = st.date_input("Admission date", profile["admission_date"], min_value=dt.date(1990, 1, 1))
                    gender = st.selectbox("Gender", g, index=g.index(profile["gender"] or ""))
                    guardian, gphone = st.text_input("Guardian", profile["guardian_name"]), st.text_input("Guardian phone", profile["guardian_phone"])
                    phone, email = st.text_input("Phone", profile["phone"]), st.text_input("E-mail", profile["email"] or "")
                    ecn, ecp = st.text_input("Emergency contact", profile["emergency_contact_name"]), st.text_input("Emergency phone", profile["emergency_contact_phone"])
                    address = st.text_area("Address", profile["address"])
                    save = st.form_submit_button("Save changes")
                if save:
                    ok, _ = run_action(ss.update_student, user, chosen, {
                        "full_name": name, "date_of_birth": dob, "admission_date": adm, "gender": gender,
                        "guardian_name": guardian, "guardian_phone": gphone, "phone": phone, "email": email or None,
                        "emergency_contact_name": ecn, "emergency_contact_phone": ecp, "address": address},
                        success="Student updated.")
            with st.expander("Archive student"):
                reason = st.text_input("Reason (recorded in the audit log)", key="arch_reason")
                if st.button("Archive", key="arch_btn"):
                    if run_action(ss.set_archived, user, chosen, True, reason, success="Student archived.")[0]:
                        st.rerun()

if is_admin:
    with tabs[1]:
        years = ref["years"]
        with st.form("add_student", clear_on_submit=False):
            a, b = st.columns(2)
            name = a.text_input("Full name *")
            code = b.text_input("Student ID (leave blank to generate)")
            dob = a.date_input("Date of birth *", dt.date(2012, 1, 1), min_value=dt.date(1990, 1, 1))
            adm = b.date_input("Admission date *", dt.date.today(), min_value=dt.date(1990, 1, 1))
            gender = a.selectbox("Gender", ["", "Male", "Female", "Other"])
            phone = b.text_input("Phone")
            guardian, gphone = a.text_input("Guardian name"), b.text_input("Guardian phone")
            ecn, ecp = a.text_input("Emergency contact"), b.text_input("Emergency phone")
            email, address = a.text_input("E-mail"), st.text_area("Address")
            st.markdown("**Enrollment (optional)**")
            e1, e2, e3 = st.columns(3)
            cls = e1.selectbox("Class", [""] + [x["name"] for x in ref["classes"]])
            sec = e2.selectbox("Section", [""] + [x["name"] for x in ref["sections"]])
            roll = e3.number_input("Roll number", 0, 9999, 0)
            go = st.form_submit_button("Add student")
        if go:
            cid = next((x["id"] for x in ref["classes"] if x["name"] == cls), None)
            sid = next((x["id"] for x in ref["sections"] if x["name"] == sec), None)
            ok, new_id = run_action(ss.create_student, user, {
                "full_name": name, "date_of_birth": dob, "admission_date": adm, "gender": gender or None,
                "guardian_name": guardian, "guardian_phone": gphone, "phone": phone, "email": email or None,
                "emergency_contact_name": ecn, "emergency_contact_phone": ecp, "address": address,
                "student_code": code.strip() or None}, class_id=cid, section_id=sid, roll_no=int(roll) or None)
            if ok:
                st.success(f"Student created (record #{new_id}).")
    with tabs[2]:
        st.markdown("Upload a UTF-8 **CSV** with these columns. Dates may be `YYYY-MM-DD` or `DD/MM/YYYY`. "
                    "The class and section names must already exist (Settings > Academic structure). "
                    "If any row is invalid, **nothing** is imported and every problem is listed.")
        st.code("full_name,date_of_birth,admission_date,class,section,roll_no,gender,guardian_name,guardian_phone\n"
                "Asha Verma,2012-05-14,2022-04-01,Class 8,A,1,Female,Ramesh Verma,9800000000", language="csv")
        year_id = year_select(ref, "imp_year", "Enroll into academic year")
        upload = st.file_uploader("CSV file", type=["csv"], key="csv_up")
        if upload and st.button("Validate and import", key="imp_btn"):
            ok, result = run_action(ss.import_students_csv, user, upload.getvalue(), year_id)
            if ok and result["imported"]:
                st.success(f"Imported {result['imported']:,} students.")
            elif ok:
                st.error(f"{result['error_count']} problem(s) found - nothing was imported.")
                st.dataframe([{"Row": r, "Problem": p} for r, p in result["errors"]], use_container_width=True, hide_index=True)
    with tabs[3]:
        rows = paginated_table(lambda p, n: ss.search_students(user, only_archived=True, page=p, page_size=n),
                               "stu_arch", columns=COLS, empty="No archived students.")
        if rows:
            labels = {f"{r['full_name']} ({r['student_code']})": r["id"] for r in rows}
            pick = labels[st.selectbox("Restore a student", list(labels))]
            if st.button("Restore", key="restore_btn") and run_action(ss.set_archived, user, pick, False, "restored",
                                                                       success="Student restored.")[0]:
                st.rerun()
