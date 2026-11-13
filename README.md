# Manhattan Restaurant Alternatives

Streamlit app that suggests comparable Manhattan restaurants for a reference restaurant,
using NYC Open Data plus optional Yelp enrichment. *Work in progress — full README comes in Phase 5.*

## Setup (Windows, Python 3.12)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env   # optional: add a Yelp API key
```

## Data build

```powershell
python scripts/build_directory.py
```

Downloads the DOHMH Restaurant Inspection Results (Socrata `43nn-pn8j`), keeps Manhattan
restaurants inspected in the last 18 months, and writes `data/directory.csv` and
`data/cuisine_families.csv`.

> **Warning:** Yelp's terms for this use (analysis, display, deployment) are unverified.
> Never commit API keys, `.env`, caches, or Yelp data.
