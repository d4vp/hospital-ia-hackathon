"""Custom loading indicator: an ambulance driving to the hospital.

Usage (replaces st.spinner in slow operations):

    with ambulance_loader(t("thinking")):
        response = api_client.post("/chat", payload)

Pure inline SVG + CSS (no external assets). Colours come from the active theme variables,
so it works in light, dark and high-contrast modes. It is announced to screen readers
(role="status", aria-live) and, with `prefers-reduced-motion`, the global theme rule stops
the animation and a static ambulance is shown instead.
"""
from contextlib import contextmanager
from html import escape
from typing import Iterator, Optional

import streamlit as st

from core.i18n import t

_SVG = """
<svg class="amb-scene" viewBox="0 0 340 90" width="340" height="90" aria-hidden="true" focusable="false">
  <line class="amb-road" x1="0" y1="78" x2="340" y2="78"/>
  <g class="amb-hospital" transform="translate(262 18)">
    <rect x="0" y="12" width="66" height="48" rx="3"/>
    <rect class="amb-hospital-top" x="18" y="0" width="30" height="14" rx="2"/>
    <rect class="amb-cross" x="31" y="2" width="4" height="10"/><rect class="amb-cross" x="28" y="5" width="10" height="4"/>
    <rect class="amb-window" x="8" y="22" width="12" height="10"/><rect class="amb-window" x="46" y="22" width="12" height="10"/>
    <rect class="amb-door" x="25" y="40" width="16" height="20"/>
  </g>
  <g class="amb-vehicle">
    <rect class="amb-body" x="0" y="40" width="60" height="30" rx="4"/>
    <path class="amb-body" d="M60 48h14l10 11v11H60z"/>
    <path class="amb-glass" d="M64 51h9l7 8H64z"/>
    <rect class="amb-stripe" x="0" y="58" width="84" height="4"/>
    <path class="amb-red" d="M24 47h8v6h6v8h-6v6h-8v-6h-6v-8h6z"/>
    <rect class="amb-siren" x="16" y="34" width="12" height="6" rx="2"/>
    <!-- position (outer) and rotation (inner) on different elements: they must not interfere -->
    <g transform="translate(16 72)"><g class="amb-wheel"><circle r="8"/><line x1="-6" y1="0" x2="6" y2="0"/><line x1="0" y1="-6" x2="0" y2="6"/></g></g>
    <g transform="translate(66 72)"><g class="amb-wheel"><circle r="8"/><line x1="-6" y1="0" x2="6" y2="0"/><line x1="0" y1="-6" x2="0" y2="6"/></g></g>
  </g>
</svg>
"""

_CSS = """
<style>
.amb-loader { display:flex; align-items:center; gap:1rem; flex-wrap:wrap; padding:.8rem 1rem; margin:.4rem 0;
              border:1px solid var(--border); border-radius:12px; background: var(--surface); }
.amb-loader p { margin:0; font-weight:700; color: var(--ink); }
.amb-scene { max-width:100%; height:auto; overflow:visible; }
.amb-road { stroke: var(--muted); stroke-width:3; stroke-dasharray: 14 10; animation: amb-road .6s linear infinite; }
.amb-hospital rect { fill: var(--surface); stroke: var(--primary); stroke-width:2.5; }
.amb-hospital .amb-hospital-top { fill: var(--primary); }
.amb-hospital .amb-cross { fill: var(--on-primary); stroke: none; }
.amb-hospital .amb-window { fill: var(--accent); stroke: none; }
.amb-hospital .amb-door { fill: var(--primary); stroke: none; }
.amb-vehicle { animation: amb-drive 2.8s cubic-bezier(.45,.05,.55,.95) infinite; }
.amb-body { fill: #FFFFFF; stroke: var(--ink); stroke-width:2; }
.amb-glass { fill: #8FD3FF; stroke: var(--ink); stroke-width:1.5; }
.amb-stripe { fill: #D92D20; }
.amb-red { fill: #D92D20; }
.amb-siren { fill: #D92D20; animation: amb-siren .5s steps(2) infinite; }
.amb-wheel circle { fill: #2B2B2B; stroke: var(--ink); stroke-width:1.5; }
.amb-wheel line { stroke: #CFCFCF; stroke-width:2; }
.amb-wheel { animation: amb-spin .45s linear infinite; transform-box: fill-box; transform-origin: 50% 50%; }
@keyframes amb-drive { 0% { transform: translateX(-10px); opacity:0; } 10% { opacity:1; }
                       80% { transform: translateX(168px); opacity:1; } 100% { transform: translateX(176px); opacity:0; } }
@keyframes amb-spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
@keyframes amb-siren { 0% { fill:#D92D20; } 100% { fill:#1E6FD9; } }
@keyframes amb-road { to { stroke-dashoffset: -24; } }
</style>
"""


def loader_html(message: str) -> str:
    return (f'{_CSS}<div class="amb-loader" role="status" aria-live="polite">{_SVG}'
            f'<p>{escape(message)}</p></div>')


@contextmanager
def ambulance_loader(message: Optional[str] = None) -> Iterator[None]:
    """Shows the animated loader while the block runs, then removes it."""
    placeholder = st.empty()
    placeholder.markdown(loader_html(message or t("loading")), unsafe_allow_html=True)
    try:
        yield
    finally:
        placeholder.empty()
