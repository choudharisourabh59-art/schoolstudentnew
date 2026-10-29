import streamlit as st

from components.dashboard_cards import empty_state
from components.forms import run_action, student_picker
from components.sidebar import require_login
from config.settings import settings
from services import upload_service as up

user = require_login("Documents")
st.title("Documents")
tab_names = ["Student documents"] + (["School documents"] if user.role in ("admin", "principal") else [])
tabs = st.tabs(tab_names)


def render_list(docs, key_prefix: str) -> None:
    if not docs:
        empty_state("No documents yet.", "📁")
    for d in docs:
        c = st.columns([4, 2, 2, 2])
        c[0].write(f"**{d['name']}**  \n{d['category']} · {d['size'] / 1024:.0f} KB" + ("" if d["approved"] else " · awaiting approval"))
        c[1].caption(f"{d['uploaded_at']:%d %b %Y}")
        if c[2].button("Prepare download", key=f"{key_prefix}_prep_{d['id']}"):
            ok, res = run_action(up.get_document, user, d["id"])
            if ok:
                c[3].download_button("Download", res[1], res[0]["name"], res[0]["content_type"], key=f"{key_prefix}_dl_{d['id']}")
        if user.role in ("admin", "teacher") and not d["approved"]:
            if c[2].button("Approve", key=f"{key_prefix}_ap_{d['id']}") and run_action(up.approve_document, user, d["id"], success="Approved.")[0]:
                st.rerun()
        if user.role == "admin":
            with c[3].popover("Delete"):
                why = st.text_input("Reason", key=f"{key_prefix}_why_{d['id']}")
                if st.button("Confirm delete", key=f"{key_prefix}_del_{d['id']}") and run_action(
                        up.delete_document, user, d["id"], why, success="Deleted.")[0]:
                    st.rerun()


with tabs[0]:
    pick = student_picker(user, "doc_student")
    if pick:
        cats = list(up.CATEGORIES) if user.role in ("admin", "teacher") else ["certificate", "other"]
        with st.form("upload_form", clear_on_submit=True):
            st.caption(f"Allowed: {', '.join(settings.allowed_extensions).upper()} · max {settings.max_upload_mb} MB. "
                       "File content is checked, not just the name.")
            category = st.selectbox("Category", cats)
            file = st.file_uploader("File", type=list(settings.allowed_extensions))
            go = st.form_submit_button("Upload")
        if go:
            if file is None:
                st.error("Choose a file first.")
            else:
                ok, _ = run_action(up.upload_document, user, file.name, file.getvalue(), category, pick["id"],
                                   success="Uploaded." + ("" if user.role in ("admin", "teacher") else " It will be visible to others once approved."))
        render_list(up.list_documents(user, pick["id"]), "sd")

if user.role in ("admin", "principal"):
    with tabs[1]:
        if user.role == "admin":
            with st.form("school_upload", clear_on_submit=True):
                category = st.selectbox("Category", ["report", "other"], key="sch_cat")
                file = st.file_uploader("File", type=list(settings.allowed_extensions), key="sch_file")
                go = st.form_submit_button("Upload school document")
            if go and file is not None:
                run_action(up.upload_document, user, file.name, file.getvalue(), category, None, success="Uploaded.")
        render_list(up.list_documents(user, None), "sc")
