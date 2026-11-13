from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

import recommender as rec
from recommender import Filters, recommend

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
FRESH = NOW.isoformat()
STALE = (NOW - timedelta(hours=25)).isoformat()
LAT, LON = 40.70, -74.00


def row(nyc_id, name, cuisine, family, dlat=None, address="1 TEST ST"):
    lat = None if dlat is None else LAT + dlat
    lon = None if dlat is None else LON
    return dict(nyc_id=str(nyc_id), name=name, address=address, zip="10001",
                lat=lat, lon=lon, cuisine=cuisine, cuisine_family=family)


def make_dir(*rows):
    df = pd.DataFrame(list(rows))
    df["lat"] = pd.to_numeric(df["lat"])
    df["lon"] = pd.to_numeric(df["lon"])
    return df


@pytest.fixture
def directory():
    # dlat 0.005 ~ 0.56 km, 0.02 ~ 2.2 km, 0.04 ~ 4.4 km, 0.06 ~ 6.7 km
    return make_dir(
        row("R", "REF ITALIAN", "Italian", "Italian/Pizza", 0.0),
        row("A", "ALPHA", "Italian", "Italian/Pizza", 0.02),
        row("B", "BRAVO", "Italian", "Italian/Pizza", 0.005),
        row("C", "CHARLIE PIZZA", "Pizza", "Italian/Pizza", 0.001),
        row("D", "DELTA", "Italian", "Italian/Pizza", None),
        row("E", "ECHO SUSHI", "Japanese", "East Asian", 0.001),
        row("F", "FOXTROT FUSION", "Fusion", "Other/Unclassified", 0.001),
        row("G", "GOLF", "Italian", "Italian/Pizza", 0.06),
        row("U", "UNKNOWN PLACE", "Other", "Other/Unclassified", 0.001),
    )


def ids(result):
    return [r["restaurant_id"] for r in result.results]


def enricher_from(records):
    return lambda ref, cands: records


def yelp(rating=None, reviews=None, price=None, fetched_at=FRESH):
    return {"rating": rating, "review_count": reviews, "price": price, "fetched_at": fetched_at}


# --------------------------------------------------------- similarity

def test_cuisine_and_family_similarity(directory):
    res = recommend(directory, "R")
    by_id = {r["restaurant_id"]: r for r in res.results}
    assert by_id["A"]["similarity_score"] == 1.0
    assert by_id["A"]["shared_cuisine_label"] == "same cuisine"
    assert by_id["C"]["similarity_score"] == 0.6
    assert by_id["C"]["shared_cuisine_label"] == "related cuisine"
    assert "E" not in by_id  # different family excluded


def test_reference_never_returned(directory):
    assert "R" not in ids(recommend(directory, "R"))
    assert "R" not in ids(recommend(directory, "R", enricher=enricher_from({"R": yelp(4.5, 100, 2)}),
                                    filters=Filters(min_rating=None, min_reviews=None, max_review_ratio=None)))


def test_unclassified_never_recommended(directory):
    res = recommend(make_dir(*[r for r in directory.to_dict("records")]), "C")
    assert "F" not in ids(res) and "U" not in ids(res)


@pytest.mark.parametrize("ref_id", ["F", "U"])
def test_unclassified_reference_rejected(directory, ref_id):
    res = recommend(directory, ref_id)
    assert res.error == rec.UNSPECIFIED_MESSAGE
    assert res.results == []


def test_unknown_reference_id(directory):
    assert recommend(directory, "nope").error


# --------------------------------------------------------- ranking

@pytest.mark.parametrize("km,band", [(0, 0), (1.0, 0), (1.0001, 1), (3.0, 1), (3.0001, 2),
                                     (5.0, 2), (5.0001, 3), (None, 99)])
def test_distance_band_boundaries(km, band):
    assert rec.distance_band(km) == band


def test_cuisine_band_before_distance(directory):
    order = ids(recommend(directory, "R"))
    # same-cuisine (A, B, D, G) all before related-cuisine C, even though C is closest
    assert order.index("C") > max(order.index(x) for x in "ABDG")


def test_order_within_band_by_distance_then_unknown_last(directory):
    order = ids(recommend(directory, "R"))
    assert order[:4] == ["B", "A", "G", "D"]  # 0.56 km, 2.2 km, 6.7 km, unknown


def test_order_within_distance_band_by_price_gap():
    d = make_dir(
        row("R", "REF", "Italian", "Italian/Pizza", 0.0),
        row("X", "AAA FAR PRICE", "Italian", "Italian/Pizza", 0.002),
        row("Y", "ZZZ SAME PRICE", "Italian", "Italian/Pizza", 0.004),
        row("Z", "MMM NO PRICE", "Italian", "Italian/Pizza", 0.001),
    )
    recs = {"R": yelp(4.5, 1000, 2), "X": yelp(4.5, 100, 4), "Y": yelp(4.5, 100, 2), "Z": yelp(4.5, 100, None)}
    res = recommend(d, "R", Filters(), enricher=enricher_from(recs), now=NOW)
    assert ids(res) == ["Y", "X", "Z"]  # gap 0, gap 2, unknown (never beats known)
    assert res.results[2]["price_gap"] == 99


def test_rating_breaks_ties_missing_last():
    d = make_dir(
        row("R", "REF", "Italian", "Italian/Pizza", 0.0),
        row("X", "X", "Italian", "Italian/Pizza", 0.001),
        row("Y", "Y", "Italian", "Italian/Pizza", 0.002),
        row("Z", "Z", "Italian", "Italian/Pizza", 0.003),
    )
    recs = {"R": yelp(4.5, 1000, 2), "X": yelp(None, 100, 2), "Y": yelp(4.2, 100, 2), "Z": yelp(4.8, 100, 2)}
    f = Filters(min_rating=None)
    assert ids(recommend(d, "R", f, enricher=enricher_from(recs), now=NOW)) == ["Z", "Y", "X"]


def test_reference_without_coordinates_skips_distance_tiers():
    d = make_dir(
        row("R", "REF", "Italian", "Italian/Pizza", None),
        row("B", "BBB", "Italian", "Italian/Pizza", 0.06),
        row("A", "AAA", "Italian", "Italian/Pizza", 0.001),
    )
    res = recommend(d, "R", Filters(max_distance_km=1))
    assert ids(res) == ["A", "B"]  # name order; distance inert
    r0 = res.results[0]
    assert r0["distance_km"] is None and r0["distance_band"] == 99
    assert "distance_band" in r0["tiers_skipped"] and "distance_km" in r0["tiers_skipped"]
    assert any("Distance limit not applied" in n for n in res.notices)


def test_missing_reference_price_skips_price_tier():
    d = make_dir(row("R", "REF", "Italian", "Italian/Pizza", 0.0),
                 row("A", "AAA", "Italian", "Italian/Pizza", 0.001))
    recs = {"R": yelp(4.5, 1000, None), "A": yelp(4.5, 100, 1)}
    res = recommend(d, "R", enricher=enricher_from(recs), now=NOW)
    assert res.results[0]["price_gap"] == 99
    assert res.results[0]["tiers_skipped"]["price_gap"] == "reference price unknown"


# --------------------------------------------------------- filters

def test_distance_limit_excludes_unknown_and_far(directory):
    res = recommend(directory, "R", Filters(max_distance_km=3))
    assert set(ids(res)) == {"A", "B", "C"}


def test_required_cuisine(directory):
    assert set(ids(recommend(directory, "R", Filters(required_cuisine="Pizza")))) == {"C"}


def test_effective_max_reviews_percentage_cap():
    assert rec.effective_max_reviews(Filters(max_review_ratio=0.5), 101) == 50
    assert rec.effective_max_reviews(Filters(max_review_ratio=0.5, absolute_max_reviews=30), 100) == 30
    assert rec.effective_max_reviews(Filters(max_review_ratio=0.5, absolute_max_reviews=80), 100) == 50
    assert rec.effective_max_reviews(Filters(max_review_ratio=0.5), None) is None
    assert rec.effective_max_reviews(Filters(max_review_ratio=0.5, absolute_max_reviews=70), None) == 70


def _limits_dir():
    return make_dir(
        row("R", "REF", "Italian", "Italian/Pizza", 0.0),
        row("LOW", "LOW", "Italian", "Italian/Pizza", 0.001),
        row("MIN", "MIN", "Italian", "Italian/Pizza", 0.001),
        row("MAX", "MAX", "Italian", "Italian/Pizza", 0.001),
        row("OVER", "OVER", "Italian", "Italian/Pizza", 0.001),
        row("RAT", "RAT", "Italian", "Italian/Pizza", 0.001),
    )


def test_inclusive_limits():
    recs = {
        "R": yelp(4.5, 200, 2),
        "LOW": yelp(4.5, 19, 2),    # below min reviews
        "MIN": yelp(4.0, 20, 2),    # exactly min reviews and min rating
        "MAX": yelp(4.5, 100, 2),   # exactly 50% of 200
        "OVER": yelp(4.5, 101, 2),  # above relative cap
        "RAT": yelp(3.9, 50, 2),    # below min rating
    }
    res = recommend(_limits_dir(), "R", Filters(), enricher=enricher_from(recs), now=NOW)
    assert set(ids(res)) == {"MIN", "MAX"}
    assert res.filters_applied["effective_max_reviews"] == 100


def test_price_cap_inclusive_and_unknown_not_cheap():
    d = make_dir(row("R", "REF", "Italian", "Italian/Pizza", 0.0),
                 row("A", "A", "Italian", "Italian/Pizza", 0.001),
                 row("B", "B", "Italian", "Italian/Pizza", 0.001),
                 row("C", "C", "Italian", "Italian/Pizza", 0.001))
    recs = {"R": yelp(4.5, 1000, 3), "A": yelp(4.5, 100, 2), "B": yelp(4.5, 100, 3), "C": yelp(4.5, 100, None)}
    res = recommend(d, "R", Filters(price_cap=2), enricher=enricher_from(recs), now=NOW)
    assert ids(res) == ["A"]


def test_missing_reference_reviews_disables_relative_filter():
    recs = {"R": yelp(4.5, None, 2), "MAX": yelp(4.5, 5000, 2)}
    res = recommend(_limits_dir(), "R", Filters(), enricher=enricher_from(recs), now=NOW)
    assert ids(res) == ["MAX"]
    assert any("Relative review limit not applied" in n for n in res.notices)


def test_conflicting_thresholds_explained():
    recs = {"R": yelp(4.5, 30, 2), "MIN": yelp(4.5, 20, 2)}
    f = Filters(max_review_ratio=0.5, min_reviews=20)  # max 15 < min 20
    res = recommend(_limits_dir(), "R", f, enricher=enricher_from(recs), now=NOW)
    assert res.results == []
    assert "conflict" in res.empty_message and "15" in res.empty_message and "20" in res.empty_message
    assert f.min_reviews == 20 and f.max_review_ratio == 0.5  # settings unchanged


def test_empty_state_suggests_filters_to_relax():
    recs = {"R": yelp(4.5, 200, 2), "MAX": yelp(3.5, 100, 2)}
    res = recommend(_limits_dir(), "R", Filters(), enricher=enricher_from(recs), now=NOW)
    assert res.results == []
    assert "minimum rating" in res.empty_message and "1 would qualify" in res.empty_message


def test_unenriched_candidates_never_pass_yelp_filters():
    recs = {"R": yelp(4.5, 200, 2)}
    res = recommend(_limits_dir(), "R", Filters(), enricher=enricher_from(recs), now=NOW)
    assert res.results == [] and res.empty_message


# --------------------------------------------------------- local mode / fallback

def test_local_mode_without_enricher(directory):
    res = recommend(directory, "R", Filters(min_rating=4.5, price_cap=1))
    assert not res.yelp_available
    assert rec.YELP_UNAVAILABLE in res.notices and rec.YELP_FILTERS_DISABLED in res.notices
    assert len(res.results) == 5  # Yelp-based filters not applied
    assert res.filters_applied["yelp_filters"].startswith("disabled")
    assert all(r["rating"] is None and not r["enriched"] for r in res.results)


@pytest.mark.parametrize("enricher", [lambda r, c: None, lambda r, c: (_ for _ in ()).throw(RuntimeError("429"))])
def test_enricher_failure_falls_back(directory, enricher):
    res = recommend(directory, "R", enricher=enricher)
    assert not res.yelp_available and len(res.results) == 5


def test_stage1_top_40_and_results_top_10():
    rows = [row("R", "REF", "Italian", "Italian/Pizza", 0.0)]
    rows += [row(f"N{i:02d}", f"N{i:02d}", "Italian", "Italian/Pizza", 0.001) for i in range(60)]
    d = make_dir(*rows)
    seen = {}

    def enricher(ref, cands):
        seen["n"] = len(cands)
        return {c["restaurant_id"]: yelp(4.5, 50, 2) for c in cands} | {"R": yelp(4.5, 200, 2)}

    res = recommend(d, "R", enricher=enricher, now=NOW)
    assert seen["n"] == 40 and len(res.results) == 10
    assert len(recommend(d, "R").results) == 10


def test_stale_yelp_data_never_used():
    recs = {"R": yelp(4.5, 200, 2, STALE), "MAX": yelp(4.5, 100, 2, STALE)}
    res = recommend(_limits_dir(), "R", Filters(min_rating=None, min_reviews=None, max_review_ratio=None),
                    enricher=enricher_from(recs), now=NOW)
    assert all(r["rating"] is None and r["data_fetched_at"] is None and not r["enriched"] for r in res.results)


# --------------------------------------------------------- output / explanation

def test_output_fields(directory):
    expected = {"restaurant_id", "name", "address", "cuisine", "shared_cuisine_label", "similarity_score",
                "rating", "review_count", "review_ratio", "price_level", "distance_km", "distance_band",
                "price_gap", "tiers_used", "tiers_skipped", "data_fetched_at", "explanation"}
    res = recommend(directory, "R")
    assert expected <= set(res.results[0])
    assert res.disclaimer == "Score reflects cuisine only, not menu, atmosphere, or quality."


def test_explanation_template():
    c = {"similarity_score": 1.0, "cuisine": "Italian", "distance_km": 1.234, "rating": None,
         "review_ratio": 0.15, "price_gap": 99}
    assert rec.explain(c) == ("Same cuisine (Italian); 1.2 km away; has 15% as many reviews as your reference; "
                              "price information is unavailable.")


def test_explanation_omits_unknown_clauses():
    c = {"similarity_score": 0.6, "cuisine": "Pizza", "distance_km": None, "rating": None,
         "review_ratio": None, "price_gap": 0}
    assert rec.explain(c) == "Related cuisine (Pizza); same price level as your reference."
