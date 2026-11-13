"""CSS and small HTML helpers for the Streamlit UI. Presentation only - no app logic.

Palette (all text pairs meet WCAG AA):
  navy text #1B2A41 on every background (>= 9:1), secondary #5A6B82 only on
  the light tints #EAF4FA / #F6FAFD / white (>= 4.8:1), never on #B9D9EB.
"""
from __future__ import annotations

import html

NAVY = "#1B2A41"
MUTED = "#5A6B82"
COLUMBIA = "#B9D9EB"
COLUMBIA_DARK = "#4A8DB7"
PAGE = "#EAF4FA"
CARD = "#F6FAFD"
YELLOW = "#FFE8A3"
YELLOW_STRONG = "#F5C842"

FAMILY_ICONS = {
    "African": "🍲",
    "American": "🍔",
    "Cafe/Bakery/Desserts": "🥐",
    "East Asian": "🥢",
    "European": "🍷",
    "Italian/Pizza": "🍝",
    "Jewish/Kosher": "🥯",
    "Latin American/Caribbean": "🌮",
    "Middle Eastern/Mediterranean": "🧆",
    "Sandwiches/Salads/Soups": "🥗",
    "Seafood": "🐟",
    "South Asian": "🍛",
    "Southeast Asian/Pacific": "🍜",
    "Vegetarian/Vegan": "🥦",
}


def icon_for(family: str) -> str:
    return FAMILY_ICONS.get(family, "🍽️")


def esc(v) -> str:
    return html.escape("" if v is None else str(v))


def chip(text: str, kind: str = "") -> str:
    """kind: '' (neutral), 'blue', 'yellow', 'muted'. Text is escaped."""
    return f"<span class='ra-chip {('ra-chip-' + kind) if kind else ''}'>{esc(text)}</span>"


CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600&display=swap');

/* ---------- base ---------- */
.stApp {{ background: {PAGE}; color: {NAVY}; font-family: 'Poppins', sans-serif; }}
.stApp h1, .stApp h2, .stApp h3 {{ font-weight: 600; color: {NAVY}; letter-spacing: -0.01em; }}
.block-container {{ padding-top: 2rem; padding-bottom: 3rem; max-width: 1200px; }}

/* hide default chrome (menu, deploy button, footer, top colour bar) */
#MainMenu, footer, [data-testid="stMainMenu"], [data-testid="stAppDeployButton"],
[data-testid="stDecoration"] {{ display: none !important; }}
[data-testid="stHeader"] {{ background: transparent; }}

/* ---------- hero ---------- */
.ra-hero {{ background: linear-gradient(135deg, {COLUMBIA} 0%, #D9ECF6 60%, {YELLOW} 140%);
            border-radius: 20px; padding: 1.8rem 2rem; margin-bottom: 1.5rem;
            box-shadow: 0 6px 20px rgba(27, 42, 65, 0.08); }}
.ra-hero h1 {{ margin: 0; padding: 0; font-size: clamp(1.5rem, 4vw, 2.2rem); font-weight: 600; color: {NAVY}; }}
.ra-hero p {{ margin: 0.4rem 0 0; color: {NAVY}; font-size: 1rem; }}

/* ---------- step headings ---------- */
.ra-step {{ display: flex; align-items: center; gap: 0.6rem; margin: 1.6rem 0 0.7rem; }}
.ra-step-num {{ width: 2rem; height: 2rem; border-radius: 50%; background: {COLUMBIA}; color: {NAVY};
                display: inline-flex; align-items: center; justify-content: center; font-weight: 600; flex: none; }}
.ra-step-title {{ font-size: 1.2rem; font-weight: 600; color: {NAVY}; }}

/* ---------- inputs & buttons ---------- */
[data-testid="stTextInput"] input {{ border-radius: 999px; padding: 0.75rem 1.25rem; font-size: 1.05rem; background: #FFFFFF; }}
[data-testid="stTextInput"] [data-baseweb="input"] {{ border-radius: 999px; box-shadow: 0 3px 12px rgba(27, 42, 65, 0.06); }}
.stButton button, [data-testid="stFormSubmitButton"] button, [data-testid="stDownloadButton"] button {{
    border-radius: 999px; font-weight: 600; transition: transform .15s ease, box-shadow .15s ease, background .15s ease; }}
.stButton button[kind="primary"], [data-testid="stFormSubmitButton"] button {{
    background: {COLUMBIA}; color: {NAVY} !important; border: 1px solid #A3CBE0; padding: 0.6rem 1.6rem; }}
.stButton button[kind="primary"] p, [data-testid="stFormSubmitButton"] button p {{ color: {NAVY} !important; }}
.stButton button[kind="primary"]:hover, [data-testid="stFormSubmitButton"] button:hover {{
    background: {YELLOW}; border-color: {YELLOW_STRONG}; transform: translateY(-1px);
    box-shadow: 0 6px 16px rgba(27, 42, 65, 0.12); }}
/* secondary buttons: keep navy text on hover/focus (accent-coloured text would be < 4.5:1) */
.stButton button:hover, .stButton button:focus, [data-testid="stDownloadButton"] button:hover,
[data-testid="stDownloadButton"] button:focus {{ color: {NAVY} !important; border-color: {COLUMBIA_DARK}; }}
.stButton button:hover p, [data-testid="stDownloadButton"] button:hover p {{ color: {NAVY} !important; }}

/* ---------- match choices: radio options rendered as cards ---------- */
.st-key-match-cards [role="radiogroup"] {{ gap: 0.6rem; width: 100%; }}
.st-key-match-cards [role="radiogroup"] > label {{
    width: 100%; background: {CARD}; border: 1px solid #D3E5F0; border-radius: 16px;
    padding: 0.85rem 1rem; margin: 0; box-shadow: 0 2px 8px rgba(27, 42, 65, 0.05);
    transition: border-color .15s ease, box-shadow .15s ease, background .15s ease; cursor: pointer; }}
.st-key-match-cards [role="radiogroup"] > label:hover {{ border-color: {COLUMBIA_DARK}; box-shadow: 0 6px 16px rgba(27, 42, 65, 0.10); }}
.st-key-match-cards [role="radiogroup"] > label:has(input:checked) {{ background: #FFFFFF; border: 2px solid {COLUMBIA_DARK}; }}

/* ---------- generic card ---------- */
.ra-card {{ display: flex; gap: 0.9rem; align-items: flex-start; background: {CARD};
            border: 1px solid #D3E5F0; border-radius: 18px; padding: 1rem 1.1rem; margin-bottom: 0.85rem;
            box-shadow: 0 3px 12px rgba(27, 42, 65, 0.06); transition: transform .15s ease, box-shadow .15s ease, border-color .15s ease;
            overflow-wrap: anywhere; }}
.ra-card:hover {{ transform: translateY(-2px); border-color: {COLUMBIA_DARK}; box-shadow: 0 10px 24px rgba(27, 42, 65, 0.12); background: #FFFFFF; }}
.ra-card-static:hover {{ transform: none; }}
.ra-body {{ flex: 1; min-width: 0; }}
.ra-circle {{ width: 2.5rem; height: 2.5rem; border-radius: 50%; flex: none; display: inline-flex;
              align-items: center; justify-content: center; font-weight: 600; font-size: 1.05rem; }}
.ra-rank {{ background: {COLUMBIA}; color: {NAVY}; }}
.ra-icon {{ background: {YELLOW}; }}
.ra-name {{ font-weight: 600; font-size: 1.05rem; color: {NAVY}; margin-right: 0.4rem; }}
.ra-address {{ color: {MUTED}; font-size: 0.88rem; margin: 0.1rem 0 0.45rem; }}
.ra-why {{ color: {NAVY}; font-size: 0.92rem; line-height: 1.5; margin-top: 0.5rem; }}
.ra-why b {{ font-weight: 600; }}
.ra-kicker {{ color: {MUTED}; font-size: 0.78rem; font-weight: 500; text-transform: uppercase; letter-spacing: 0.06em; }}

/* ---------- chips & badges ---------- */
.ra-chips {{ display: flex; flex-wrap: wrap; gap: 0.4rem; }}
.ra-chip {{ display: inline-block; padding: 0.18rem 0.7rem; border-radius: 999px; font-size: 0.8rem; font-weight: 500;
            background: #FFFFFF; color: {NAVY}; border: 1px solid #D3E5F0; white-space: nowrap; }}
.ra-chip-blue {{ background: {COLUMBIA}; border-color: {COLUMBIA}; }}
.ra-chip-yellow {{ background: {YELLOW}; border-color: {YELLOW_STRONG}; }}
.ra-chip-muted {{ background: {PAGE}; color: {MUTED}; }}
.ra-mode {{ display: inline-flex; align-items: center; gap: 0.35rem; padding: 0.2rem 0.75rem; border-radius: 999px;
            font-size: 0.8rem; font-weight: 500; background: #FFFFFF; border: 1px dashed #A3CBE0; color: {NAVY}; cursor: help; }}
.ra-mode-on {{ border-style: solid; background: {YELLOW}; border-color: {YELLOW_STRONG}; }}

/* ---------- preferences panel ---------- */
.st-key-prefs {{ background: {CARD}; border: 1px solid #D3E5F0; border-radius: 20px; padding: 1.2rem 1.4rem;
                 box-shadow: 0 3px 12px rgba(27, 42, 65, 0.06); }}
.st-key-prefs [data-testid="stForm"] {{ border: none; padding: 0; }}
.ra-group {{ font-weight: 600; font-size: 0.95rem; margin-bottom: 0.2rem; }}
.ra-note {{ color: {MUTED}; font-size: 0.82rem; margin: 0 0 0.4rem; }}

/* ---------- alerts as soft cards; the notice pill stays small ---------- */
[data-testid="stAlert"] {{ border-radius: 16px; }}
[data-testid="stAlert"] > div {{ border-radius: 16px; color: {NAVY}; }}
.st-key-notices [data-testid="stAlert"] > div {{ padding: 0.35rem 0.9rem; font-size: 0.85rem; min-height: 0;
                                                  background: #FFFFFF; border: 1px solid #D3E5F0; border-radius: 999px; }}
.st-key-notices [data-testid="stAlert"] p {{ font-size: 0.85rem; }}
.st-key-notices {{ gap: 0.35rem; }}

/* ---------- small print ---------- */
[data-testid="stCaptionContainer"] {{ color: {MUTED}; }}
[data-testid="stExpander"] details {{ border-radius: 16px; background: {CARD}; }}
[data-testid="stSidebar"] {{ background: {CARD}; }}

/* ---------- phones ---------- */
@media (max-width: 640px) {{
  .block-container {{ padding-left: 0.9rem; padding-right: 0.9rem; padding-top: 1rem; }}
  .ra-hero {{ padding: 1.2rem 1.2rem; }}
  .ra-card {{ padding: 0.85rem; gap: 0.6rem; }}
  .ra-circle {{ width: 2.1rem; height: 2.1rem; font-size: 0.95rem; }}
  .st-key-prefs {{ padding: 0.9rem; }}
}}
</style>
"""
