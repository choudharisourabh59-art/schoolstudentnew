import datetime as dt

import pandas as pd
import streamlit as st

from components import charts
from components.dashboard_cards import card_title, empty_state, kpi_card, mini_card, progress_list
from components.forms import class_select, section_select, subject_select, year_select
from components.sidebar import require_login
from components.theme import BLUE, GREEN, LAVENDER, PEACH, SALMON
from config.settings import today
from services import academic_service, attendance_service as att, grade_service as gs, report_service as rs

user = require_login("School Overview", ("admin", "principal"))
st.title("School Overview")
ref = academic_service.reference_data()
if not ref["years"]:
    empty_state("No academic year exists yet. An administrator can create one under Settings > Academic structure.")
    st.stop()


def show(fig, message: str) -> None:
    if fig is None:
        empty_state(message, "📭")
    else:
        st.plotly_chart(fig, use_container_width=True)


f = st.columns(5)
with f[0]:
    year_id = year_select(ref, "d_year")
with f[1]:
    class_id = class_select(ref, "d_class")
with f[2]:
    section_id = section_select(ref, "d_section")
terms = sorted({e["term"] for e in gs.list_examinations(year_id)})
with f[3]:
    term = st.selectbox("Term", ["All"] + terms, key="d_term")
    term = None if term == "All" else term
with f[4]:
    subject_id = subject_select(ref, "d_subject")

m = rs.dashboard_metrics(user, year_id, class_id, section_id)
a30 = att.breakdown(user, year_id, class_id, section_id, since=today() - dt.timedelta(days=30))
a_today = att.breakdown(user, year_id, class_id, section_id, since=today(), until=today())
missing = gs.students_with_missing_marks(user, year_id, class_id, section_id)
active = m["active_students"]
present_today = a_today["P"] + a_today["L"]

k = st.columns(4)
with k[0]:
    rate = a30["percentage"]
    kpi_card("Attendance rate (30 days)", f"{float(rate):.1f}%" if rate is not None else "No data", icon="🗓️",
             tint="green", ring=float(rate) if rate is not None else None)
with k[1]:
    marked = a_today["recorded_days"]
    kpi_card("Students present today", str(present_today), str(active), "🧑‍🎓", "blue",
             ring=(present_today / active * 100) if active and marked else None)
with k[2]:
    kpi_card("Students with missing marks", str(missing), str(active), "📝", "pink",
             ring=(missing / active * 100) if active else None, ring_color=SALMON)
with k[3]:
    kpi_card("Academic year", m["year"], "", "🏫", "purple")

mm = st.columns(4)
with mm[0]:
    mini_card("Active students", f"{active:,}", "👥", "purple")
with mm[1]:
    mini_card("Teachers", f"{m['teachers']:,}", "👩‍🏫", "green")
with mm[2]:
    mini_card("Classes", str(m["classes"]), "📚", "peach")
with mm[3]:
    mini_card("Sections", str(m["sections"]), "🗂️", "blue")

left, right = st.columns([3, 2])
with left:
    card_title("Class-wise performance", "Average of published results", "📊")
    perf = pd.DataFrame(gs.class_performance(user, year_id, term, subject_id, section_id))
    show(charts.class_grouped_bar(perf) if not perf.empty else None,
         "No published results yet for these filters.")
with right:
    card_title("Monthly attendance", "Attendance % with absences and late arrivals", "📈")
    trend = pd.DataFrame(att.monthly_trend(user, year_id, class_id, section_id))
    show(charts.monthly_attendance(trend) if not trend.empty else None, "No attendance has been recorded yet.")

d = st.columns(4)
with d[0]:
    card_title("Attendance (30 days)", "", "🥧")
    show(charts.attendance_donut(a30), "No attendance recorded in the last 30 days.")
with d[1]:
    card_title("Enrollment status", m["year"], "🧾")
    show(charts.status_donut(m["enrollment_status"], "Enrollment status"), "No enrollments in this year.")
with d[2]:
    card_title("Marks entry progress", "Cells filled per examination", "✍️")
    items = [(p["exam"] + (" ✔" if p["status"] == "published" else ""),
              f"{p['filled']}/{p['expected']}", (p["filled"] / p["expected"] * 100) if p["expected"] else 0, GREEN)
             for p in gs.entry_progress(user, year_id)]
    progress_list(items, "No examinations created yet.")
with d[3]:
    card_title("Attendance today", today().strftime("%d %b %Y"), "✅")
    total = a_today["recorded_days"]
    names = [("PRESENT", "P", GREEN), ("LATE", "L", PEACH), ("ABSENT", "A", SALMON), ("EXCUSED", "E", LAVENDER)]
    progress_list([(f"{n[1] and a_today[n[1]]} {n[0]}", f"{a_today[n[1]] / total * 100:.1f}%", a_today[n[1]] / total * 100, n[2])
                   for n in names] if total else [], "Attendance has not been marked today.")

e = st.columns(2)
with e[0]:
    card_title("Subject-wise average", "Published results", "📘")
    sub = pd.DataFrame(gs.subject_averages(user, year_id, class_id, section_id, term))
    show(charts.subject_bar(sub.rename(columns={"average": "value"}), "Average % by subject") if not sub.empty else None,
         "No published results for these filters.")
with e[1]:
    card_title("Grade distribution", "Students by overall grade", "🎯")
    dist = pd.DataFrame(gs.grade_distribution(user, year_id, class_id, section_id, term, subject_id))
    show(charts.grade_distribution(dist) if not dist.empty else None, "No published results for these filters.")

card_title("Recent administrative activity", "Latest audit entries", "🛡️")
activity = rs.recent_activity(user, 10)
if activity:
    st.dataframe(pd.DataFrame(activity), use_container_width=True, hide_index=True)
else:
    empty_state("No activity recorded yet.")
