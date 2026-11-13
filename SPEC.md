# SPEC: Manhattan Restaurant-Alternative App (Python + Streamlit)

## 1. Goal and definition of done
User types a restaurant, confirms the match, sets filters, clicks "Find alternatives", and sees comparable restaurants with data-based, template-written explanations. No runtime LLM calls. Use Python 3.12.
Done means ALL of these are true:
- `pytest` passes; `streamlit run app.py` starts with NO Yelp key and works end to end in local mode.
- Searching "don angie" gives suggestions with addresses, and results appear after selection.
- With a Yelp key, results are enriched with rating, review count and price per Section 5.
- README explains setup, data build, testing, and deployment; no secrets or real Yelp data are in the repo.
- The app is deployable on Streamlit Community Cloud from `main` with entry file `app.py`.
The repo starts EMPTY (no notebooks, no data, no README). Create everything.

## 2. Data (build it, don't assume it)
- Source: NYC Open Data, "DOHMH New York City Restaurant Inspection Results" (Socrata dataset id 43nn-pn8j on data.cityofnewyork.us). Verify the id and column names on first download.
- Write `scripts/build_directory.py` to download it (Socrata CSV/JSON API, filter BORO = Manhattan), keep one row per CAMIS (restaurant id) using the most recent inspection, drop restaurants whose latest inspection is older than 18 months, and write `data/directory.csv` with columns: nyc_id, name, address, zip, lat, lon, cuisine, cuisine_family.
- If lat/lon are missing for a row, leave them empty (the distance tier then skips that restaurant). Do not guess.
- Write `data/cuisine_families.csv` mapping each distinct cuisine description in the data to one of at most 15 conservative families (e.g. Italian/Pizza, East Asian, Latin American, Middle Eastern/Mediterranean). A cuisine with no clear family gets its own family. List the mapping in your phase summary so I can review it.
- Create `data/aliases.csv` with header only: alias, nyc_id, source.
- COMMIT `data/directory.csv`, `data/cuisine_families.csv`, `data/aliases.csv` (public open data). Do NOT commit the SQLite cache or anything under `data/cache/`.

## 3. Storage
- Permanent: directory, aliases, mappings (nyc_id -> yelp_id with matched_at and method).
- Temporary: yelp_cache (yelp_id, name, rating, review_count, price, fetched_at), expires after 24 hours.
- Use SQLite at runtime in a temp or `data/cache/` path. On startup the app loads the directory CSV, creates empty cache and mapping tables if missing, and must work with an empty cache (hosting disk is not reliable). Support CSV import/export of mappings.

## 4. Name matching (`name_matching.py`, local, RapidFuzz)
Pipeline: normalize (lowercase, trim, punctuation, accents via `unicodedata`) -> exact match -> alias match -> fuzzy top 5 (`rapidfuzz.process.extract`) -> user must choose from names shown with addresses.
A fuzzy score never selects a restaurant. Typos, "don angie", and "Don Angie NYC" produce suggestions. Duplicate names and branches always require a choice. No match shows a clear empty state.

## 5. Recommendations (`recommender.py`)
**Cuisine similarity:** same cuisine = 1.0; same cuisine_family but different cuisine = 0.6; otherwise excluded. Label: 1.0 "same cuisine", 0.6 "related cuisine". Rating, reviews, price and distance are never part of this score.

**Two-stage retrieval:**
- Stage 1 (local): exclude the reference; keep similarity > 0; apply optional distance limit and optional required cuisine; sort by the tier key below (using only available tiers); take the top 40.
- Stage 2 (only on button click, only if a Yelp key is present and Yelp is enabled): fetch Yelp data for the reference and those 40 via mapped IDs, cache-first. Resolve a missing mapping with Business Match (name + address); skip an uncertain match and label it "not enriched". Hard cap of 45 Yelp calls per click. Then apply rating, review and price filters and return the top 10.
- Without Yelp (no key, disabled, 429, auth error, or expired data): return Stage 1 results (top 10), show "Yelp data unavailable", and disable the rating/review/price controls with a visible explanation. Never show Yelp data older than 24 hours.

**Filters (defaults are starting assumptions):** max reviews relative to reference 50%; min reviews 20; min rating 4.0; optional absolute max reviews, max distance, price cap, required cuisine. effective_max_reviews = min(absolute_max, int(reference_reviews * ratio)); limits are inclusive. Missing reference review count disables the relative filter. If the relative max is below the minimum, explain the conflict. If nothing qualifies, show an empty state suggesting which filters to relax. Never change a user's settings silently.

**Ranking key (ascending):**
1. cuisine_band = -floor(similarity * 10)
2. distance_band: <=1 km 0, <=3 km 1, <=5 km 2, >5 km 3, unknown 99 (haversine; skip if reference has no coordinates)
3. price_gap = abs(candidate_price - reference_price) (Yelp price level 1 to 4); unknown on either side 99
4. -rating (missing last)
5. exact distance_km (missing last)
6. name A to Z
A tier whose reference value is missing is inert for all candidates and is listed in tiers_skipped with the reason. Unknown values never count as a match, and unknown price is not cheap. The reference is never returned.

**Output per result:** restaurant_id, name, address, cuisine, shared_cuisine_label, similarity_score, rating, review_count, review_ratio, price_level, distance_km, distance_band, price_gap, tiers_used, tiers_skipped, data_fetched_at.
**Explanation (template only):** "Same cuisine (Italian); 1.2 km away; has 15% as many reviews as your reference; price information is unavailable." Add clauses only for known values. Show: "Score reflects cuisine only, not menu, atmosphere, or quality."

## 6. Yelp rules (mandatory)
- Yelp content may be cached at most 24 hours (fetched_at on every record); only Yelp business IDs are permanent.
- Call Yelp only on the Find alternatives click, never on keystrokes or Streamlit reruns. No bulk pre-fetching or scheduled calls.
- All Yelp code lives in `yelp_client.py` (match, details, quota counting per session and day, handling of missing key, auth errors, 429), switchable by a YELP_ENABLED setting so the app runs without it.
- `refresh.py` refreshes a FIXED set of IDs and deletes expired entries. Do not use Streamlit caching for Yelp data; expiry is checked in our own code.
- Never put real Yelp data in tests, fixtures, prompts or Git. Use synthetic fixtures and mocked responses.
- Yelp's terms for our use are UNVERIFIED (analysis, display, deployment). Put a clear warning in the README. The free trial is time-limited (about 30 days) and capped at about 5,000 calls.
- Keys: `.env` locally, Streamlit Cloud Secrets when deployed. Commit only `.env.example` and `.streamlit/secrets.toml.example` with placeholders.

## 7. UI (`app.py`)
Name search (local only) -> suggestions with addresses -> confirmed reference summary -> filters (cuisine, min rating, min reviews, relative-review slider, optional absolute max, price, distance) -> Find alternatives -> result cards (label, explanation, evidence, fetched-at) -> map when coordinates exist -> panel showing the filters actually applied -> clear "unavailable" labels. Use @st.cache_resource only for the directory and DB connection.

## 8. Tests (pytest; synthetic data and mocked Yelp only)
Reference never returned; inclusive limits; percentage cap; conflicting thresholds; cuisine and family similarity; band boundaries; ordering within a band by distance then price; missing distance or price (candidate and reference); unknown never beats known; typo suggestions, no-match, duplicate branches; cache expiry and failed refresh; missing key, auth error and 429 fall back to local mode; Yelp call cap.

## 9. Phases (stop after each with a summary of 5 lines or fewer)
1. Skeleton and data: folders, pinned `requirements.txt` (streamlit, pandas, rapidfuzz, requests, python-dotenv, pytest), `.gitignore`, `.env.example`, `.streamlit/secrets.toml.example`, README stub, `scripts/build_directory.py`, run it, show the cuisine-family mapping for my review.
2. `data_store.py` and `name_matching.py` with tests.
3. `recommender.py` (local mode: similarity, filters, ranking, explanations) with tests.
4. `yelp_client.py`, cache, `refresh.py`, and the fallback with mocked tests.
5. `app.py`; run it locally and fix errors; then the final README (setup, data build, tests, methodology, limitations, Yelp warning, Streamlit Cloud deployment steps) and a check that no secrets, caches or Yelp data are in the repo.

## 10. Git
Work directly on `main`; commit after each phase with a clear message.

## 11. Still open (flag, do not decide)
Whether Yelp permits our academic/app use; whether to replace Yelp with another licensed source for ratings and review counts.