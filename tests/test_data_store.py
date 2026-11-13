from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

import data_store as ds

NOW = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn():
    c = ds.connect(":memory:")
    yield c
    c.close()


def test_load_directory_types_and_missing_coords(directory):
    assert list(directory.columns) == ds.DIRECTORY_COLUMNS
    assert directory["nyc_id"].tolist()[:2] == ["1", "2"]
    row = directory.set_index("nyc_id").loc["3"]
    assert pd.isna(row["lat"]) and pd.isna(row["lon"])
    assert directory["zip"].iloc[0] == "10014"


def test_load_directory_rejects_missing_columns(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("nyc_id,name\n1,X\n", encoding="utf-8")
    with pytest.raises(ValueError):
        ds.load_directory(p)


def test_load_aliases_missing_file_is_empty(tmp_path):
    df = ds.load_aliases(tmp_path / "nope.csv")
    assert df.empty and list(df.columns) == ds.ALIAS_COLUMNS


def test_real_repo_files_load():
    df = ds.load_directory()
    assert set(ds.DIRECTORY_COLUMNS) == set(df.columns) and len(df) > 0
    fams = ds.load_cuisine_families()
    assert set(df["cuisine_family"]) <= set(fams.values())
    assert list(ds.load_aliases().columns) == ds.ALIAS_COLUMNS


def test_connect_creates_tables_on_new_file(tmp_path):
    path = tmp_path / "cache" / "app.sqlite"
    c = ds.connect(path)
    names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"mappings", "yelp_cache"} <= names
    c.close()
    assert ds.connect(path).execute("SELECT COUNT(*) FROM yelp_cache").fetchone()[0] == 0


def test_mapping_upsert_and_roundtrip_csv(conn, tmp_path):
    ds.upsert_mapping(conn, "1", "yelp-a", "business_match", NOW)
    ds.upsert_mapping(conn, "1", "yelp-b", "manual", NOW)
    ds.upsert_mapping(conn, "2", "yelp-c", "business_match", NOW)
    assert ds.get_mapping(conn, "1")["yelp_id"] == "yelp-b"
    assert ds.get_mapping(conn, "99") is None

    out = tmp_path / "mappings.csv"
    assert ds.export_mappings_csv(conn, out) == 2

    other = ds.connect(":memory:")
    assert ds.import_mappings_csv(other, out) == 2
    m = ds.get_mapping(other, "2")
    assert m["yelp_id"] == "yelp-c" and m["method"] == "business_match"
    assert ds._parse(m["matched_at"]) == NOW


def test_import_skips_incomplete_rows_and_checks_header(conn, tmp_path):
    p = tmp_path / "m.csv"
    p.write_text("nyc_id,yelp_id,matched_at,method\n1,y1,,manual\n2,,,manual\n", encoding="utf-8")
    assert ds.import_mappings_csv(conn, p) == 1
    bad = tmp_path / "bad.csv"
    bad.write_text("nyc_id,yelp_id\n1,y1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        ds.import_mappings_csv(conn, bad)


def test_cache_fresh_and_expired(conn):
    ds.put_cache(conn, "y1", "Synthetic Place", 4.5, 120, 2, fetched_at=NOW)
    assert ds.get_cache(conn, "y1", now=NOW + timedelta(hours=23, minutes=59))["review_count"] == 120
    assert ds.get_cache(conn, "y1", now=NOW + timedelta(hours=24)) is None
    assert ds.get_cache(conn, "missing", now=NOW) is None


def test_delete_expired(conn):
    ds.put_cache(conn, "old", "A", 4.0, 10, 1, fetched_at=NOW - timedelta(hours=25))
    ds.put_cache(conn, "new", "B", 4.0, 10, 1, fetched_at=NOW - timedelta(hours=1))
    assert ds.delete_expired(conn, now=NOW) == 1
    ids = [r[0] for r in conn.execute("SELECT yelp_id FROM yelp_cache")]
    assert ids == ["new"]
