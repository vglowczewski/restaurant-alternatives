"""Streamlit UI: name search -> confirm reference -> filters -> Find alternatives.

Yelp is called only inside the "Find alternatives" click handler (never on
keystrokes or reruns). Results are kept in session_state so reruns redraw them
without new calls. @st.cache_resource is used only for the directory and the
DB connection; Yelp data is never cached by Streamlit.
"""
from __future__ import annotations

import html
import io
import sqlite3
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

import data_store as ds
import recommender as rec
import yelp_client as yc
from name_matching import NameMatcher

PRICE_LABELS = {1: "$", 2: "$$", 3: "$$$", 4: "$$$$"}

st.set_page_config(page_title="Manhattan Restaurant Alternatives", page_icon="🍽️", layout="wide")

# Minimal styling: badges, the muted evidence line and the header subtitle.
st.markdown(
    """
<style>
.app-subtitle { color: #52606D; margin-top: -0.75rem; margin-bottom: 1.25rem; }
.badge { display: inline-block; padding: 0.1rem 0.55rem; border-radius: 999px;
         font-size: 0.75rem; font-weight: 600; vertical-align: middle; margin-left: 0.4rem; }
.badge-same { background: #E3F4EA; color: #1E7B45; }
.badge-related { background: #FFF1E0; color: #A85A00; }
.card-title { font-size: 1.05rem; font-weight: 600; }
.card-address { color: #616E7C; font-size: 0.85rem; }
.evidence { color: #616E7C; font-size: 0.8rem; margin-top: 0.25rem; }
</style>
""",
    unsafe_allow_html=True,
)


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


def fmt_missing(v, fmt=str, unknown="unavailable"):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return unknown
    return fmt(v)


def esc(v) -> str:
    return html.escape(str(v))


# ---------------------------------------------------------------- Yelp status

def yelp_status(client: yc.YelpClient) -> tuple[bool, str | None]:
    """(yelp_ready, reason_if_not)."""
    s = client.settings
    if not s.api_key or s.api_key.startswith("your-"):
        return False, "No Yelp API key is configured, so the app runs on local data only."
    if not s.enabled:
        return False, "Yelp is disabled (YELP_ENABLED=false), so the app runs on local data only."
    if client.budget_used_up():
        return False, f"{yc.BUDGET_USED_UP_MESSAGE}. The app runs on local data only."
    return True, None


def data_sources_panel(conn, client: yc.YelpClient, yelp_ready: bool, reason: str | None) -> None:
    s = client.settings
    with st.sidebar.expander("Data sources & Yelp", expanded=False):
        st.write("**Directory:** NYC DOHMH Restaurant Inspection Results (Manhattan).")
        if reason:
            st.warning(f"Yelp: off. {reason}")
        else:
            st.success("Yelp: on (called only when you click Find alternatives).")
        st.caption(
            f"Yelp calls — this session: {client.session_calls}; "
            f"today (UTC): {ds.get_daily_calls(conn)}/{s.daily_limit}; "
            f"total: {ds.get_total_calls(conn)}/{s.total_limit}."
        )
        st.markdown("**Yelp ID mappings**")
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


# ---------------------------------------------------------------- reference

def choose_reference(matcher: NameMatcher) -> dict | None:
    query = st.text_input("Restaurant name (Manhattan)", placeholder="e.g. Don Angie", key="query")
    if not query.strip():
        st.session_state.pop("reference_id", None)
        st.caption("Start by typing the name of a Manhattan restaurant you like.")
        return None

    match = matcher.match(query)
    if match.is_empty:
        st.info(match.message)
        st.session_state.pop("reference_id", None)
        return None

    st.write(match.message)
    labels = [f"{c.label} ({c.cuisine})" for c in match.candidates]
    ids = [c.nyc_id for c in match.candidates]
    # only a single exact/alias hit is pre-selected; fuzzy hits and branches never are
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
    with st.container(border=True):
        st.markdown(
            f"<div class='card-title'>Reference: {esc(row['name'])}</div>"
            f"<div class='card-address'>{esc(row['address'])}, {esc(row['zip'])}</div>"
            f"<div class='evidence'>Cuisine: {esc(row['cuisine'] or 'not specified')} · "
            f"Family: {esc(row['cuisine_family'] or 'not specified')}</div>",
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------- filters (sidebar)

def filter_form(directory: pd.DataFrame, ref: dict, yelp_ready: bool, yelp_reason: str | None):
    """Rendered in the sidebar. Returns Filters when Find alternatives is clicked, else None."""
    family = directory[directory["cuisine_family"] == ref["cuisine_family"]]["cuisine"]
    cuisines = sorted(set(family))
    with st.sidebar:
        st.header("Filters")
        with st.form("filters", border=False):
            st.markdown("**Cuisine & distance**")
            cuisine = st.selectbox(
                "Required cuisine (optional)", ["Any"] + cuisines,
                help="Only cuisines in the same family as your reference can be similar.",
            )
            use_distance = st.checkbox("Limit distance")
            max_km = st.slider("Max distance (km)", 0.5, 15.0, 3.0, 0.5)

            st.markdown("**Rating, reviews & price** (Yelp)")
            if not yelp_ready:
                st.info(f"These filters are disabled: {yelp_reason}")
            min_rating = st.slider("Minimum rating", 1.0, 5.0, 4.0, 0.5, disabled=not yelp_ready)
            min_reviews = st.number_input("Minimum reviews", 0, 100000, 20, 5, disabled=not yelp_ready)
            use_ratio = st.checkbox("Limit reviews relative to the reference", True, disabled=not yelp_ready)
            ratio_pct = st.slider("Max reviews (% of reference)", 5, 200, 50, 5, disabled=not yelp_ready)
            abs_max = st.number_input("Absolute max reviews (optional)", min_value=1, value=None,
                                      placeholder="No limit", disabled=not yelp_ready)
            price = st.selectbox("Price cap", ["Any", "$", "$$", "$$$", "$$$$"], disabled=not yelp_ready)
            clicked = st.form_submit_button("Find alternatives", type="primary", width="stretch")
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


# ---------------------------------------------------------------- results

def result_card(i: int, c: dict, yelp_available: bool) -> None:
    same = c["similarity_score"] == rec.SAME_CUISINE
    badge = f"<span class='badge {'badge-same' if same else 'badge-related'}'>{esc(c['shared_cuisine_label'])}</span>"
    if not yelp_available:
        freshness = "Yelp data unavailable"
    elif c.get("data_fetched_at"):
        freshness = f"Yelp data fetched {c['data_fetched_at'][:16].replace('T', ' ')} UTC"
    else:
        freshness = f"Yelp: {yc.NOT_ENRICHED}"
    evidence = " · ".join([
        fmt_missing(c["distance_km"], lambda v: f"{v:.2f} km", "distance unknown"),
        esc(c["cuisine"]),
        freshness,
    ])
    yelp_line = ""
    if yelp_available and c.get("enriched"):
        yelp_line = "<div class='evidence'>" + " · ".join([
            "rating " + fmt_missing(c["rating"], lambda v: f"{v:g}"),
            fmt_missing(c["review_count"], lambda v: f"{int(v):,}", "unknown") + " reviews",
            "price " + fmt_missing(c["price_level"], lambda v: PRICE_LABELS.get(int(v), "unavailable")),
        ]) + "</div>"
    with st.container(border=True):
        st.markdown(
            f"<div class='card-title'>{i}. {esc(c['name'])}{badge}</div>"
            f"<div class='card-address'>{esc(c['address'])}, {esc(c['zip'])}</div>",
            unsafe_allow_html=True,
        )
        st.write(c["explanation"])
        st.markdown(f"<div class='evidence'>{evidence}</div>{yelp_line}", unsafe_allow_html=True)


def show_map(ref: dict, results: list[dict]) -> None:
    points = [{"lat": c["lat"], "lon": c["lon"], "color": "#1F6FEB", "size": 40}
              for c in results if c.get("lat") is not None and c.get("lon") is not None]
    if not pd.isna(ref.get("lat")) and not pd.isna(ref.get("lon")):
        points.append({"lat": ref["lat"], "lon": ref["lon"], "color": "#E4572E", "size": 70})
    if points:
        st.map(pd.DataFrame(points), latitude="lat", longitude="lon", color="color", size="size")
        st.caption("Orange: your reference. Blue: alternatives. Restaurants without coordinates are not shown.")
    else:
        st.info("Map unavailable: none of these restaurants has coordinates.")


def show_applied(result: rec.RecommendationResult) -> None:
    with st.expander("Filters actually applied"):
        rows = [{"Filter": k, "Value": "not set" if v is None else str(v)}
                for k, v in result.filters_applied.items()]
        st.table(pd.DataFrame(rows))
        if result.results:
            st.write("**Ranking tiers used:** " + ", ".join(result.results[0]["tiers_used"]))
            skipped = result.results[0]["tiers_skipped"]
            if skipped:
                st.write("**Tiers skipped:** " + "; ".join(f"{k} ({v})" for k, v in skipped.items()))


def show_results(result: rec.RecommendationResult) -> None:
    if result.error:
        st.warning(result.error)
        return
    ref = result.reference
    if result.yelp_available and ref.get("data_fetched_at"):
        st.caption(
            f"Reference on Yelp: rating {fmt_missing(ref.get('rating'), lambda v: f'{v:g}')}, "
            f"{fmt_missing(ref.get('review_count'), lambda v: f'{int(v):,}')} reviews, price "
            f"{fmt_missing(ref.get('price_level'), lambda v: PRICE_LABELS.get(int(v), 'unavailable'))}."
        )
    for n in dict.fromkeys(result.notices):
        st.info(n)
    if not result.results:
        st.warning(result.empty_message or "No comparable restaurants found.")
        show_applied(result)
        return
    st.subheader(f"{len(result.results)} alternatives")
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
    st.title("🍽️ Manhattan Restaurant Alternatives")
    st.markdown(
        "<p class='app-subtitle'>Name a restaurant you like and find comparable places nearby, "
        "by cuisine, distance and (optionally) Yelp rating, reviews and price.</p>",
        unsafe_allow_html=True,
    )

    directory, matcher = load_directory()
    conn = get_connection()
    client = get_client(conn)
    yelp_ready, yelp_reason = yelp_status(client)

    ref = choose_reference(matcher)
    if ref is None:
        st.sidebar.header("Filters")
        st.sidebar.caption("Choose a reference restaurant to set filters.")
        data_sources_panel(conn, client, yelp_ready, yelp_reason)
        return
    show_reference(ref)
    if rec.is_unclassified(ref):
        st.warning(rec.UNSPECIFIED_MESSAGE)
        data_sources_panel(conn, client, yelp_ready, yelp_reason)
        return

    filters = filter_form(directory, ref, yelp_ready, yelp_reason)
    if filters is not None:
        # the ONLY place Yelp may be called
        enricher = client.enrich if yelp_ready else None
        with st.spinner("Finding alternatives..."):
            st.session_state.result = rec.recommend(directory, ref["nyc_id"], filters, enricher)
        st.session_state.result_ref = ref["nyc_id"]
    data_sources_panel(conn, client, yelp_ready, yelp_reason)

    result = st.session_state.get("result")
    if result is not None and st.session_state.get("result_ref") == ref["nyc_id"]:
        show_results(result)
    else:
        st.info("Set your filters in the sidebar, then click **Find alternatives**.")


main()
