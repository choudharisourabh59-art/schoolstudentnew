import datetime as dt

import pandas as pd
import streamlit as st

from components import charts
from components.dashboard_cards import empty_state
from components.forms import run_action, student_picker
from components.sidebar import require_login
from services import academic_service as ac, attendance_service as att, grade_service as gs
from services import student_service as ss, upload_service as up
from services.errors import SISError

user = require_login("Student Portfolio")
st.title("Student Portfolio")
ref = ac.reference_data()
pick = student_picker(user, "pf_student")
if not pick:
    st.stop()
sid = pick["id"]
p = ss.get_student(user, sid)

head = st.columns([1, 5])
with head[0]:
    if p["photo_document_id"]:
        try:
            _meta, data = up.get_document(user, p["photo_document_id"])
            st.image(data, width=120)
        except SISError:
            st.markdown("<div style='font-size:4rem'>🧑‍🎓</div>", unsafe_allow_html=True)
    else:
        st.markdown("<div style='font-size:4rem'>🧑‍🎓</div>", unsafe_allow_html=True)
with head[1]:
    cur = p["current"]
    st.subheader(p["full_name"])
    st.caption(f"{p['student_code']} · " + (f"{cur['class_name']}-{cur['section_name']} · roll {cur['roll_no'] or '-'} · {cur['year_name']}"
                                           if cur else "not enrolled in the current year") + f" · status: {p['status']}")

tabs = st.tabs(["Overview", "Academics", "History", "Documents", "Awards and remarks"])

with tabs[0]:
    a, b = st.columns(2)
    a.markdown(f"**Date of birth:** {p['date_of_birth']:%d %b %Y}  \n**Gender:** {p['gender'] or '-'}  \n"
               f"**Admission date:** {p['admission_date']:%d %b %Y}  \n**Address:** {p['address'] or '-'}")
    b.markdown(f"**Guardian:** {p['guardian_name'] or '-'} ({p['guardian_phone'] or '-'})  \n"
               f"**Emergency contact:** {p['emergency_contact_name'] or '-'} ({p['emergency_contact_phone'] or '-'})  \n"
               f"**Phone:** {p['phone'] or '-'}  \n**E-mail:** {p['email'] or '-'}")
    year = next((y for y in ref["years"] if y["is_current"]), None)
    summary = att.student_summary(user, sid, year["id"] if year else None)
    st.markdown(f"#### Attendance ({year['name'] if year else 'all years'})")
    m = st.columns(5)
    m[0].metric("Attendance", f"{float(summary['percentage']):.1f}%" if summary["percentage"] is not None else "No data")
    for col, key, name in zip(m[1:], "PALE", ("Present", "Absent", "Late", "Excused")):
        col.metric(name, summary[key])
    fig = charts.attendance_donut(summary)
    if fig:
        st.plotly_chart(fig, use_container_width=True)

with tabs[1]:
    results = gs.student_results(user, sid)
    if not results:
        empty_state("No examination results yet.")
    else:
        scored = [r for r in results if r["percentage"] is not None and r["result"] in ("PASS", "FAIL")]
        c1, c2 = st.columns(2)
        fig = charts.performance_line(pd.DataFrame([{"label": f"{r['year']} {r['term']} {r['exam']}",
                                                     "percentage": float(r["percentage"])} for r in scored])) if scored else None
        if fig:
            c1.plotly_chart(fig, use_container_width=True)
        per_subject: dict[str, list[float]] = {}
        for r in results:
            for s in r["subjects"]:
                if s["marks"] is not None and not s["absent"]:
                    per_subject.setdefault(s["subject"], []).append(float(s["marks"] / s["max"] * 100))
        fig = charts.subject_bar(pd.DataFrame([{"subject": k, "value": sum(v) / len(v)} for k, v in sorted(per_subject.items())]),
                                 "Subject-wise average (%)") if per_subject else None
        if fig:
            c2.plotly_chart(fig, use_container_width=True)
        for r in results:
            label = f"{r['year']} · {r['class']} · {r['term']} · {r['exam']} - {r['result']}" + ("" if r["status"] == "published" else " (draft)")
            with st.expander(label):
                m = st.columns(4)
                m[0].metric("Total", f"{gs.fmt(r['total'])} / {gs.fmt(r['max_total'])}")
                m[1].metric("Percentage", f"{gs.fmt(r['percentage'])}%")
                m[2].metric("Grade", r["grade"] or "-")
                m[3].metric("Result", r["result"])
                st.dataframe(pd.DataFrame([{"Subject": s["subject"], "Marks": "ABSENT" if s["absent"] else gs.fmt(s["marks"]),
                                            "Max": gs.fmt(s["max"]), "Pass mark": gs.fmt(s["passing"])} for s in r["subjects"]]),
                             use_container_width=True, hide_index=True)

with tabs[2]:
    hist = ss.enrollment_history(user, sid)
    if not hist:
        empty_state("No enrollment history.")
    else:
        df = pd.DataFrame(hist)
        st.dataframe(df[["year_name", "class_name", "section_name", "roll_no", "status"]].rename(columns={
            "year_name": "Year", "class_name": "Class", "section_name": "Section", "roll_no": "Roll", "status": "Outcome"}),
            use_container_width=True, hide_index=True)
        fig = charts.progression_line(df.rename(columns={"year_name": "year", "class_name": "class"})[["year", "level", "class"]])
        if fig:
            st.plotly_chart(fig, use_container_width=True)

with tabs[3]:
    docs = up.list_documents(user, sid)
    if not docs:
        empty_state("No documents available. Upload them from the Documents page.")
    for d in docs:
        c = st.columns([4, 2, 2, 2])
        c[0].write(f"**{d['name']}**  \n{d['category']} · {d['size'] / 1024:.0f} KB" + ("" if d["approved"] else " · awaiting approval"))
        c[1].caption(f"{d['uploaded_at']:%d %b %Y}")
        if c[2].button("Prepare download", key=f"pf_prep_{d['id']}"):
            ok, res = run_action(up.get_document, user, d["id"])
            if ok:
                c[3].download_button("Download", res[1], res[0]["name"], res[0]["content_type"], key=f"pf_dl_{d['id']}")

with tabs[4]:
    c1, c2 = st.columns(2)
    c1.markdown("#### Awards and achievements")
    awards = ss.list_awards(user, sid)
    for a in awards:
        c1.markdown(f"🏆 **{a['title']}** ({a['awarded_on']:%d %b %Y})  \n{a['description']}")
    if not awards:
        c1.caption("No awards recorded.")
    c2.markdown("#### Teacher remarks")
    remarks = ss.list_remarks(user, sid)
    for r in remarks:
        c2.markdown(f"💬 {r['text']}  \n<small>{r['created_at']:%d %b %Y}</small>", unsafe_allow_html=True)
    if not remarks:
        c2.caption("No remarks yet.")
    if user.role in ("admin", "teacher"):
        with c1.form("award_form", clear_on_submit=True):
            title, desc = st.text_input("Award title"), st.text_area("Description")
            when = st.date_input("Date", dt.date.today())
            if st.form_submit_button("Add award"):
                run_action(ss.add_award, user, sid, title, desc, when, success="Award added.")
        with c2.form("remark_form", clear_on_submit=True):
            text = st.text_area("New remark")
            if st.form_submit_button("Add remark"):
                run_action(ss.add_remark, user, sid, text, success="Remark added.")
