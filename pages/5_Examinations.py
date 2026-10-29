import pandas as pd
import streamlit as st

from components.dashboard_cards import empty_state
from components.forms import class_select, run_action, subject_select, year_select
from components.sidebar import require_login
from services import academic_service as ac, grade_service as gs

user = require_login("Examinations", ("admin", "principal", "teacher"))
st.title("Examinations")
ref = ac.reference_data()
if not ref["years"]:
    empty_state("Create an academic year first (Settings > Academic structure).")
    st.stop()
year_id = year_select(ref, "ex_year")
exams = gs.list_examinations(year_id)
names = (["Enter marks"] if user.role in ("admin", "teacher") else []) + (["Manage examinations"] if user.role == "admin" else []) \
    + (["Publish and amend"] if user.role in ("admin", "principal") else [])
tabs = dict(zip(names, st.tabs(names)))

if "Enter marks" in tabs:
    with tabs["Enter marks"]:
        if not exams:
            empty_state("No examinations exist for this year yet.")
        else:
            exam = {f"{e['term']} - {e['name']} [{e['status']}]": e for e in exams}
            exam = exam[st.selectbox("Examination", list(exam), key="em_exam")]
            pairs = ac.my_assigned_classes(user, year_id)
            labels = {f"{p['class']} - {p['section']}": p for p in pairs}
            if not labels:
                empty_state("You are not assigned to any class this year.")
            else:
                sel = labels[st.selectbox("Class and section", list(labels), key="em_cls")]
                subs = gs.list_exam_subjects(exam["id"], sel["class_id"])
                if not subs:
                    empty_state("No subjects have been added to this examination for the class.")
                else:
                    sub_labels = {f"{s['subject']} (max {gs.fmt(s['max_marks'])}, pass {gs.fmt(s['passing_marks'])})": s for s in subs}
                    es = sub_labels[st.selectbox("Subject", list(sub_labels), key="em_sub")]
                    roster = gs.marks_roster(user, es["id"], sel["section_id"])
                    published = exam["status"] == "published"
                    if published:
                        st.warning("These results are published. Only an authorised administrator can amend them, "
                                   "and every change is recorded with your reason.")
                    if not roster:
                        empty_state("No students in this class and section.")
                    else:
                        blanks = sum(1 for r in roster if r["marks"] is None and not r["absent"])
                        st.caption(f"{blanks} of {len(roster)} students still have no mark. Leave a cell empty if the mark is "
                                   "not available yet - it is never treated as zero.")
                        df = pd.DataFrame([{"enrollment_id": r["enrollment_id"], "Roll": r["roll_no"],
                                            "Student ID": r["student_code"], "Name": r["name"],
                                            "Marks": None if r["marks"] is None else float(r["marks"]),
                                            "Absent": r["absent"]} for r in roster]).set_index("enrollment_id")
                        low = None if ref["policy"]["allow_negative_marks"] else 0.0
                        edited = st.data_editor(
                            df, hide_index=True, use_container_width=True, disabled=["Roll", "Student ID", "Name"],
                            column_config={"Marks": st.column_config.NumberColumn(
                                "Marks", min_value=low, max_value=float(es["max_marks"]), step=0.01, format="%.2f"),
                                "Absent": st.column_config.CheckboxColumn("Absent")},
                            key=f"em_edit_{es['id']}_{sel['section_id']}")
                        reason = st.text_input("Reason for amendment (required for published results)", key="em_reason") if published else ""
                        if st.button("Save marks", type="primary", key="em_save", disabled=published and user.role != "admin"):
                            entries = [gs.MarkEntry(int(i), None if pd.isna(r["Marks"]) else r["Marks"], bool(r["Absent"]))
                                       for i, r in edited.iterrows()]
                            ok, res = run_action(gs.enter_marks, user, es["id"], entries, reason)
                            if ok:
                                st.success(f"Saved. {res['changed']} mark(s) changed; results recalculated.")

if "Manage examinations" in tabs:
    with tabs["Manage examinations"]:
        with st.form("new_exam", clear_on_submit=True):
            a, b = st.columns(2)
            term, name = a.text_input("Term (e.g. Term 1)"), b.text_input("Examination name (e.g. Mid-term)")
            if st.form_submit_button("Create examination"):
                run_action(gs.create_examination, user, year_id, term, name, success="Examination created.")
        if exams:
            pick = {f"{e['term']} - {e['name']}": e for e in exams}
            chosen = pick[st.selectbox("Add subjects to", list(pick), key="ms_exam")]
            with st.form("new_exam_subject"):
                c = st.columns(4)
                with c[0]:
                    subject_id = subject_select(ref, "ms_subject", allow_all=False)
                with c[1]:
                    class_id = class_select(ref, "ms_class", allow_all=False)
                mx = c[2].number_input("Maximum marks", 1.0, 1000.0, 100.0, step=0.5)
                ps = c[3].number_input("Passing marks", 0.0, 1000.0, 35.0, step=0.5)
                if st.form_submit_button("Add subject"):
                    run_action(gs.add_exam_subject, user, chosen["id"], subject_id, class_id, mx, ps, success="Subject added.")
            rows = gs.list_exam_subjects(chosen["id"])
            if rows:
                st.dataframe(pd.DataFrame(rows).drop(columns=["id", "subject_id", "class_id"]), use_container_width=True, hide_index=True)

if "Publish and amend" in tabs:
    with tabs["Publish and amend"]:
        if not exams:
            empty_state("No examinations yet.")
        else:
            pick = {f"{e['term']} - {e['name']} [{e['status']}]": e for e in exams}
            chosen = pick[st.selectbox("Examination", list(pick), key="pb_exam")]
            if chosen["status"] == "draft":
                allow = st.checkbox("Publish even if some marks are missing (those results show as INCOMPLETE)")
                if st.button("Publish results", type="primary", key="pb_btn"):
                    if run_action(gs.publish_results, user, chosen["id"], allow, success="Results published.")[0]:
                        st.rerun()
            else:
                st.success("These results are published and visible to students and parents.")
                if user.role == "admin":
                    why = st.text_input("Reason for reopening (recorded in the audit log)", key="pb_reason")
                    if st.button("Reopen for amendment", key="pb_reopen") and run_action(
                            gs.reopen_examination, user, chosen["id"], why, success="Reopened as draft.")[0]:
                        st.rerun()
