"""Streamlit UI: name search -> confirm reference -> preferences -> Find alternatives.

Yelp is called only inside the "Find alternatives" click handler (never on
keystrokes or reruns). Results are kept in session_state so reruns redraw them
without new calls. @st.cache_resource is used only for the directory and the
DB connection; Yelp data is never cached by Streamlit.
Presentation (CSS, chips, icons) lives in ui/styles.py.
"""
from __future__ import annotations

import io
import re
import sqlite3
import tempfile
from pathlib import Path

import pandas as pd
import pydeck as pdk
import streamlit as st

import data_store as ds
import recommender as rec
import yelp_client as yc
from name_matching import NameMatcher
from ui import styles as ui
from ui.styles import chip, esc

PRICE_LABELS = {1: "$", 2: "$$", 3: "$$$", 4: "$$$$"}
LOCAL_MODE_TIP = ("Ratings, review counts and price come from Yelp. Without Yelp, alternatives "
                  "are ranked on cuisine and distance only.")

st.set_page_config(page_title="Manhattan Restaurant Alternatives", page_icon="🍽️",
                   layout="wide", initial_sidebar_state="collapsed")
st.markdown(ui.CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------- resources

@st.cache_resource
def load_directory():
    """Directory DataFrame plus its name index (built once from the CSVs)."""
    directory = ds.load_directory()
    return directory, NameMatcher(directory, ds.load_aliases())


@st.cache_resource
def get_connection():
    """Runtime SQLite DB; falls back to a temp dir if data/cache is not writable."""
    try:
        return ds.connect(ds.DEFAULT_DB_PATH)
    except (OSError, sqlite3.Error):
        return ds.connect(Path(tempfile.gettempdir()) / "restaurant_alternatives.sqlite")


def get_client(conn) -> yc.YelpClient:
    if "yelp_client" not in st.session_state:
        st.session_state.yelp_client = yc.YelpClient(conn, yc.YelpSettings.load())
    return st.session_state.yelp_client


def known(v) -> bool:
    return not (v is None or (isinstance(v, float) and pd.isna(v)))


def md_safe(text) -> str:
    """Escape Markdown control characters for widget labels."""
    return re.sub(r"([\\*_\[\]`#<>|~])", r"\\\1", str(text))


# ---------------------------------------------------------------- small UI pieces

def step(n: int, title: str) -> None:
    st.markdown(f"<div class='ra-step'><span class='ra-step-num'>{n}</span>"
                f"<span class='ra-step-title'>{esc(title)}</span></div>", unsafe_allow_html=True)


def card(body_html: str, left_html: str = "", static: bool = True) -> None:
    cls = "ra-card ra-card-static" if static else "ra-card"
    st.markdown(f"<div class='{cls}'>{left_html}<div class='ra-body'>{body_html}</div></div>",
                unsafe_allow_html=True)


def empty_state(icon: str, title: str, suggestion: str) -> None:
    card(f"<div class='ra-name'>{esc(title)}</div><div class='ra-why'>{esc(suggestion)}</div>",
         f"<span class='ra-circle ra-icon'>{icon}</span>")


def mode_badge(yelp_ready: bool, reason: str | None) -> str:
    if yelp_ready:
        return ("<span class='ra-mode ra-mode-on' title='Ratings, reviews and price are fetched from "
                "Yelp when you click Find alternatives.'>● Yelp data on</span>")
    return f"<span class='ra-mode' title='{esc(reason or '')} {esc(LOCAL_MODE_TIP)}'>○ Local data mode ⓘ</span>"


# ---------------------------------------------------------------- Yelp status / sidebar

def yelp_status(client: yc.YelpClient) -> tuple[bool, str | None]:
    """(yelp_ready, reason_if_not)."""
    s = client.settings
    if not s.api_key or s.api_key.startswith("your-"):
        return False, "No Yelp API key is configured."
    if not s.enabled:
        return False, "Yelp is switched off (YELP_ENABLED=false)."
    if client.budget_used_up():
        return False, f"{yc.BUDGET_USED_UP_MESSAGE}."
    return True, None


def data_sources_panel(conn, client: yc.YelpClient, yelp_ready: bool, reason: str | None) -> None:
    s = client.settings
    with st.sidebar:
        st.markdown("### Data sources")
        st.markdown(mode_badge(yelp_ready, reason), unsafe_allow_html=True)
        st.caption("Directory: NYC DOHMH Restaurant Inspection Results (Manhattan)." +
                   ("" if yelp_ready else f" {reason} {LOCAL_MODE_TIP}"))
        st.caption(
            f"Yelp calls — this session: {client.session_calls}; "
            f"today (UTC): {ds.get_daily_calls(conn)}/{s.daily_limit}; "
            f"total: {ds.get_total_calls(conn)}/{s.total_limit}."
        )
        with st.expander("Yelp ID mappings"):
            buf = io.StringIO()
            rows = conn.execute("SELECT nyc_id, yelp_id, matched_at, method FROM mappings ORDER BY nyc_id").fetchall()
            pd.DataFrame([tuple(r) for r in rows], columns=ds.MAPPING_COLUMNS).to_csv(buf, index=False)
            st.download_button("Export mappings CSV", buf.getvalue(), "mappings.csv", "text/csv")
            up = st.file_uploader("Import mappings CSV", type="csv")
            if up is not None and st.button("Import"):
                tmp = Path(tempfile.gettempdir()) / "mappings_import.csv"
                tmp.write_bytes(up.getvalue())
                try:
                    n = ds.import_mappings_csv(conn, tmp)
                    st.success(f"Imported {n} mappings.")
                except ValueError as e:
                    st.error(str(e))
                finally:
                    tmp.unlink(missing_ok=True)


# ---------------------------------------------------------------- step 1: reference

def choose_reference(matcher: NameMatcher) -> dict | None:
    step(1, "Find your restaurant")
    query = st.text_input("Restaurant name", placeholder="e.g. Don Angie", key="query",
                          label_visibility="collapsed")
    if not query.strip():
        st.session_state.pop("reference_id", None)
        st.caption("Type the name of a Manhattan restaurant you like. Small typos are fine.")
        return None

    match = matcher.match(query)
    if match.is_empty:
        st.info(f"{match.message} Tip: leave out words like “NYC” or “restaurant”.", icon="🔎")
        st.session_state.pop("reference_id", None)
        return None

    st.caption(match.message)
    ids = [c.nyc_id for c in match.candidates]
    labels = [
        f"**{md_safe(c.name)}** · {md_safe(c.address)}, {md_safe(c.zip)}  "
        f":blue-background[{md_safe(c.cuisine or 'cuisine not specified')}]"
        for c in match.candidates
    ]
    # only a single exact/alias hit is pre-selected; fuzzy hits and branches never are
    with st.container(key="match-cards"):
        choice = st.radio(
            "Choose the restaurant", range(len(ids)), format_func=lambda i: labels[i],
            index=None if match.needs_choice else 0, key=f"choice::{match.query}",
            label_visibility="collapsed",
        )
    if choice is None:
        st.session_state.pop("reference_id", None)
        return None

    nyc_id = ids[choice]
    if st.session_state.get("reference_id") != nyc_id:
        st.session_state.reference_id = nyc_id
        st.session_state.pop("result", None)
    return matcher.get(nyc_id)


def show_reference(row: dict) -> None:
    chips = (chip(row["cuisine"] or "cuisine not specified", "blue") +
             chip(row["cuisine_family"] or "family not specified", "yellow"))
    card(
        "<div class='ra-kicker'>Your reference</div>"
        f"<div class='ra-name'>{esc(row['name'])}</div>"
        f"<div class='ra-address'>{esc(row['address'])}, {esc(row['zip'])}</div>"
        f"<div class='ra-chips'>{chips}</div>",
        f"<span class='ra-circle ra-icon'>{ui.icon_for(row['cuisine_family'])}</span>",
    )


# ---------------------------------------------------------------- step 2: preferences

def preferences(directory: pd.DataFrame, ref: dict, yelp_ready: bool, yelp_reason: str | None):
    """Returns Filters when Find alternatives is clicked, else None."""
    step(2, "Set preferences")
    cuisines = sorted(set(directory[directory["cuisine_family"] == ref["cuisine_family"]]["cuisine"]))
    off = not yelp_ready
    with st.container(key="prefs"):
        st.markdown(mode_badge(yelp_ready, yelp_reason), unsafe_allow_html=True)
        with st.form("filters", border=False):
            left, right = st.columns(2, gap="large")
            with left:
                st.markdown("<div class='ra-group'>Cuisine & distance</div>", unsafe_allow_html=True)
                cuisine = st.selectbox(
                    "Cuisine", ["Any"] + cuisines,
                    help="Only cuisines in the same family as your reference can be similar.")
                use_distance = st.checkbox("Limit distance", help="Only show places within the distance below.")
                max_km = st.slider("Max distance (km)", 0.5, 15.0, 3.0, 0.5,
                                   help="Straight-line distance from your reference. Used only if 'Limit distance' is on.")
            with right:
                st.markdown("<div class='ra-group'>Rating, reviews & price</div>", unsafe_allow_html=True)
                if off:
                    st.markdown("<p class='ra-note'>Needs Yelp data — not available right now.</p>",
                                unsafe_allow_html=True)
                min_rating = st.slider("Min rating", 1.0, 5.0, 4.0, 0.5, disabled=off,
                                       help="Lowest Yelp star rating to include.")
                min_reviews = st.number_input("Min reviews", 0, 100000, 20, 5, disabled=off,
                                              help="Fewest Yelp reviews a place must have.")
                use_ratio = st.checkbox("Limit reviews vs. reference", True, disabled=off,
                                        help="Favour lesser-known places: cap reviews at a share of your reference's count.")
                ratio_pct = st.slider("Max reviews vs. reference (%)", 5, 200, 50, 5, disabled=off,
                                      help="50% = at most half as many reviews as your reference.")
                abs_max = st.number_input("Max reviews (absolute)", min_value=1, value=None,
                                          placeholder="No limit", disabled=off,
                                          help="Optional hard cap on the number of reviews.")
                price = st.selectbox("Price cap", ["Any", "$", "$$", "$$$", "$$$$"], disabled=off,
                                     help="Most expensive Yelp price level to include.")
            clicked = st.form_submit_button("Find alternatives", type="primary")
    if not clicked:
        return None
    return rec.Filters(
        max_review_ratio=ratio_pct / 100 if use_ratio else None,
        min_reviews=int(min_reviews),
        min_rating=float(min_rating),
        absolute_max_reviews=int(abs_max) if abs_max is not None else None,
        max_distance_km=float(max_km) if use_distance else None,
        price_cap=None if price == "Any" else len(price),
        required_cuisine=None if cuisine == "Any" else cuisine,
    )


# ---------------------------------------------------------------- step 3: results

def result_card(i: int, c: dict, yelp_available: bool) -> None:
    same = c["similarity_score"] == rec.SAME_CUISINE
    badge = chip("Same cuisine" if same else "Related cuisine", "blue" if same else "yellow")
    chips = []
    if known(c["distance_km"]):
        chips.append(chip(f"📍 {c['distance_km']:.1f} km"))
    chips.append(chip(c["cuisine"]))
    if yelp_available and c.get("enriched"):
        if known(c["rating"]):
            chips.append(chip(f"★ {c['rating']:g}"))
        if known(c["review_count"]):
            chips.append(chip(f"{int(c['review_count']):,} reviews"))
        if known(c["price_level"]):
            chips.append(chip(PRICE_LABELS.get(int(c["price_level"]), "price n/a")))
    if not yelp_available:
        chips.append(chip("Local data", "muted"))
    elif c.get("data_fetched_at"):
        chips.append(chip(f"Yelp · {c['data_fetched_at'][:16].replace('T', ' ')} UTC", "muted"))
    else:
        chips.append(chip(f"Yelp {yc.NOT_ENRICHED}", "muted"))
    left = (f"<div style='display:flex;flex-direction:column;gap:0.4rem;align-items:center'>"
            f"<span class='ra-circle ra-rank'>{i}</span>"
            f"<span class='ra-circle ra-icon' title='{esc(c['cuisine_family'])}'>{ui.icon_for(c['cuisine_family'])}</span></div>")
    card(
        f"<div><span class='ra-name'>{esc(c['name'])}</span>{badge}</div>"
        f"<div class='ra-address'>{esc(c['address'])}, {esc(c['zip'])}</div>"
        f"<div class='ra-chips'>{''.join(chips)}</div>"
        f"<div class='ra-why'><b>Why it matches:</b> {esc(c['explanation'])}</div>",
        left, static=False,
    )


def show_map(ref: dict, results: list[dict]) -> None:
    points = [{"lat": c["lat"], "lon": c["lon"], "label": f"{i}. {c['name']}",
               "fill": [62, 127, 174, 220], "radius": 7}
              for i, c in enumerate(results, 1) if known(c.get("lat")) and known(c.get("lon"))]
    if known(ref.get("lat")) and known(ref.get("lon")):
        points.append({"lat": ref["lat"], "lon": ref["lon"], "label": f"Your reference: {ref['name']}",
                       "fill": [245, 200, 66, 255], "radius": 11})
    if not points:
        empty_state("🗺️", "No map for these results", "None of these restaurants has coordinates in the city data.")
        return
    df = pd.DataFrame(points)
    span = max(df["lat"].max() - df["lat"].min(), df["lon"].max() - df["lon"].min())
    zoom = 14 if span < 0.01 else 13 if span < 0.03 else 12 if span < 0.07 else 11
    layer = pdk.Layer(
        "ScatterplotLayer", df, get_position="[lon, lat]", get_fill_color="fill", get_radius="radius",
        radius_units="pixels", stroked=True, get_line_color=[27, 42, 65, 200], line_width_min_pixels=1,
        pickable=True,
    )
    deck = pdk.Deck(
        layers=[layer], map_provider="carto", map_style="light",
        initial_view_state=pdk.ViewState(latitude=float(df["lat"].mean()), longitude=float(df["lon"].mean()), zoom=zoom),
        tooltip={"text": "{label}"},
    )
    st.pydeck_chart(deck, height=440)
    st.caption("Yellow: your reference · Blue: alternatives. Places without coordinates are not shown.")


def _fmt_applied(key: str, v) -> str | None:
    names = {"required_cuisine": "Cuisine", "max_distance_km": "Max distance", "yelp_filters": "Yelp filters",
             "min_rating": "Min rating", "min_reviews": "Min reviews",
             "effective_max_reviews": "Max reviews", "price_cap": "Price cap"}
    if v is None:
        val = "any"
    elif key == "max_distance_km":
        val = f"{v:g} km"
    elif key == "price_cap":
        val = PRICE_LABELS.get(int(v), str(v))
    elif key == "min_rating":
        val = f"{v:g}★"
    else:
        val = str(v)
    return f"{names.get(key, key)}: {val}"


def show_applied(result: rec.RecommendationResult) -> None:
    chips = [chip(_fmt_applied(k, v)) for k, v in result.filters_applied.items()]
    if result.results and result.results[0]["tiers_skipped"]:
        chips += [chip(f"Ranking skips {k.replace('_', ' ')} ({v})", "muted")
                  for k, v in result.results[0]["tiers_skipped"].items()]
    st.markdown("<div class='ra-kicker' style='margin:0.6rem 0 0.35rem'>Filters applied</div>"
                f"<div class='ra-chips'>{''.join(chips)}</div>", unsafe_allow_html=True)


def show_notices(result: rec.RecommendationResult) -> None:
    notices = [n for n in dict.fromkeys(result.notices)
               if n != rec.YELP_FILTERS_DISABLED and n != result.empty_message]
    if not notices:
        return
    with st.container(key="notices"):
        for n in notices:
            if n == rec.YELP_UNAVAILABLE:
                st.info(f"Local data mode · {rec.YELP_UNAVAILABLE} — ranked on cuisine and distance only.", icon="ℹ️")
            else:
                st.info(n, icon="ℹ️")


def show_results(result: rec.RecommendationResult) -> None:
    step(3, "See alternatives")
    if result.error:
        empty_state("🤔", "We can't compare this one", result.error)
        return
    ref = result.reference
    if result.yelp_available and ref.get("data_fetched_at"):
        parts = [f"★ {ref['rating']:g}" if known(ref.get("rating")) else "rating n/a",
                 f"{int(ref['review_count']):,} reviews" if known(ref.get("review_count")) else "reviews n/a",
                 PRICE_LABELS.get(int(ref["price_level"]), "price n/a") if known(ref.get("price_level")) else "price n/a"]
        st.caption("Your reference on Yelp: " + " · ".join(parts))
    show_notices(result)
    if not result.results:
        empty_state("🔎", "No alternatives match these preferences",
                    result.empty_message or "Try removing the cuisine or distance limit.")
        show_applied(result)
        return
    st.markdown(f"**{len(result.results)} alternatives to {esc(ref['name'])}**")
    st.caption(result.disclaimer)
    left, right = st.columns([3, 2], gap="large")
    with left:
        for i, c in enumerate(result.results, 1):
            result_card(i, c, result.yelp_available)
    with right:
        show_map(ref, result.results)
        show_applied(result)


# ---------------------------------------------------------------- main

def main() -> None:
    st.markdown(
        "<div class='ra-hero'><h1>Manhattan Restaurant Alternatives</h1>"
        "<p>Name a restaurant you love and discover comparable places nearby, matched by cuisine and distance.</p></div>",
        unsafe_allow_html=True,
    )

    directory, matcher = load_directory()
    conn = get_connection()
    client = get_client(conn)
    yelp_ready, yelp_reason = yelp_status(client)

    ref = choose_reference(matcher)
    if ref is not None:
        show_reference(ref)
        if rec.is_unclassified(ref):
            empty_state("🤔", "We can't compare this one",
                        rec.UNSPECIFIED_MESSAGE + " Try another restaurant.")
        else:
            filters = preferences(directory, ref, yelp_ready, yelp_reason)
            if filters is not None:
                # the ONLY place Yelp may be called
                enricher = client.enrich if yelp_ready else None
                with st.spinner("Finding alternatives..."):
                    st.session_state.result = rec.recommend(directory, ref["nyc_id"], filters, enricher)
                st.session_state.result_ref = ref["nyc_id"]
            result = st.session_state.get("result")
            if result is not None and st.session_state.get("result_ref") == ref["nyc_id"]:
                show_results(result)
    data_sources_panel(conn, client, yelp_ready, yelp_reason)


main()
