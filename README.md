# Manhattan Restaurant Alternatives

A Streamlit app that suggests comparable Manhattan restaurants for a restaurant you name.
It uses public NYC Open Data for the directory and, optionally, Yelp for rating, review
count and price. Explanations are template-written from the data — there are **no LLM calls**.

> **⚠️ Yelp warning.** Yelp's terms for this use (analysis, display, public deployment)
> have **not been verified**. Check the Yelp Fusion API Terms of Use before enabling Yelp,
> especially for a public deployment. The free trial is time-limited (about 30 days) and
> capped at about 5,000 API calls. The app works fully without Yelp (local mode).
> Never commit API keys, `.env`, `secrets.toml`, the SQLite cache, or any Yelp data.

## Setup (Windows, Python 3.12)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env      # optional: add YELP_API_KEY to enable Yelp
streamlit run app.py
```

With no key (or `YELP_ENABLED=false`) the app runs in **local mode**: cuisine and distance
only, with the rating/review/price filters disabled and a visible explanation.

### Settings (`.env` locally, Streamlit Secrets when deployed)

| Name | Default | Meaning |
|---|---|---|
| `YELP_API_KEY` | — | Yelp Fusion API key. Missing = local mode. |
| `YELP_ENABLED` | `true` | `false` switches Yelp off entirely. |
| `YELP_DAILY_LIMIT` | `300` | Max Yelp calls per UTC day. |
| `YELP_TOTAL_LIMIT` | `4500` | Lifetime call budget (stored in the `yelp_quota` table). When reached: local mode with "Yelp trial call budget used up". |

## Data build

```powershell
python scripts/build_directory.py
```

Downloads the DOHMH *New York City Restaurant Inspection Results* (Socrata dataset
`43nn-pn8j`), filters to Manhattan, keeps the most recent inspection per restaurant (CAMIS),
drops restaurants whose latest inspection is older than 18 months, and writes:

- `data/directory.csv` — `nyc_id, name, address, zip, lat, lon, cuisine, cuisine_family`
  (missing coordinates are left empty, never guessed)
- `data/cuisine_families.csv` — each cuisine mapped to one of ≤15 conservative families
- `data/aliases.csv` — header only (`alias, nyc_id, source`); add your own aliases here

These three files are public open data and are committed. The runtime SQLite database
(`data/cache/`) is created on startup and is never committed.

## Tests

```powershell
pytest
```

All tests use synthetic data and mocked Yelp responses — no network and no real Yelp data.
`tests/test_app.py` runs the app end to end in local mode with Streamlit's `AppTest`.

## Using the app

1. Type a restaurant name. Matching is local: exact name → alias → fuzzy top 5.
   You always choose from names shown **with addresses**; a fuzzy score never picks for you,
   and restaurants with several branches always require a choice.
2. Confirm the reference and set filters.
3. Click **Find alternatives**. This click is the only time Yelp is ever called.
4. Read the result cards (label, explanation, evidence, Yelp fetched-at time), the map, and
   the "Filters actually applied" panel.

### Refreshing the Yelp cache

```powershell
python refresh.py YELP_ID [YELP_ID ...]     # refresh a fixed list of IDs
python refresh.py --ids-file ids.txt
python refresh.py --purge-only              # only delete expired entries
```

`refresh.py` never discovers IDs or runs on a schedule. Mappings (NYC id → Yelp id) can be
exported/imported as CSV from the app's sidebar.

## Methodology

**Cuisine similarity** (the only "score"): same cuisine = 1.0 ("same cuisine"); same
family but different cuisine = 0.6 ("related cuisine"); anything else is excluded. Rating,
reviews, price and distance never enter this score. Restaurants in the *Other/Unclassified*
family (Fusion, Other, Not Listed/Not Applicable, Australian) are never recommended and
cannot be used as a reference.

**Two-stage retrieval.**
- *Stage 1 (local):* exclude the reference and all branches with the same normalized name;
  apply optional distance limit and required cuisine; rank; keep the top 40.
- *Stage 2 (Yelp, on click only):* cache-first lookup of the reference and those 40
  (Business Match by name + address when no mapping exists; uncertain matches are skipped
  and labelled "not enriched"); hard cap of 45 calls per click; then rating, review and
  price filters apply and the top 10 are returned.
- *Fallback:* no key, Yelp disabled, 401/403, 429, exhausted budget, or only expired data →
  Stage 1 top 10 with "Yelp data unavailable".

**Filters** (defaults are starting assumptions; all limits inclusive): min rating 4.0,
min reviews 20, max reviews 50% of the reference, optional absolute max reviews, price cap,
max distance, required cuisine. `effective_max_reviews = min(absolute_max, int(reference_reviews × ratio))`.
A missing reference review count disables the relative limit; conflicting limits are
explained; empty results suggest which filter to relax. Settings are never changed silently.

**Ranking** (ascending): cuisine band → distance band (≤1, ≤3, ≤5, >5 km, unknown last;
haversine) → price-level gap → higher rating → exact distance → name. A tier whose
reference value is missing is skipped for everyone and listed with its reason. Unknown
values never count as a match, and unknown price is not treated as cheap.

**Explanations** are templates with clauses only for known values, e.g.
*"Same cuisine (Italian); 1.2 km away; has 15% as many reviews as your reference; price
information is unavailable."*

## Limitations

- Similarity reflects cuisine labels only — not menu, atmosphere or quality. Inspection-data
  cuisine labels are coarse and sometimes wrong.
- The directory comes from health inspections: it may include closed restaurants or miss
  new ones, and branch detection is by exact normalized name only.
- Some restaurants have no coordinates; they are excluded from distance filtering and the map.
- Yelp data is held for at most 24 hours; with an empty cache, every click may spend calls.
  The 45-call cap means some candidates can remain "not enriched".
- Streamlit Community Cloud disk is not persistent: the cache, mappings and quota counter
  can reset on redeploy/restart (so the lifetime counter is a safeguard, not a guarantee —
  watch your usage in the Yelp developer console too).
- Yelp's permission for this use is an open question (see warning above), as is whether to
  replace Yelp with another licensed source for ratings and review counts.

## Deploying on Streamlit Community Cloud

1. Push this repo to GitHub (check that `.env`, `.streamlit/secrets.toml` and `data/cache/`
   are not tracked: `git ls-files`).
2. At <https://share.streamlit.io>, click **Create app** → choose the repo, branch `main`,
   main file `app.py`.
3. Under **Advanced settings**, pick Python 3.12 and paste secrets in TOML format
   (see `.streamlit/secrets.toml.example`), or leave them empty for local mode:
   ```toml
   YELP_API_KEY = "..."
   YELP_ENABLED = "true"
   YELP_DAILY_LIMIT = "300"
   YELP_TOTAL_LIMIT = "4500"
   ```
4. Deploy. The app loads `data/directory.csv` from the repo and creates an empty SQLite
   cache at startup.
