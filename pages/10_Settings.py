import datetime as dt
import json

import pandas as pd
import streamlit as st

from components.dashboard_cards import empty_state
from components.forms import run_action
from components.sidebar import require_login
from components.tables import paginated_table
from config.settings import settings
from services import academic_service as ac, auth_service, backup_service as bk, report_service as rs

user = require_login("Settings")
st.title("Settings")
ref = ac.reference_data()
is_admin = user.role == "admin"
names = ["My account"] + (["Academic structure", "Users", "Grading policy", "Backups and storage"] if is_admin else []) \
    + (["Audit log"] if user.role in ("admin", "principal") else [])
tabs = dict(zip(names, st.tabs(names)))

with tabs["My account"]:
    st.write(f"Signed in as **{user.full_name}** ({user.username}), role: {user.role}.")
    with st.form("pw_form", clear_on_submit=True):
        old, new, new2 = (st.text_input("Current password", type="password"), st.text_input("New password", type="password"),
                          st.text_input("Repeat new password", type="password"))
        go = st.form_submit_button("Change password")
    if go:
        if new != new2:
            st.error("The new passwords do not match.")
        else:
            run_action(auth_service.change_password, user, old, new, success="Password changed.")

if is_admin:
    with tabs["Academic structure"]:
        a, b = st.columns(2)
        with a:
            st.markdown("#### Academic years")
            if ref["years"]:
                st.dataframe(pd.DataFrame(ref["years"]).drop(columns="id").rename(columns={"is_current": "current"}),
                             use_container_width=True, hide_index=True)
            with st.form("year_form", clear_on_submit=True):
                name = st.text_input("Name (e.g. 2026-2027)")
                c = st.columns(2)
                start = c[0].date_input("Starts", dt.date(dt.date.today().year, 4, 1))
                end = c[1].date_input("Ends", dt.date(dt.date.today().year + 1, 3, 31))
                make_current = st.checkbox("Make this the current year")
                if st.form_submit_button("Add academic year"):
                    run_action(ac.create_year, user, name, start, end, make_current, success="Academic year added.")
            if ref["years"]:
                pick = {y["name"]: y["id"] for y in ref["years"]}
                chosen = st.selectbox("Set current year", list(pick), key="cur_year")
                if st.button("Set as current", key="cur_btn") and run_action(ac.set_current_year, user, pick[chosen],
                                                                              success="Current year updated.")[0]:
                    st.rerun()
        with b:
            st.markdown("#### Classes, sections and subjects")
            st.caption("Class level drives promotion (level 8 -> level 9).")
            with st.form("class_form", clear_on_submit=True):
                c = st.columns(2)
                cname, level = c[0].text_input("Class name (e.g. Class 8)"), c[1].number_input("Level", 1, 20, 1)
                if st.form_submit_button("Add class"):
                    run_action(ac.create_class, user, cname, int(level), success="Class added.")
            with st.form("section_form", clear_on_submit=True):
                sname = st.text_input("Section name (e.g. A)")
                if st.form_submit_button("Add section"):
                    run_action(ac.create_section, user, sname, success="Section added.")
            with st.form("subject_form", clear_on_submit=True):
                c = st.columns(2)
                code, sub = c[0].text_input("Subject code (e.g. MATH)"), c[1].text_input("Subject name")
                if st.form_submit_button("Add subject"):
                    run_action(ac.create_subject, user, code, sub, success="Subject added.")
            st.write("Classes: " + (", ".join(c["name"] for c in ref["classes"]) or "none") +
                     "  \nSections: " + (", ".join(s["name"] for s in ref["sections"]) or "none") +
                     "  \nSubjects: " + (", ".join(s["name"] for s in ref["subjects"]) or "none"))

    with tabs["Users"]:
        users = auth_service.list_users(user)
        st.dataframe(pd.DataFrame(users).drop(columns="id"), use_container_width=True, hide_index=True)
        with st.expander("Create a user"):
            with st.form("user_form", clear_on_submit=True):
                c = st.columns(2)
                full, uname = c[0].text_input("Full name"), c[1].text_input("Username")
                email, role = c[0].text_input("E-mail (optional)"), c[1].selectbox("Role", ["admin", "principal", "teacher", "student", "parent"])
                emp, pw = c[0].text_input("Employee number (teachers)"), c[1].text_input("Temporary password", type="password")
                if st.form_submit_button("Create user"):
                    run_action(auth_service.create_user, user, uname, full, role, pw, email or None, emp or None,
                               success="User created.")
        labels = {f"{u['username']} ({u['role']})": u for u in users}
        if labels:
            target = labels[st.selectbox("Manage account", list(labels), key="mu_pick")]
            c = st.columns(3)
            if target["is_active"]:
                if c[0].button("Deactivate", key="mu_off") and run_action(auth_service.set_user_active, user, target["id"], False,
                                                                           success="Account deactivated.")[0]:
                    st.rerun()
            elif c[0].button("Activate", key="mu_on") and run_action(auth_service.set_user_active, user, target["id"], True,
                                                                     success="Account activated.")[0]:
                st.rerun()
            if c[1].button("Issue password-reset code", key="mu_reset"):
                ok, token = run_action(auth_service.issue_reset_token, user, target["id"])
                if ok:
                    st.code(token)
                    st.caption(f"Give this one-time code to the user. It expires in {settings.reset_token_minutes} minutes.")
            if target["role"] in ("admin", "principal", "student") and target["id"] != user.id:
                new_role = c[2].selectbox("Change role", ["admin", "principal", "student"], key="mu_role")
                if c[2].button("Apply role", key="mu_role_btn"):
                    run_action(auth_service.change_role, user, target["id"], new_role, success="Role changed.")
        st.markdown("#### Link accounts to students")
        from services import student_service as ss
        q = st.text_input("Find a student", key="lk_q")
        rows, _ = ss.search_students(user, query=q or None, page_size=20)
        if rows:
            stu = {f"{r['full_name']} ({r['student_code']})": r["id"] for r in rows}
            sid = stu[st.selectbox("Student", list(stu), key="lk_student")]
            for role, fn, label in (("student", auth_service.link_student_account, "Link student login"),
                                    ("parent", auth_service.link_parent, "Link parent (verified)")):
                cand = {f"{u['username']} - {u['full_name']}": u["id"] for u in users if u["role"] == role}
                if cand:
                    who = cand[st.selectbox(f"{role.title()} account", list(cand), key=f"lk_{role}")]
                    if st.button(label, key=f"lk_btn_{role}"):
                        run_action(fn, user, sid, who, success="Linked.") if role == "student" else run_action(
                            fn, user, who, sid, success="Linked.")

    with tabs["Grading policy"]:
        pol = ref["policy"]
        st.caption("These rules drive grades, results, attendance rates and promotion suggestions. Changes are audited.")
        with st.form("policy_form"):
            c = st.columns(3)
            pass_pct = c[0].number_input("Pass percentage", 0.0, 100.0, float(pol["pass_percentage"]))
            subj_pass = c[1].checkbox("Must pass every subject", pol["require_subject_pass"])
            absent_fail = c[2].checkbox("Absent in a subject = fail", pol["absent_is_fail"])
            c = st.columns(3)
            neg = c[0].checkbox("Allow negative marks", pol["allow_negative_marks"])
            late = c[1].checkbox("Late counts as present", pol["late_counts_as_present"])
            edit_days = c[2].number_input("Days teachers may edit past attendance", 0, 365, int(pol["attendance_edit_days"]))
            c = st.columns(3)
            promo_pct = c[0].number_input("Promotion: minimum %", 0.0, 100.0, float(pol["promotion_min_percentage"]))
            promo_att = c[1].number_input("Promotion: minimum attendance %", 0.0, 100.0, float(pol["promotion_min_attendance"]))
            step = c[2].number_input("Levels gained when promoted", 1, 3, int(pol["promotion_level_step"]))
            st.markdown("**Grade bands** (minimum percentage -> grade; keep a 0 row)")
            bands = st.data_editor(pd.DataFrame(pol["grade_bands"], columns=["Minimum %", "Grade"]).astype({"Minimum %": float}),
                                   num_rows="dynamic", use_container_width=True, hide_index=True)
            if st.form_submit_button("Save policy"):
                run_action(ac.update_policy, user, {
                    "pass_percentage": str(pass_pct), "require_subject_pass": subj_pass, "absent_is_fail": absent_fail,
                    "allow_negative_marks": neg, "late_counts_as_present": late, "attendance_edit_days": int(edit_days),
                    "promotion_min_percentage": str(promo_pct), "promotion_min_attendance": str(promo_att),
                    "promotion_level_step": int(step),
                    "grade_bands": [[str(r["Minimum %"]), str(r["Grade"])] for _, r in bands.dropna().iterrows()]},
                    success="Policy saved.")

    with tabs["Backups and storage"]:
        st.warning("A backup only counts once you have restored it somewhere and checked it. See README > Backup and recovery.")
        st.write(f"Database: **{settings.database_url.split(':')[0]}** · document storage: **{settings.storage_backend}**")
        c = st.columns(3)
        if c[0].button("Back up database now", key="bk_db"):
            ok, m = run_action(bk.backup_database, user)
            if ok:
                st.success(f"Created {m['file']} ({m['size'] / 1024:.0f} KB).")
        if c[1].button("Back up uploaded files", key="bk_files"):
            ok, m = run_action(bk.backup_files, user)
            if ok:
                st.success(f"Created {m['file']} ({m['size'] / 1024:.0f} KB).")
        if c[2].button("Check documents vs storage", key="bk_check"):
            ok, r = run_action(bk.storage_consistency, user)
            if ok:
                (st.success if r["ok"] else st.error)(f"{r['documents']} documents checked. Missing files: {len(r['missing_files'])}. Orphaned files: {len(r['orphaned_files'])}.")
                if not r["ok"]:
                    st.json({"missing_files": r["missing_files"][:50], "orphaned_files": r["orphaned_files"][:50]})
        backups = bk.list_backups()
        if backups:
            st.dataframe(pd.DataFrame(backups), use_container_width=True, hide_index=True)
            pick = st.selectbox("Verify a backup", [b["name"] for b in backups], key="bk_pick")
            if st.button("Verify integrity", key="bk_verify"):
                result = bk.verify_backup(settings.backup_dir / pick)
                st.success("Backup verified.") if result["ok"] else st.error("; ".join(result["problems"]))
        else:
            empty_state("No backups yet.")
        st.caption("On Streamlit Community Cloud the server disk is temporary: use your database provider's backups "
                   "or download dumps to a safe location.")

if "Audit log" in tabs:
    with tabs["Audit log"]:
        action = st.text_input("Filter by action (e.g. marks_change, login_failed)", key="aud_action").strip() or None

        def fetch(page, size):
            rows, total = rs.audit_page(user, page, size, action)
            for r in rows:
                r["previous"] = json.dumps(r["previous"], default=str) if r["previous"] is not None else ""
                r["new"] = json.dumps(r["new"], default=str) if r["new"] is not None else ""
            return rows, total

        paginated_table(fetch, f"aud_{abs(hash(action))}", 50, "No audit entries.",
                        {"when": "When (UTC)", "user": "User", "action": "Action", "record_type": "Record", "record_id": "ID",
                         "previous": "Previous", "new": "New"})
