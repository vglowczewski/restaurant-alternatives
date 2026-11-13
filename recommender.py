"""Recommendations: cuisine similarity, filters, tiered ranking, template explanations.

Two-stage retrieval:
  Stage 1 (local): similarity > 0, optional distance limit / required cuisine,
                   rank on available tiers, top 40.
  Stage 2 (Yelp):  an `enricher` (supplied by yelp_client in Phase 4) returns
                   fresh Yelp records for the reference and candidates; then the
                   rating/review/price filters apply and the top 10 are returned.
Without an enricher, or if it reports Yelp unavailable, Stage 1's top 10 are
returned and the Yelp-based filters are disabled with a visible explanation.
No LLM calls; explanations are template-only.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

from data_store import is_fresh
from name_matching import normalize

UNCLASSIFIED_FAMILY = "Other/Unclassified"
UNSPECIFIED_MESSAGE = "This restaurant's cuisine isn't specified, so we can't find comparable restaurants."
DISCLAIMER = "Score reflects cuisine only, not menu, atmosphere, or quality."
YELP_UNAVAILABLE = "Yelp data unavailable"
YELP_FILTERS_DISABLED = (
    "Rating, review and price filters are disabled because Yelp data is unavailable; "
    "results are ranked on local data only."
)

STAGE1_LIMIT = 40
RESULT_LIMIT = 10
UNKNOWN_BAND = 99
SAME_CUISINE, RELATED_CUISINE = 1.0, 0.6
SIMILARITY_LABELS = {SAME_CUISINE: "same cuisine", RELATED_CUISINE: "related cuisine"}

TIERS = ["cuisine_band", "distance_band", "price_gap", "rating", "distance_km", "name"]


@dataclass
class Filters:
    """User filters. Defaults are starting assumptions; limits are inclusive."""
    max_review_ratio: float | None = 0.5
    min_reviews: int | None = 20
    min_rating: float | None = 4.0
    absolute_max_reviews: int | None = None
    max_distance_km: float | None = None
    price_cap: int | None = None
    required_cuisine: str | None = None


@dataclass
class RecommendationResult:
    reference: dict | None
    results: list[dict] = field(default_factory=list)
    error: str | None = None
    yelp_available: bool = False
    notices: list[str] = field(default_factory=list)
    filters_applied: dict = field(default_factory=dict)
    empty_message: str | None = None
    disclaimer: str = DISCLAIMER


# ---------------------------------------------------------------- primitives

def _missing(v) -> bool:
    if v is None:
        return True
    try:
        return math.isnan(v)
    except TypeError:
        return v == ""


def is_unclassified(row) -> bool:
    return str(row.get("cuisine_family", "")).strip() in ("", UNCLASSIFIED_FAMILY) or not str(
        row.get("cuisine", "")
    ).strip()


def cuisine_similarity(reference, candidate) -> float:
    """1.0 same cuisine, 0.6 same family, else 0. Unclassified is always 0."""
    if is_unclassified(reference) or is_unclassified(candidate):
        return 0.0
    if reference["cuisine"] == candidate["cuisine"]:
        return SAME_CUISINE
    if reference["cuisine_family"] == candidate["cuisine_family"]:
        return RELATED_CUISINE
    return 0.0


def haversine_km(lat1, lon1, lat2, lon2) -> float | None:
    if any(_missing(v) for v in (lat1, lon1, lat2, lon2)):
        return None
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(a))


def distance_band(km: float | None) -> int:
    if km is None:
        return UNKNOWN_BAND
    if km <= 1:
        return 0
    if km <= 3:
        return 1
    if km <= 5:
        return 2
    return 3


def price_gap(candidate_price, reference_price) -> int:
    if _missing(candidate_price) or _missing(reference_price):
        return UNKNOWN_BAND
    return abs(int(candidate_price) - int(reference_price))


def effective_max_reviews(filters: Filters, reference_reviews) -> int | None:
    """min(absolute_max, int(reference_reviews * ratio)); the relative part is
    dropped when the reference review count (or the ratio) is missing."""
    limits = []
    if filters.absolute_max_reviews is not None:
        limits.append(int(filters.absolute_max_reviews))
    if filters.max_review_ratio is not None and not _missing(reference_reviews):
        limits.append(int(reference_reviews * filters.max_review_ratio))
    return min(limits) if limits else None


def review_conflict(filters: Filters, reference_reviews) -> str | None:
    eff = effective_max_reviews(filters, reference_reviews)
    if eff is None or filters.min_reviews is None or eff >= filters.min_reviews:
        return None
    return (
        f"The review limits conflict: the maximum allowed is {eff} reviews but the minimum is "
        f"{filters.min_reviews}, so no restaurant can qualify. Lower the minimum reviews or "
        f"raise the relative/absolute maximum."
    )


# ---------------------------------------------------------------- ranking

def _skipped_tiers(reference: dict, candidates: list[dict], stage2: bool) -> dict[str, str]:
    skipped = {}
    if _missing(reference.get("lat")) or _missing(reference.get("lon")):
        reason = "reference location unknown"
        skipped["distance_band"] = reason
        skipped["distance_km"] = reason
    if not stage2:
        skipped["price_gap"] = "Yelp data unavailable"
        skipped["rating"] = "Yelp data unavailable"
    else:
        if _missing(reference.get("price_level")):
            skipped["price_gap"] = "reference price unknown"
        if all(_missing(c.get("rating")) for c in candidates):
            skipped["rating"] = "no ratings available"
    return skipped


def _sort_key(c: dict, skipped: dict[str, str]):
    rating = c.get("rating")
    km = c.get("distance_km")
    return (
        -math.floor(c["similarity_score"] * 10),
        0 if "distance_band" in skipped else c["distance_band"],
        0 if "price_gap" in skipped else c["price_gap"],
        0 if "rating" in skipped else ((1, 0) if _missing(rating) else (0, -rating)),
        0 if "distance_km" in skipped else ((1, 0) if km is None else (0, km)),
        str(c["name"]).lower(),
        str(c["restaurant_id"]),
    )


def rank(reference: dict, candidates: list[dict], stage2: bool) -> list[dict]:
    """Sort ascending by the tier key, using only available tiers. Records tiers on each result."""
    skipped = _skipped_tiers(reference, candidates, stage2)
    used = [t for t in TIERS if t not in skipped]
    for c in candidates:
        c["tiers_used"] = list(used)
        c["tiers_skipped"] = dict(skipped)
    return sorted(candidates, key=lambda c: _sort_key(c, skipped))


# ---------------------------------------------------------------- stages

def _as_reference(row) -> dict:
    ref = dict(row)
    ref.setdefault("rating", None)
    ref.setdefault("review_count", None)
    ref.setdefault("price_level", None)
    return ref


def _candidate(reference: dict, row: dict, sim: float) -> dict:
    km = haversine_km(reference.get("lat"), reference.get("lon"), row.get("lat"), row.get("lon"))
    return {
        "restaurant_id": str(row["nyc_id"]),
        "name": row["name"],
        "address": row["address"],
        "zip": row.get("zip", ""),
        "lat": None if _missing(row.get("lat")) else row["lat"],
        "lon": None if _missing(row.get("lon")) else row["lon"],
        "cuisine": row["cuisine"],
        "cuisine_family": row["cuisine_family"],
        "shared_cuisine_label": SIMILARITY_LABELS[sim],
        "similarity_score": sim,
        "rating": None,
        "review_count": None,
        "review_ratio": None,
        "price_level": None,
        "distance_km": None if km is None else round(km, 3),
        "distance_band": distance_band(km),
        "price_gap": UNKNOWN_BAND,
        "tiers_used": [],
        "tiers_skipped": {},
        "data_fetched_at": None,
        "enriched": False,
    }


def stage1(directory, reference: dict, filters: Filters, limit: int = STAGE1_LIMIT) -> tuple[list[dict], list[str]]:
    """Local candidates: exclude the reference and other branches with the same
    normalized name, similarity > 0, distance limit,
    required cuisine; ranked on local tiers; top `limit`."""
    notices = []
    ref_id = str(reference["nyc_id"])
    ref_name = normalize(reference["name"])
    ref_has_coords = not (_missing(reference.get("lat")) or _missing(reference.get("lon")))
    use_distance = filters.max_distance_km is not None
    if use_distance and not ref_has_coords:
        use_distance = False
        notices.append("Distance limit not applied: the reference restaurant has no location data.")

    out = []
    for row in directory.to_dict("records"):
        # never return the reference or another branch of the same restaurant name
        if str(row["nyc_id"]) == ref_id or (ref_name and normalize(row["name"]) == ref_name):
            continue
        sim = cuisine_similarity(reference, row)
        if sim <= 0:
            continue
        if filters.required_cuisine and row["cuisine"] != filters.required_cuisine:
            continue
        cand = _candidate(reference, row, sim)
        if use_distance and (cand["distance_km"] is None or cand["distance_km"] > filters.max_distance_km):
            continue
        out.append(cand)
    return rank(reference, out, stage2=False)[:limit], notices


def apply_enrichment(reference: dict, candidates: list[dict], records: dict, now=None) -> None:
    """Attach fresh Yelp records (keyed by nyc_id) in place. Stale or missing records
    leave the restaurant 'not enriched'. Yelp data older than 24 h is never used."""
    def fresh(rec):
        return rec if rec and rec.get("fetched_at") and is_fresh(rec["fetched_at"], now) else None

    ref_rec = fresh(records.get(str(reference["nyc_id"])))
    if ref_rec:
        reference.update(rating=ref_rec.get("rating"), review_count=ref_rec.get("review_count"),
                         price_level=ref_rec.get("price"), data_fetched_at=ref_rec["fetched_at"])
    for c in candidates:
        rec = fresh(records.get(c["restaurant_id"]))
        if not rec:
            continue
        c.update(rating=rec.get("rating"), review_count=rec.get("review_count"),
                 price_level=rec.get("price"), data_fetched_at=rec["fetched_at"], enriched=True)
    for c in candidates:
        c["price_gap"] = price_gap(c["price_level"], reference.get("price_level"))
        rc, ref_rc = c["review_count"], reference.get("review_count")
        c["review_ratio"] = None if _missing(rc) or _missing(ref_rc) or not ref_rc else round(rc / ref_rc, 4)


def _yelp_checks(filters: Filters, eff_max: int | None) -> list[tuple[str, str, Callable[[dict], bool]]]:
    """(key, relax-hint, passes) for each active Yelp filter. Unknown values never pass."""
    checks = []
    if filters.min_rating is not None:
        checks.append(("min_rating", "lower the minimum rating",
                       lambda c: not _missing(c["rating"]) and c["rating"] >= filters.min_rating))
    if filters.min_reviews is not None:
        checks.append(("min_reviews", "lower the minimum number of reviews",
                       lambda c: not _missing(c["review_count"]) and c["review_count"] >= filters.min_reviews))
    if eff_max is not None:
        checks.append(("max_reviews", "raise the maximum reviews (relative or absolute)",
                       lambda c: not _missing(c["review_count"]) and c["review_count"] <= eff_max))
    if filters.price_cap is not None:
        checks.append(("price_cap", "remove or raise the price cap",
                       lambda c: not _missing(c["price_level"]) and c["price_level"] <= filters.price_cap))
    return checks


def _relax_hint(candidates: list[dict], checks) -> str:
    hints = []
    for key, hint, _ in checks:
        others = [f for k, _, f in checks if k != key]
        n = sum(all(f(c) for f in others) for c in candidates)
        if n:
            hints.append(f"{hint} ({n} would qualify)")
    if not hints:
        hints = [h for _, h, _ in checks]
    return "No restaurants match all filters. Try to " + "; or ".join(hints) + "."


# ---------------------------------------------------------------- explanation

def _fmt_km(km: float) -> str:
    return f"{km:.1f} km away" if km >= 0.1 else "less than 0.1 km away"


def explain(c: dict) -> str:
    """Template explanation; clauses only for known values (price always stated)."""
    head = "Same cuisine" if c["similarity_score"] == SAME_CUISINE else "Related cuisine"
    parts = [f"{head} ({c['cuisine']})"]
    if c["distance_km"] is not None:
        parts.append(_fmt_km(c["distance_km"]))
    if not _missing(c.get("rating")):
        parts.append(f"rated {c['rating']:g}")
    if c.get("review_ratio") is not None:
        parts.append(f"has {round(c['review_ratio'] * 100)}% as many reviews as your reference")
    gap = c.get("price_gap", UNKNOWN_BAND)
    if gap == UNKNOWN_BAND:
        parts.append("price information is unavailable")
    elif gap == 0:
        parts.append("same price level as your reference")
    else:
        parts.append(f"price level differs by {gap} from your reference")
    return "; ".join(parts) + "."


# ---------------------------------------------------------------- entry point

Enricher = Callable[[dict, list[dict]], "dict | None"]


def recommend(directory, reference_id: str, filters: Filters | None = None,
              enricher: Enricher | None = None, now=None) -> RecommendationResult:
    """Full pipeline. `enricher(reference, candidates)` returns {nyc_id: yelp record}
    or None when Yelp is unavailable (no key, disabled, 429, auth error)."""
    filters = filters or Filters()
    rows = directory[directory["nyc_id"].astype(str) == str(reference_id)].to_dict("records")
    if not rows:
        return RecommendationResult(None, error="Reference restaurant not found in the directory.")
    reference = _as_reference(rows[0])
    if is_unclassified(reference):
        return RecommendationResult(reference, error=UNSPECIFIED_MESSAGE)

    candidates, notices = stage1(directory, reference, filters)
    has_coords = not (_missing(reference.get("lat")) or _missing(reference.get("lon")))
    applied = {
        "required_cuisine": filters.required_cuisine,
        "max_distance_km": filters.max_distance_km if has_coords else None,
    }

    records = None
    if enricher is not None:
        try:
            records = enricher(reference, candidates)
        except Exception as e:  # any Yelp failure falls back to local mode
            records = None
            if getattr(e, "user_message", None):
                notices.append(e.user_message)

    if records is None:
        notices += [YELP_UNAVAILABLE, YELP_FILTERS_DISABLED]
        applied["yelp_filters"] = "disabled (Yelp data unavailable)"
        results = candidates[:RESULT_LIMIT]
        result = RecommendationResult(reference, results, yelp_available=False, notices=notices,
                                      filters_applied=applied)
    else:
        apply_enrichment(reference, candidates, records, now)
        ref_reviews = reference.get("review_count")
        if filters.max_review_ratio is not None and _missing(ref_reviews):
            notices.append("Relative review limit not applied: the reference's review count is unknown.")
        if not reference.get("data_fetched_at"):
            notices.append("The reference restaurant is not enriched with Yelp data.")
        eff_max = effective_max_reviews(filters, ref_reviews)
        applied.update(min_rating=filters.min_rating, min_reviews=filters.min_reviews,
                       effective_max_reviews=eff_max, price_cap=filters.price_cap)
        conflict = review_conflict(filters, ref_reviews)
        result = RecommendationResult(reference, yelp_available=True, notices=notices, filters_applied=applied)
        if conflict:
            result.notices.append(conflict)
            result.empty_message = conflict
            return result
        checks = _yelp_checks(filters, eff_max)
        ranked = rank(reference, candidates, stage2=True)
        passing = [c for c in ranked if all(f(c) for _, _, f in checks)]
        result.results = passing[:RESULT_LIMIT]
        if not result.results and ranked:
            result.empty_message = _relax_hint(ranked, checks)

    for c in result.results:
        c["explanation"] = explain(c)
    if not result.results and not result.empty_message:
        hint = []
        if filters.required_cuisine:
            hint.append("remove the required cuisine")
        if applied.get("max_distance_km") is not None:
            hint.append("increase the maximum distance")
        result.empty_message = "No comparable restaurants found." + (
            " Try to " + " or ".join(hint) + "." if hint else "")
    return result
