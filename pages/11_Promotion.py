import pandas as pd
import streamlit as st

from components.dashboard_cards import empty_state
from components.forms import class_select, run_action, section_select
from components.sidebar import require_login
from services import academic_service as ac, promotion_service as ps

user = require_login("Promotion", ("admin",))
st.title("Class promotion")
st.caption("Promotion is always an explicit decision. Nothing happens because a date has passed, the previous year's "
           "marks, attendance and class stay on record, and the whole batch is applied or rejected together.")
ref = ac.reference_data()
if len(ref["years"]) < 2:
    empty_state("You need at least two academic years (the current one and the next one) - add them in Settings.")
    st.stop()
years = {y["name"]: y for y in ref["years"]}
names = list(years)
cur_idx = next((i for i, y in enumerate(ref["years"]) if y["is_current"]), 0)
c = st.columns(4)
from_name = c[0].selectbox("1. Current academic year", names, index=cur_idx, key="pr_from")
older = [n for n in names if years[n]["start"] > years[from_name]["start"]]
if not older:
    empty_state("There is no later academic year yet. Create the next year in Settings first.")
    st.stop()
to_name = c[1].selectbox("2. Target academic year", older, key="pr_to")
with c[2]:
    class_id = class_select(ref, "pr_class", allow_all=False, label="3. Class to review")
with c[3]:
    section_id = section_select(ref, "pr_section")
fy, ty = years[from_name]["id"], years[to_name]["id"]
key = (fy, ty, class_id, section_id)

if st.button("4. Show students awaiting a decision", key="pr_load"):
    ok, rows = run_action(ps.eligible_students, user, fy, ty, class_id, section_id)
    if ok:
        st.session_state["promo"] = {"key": key, "rows": rows}
        st.session_state.pop("promo_preview", None)
state = st.session_state.get("promo")
if not state or state["key"] != key:
    st.info("Choose the years and a class, then load the students.")
    st.stop()
rows = state["rows"]
if not rows:
    empty_state("No students in this class are awaiting a decision for the selected years.", "✅")
    st.stop()

class_names = [x["name"] for x in ref["classes"]]
section_names = [x["name"] for x in ref["sections"]]
OUT = ["(decide later)", "promoted", "retained", "transferred", "graduated", "withdrawn"]
st.markdown("**5. Record a decision for each student.** Suggestions come from your grading policy and are only suggestions. "
            "Leave target class/section blank to use the standard next class (promoted) or the same class (retained).")
prefill = st.checkbox("Pre-fill every decision with the suggestion (you can still change each one)", key="pr_prefill")
df = pd.DataFrame([{"enrollment_id": r["enrollment_id"], "Student ID": r["student_code"], "Name": r["name"],
                    "Class": f"{r['class']}-{r['section']}", "Average %": None if r["percentage"] is None else round(float(r["percentage"]), 1),
                    "Attendance %": None if r["attendance"] is None else round(float(r["attendance"]), 1),
                    "Suggested": r["suggested"], "Decision": r["suggested"] if prefill else "(decide later)", "Target class": "", "Target section": "",
                    "Roll no": None} for r in rows]).set_index("enrollment_id")
edited = st.data_editor(
    df, hide_index=True, use_container_width=True, key=f"pr_edit_{abs(hash(key))}_{int(prefill)}",
    disabled=["Student ID", "Name", "Class", "Average %", "Attendance %", "Suggested"],
    column_config={"Decision": st.column_config.SelectboxColumn("Decision", options=OUT, required=True),
                   "Target class": st.column_config.SelectboxColumn("Target class", options=[""] + class_names),
                   "Target section": st.column_config.SelectboxColumn("Target section", options=[""] + section_names),
                   "Roll no": st.column_config.NumberColumn("Roll no", min_value=1, step=1)})
level_of = {x["name"]: x["level"] for x in ref["classes"]}
by_level = {x["level"]: x for x in ref["classes"]}
by_name = {x["name"]: x for x in ref["classes"]}
sec_by_name = {x["name"]: x for x in ref["sections"]}
step = int(ref["policy"]["promotion_level_step"])
src = {r["enrollment_id"]: r for r in rows}


def build() -> list[ps.Decision]:
    out = []
    for eid, row in edited.iterrows():
        if row["Decision"] == "(decide later)":
            continue
        r, outcome = src[int(eid)], row["Decision"]
        tc = ts = None
        if outcome in ("promoted", "retained"):
            cls = by_name.get(row["Target class"]) if row["Target class"] else by_level.get(r["level"] + (step if outcome == "promoted" else 0))
            sec = sec_by_name.get(row["Target section"]) if row["Target section"] else {"id": r["section_id"]}
            tc, ts = (cls["id"] if cls else None), sec["id"]
        roll = None if pd.isna(row["Roll no"]) else int(row["Roll no"])
        out.append(ps.Decision(int(eid), outcome, tc, ts, roll))
    return out


decisions = build()
st.write(f"{len(decisions)} decision(s) recorded; {len(rows) - len(decisions)} student(s) left undecided (they stay in their current class).")
if st.button("6. Preview", key="pr_preview", disabled=not decisions):
    ok, res = run_action(ps.preview_promotion, user, fy, ty, decisions)
    if ok:
        st.session_state["promo_preview"] = res
prev = st.session_state.get("promo_preview")
if prev:
    if prev["ok"]:
        st.success("Valid. Outcomes: " + ", ".join(f"{k}: {v}" for k, v in sorted(prev["counts"].items())))
    else:
        st.error("Fix these problems first:")
        for e in prev["errors"][:30]:
            st.write("- " + e)
if prev and prev["ok"]:
    sure = st.checkbox(f"7. I confirm these {len(decisions)} decisions follow the school's promotion policy.", key="pr_confirm")
    if st.button("Apply promotion", type="primary", disabled=not sure, key="pr_apply"):
        ok, counts = run_action(ps.apply_promotion, user, fy, ty, decisions)
        if ok:
            st.success("Promotion applied: " + ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())) +
                       ". History was preserved and dashboards now reflect the new enrollments.")
            st.session_state.pop("promo", None)
            st.session_state.pop("promo_preview", None)
