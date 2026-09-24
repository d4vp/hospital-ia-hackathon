"""Accessible visual themes: light, dark and high contrast.

Text size follows the browser / OS zoom (no in-app font scaling, which broke layouts).

Typography: Atkinson Hyperlegible (Braille Institute) — designed to maximise character
recognition for readers with low vision. Palette derived from the hospital's greens.
Charts use the Okabe-Ito colour-blind-safe palette (and a yellow/cyan/white set in
high-contrast mode). Every colour pair used for text meets WCAG AA (4.5:1) or better.
"""
import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

PALETTES = {
    "light": {
        "bg": "#F4F7F5", "surface": "#FFFFFF", "ink": "#16211C", "muted": "#46554E",
        "primary": "#1F5F4A", "on_primary": "#FFFFFF", "accent": "#4E8A1F", "border": "#C9D5CF",
        "critical": "#B42318", "high": "#B54708", "medium": "#8A6100", "focus": "#1F5F4A",
        "critical_bg": "#FDECEA", "high_bg": "#FEF0E6", "medium_bg": "#FBF3DC",
        "chart": ["#0072B2", "#E69F00", "#009E73", "#CC79A7", "#56B4E9", "#D55E00", "#6B6B6B", "#F0E442"],
    },
    "dark": {
        "bg": "#0F1714", "surface": "#18231F", "ink": "#E9F0EC", "muted": "#AAB9B2",
        "primary": "#7CC7A6", "on_primary": "#0B1411", "accent": "#A5D86E", "border": "#2F403A",
        "critical": "#FF8A7F", "high": "#FFB570", "medium": "#F5D06B", "focus": "#A5D86E",
        "critical_bg": "#3A1714", "high_bg": "#3A2513", "medium_bg": "#353013",
        "chart": ["#56B4E9", "#E69F00", "#2EC4A0", "#E08BC3", "#F0E442", "#FF7F50", "#C8C8C8", "#8FD3FF"],
    },
    "high_contrast": {
        "bg": "#000000", "surface": "#000000", "ink": "#FFFFFF", "muted": "#FFFFFF",
        "primary": "#FFE600", "on_primary": "#000000", "accent": "#00E5FF", "border": "#FFFFFF",
        "critical": "#FF7B7B", "high": "#FFB347", "medium": "#FFE600", "focus": "#00E5FF",
        "critical_bg": "#000000", "high_bg": "#000000", "medium_bg": "#000000",
        "chart": ["#FFE600", "#00E5FF", "#FFFFFF", "#FF7B7B", "#9CFF57", "#FFB347", "#D59BFF", "#8FD3FF"],
    },
}


def palette() -> dict:
    return PALETTES.get(st.session_state.get("theme", "light"), PALETTES["light"])


THEMES = ("light", "dark", "high_contrast")


def apply_theme() -> None:
    p = palette()
    hc = st.session_state.get("theme") == "high_contrast"
    border_w = "2px" if hc else "1px"
    st.markdown(
        f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible:ital,wght@0,400;0,700;1,400&display=swap');
:root {{ --bg:{p['bg']}; --surface:{p['surface']}; --ink:{p['ink']}; --muted:{p['muted']};
         --primary:{p['primary']}; --on-primary:{p['on_primary']}; --accent:{p['accent']};
         --border:{p['border']}; --focus:{p['focus']}; }}
html, body, .stApp, .stApp * {{ font-family: 'Atkinson Hyperlegible', 'Segoe UI', Roboto, Arial, sans-serif; }}
.stApp [data-testid="stIconMaterial"], .stApp .material-symbols-rounded {{ font-family: 'Material Symbols Rounded' !important; }}
.stApp {{ background: var(--bg); color: var(--ink); }}
[data-testid="stHeader"] {{ background: var(--bg); }}
[data-testid="stSidebar"], [data-testid="stSidebar"] > div {{ background: var(--surface); border-right: {border_w} solid var(--border); }}
.stApp p, .stApp li, .stApp label, .stApp span, .stApp div, .stApp h1, .stApp h2, .stApp h3, .stApp h4,
.stMarkdown, [data-testid="stCaptionContainer"] {{ color: var(--ink); }}
.stApp p, .stApp li, .stApp label {{ font-size: 1rem; line-height: 1.55; }}
[data-testid="stCaptionContainer"], .muted {{ color: var(--muted) !important; }}
h1 {{ font-size: 2rem; font-weight: 700; letter-spacing: -0.01em; }}
h2 {{ font-size: 1.45rem; font-weight: 700; }}
h3 {{ font-size: 1.15rem; font-weight: 700; }}
.block-container {{ max-width: 1280px; padding-top: 2.2rem; }}

/* Inputs */
[data-baseweb="input"] > div, [data-baseweb="base-input"], [data-baseweb="select"] > div,
[data-baseweb="textarea"], .stTextInput input, .stTextArea textarea, [data-testid="stChatInput"] textarea,
[data-testid="stChatInput"] > div {{
  background: var(--surface) !important; color: var(--ink) !important; border-color: var(--border) !important;
}}
[data-baseweb="popover"] li, [data-baseweb="menu"] {{ background: var(--surface) !important; color: var(--ink) !important; }}

/* Buttons */
.stButton > button, [data-testid="stFormSubmitButton"] > button, [data-testid="stDownloadButton"] > button {{
  background: var(--surface); color: var(--ink); border: {border_w} solid var(--border); border-radius: 8px;
  min-height: 2.75rem; font-weight: 700;
}}
.stButton > button[kind="primary"], [data-testid="stFormSubmitButton"] > button {{
  background: var(--primary); color: var(--on-primary); border-color: var(--primary);
}}
.stButton > button[kind="primary"] p, [data-testid="stFormSubmitButton"] > button p {{ color: var(--on-primary); }}

/* Keyboard focus always visible (WCAG 2.4.7) */
.stApp *:focus-visible {{ outline: 3px solid var(--focus) !important; outline-offset: 2px !important; }}
.stApp a {{ color: var(--primary); text-decoration: underline; text-underline-offset: 3px; }}

/* Metrics */
[data-testid="stMetric"] {{ background: var(--surface); border: {border_w} solid var(--border); border-radius: 10px;
                            padding: 0.9rem 1rem; }}
[data-testid="stMetricLabel"] p {{ color: var(--muted) !important; font-size: 0.95rem; }}
[data-testid="stMetricValue"] {{ color: var(--ink); font-size: 2rem; font-weight: 700; }}

/* Containers, expanders, tabs, chat */
[data-testid="stExpander"] details, [data-testid="stVerticalBlockBorderWrapper"] {{ border-color: var(--border) !important; }}
[data-testid="stExpander"] summary {{ background: var(--surface); }}
.stTabs [data-baseweb="tab"] p {{ font-size: 1rem; font-weight: 700; }}
.stTabs [aria-selected="true"] {{ border-bottom: 3px solid var(--primary); }}
[data-testid="stChatMessage"] {{ background: var(--surface); border: {border_w} solid var(--border); border-radius: 10px; }}
[data-testid="stTable"] table, [data-testid="stTable"] th, [data-testid="stTable"] td {{
  color: var(--ink); border-color: var(--border) !important; background: var(--surface); font-size: 0.95rem; }}

/* Page header with vector icon */
.page-header {{ display:flex; align-items:center; gap:.75rem; margin: 0 0 .25rem 0; }}
.page-header svg {{ flex: 0 0 auto; color: var(--primary); }}
.page-header h1 {{ margin: 0; padding: 0; }}
.brand {{ display:flex; align-items:center; gap:.6rem; margin-bottom:.5rem; }}
.brand strong {{ font-size: 1.05rem; line-height:1.2; }}
.brand small {{ display:block; color: var(--muted); }}

/* Alerts (text label + icon + colour: never colour alone) */
.alert-card {{ border-left: 6px solid var(--sev); background: var(--sev-bg); border-radius: 8px;
               padding: .8rem 1rem; margin: 0 0 .6rem 0; border-top: {border_w} solid var(--border);
               border-right: {border_w} solid var(--border); border-bottom: {border_w} solid var(--border); }}
.alert-card .sev {{ display:inline-flex; align-items:center; gap:.35rem; font-weight:700; color: var(--sev); }}
.alert-card p {{ margin: .25rem 0 0 0; }}
.alert-card .rec {{ color: var(--muted); }}
.sev-critical {{ --sev: {p['critical']}; --sev-bg: {p['critical_bg']}; }}
.sev-high {{ --sev: {p['high']}; --sev-bg: {p['high_bg']}; }}
.sev-medium {{ --sev: {p['medium']}; --sev-bg: {p['medium_bg']}; }}
.wf-badge {{ display:inline-block; margin-left:.5rem; padding:.05rem .55rem; border-radius:999px; font-size:.85rem;
             font-weight:700; border:{border_w} solid var(--border); color: var(--ink); background: var(--surface); }}
.wf-new {{ border-color: var(--sev, var(--primary)); }}
.wf-in_progress {{ border-color: var(--primary); color: var(--primary); }}

/* Floating accessibility menu (the app's ONLY popover): small pill at the top-right corner */
[data-testid="stPopover"] {{ position: fixed; top: .55rem; right: 3.6rem; z-index: 999991; width: auto !important; }}
[data-testid="stPopover"] > div > button, [data-testid="stPopoverButton"] {{
  min-height: 2.2rem; padding: .15rem .8rem; border-radius: 999px; font-size: .9rem;
  background: var(--surface); color: var(--ink); border: {border_w} solid var(--border);
  box-shadow: 0 2px 8px rgba(0,0,0,.12); }}
[data-testid="stPopoverBody"] {{ background: var(--surface) !important; color: var(--ink); min-width: 15rem; }}

/* Alert ticker: counters + horizontally sliding list (pauses on hover/focus, scrollable by hand) */
.alert-ticker {{ display:flex; align-items:center; gap:.6rem; background: var(--surface);
                 border:{border_w} solid var(--border); border-radius:10px; padding:.45rem .6rem; margin:.2rem 0 .8rem 0;
                 min-height: 2.9rem; }}
.alert-ticker .counts {{ display:flex; gap:.4rem; flex:0 0 auto; }}
.alert-ticker .count {{ display:inline-flex; align-items:center; gap:.3rem; padding:.15rem .6rem; border-radius:999px;
                        font-weight:700; color: var(--sev); background: var(--sev-bg); border:{border_w} solid var(--sev);
                        white-space:nowrap; font-size:.92rem; }}
.alert-ticker .count.zero {{ opacity:.55; }}
.alert-ticker .track {{ flex:1 1 auto; overflow-x:auto; overflow-y:hidden; white-space:nowrap; scrollbar-width:thin;
                        mask-image: linear-gradient(90deg, transparent 0, #000 1.2rem, #000 calc(100% - 1.2rem), transparent 100%); }}
.alert-ticker .items {{ display:inline-flex; gap:.5rem; padding: 0 1rem; }}
.alert-ticker .items.moving {{ animation: ticker var(--ticker-duration, 40s) linear infinite; }}
.alert-ticker .track:hover .items, .alert-ticker .track:focus-within .items, .alert-ticker .track:focus .items {{
  animation-play-state: paused; }}
.alert-ticker .item {{ display:inline-flex; align-items:center; gap:.35rem; padding:.15rem .6rem; border-radius:8px;
                       border-left:4px solid var(--sev); background: var(--sev-bg); font-size:.92rem; }}
.alert-ticker .item b {{ color: var(--sev); }}
.alert-ticker .item .st {{ color: var(--muted); font-size:.82rem; }}
.alert-ticker .empty {{ color: var(--muted); }}
@keyframes ticker {{ from {{ transform: translateX(0); }} to {{ transform: translateX(-50%); }} }}
.conclusion {{ background: var(--surface); border: {border_w} solid var(--border); border-left: 5px solid var(--primary);
               border-radius: 8px; padding: .8rem 1rem; margin: .4rem 0 .8rem 0; max-width: 78ch; }}

/* Responsive: phones and small tablets */
@media (max-width: 640px) {{
  .block-container {{ padding: 3.8rem .8rem 4rem .8rem; }}
  h1 {{ font-size: 1.55rem; }}
  [data-testid="stMetricValue"] {{ font-size: 1.5rem; }}
  .page-header svg {{ width: 26px; height: 26px; }}
  [data-testid="stPopover"] {{ right: 3.2rem; }}
  .alert-ticker {{ flex-wrap: wrap; }}
  .alert-ticker .track {{ flex-basis: 100%; }}
}}
@media (prefers-reduced-motion: reduce) {{ .stApp * {{ animation: none !important; transition: none !important; }} }}
</style>
""",
        unsafe_allow_html=True,
    )
    _register_plotly_template(p)


def _register_plotly_template(p: dict) -> None:
    template = go.layout.Template()
    template.layout = go.Layout(
        font={"family": "Atkinson Hyperlegible, Segoe UI, Arial, sans-serif", "size": 14, "color": p["ink"]},
        paper_bgcolor=p["surface"], plot_bgcolor=p["surface"], colorway=p["chart"],
        xaxis={"gridcolor": p["border"], "linecolor": p["ink"], "zerolinecolor": p["border"], "automargin": True},
        yaxis={"gridcolor": p["border"], "linecolor": p["ink"], "zerolinecolor": p["border"], "automargin": True},
        legend={"orientation": "h", "yanchor": "top", "y": -0.18, "x": 0},
        margin={"l": 10, "r": 10, "t": 50, "b": 10},
        hoverlabel={"font": {"size": 14}},
        title={"font": {"size": 17}},
    )
    pio.templates["hospital"] = template
    pio.templates.default = "hospital"


def uses_html_tables() -> bool:
    """The canvas data grid ignores CSS, so dark/high-contrast modes use HTML tables."""
    return st.session_state.get("theme", "light") != "light"
