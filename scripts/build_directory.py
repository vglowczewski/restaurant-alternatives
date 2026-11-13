"""Build data/directory.csv and data/cuisine_families.csv from NYC Open Data.

Source: DOHMH New York City Restaurant Inspection Results (Socrata id 43nn-pn8j).
Keeps Manhattan only, one row per CAMIS (most recent inspection), and drops
restaurants whose latest inspection is older than 18 months.

Usage:
    python scripts/build_directory.py [--as-of YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import requests

DATASET_URL = "https://data.cityofnewyork.us/resource/43nn-pn8j.json"
PAGE_SIZE = 50_000
MAX_AGE_MONTHS = 18

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DIRECTORY_CSV = DATA_DIR / "directory.csv"
FAMILIES_CSV = DATA_DIR / "cuisine_families.csv"
ALIASES_CSV = DATA_DIR / "aliases.csv"

SOURCE_COLUMNS = [
    "camis", "dba", "building", "street", "zipcode",
    "latitude", "longitude", "cuisine_description", "inspection_date",
]
OUTPUT_COLUMNS = ["nyc_id", "name", "address", "zip", "lat", "lon", "cuisine", "cuisine_family"]

# Conservative cuisine -> family mapping (max 15 families). Review before relying on it.
FAMILIES: dict[str, list[str]] = {
    "Italian/Pizza": ["Italian", "Pizza"],
    "East Asian": ["Chinese", "Japanese", "Korean", "Chinese/Japanese", "Asian/Asian Fusion"],
    "Southeast Asian/Pacific": ["Thai", "Filipino", "Indonesian", "Southeast Asian", "Polynesian", "Hawaiian"],
    "South Asian": ["Indian", "Pakistani", "Bangladeshi", "Afghan"],
    "Middle Eastern/Mediterranean": [
        "Middle Eastern", "Mediterranean", "Lebanese", "Turkish", "Greek",
        "Iranian", "Armenian", "Egyptian", "Moroccan",
    ],
    "Latin American/Caribbean": [
        "Latin American", "Mexican", "Tex-Mex", "Peruvian", "Brazilian",
        "Chilean", "Caribbean", "Chinese/Cuban",
    ],
    "European": [
        "French", "New French", "Spanish", "Tapas", "Basque", "Portuguese",
        "German", "Czech", "Polish", "Russian", "Eastern European",
        "Scandinavian", "English", "Irish", "Continental", "Haute Cuisine",
    ],
    "American": [
        "American", "New American", "Californian", "Southwestern", "Australian",
        "Hamburgers", "Hotdogs", "Hotdogs/Pretzels", "Barbecue", "Steakhouse",
        "Chicken", "Soul Food", "Cajun", "Creole", "Creole/Cajun",
    ],
    "African": ["African", "Ethiopian"],
    "Cafe/Bakery/Desserts": [
        "Coffee/Tea", "Bakery Products/Desserts", "Donuts", "Frozen Desserts",
        "Bagels/Pretzels", "Pancakes/Waffles", "Nuts/Confectionary",
        "Juice, Smoothies, Fruit Salads", "Bottled Beverages", "Fruits/Vegetables",
    ],
    "Sandwiches/Salads/Soups": [
        "Sandwiches", "Salads", "Sandwiches/Salads/Mixed Buffet", "Soups", "Soups/Salads/Sandwiches",
    ],
    "Vegetarian/Vegan": ["Vegetarian", "Vegan"],
    "Seafood": ["Seafood"],
    "Jewish/Kosher": ["Jewish/Kosher"],
    "Other/Unclassified": ["Fusion", "Other", "Not Listed/Not Applicable"],
}
CUISINE_TO_FAMILY = {c: fam for fam, cuisines in FAMILIES.items() for c in cuisines}


def download(cutoff: date) -> pd.DataFrame:
    """Download Manhattan rows inspected on/after cutoff, paging through the API."""
    where = f"boro='Manhattan' AND inspection_date >= '{cutoff.isoformat()}T00:00:00'"
    frames, offset = [], 0
    while True:
        params = {
            "$select": ",".join(SOURCE_COLUMNS),
            "$where": where,
            "$order": "camis,inspection_date",
            "$limit": PAGE_SIZE,
            "$offset": offset,
        }
        resp = requests.get(DATASET_URL, params=params, timeout=120)
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            break
        frames.append(pd.DataFrame(rows))
        print(f"  fetched {offset + len(rows)} rows", file=sys.stderr)
        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    if not frames:
        return pd.DataFrame(columns=SOURCE_COLUMNS)
    df = pd.concat(frames, ignore_index=True)
    missing = [c for c in SOURCE_COLUMNS if c not in df.columns]
    for c in missing:  # Socrata omits keys that are null in every row of a page
        df[c] = pd.NA
    return df[SOURCE_COLUMNS]


def _clean_str(s: pd.Series) -> pd.Series:
    return s.fillna("").astype(str).str.strip().str.replace(r"\s+", " ", regex=True)


def _coord(s: pd.Series) -> pd.Series:
    """Numeric coordinate; 0 (the dataset's 'no location' value) becomes empty."""
    v = pd.to_numeric(s, errors="coerce")
    return v.where(v != 0)


def build_directory(raw: pd.DataFrame, cutoff: date) -> pd.DataFrame:
    df = raw.copy()
    df["inspection_date"] = pd.to_datetime(df["inspection_date"], errors="coerce")
    df = df[df["inspection_date"] >= pd.Timestamp(cutoff)]
    df = df.sort_values(["camis", "inspection_date"]).drop_duplicates("camis", keep="last")

    building, street = _clean_str(df["building"]), _clean_str(df["street"])
    cuisine = _clean_str(df["cuisine_description"])
    out = pd.DataFrame({
        "nyc_id": _clean_str(df["camis"]),
        "name": _clean_str(df["dba"]),
        "address": (building + " " + street).str.strip(),
        "zip": _clean_str(df["zipcode"]),
        "lat": _coord(df["latitude"]),
        "lon": _coord(df["longitude"]),
        "cuisine": cuisine,
        "cuisine_family": cuisine.map(family_for),
    })
    out = out[(out["nyc_id"] != "") & (out["name"] != "")]
    return out.sort_values("nyc_id").reset_index(drop=True)[OUTPUT_COLUMNS]


def family_for(cuisine: str) -> str:
    """Mapped family; an unmapped cuisine gets its own family. Empty stays empty."""
    if not cuisine:
        return ""
    return CUISINE_TO_FAMILY.get(cuisine, cuisine)


def build_families(directory: pd.DataFrame) -> pd.DataFrame:
    cuisines = sorted(c for c in directory["cuisine"].unique() if c)
    unmapped = [c for c in cuisines if c not in CUISINE_TO_FAMILY]
    if unmapped:
        print(f"WARNING: unmapped cuisines given their own family: {unmapped}", file=sys.stderr)
    return pd.DataFrame({"cuisine": cuisines, "cuisine_family": [family_for(c) for c in cuisines]})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today(),
                        help="Reference date for the 18-month cutoff (default: today)")
    args = parser.parse_args()
    cutoff = (pd.Timestamp(args.as_of) - pd.DateOffset(months=MAX_AGE_MONTHS)).date()

    print(f"Downloading Manhattan inspections since {cutoff} ...", file=sys.stderr)
    raw = download(cutoff)
    directory = build_directory(raw, cutoff)
    families = build_families(directory)

    DATA_DIR.mkdir(exist_ok=True)
    directory.to_csv(DIRECTORY_CSV, index=False, float_format="%.6f")
    families.to_csv(FAMILIES_CSV, index=False)
    if not ALIASES_CSV.exists():
        ALIASES_CSV.write_text("alias,nyc_id,source\n", encoding="utf-8")

    n_no_coords = int(directory["lat"].isna().sum())
    print(f"Wrote {len(directory)} restaurants ({n_no_coords} without coordinates), "
          f"{len(families)} cuisines in {families['cuisine_family'].nunique()} families.", file=sys.stderr)


if __name__ == "__main__":
    main()
