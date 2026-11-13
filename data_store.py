"""Storage: directory/alias/family CSVs (permanent) and SQLite tables for
Yelp ID mappings (permanent) and the Yelp cache (temporary, 24-hour expiry).

The app must work with an empty or missing database: every connection
creates the tables if they do not exist.
"""
from __future__ import annotations

import csv
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DIRECTORY_CSV = DATA_DIR / "directory.csv"
ALIASES_CSV = DATA_DIR / "aliases.csv"
FAMILIES_CSV = DATA_DIR / "cuisine_families.csv"
DEFAULT_DB_PATH = DATA_DIR / "cache" / "app.sqlite"

DIRECTORY_COLUMNS = ["nyc_id", "name", "address", "zip", "lat", "lon", "cuisine", "cuisine_family"]
ALIAS_COLUMNS = ["alias", "nyc_id", "source"]
MAPPING_COLUMNS = ["nyc_id", "yelp_id", "matched_at", "method"]
CACHE_TTL = timedelta(hours=24)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS mappings (
    nyc_id     TEXT PRIMARY KEY,
    yelp_id    TEXT NOT NULL,
    matched_at TEXT NOT NULL,
    method     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS yelp_cache (
    yelp_id      TEXT PRIMARY KEY,
    name         TEXT,
    rating       REAL,
    review_count INTEGER,
    price        INTEGER,
    fetched_at   TEXT NOT NULL
);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # fixed width so string comparison in SQL matches time order
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _parse(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- CSV data

def load_directory(path: str | Path = DIRECTORY_CSV) -> pd.DataFrame:
    """Load the restaurant directory. IDs/zip stay strings; missing lat/lon are NaN."""
    df = pd.read_csv(path, dtype={"nyc_id": str, "zip": str}, keep_default_na=False, na_values={"lat": [""], "lon": [""]})
    missing = set(DIRECTORY_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"directory CSV is missing columns: {sorted(missing)}")
    df = df[DIRECTORY_COLUMNS].copy()
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
    for col in ("nyc_id", "name", "address", "zip", "cuisine", "cuisine_family"):
        df[col] = df[col].astype(str).str.strip()
    return df.reset_index(drop=True)


def load_aliases(path: str | Path = ALIASES_CSV) -> pd.DataFrame:
    """Load aliases (alias, nyc_id, source). A missing file means no aliases."""
    path = Path(path)
    if not path.exists():
        return pd.DataFrame(columns=ALIAS_COLUMNS)
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    return df.reindex(columns=ALIAS_COLUMNS, fill_value="")


def load_cuisine_families(path: str | Path = FAMILIES_CSV) -> dict[str, str]:
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    return dict(zip(df["cuisine"].str.strip(), df["cuisine_family"].str.strip()))


# ---------------------------------------------------------------- SQLite

def connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open (creating if needed) the runtime DB and ensure tables exist.
    Pass ":memory:" for an in-memory DB."""
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


# Mappings (permanent: only Yelp business IDs may be stored long term)

def upsert_mapping(conn, nyc_id: str, yelp_id: str, method: str, matched_at: datetime | None = None) -> None:
    conn.execute(
        "INSERT INTO mappings (nyc_id, yelp_id, matched_at, method) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(nyc_id) DO UPDATE SET yelp_id=excluded.yelp_id, "
        "matched_at=excluded.matched_at, method=excluded.method",
        (str(nyc_id), str(yelp_id), _iso(matched_at or utcnow()), method),
    )
    conn.commit()


def get_mapping(conn, nyc_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM mappings WHERE nyc_id = ?", (str(nyc_id),)).fetchone()
    return dict(row) if row else None


def export_mappings_csv(conn, path: str | Path) -> int:
    rows = conn.execute("SELECT nyc_id, yelp_id, matched_at, method FROM mappings ORDER BY nyc_id").fetchall()
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(MAPPING_COLUMNS)
        writer.writerows([tuple(r) for r in rows])
    return len(rows)


def import_mappings_csv(conn, path: str | Path) -> int:
    """Import mappings; rows missing nyc_id or yelp_id are skipped. Returns rows imported."""
    count = 0
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = set(MAPPING_COLUMNS) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"mappings CSV is missing columns: {sorted(missing)}")
        for row in reader:
            nyc_id, yelp_id = (row["nyc_id"] or "").strip(), (row["yelp_id"] or "").strip()
            if not nyc_id or not yelp_id:
                continue
            matched_at = _parse(row["matched_at"]) if row["matched_at"] else None
            upsert_mapping(conn, nyc_id, yelp_id, row["method"] or "import", matched_at)
            count += 1
    return count


# Yelp cache (temporary, max 24 hours)

def put_cache(conn, yelp_id: str, name, rating, review_count, price, fetched_at: datetime | None = None) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO yelp_cache (yelp_id, name, rating, review_count, price, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (yelp_id, name, rating, review_count, price, _iso(fetched_at or utcnow())),
    )
    conn.commit()


def is_fresh(fetched_at: str, now: datetime | None = None) -> bool:
    return (now or utcnow()) - _parse(fetched_at) < CACHE_TTL


def get_cache(conn, yelp_id: str, now: datetime | None = None) -> dict | None:
    """Return a cache record only if it is younger than 24 hours."""
    row = conn.execute("SELECT * FROM yelp_cache WHERE yelp_id = ?", (yelp_id,)).fetchone()
    if row is None or not is_fresh(row["fetched_at"], now):
        return None
    return dict(row)


def delete_expired(conn, now: datetime | None = None) -> int:
    cutoff = _iso((now or utcnow()) - CACHE_TTL)
    cur = conn.execute("DELETE FROM yelp_cache WHERE fetched_at <= ?", (cutoff,))
    conn.commit()
    return cur.rowcount
