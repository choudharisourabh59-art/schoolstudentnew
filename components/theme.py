"""Visual theme modelled on the 'scholix' reference: pastel background, white rounded cards, green accents."""
import streamlit as st

GREEN, GREEN_SOFT = "#6DBB7A", "#BFE3C4"
SALMON, LAVENDER, BLUE, PEACH, PINK = "#F0A090", "#B7B2F0", "#8CC8E8", "#F5C28A", "#F4A6C0"
PALETTE = [GREEN, SALMON, LAVENDER, BLUE, PEACH, PINK]

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
html, body, [class*="css"], .stApp { font-family: 'Inter', system-ui, sans-serif; }
.stApp { background: linear-gradient(135deg,#e9f3ee 0%,#e6edf8 45%,#fbf0e8 100%); background-attachment: fixed; }
[data-testid="stHeader"] { background: transparent; }
#MainMenu, footer { visibility: hidden; }
.block-container { padding-top: 1.6rem; max-width: 1500px; }
[data-testid="stSidebarNav"] { display: none; }
[data-testid="stSidebar"] { background: #ffffff; border-right: 1px solid #eceff3; }
[data-testid="stSidebar"] > div:first-child { padding-top: .5rem; }
a[data-testid="stPageLink-NavLink"] { border-radius: 12px; padding: .45rem .7rem; color: #1f2937; }
a[data-testid="stPageLink-NavLink"]:hover { background: #f3f7f4; }
a[data-testid="stPageLink-NavLink"][aria-current="page"] { background: linear-gradient(90deg,#8fcf82,#c9e86c); font-weight: 700; }
.brand { display:flex; align-items:center; gap:.5rem; font-size:1.55rem; font-weight:700; letter-spacing:-.5px; padding:.4rem .4rem 1rem; }
.brand .logo { width:30px; height:30px; border-radius:9px; background:linear-gradient(135deg,#6DBB7A,#c9e86c); display:inline-flex; align-items:center; justify-content:center; font-size:1rem; }
.user-chip { border:1px solid #eceff3; border-radius:14px; padding:.6rem .8rem; background:#fafcfb; font-size:.85rem; }
.user-chip b { display:block; font-size:.95rem; }
.card { background:#fff; border:1px solid #eceff3; border-radius:18px; padding:1rem 1.1rem; box-shadow:0 1px 2px rgba(16,24,40,.04); position:relative; overflow:hidden; margin-bottom:.9rem; }
.card.t-green { background:linear-gradient(135deg,#f3fbf4 0%,#fff 60%); }
.card.t-blue { background:linear-gradient(135deg,#eef6fd 0%,#fff 60%); }
.card.t-purple { background:linear-gradient(135deg,#f4f1fe 0%,#fff 60%); }
.card.t-peach { background:linear-gradient(135deg,#fdf3ea 0%,#fff 60%); }
.card.t-pink { background:linear-gradient(135deg,#fdeff3 0%,#fff 60%); }
.kpi { display:flex; justify-content:space-between; align-items:flex-start; gap:.6rem; }
.icon-tile { width:40px; height:40px; border-radius:12px; display:flex; align-items:center; justify-content:center; font-size:1.2rem; background:#fff; border:1px solid #eceff3; margin-bottom:.7rem; }
.kpi .label { color:#4b5563; font-size:.92rem; }
.kpi .value { font-size:1.9rem; font-weight:700; letter-spacing:-.5px; line-height:1.15; }
.kpi .value small { font-size:1.3rem; color:#6b7280; font-weight:400; }
.mini { display:flex; gap:.8rem; align-items:center; }
.mini .label { color:#6b7280; font-size:.9rem; }
.mini .value { font-size:1.35rem; font-weight:700; }
.ring { --p:0; --c:#6DBB7A; position:relative; width:64px; height:64px; border-radius:50%; flex:none;
  background:conic-gradient(var(--c) calc(var(--p)*1%), #eef1f4 0); display:flex; align-items:center; justify-content:center; }
.ring::before { content:""; position:absolute; inset:5px; background:#fff; border-radius:50%; }
.ring span { position:relative; font-size:.78rem; font-weight:600; }
.card-title { display:flex; align-items:center; gap:.6rem; margin-bottom:.4rem; }
.card-title .t { font-weight:600; font-size:1rem; line-height:1.2; }
.card-title .s { color:#6b7280; font-size:.78rem; }
.prow { margin:.65rem 0; }
.prow .top { display:flex; justify-content:space-between; font-size:.8rem; color:#374151; margin-bottom:.25rem; }
.bar { height:11px; border-radius:99px; background:#eef1f4; overflow:hidden; }
.bar > i { display:block; height:100%; border-radius:99px; }
.demo-banner { background:#fff7e6; border:1px solid #f5d28a; color:#7a4b00; padding:.5rem .9rem; border-radius:12px; font-size:.85rem; margin-bottom:.8rem; }
.empty { text-align:center; color:#6b7280; padding:1.6rem .5rem; }
.empty .big { font-size:2rem; }
.stTabs [data-baseweb="tab-list"] { gap:.3rem; }
.stTabs [aria-selected="true"] { background:linear-gradient(90deg,#8fcf82,#c9e86c); border-radius:10px; }
div[data-testid="stForm"] { background:#fff; border:1px solid #eceff3; border-radius:18px; }
.stButton > button, .stDownloadButton > button, [data-testid="stFormSubmitButton"] > button { border-radius:12px; }
@media (max-width: 760px) { .block-container { padding: .8rem .6rem; } .kpi .value { font-size:1.5rem; } .ring { width:52px; height:52px; } }
</style>
"""
HIDE_SIDEBAR = "<style>[data-testid='stSidebar'], [data-testid='collapsedControl'] { display:none; }</style>"


def inject_css(hide_sidebar: bool = False) -> None:
    st.markdown(CSS + (HIDE_SIDEBAR if hide_sidebar else ""), unsafe_allow_html=True)
