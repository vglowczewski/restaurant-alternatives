"""Streamlit UI: name search -> confirm reference -> filters -> Find alternatives.

Yelp is called only inside the "Find alternatives" click handler (never on
keystrokes or reruns). Results are kept in session_state so reruns redraw them
without new calls. @st.cache_resource is used only for the directory and the
DB connection; Yelp data is never cached by Streamlit.
"""
from __future__ import annotations

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


# ---------------------------------------------------------------- sidebar

def sidebar(conn, client: yc.YelpClient) -> tuple[bool, str | None]:
    """Shows Yelp status; returns (yelp_ready, reason_if_not)."""
    s = client.settings
    if not s.api_key or s.api_key.startswith("your-"):
        reason = "No Yelp API key is configured, so the app runs on local data only."
    elif not s.enabled:
        reason = "Yelp is disabled (YELP_ENABLED=false), so the app runs on local data only."
    elif client.budget_used_up():
        reason = f"{yc.BUDGET_USED_UP_MESSAGE}. The app runs on local data only."
    else:
        reason = None

    with st.sidebar:
        st.header("Data sources")
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

        with st.expander("Yelp ID mappings (import / export)"):
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
    return reason is None, reason


# ---------------------------------------------------------------- reference

def choose_reference(matcher: NameMatcher) -> dict | None:
    st.subheader("1. Find your restaurant")
    query = st.text_input("Restaurant name (Manhattan)", placeholder="e.g. Don Angie", key="query")
    if not query.strip():
        st.session_state.pop("reference_id", None)
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
    st.subheader("2. Your reference restaurant")
    with st.container(border=True):
        st.markdown(f"**{row['name']}**  \n{row['address']}, {row['zip']}")
        st.caption(f"Cuisine: {row['cuisine'] or 'not specified'} · Family: {row['cuisine_family'] or 'not specified'}")


# ---------------------------------------------------------------- filters

def filter_form(directory: pd.DataFrame, ref: dict, yelp_ready: bool, yelp_reason: str | None):
    """Returns Filters when Find alternatives is clicked, else None."""
    st.subheader("3. Filters")
    family = directory[directory["cuisine_family"] == ref["cuisine_family"]]["cuisine"]
    cuisines = sorted(set(family))
    with st.form("filters"):
        c1, c2 = st.columns(2)
        with c1:
            cuisine = st.selectbox(
                "Required cuisine (optional)", ["Any"] + cuisines,
                help="Only cuisines in the same family as your reference can be similar.",
            )
            use_distance = st.checkbox("Limit distance")
            max_km = st.slider("Max distance (km)", 0.5, 15.0, 3.0, 0.5)
        with c2:
            if not yelp_ready:
                st.info(f"Rating, review and price filters are disabled: {yelp_reason}")
            min_rating = st.slider("Minimum rating", 1.0, 5.0, 4.0, 0.5, disabled=not yelp_ready)
            min_reviews = st.number_input("Minimum reviews", 0, 100000, 20, 5, disabled=not yelp_ready)
            use_ratio = st.checkbox("Limit reviews relative to the reference", True, disabled=not yelp_ready)
            ratio_pct = st.slider("Max reviews (% of reference)", 5, 200, 50, 5, disabled=not yelp_ready)
            abs_max = st.number_input("Absolute max reviews (optional)", min_value=1, value=None,
                                      placeholder="No limit", disabled=not yelp_ready)
            price = st.selectbox("Price cap", ["Any", "$", "$$", "$$$", "$$$$"], disabled=not yelp_ready)
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


# ---------------------------------------------------------------- results

def result_card(i: int, c: dict, yelp_available: bool) -> None:
    with st.container(border=True):
        st.markdown(f"**{i}. {c['name']}** · `{c['shared_cuisine_label']}`")
        st.caption(f"{c['address']}, {c['zip']}")
        st.write(c["explanation"])
        evidence = {
            "Cuisine": c["cuisine"],
            "Similarity score": f"{c['similarity_score']:.1f}",
            "Distance": fmt_missing(c["distance_km"], lambda v: f"{v:.2f} km"),
            "Rating": fmt_missing(c["rating"], lambda v: f"{v:g}"),
            "Reviews": fmt_missing(c["review_count"], lambda v: f"{int(v):,}"),
            "Reviews vs reference": fmt_missing(c["review_ratio"], lambda v: f"{v:.0%}"),
            "Price": fmt_missing(c["price_level"], lambda v: PRICE_LABELS.get(int(v), "unavailable")),
        }
        st.caption(" · ".join(f"{k}: {v}" for k, v in evidence.items()))
        if not yelp_available:
            st.caption("Yelp data unavailable.")
        elif c.get("data_fetched_at"):
            st.caption(f"Yelp data fetched at {c['data_fetched_at'][:19].replace('T', ' ')} UTC.")
        else:
            st.caption(f"Yelp: {yc.NOT_ENRICHED}.")


def show_map(ref: dict, results: list[dict]) -> None:
    points = [{"lat": c["lat"], "lon": c["lon"], "color": "#1f77b4", "size": 40}
              for c in results if c.get("lat") is not None and c.get("lon") is not None]
    if not pd.isna(ref.get("lat")) and not pd.isna(ref.get("lon")):
        points.append({"lat": ref["lat"], "lon": ref["lon"], "color": "#d62728", "size": 70})
    if points:
        st.map(pd.DataFrame(points), latitude="lat", longitude="lon", color="color", size="size")
        st.caption("Red: your reference. Blue: alternatives. Restaurants without coordinates are not shown.")
    else:
        st.caption("Map unavailable: no coordinates for these restaurants.")


def show_applied(result: rec.RecommendationResult) -> None:
    with st.expander("Filters actually applied"):
        applied = dict(result.filters_applied)
        rows = [{"Filter": k, "Value": "not set" if v is None else str(v)} for k, v in applied.items()]
        st.table(pd.DataFrame(rows))
        if result.results:
            st.write("**Ranking tiers used:** " + ", ".join(result.results[0]["tiers_used"]))
            skipped = result.results[0]["tiers_skipped"]
            if skipped:
                st.write("**Tiers skipped:** " + "; ".join(f"{k} ({v})" for k, v in skipped.items()))


def show_results(result: rec.RecommendationResult) -> None:
    st.subheader("4. Alternatives")
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
    show_applied(result)
    if not result.results:
        st.warning(result.empty_message or "No comparable restaurants found.")
        return
    st.caption(result.disclaimer)
    left, right = st.columns([3, 2])
    with left:
        for i, c in enumerate(result.results, 1):
            result_card(i, c, result.yelp_available)
    with right:
        show_map(ref, result.results)


# ---------------------------------------------------------------- main

def main() -> None:
    st.title("Manhattan Restaurant Alternatives")
    st.caption("Find comparable restaurants by cuisine, distance and (optionally) Yelp rating, reviews and price.")

    directory, matcher = load_directory()
    conn = get_connection()
    client = get_client(conn)
    yelp_ready, yelp_reason = sidebar(conn, client)

    ref = choose_reference(matcher)
    if ref is None:
        return
    show_reference(ref)
    if rec.is_unclassified(ref):
        st.warning(rec.UNSPECIFIED_MESSAGE)
        return

    filters = filter_form(directory, ref, yelp_ready, yelp_reason)
    if filters is not None:
        # the ONLY place Yelp may be called
        enricher = client.enrich if yelp_ready else None
        with st.spinner("Finding alternatives..."):
            st.session_state.result = rec.recommend(directory, ref["nyc_id"], filters, enricher)
        st.session_state.result_ref = ref["nyc_id"]

    result = st.session_state.get("result")
    if result is not None and st.session_state.get("result_ref") == ref["nyc_id"]:
        show_results(result)


main()
