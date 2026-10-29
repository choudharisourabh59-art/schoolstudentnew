import pandas as pd
import streamlit as st

from components import charts
from components.dashboard_cards import card_title, empty_state
from components.forms import class_select, run_action, section_select, student_picker, year_select
from components.sidebar import require_login
from components.tables import export_buttons
from services import academic_service as ac, grade_service as gs, report_service as rs

user = require_login("Results")
st.title("Results")
ref = ac.reference_data()

if user.role in ("student", "parent"):
    pick = student_picker(user, "res_student")
    if pick:
        results = gs.student_results(user, pick["id"])
        if not results:
            empty_state("No published results yet.")
        scored = [r for r in results if r["percentage"] is not None and r["result"] in ("PASS", "FAIL")]
        fig = charts.performance_line(pd.DataFrame([{"label": f"{r['term']} {r['exam']}", "percentage": float(r["percentage"])}
                                                    for r in scored])) if scored else None
        if fig:
            st.plotly_chart(fig, use_container_width=True)
        for r in results:
            with st.expander(f"{r['year']} - {r['term']} - {r['exam']}: {r['result']}", expanded=False):
                c = st.columns(4)
                c[0].metric("Total", f"{gs.fmt(r['total'])} / {gs.fmt(r['max_total'])}")
                c[1].metric("Percentage", f"{gs.fmt(r['percentage'])}%")
                c[2].metric("Grade", r["grade"] or "-")
                c[3].metric("Result", r["result"])
                st.dataframe(pd.DataFrame([{"Subject": s["subject"], "Marks": "ABSENT" if s["absent"] else gs.fmt(s["marks"]),
                                            "Max": gs.fmt(s["max"]), "Pass mark": gs.fmt(s["passing"])} for s in r["subjects"]]),
                             use_container_width=True, hide_index=True)
    st.stop()

if not ref["years"]:
    empty_state("Create an academic year first.")
    st.stop()
c = st.columns(4)
with c[0]:
    year_id = year_select(ref, "res_year")
exams = gs.list_examinations(year_id)
if not exams:
    empty_state("No examinations for this year.")
    st.stop()
with c[1]:
    labels = {f"{e['term']} - {e['name']} [{e['status']}]": e for e in exams}
    exam = labels[st.selectbox("Examination", list(labels), key="res_exam")]
with c[2]:
    class_id = class_select(ref, "res_class", allow_all=False)
with c[3]:
    section_id = section_select(ref, "res_section")
ok, df = run_action(rs.exam_report, user, exam["id"], class_id, section_id)
if ok:
    if df.empty:
        empty_state("No students or results for this selection.")
    else:
        st.dataframe(df, use_container_width=True, hide_index=True)
        export_buttons(df, f"results_{exam['name']}", f"Results - {exam['term']} {exam['name']}", f"Status: {exam['status']}")
        if "Grade" in df:
            dist = df[df["Grade"] != "-"]["Grade"].value_counts().reindex([g for _m, g in ref["policy"]["grade_bands"]], fill_value=0)
            fig = charts.grade_distribution(pd.DataFrame({"grade": dist.index, "students": dist.values}))
            if fig:
                card_title("Grade distribution", "This examination and class", "🎯")
                st.plotly_chart(fig, use_container_width=True)
