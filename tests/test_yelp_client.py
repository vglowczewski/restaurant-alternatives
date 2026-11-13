"""Yelp client, cache, refresh and fallback tests. Synthetic data and mocked HTTP only."""
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
import requests

import data_store as ds
import refresh as rf
import yelp_client as yc
from recommender import YELP_UNAVAILABLE, Filters, recommend

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
KEY = yc.YelpSettings(api_key="test-key", enabled=True, daily_limit=10_000)


class FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload


class FakeHttp:
    """Synthetic Yelp: match returns 'y-<nyc name>'; details return fixed values."""

    def __init__(self, status=200, details=None, match_name=None, fail_ids=()):
        self.status = status
        self.details = details or {}
        self.match_name = match_name
        self.fail_ids = set(fail_ids)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params))
        assert headers["Authorization"] == "Bearer test-key"
        if self.status != 200:
            return FakeResponse(self.status)
        if url.endswith("/businesses/matches"):
            name = self.match_name or params["name"]
            return FakeResponse(200, {"businesses": [{
                "id": f"y-{params['name'].lower().replace(' ', '-')}", "name": name,
                "location": {"zip_code": params.get("postal_code") or ""}}]})
        yelp_id = url.rsplit("/", 1)[1]
        if yelp_id in self.fail_ids:
            return FakeResponse(500)
        d = self.details.get(yelp_id, {"rating": 4.5, "review_count": 50, "price": "$$"})
        return FakeResponse(200, {"id": yelp_id, "name": "SYNTHETIC", **d})


@pytest.fixture
def conn():
    return ds.connect(":memory:")


def client(conn, http, settings=KEY, now=NOW):
    return yc.YelpClient(conn, settings, http=http, now=lambda: now)


def row(i, name):
    return {"nyc_id": str(i), "name": name, "address": f"{i} TEST ST", "zip": "10001"}


def make_dir(n):
    rows = [dict(row("R", "REF"), lat=40.7, lon=-74.0, cuisine="Italian", cuisine_family="Italian/Pizza")]
    rows += [dict(row(i, f"CAND {i}"), lat=40.7 + i * 0.001, lon=-74.0, cuisine="Italian",
                  cuisine_family="Italian/Pizza") for i in range(n)]
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- settings

def test_settings_missing_key_not_usable(monkeypatch):
    monkeypatch.setattr(yc, "_secret", lambda name: None)
    s = yc.YelpSettings.load()
    assert s.api_key is None and not s.usable


def test_settings_disabled_and_placeholder(monkeypatch):
    vals = {"YELP_API_KEY": "real", "YELP_ENABLED": "false"}
    monkeypatch.setattr(yc, "_secret", vals.get)
    assert not yc.YelpSettings.load().usable
    assert not yc.YelpSettings(api_key="your-yelp-api-key-here").usable


def test_price_level():
    assert [yc.price_level(p) for p in ("$", "$$$$", "", None, "$$$$$", "€")] == [1, 4, None, None, None, None]


# ---------------------------------------------------------------- matching and cache

def test_match_then_details_cached_and_mapped(conn):
    http = FakeHttp()
    c = client(conn, http)
    rec = c.record_for(row(1, "DON ANGIE"))
    assert rec["rating"] == 4.5 and rec["price"] == 2 and rec["fetched_at"]
    assert ds.get_mapping(conn, "1")["method"] == "business_match"
    assert len(http.calls) == 2
    c.record_for(row(1, "DON ANGIE"))  # cache hit: no new calls
    assert len(http.calls) == 2


def test_uncertain_match_is_not_enriched(conn):
    http = FakeHttp(match_name="Totally Different Place")
    assert client(conn, http).record_for(row(1, "DON ANGIE")) is None
    assert ds.get_mapping(conn, "1") is None


def test_expired_cache_is_refetched(conn):
    ds.upsert_mapping(conn, "1", "y-1", "manual")
    ds.put_cache(conn, "y-1", "OLD", 3.0, 10, 1, NOW - timedelta(hours=25))
    http = FakeHttp()
    rec = client(conn, http).record_for(row(1, "X"))
    assert rec["rating"] == 4.5 and len(http.calls) == 1


# ---------------------------------------------------------------- fallback

def test_missing_key_falls_back_to_local(conn):
    assert yc.make_enricher(conn, yc.YelpSettings(api_key=None)) is None
    res = recommend(make_dir(3), "R", Filters(), enricher=None)
    assert not res.yelp_available and YELP_UNAVAILABLE in res.notices and len(res.results) == 3


@pytest.mark.parametrize("status", [401, 403, 429])
def test_auth_error_and_429_fall_back_to_local(conn, status):
    http = FakeHttp(status=status)
    enricher = client(conn, http).enrich
    res = recommend(make_dir(3), "R", Filters(), enricher=enricher, now=NOW)
    assert not res.yelp_available and YELP_UNAVAILABLE in res.notices
    assert len(res.results) == 3 and all(r["rating"] is None for r in res.results)
    assert len(http.calls) == 1  # stops at the first fatal error


def test_network_error_on_one_business_leaves_it_not_enriched(conn):
    class Flaky(FakeHttp):
        def get(self, url, **kw):
            if "CAND 0" in str(kw.get("params")):
                raise requests.ConnectionError("boom")
            return super().get(url, **kw)

    recs = client(conn, Flaky()).enrich(row("R", "REF"), [dict(row(0, "CAND 0"), restaurant_id="0"),
                                                          dict(row(1, "CAND 1"), restaurant_id="1")])
    assert set(recs) == {"R", "1"}


def test_only_expired_data_falls_back(conn):
    ds.upsert_mapping(conn, "R", "y-r", "manual")
    ds.put_cache(conn, "y-r", "OLD", 4.0, 100, 2, NOW - timedelta(hours=30))
    c = client(conn, FakeHttp(status=429))
    assert c.enrich(row("R", "REF"), []) is None


def test_stale_data_never_shown(conn):
    # enricher hands over a stale record: recommender must not use it
    stale = {"R": {"rating": 4.5, "review_count": 100, "price": 2, "fetched_at": NOW.isoformat()},
             "0": {"rating": 5.0, "review_count": 30, "price": 2,
                   "fetched_at": (NOW - timedelta(hours=24, seconds=1)).isoformat()}}
    res = recommend(make_dir(1), "R", Filters(min_rating=None, min_reviews=None, max_review_ratio=None),
                    enricher=lambda ref, cands: stale, now=NOW)
    assert res.results[0]["rating"] is None and res.results[0]["data_fetched_at"] is None


def test_enrichment_end_to_end(conn):
    http = FakeHttp(details={"y-ref": {"rating": 4.6, "review_count": 200, "price": "$$"}})
    res = recommend(make_dir(3), "R", Filters(), enricher=client(conn, http).enrich, now=NOW)
    assert res.yelp_available
    assert [r["review_count"] for r in res.results] == [50, 50, 50]
    assert all(r["enriched"] and r["data_fetched_at"] for r in res.results)


# ---------------------------------------------------------------- call caps

def test_call_cap_45_per_click(conn):
    http = FakeHttp()
    c = client(conn, http)
    d = make_dir(40)
    res = recommend(d, "R", Filters(min_rating=None, min_reviews=None, max_review_ratio=None),
                    enricher=c.enrich, now=NOW)
    assert len(http.calls) == yc.CALLS_PER_CLICK
    assert c.session_calls == 45 and ds.get_daily_calls(conn, NOW) == 45
    assert res.yelp_available
    # second click: cached ones need no calls, the cap restarts at 45
    recommend(d, "R", Filters(), enricher=c.enrich, now=NOW)
    assert len(http.calls) <= 90 and c.session_calls == len(http.calls)


def test_daily_limit_stops_calls(conn):
    http = FakeHttp()
    c = client(conn, http, settings=yc.YelpSettings(api_key="test-key", daily_limit=3))
    c.enrich(row("R", "REF"), [dict(row(i, f"C {i}"), restaurant_id=str(i)) for i in range(5)])
    assert len(http.calls) == 3 and ds.get_daily_calls(conn, NOW) == 3


def test_no_calls_without_enrich(conn):
    http = FakeHttp()
    client(conn, http)
    assert http.calls == []


# ---------------------------------------------------------------- refresh

def test_refresh_fixed_ids_and_purge(conn):
    ds.put_cache(conn, "y-old", "OLD", 3.0, 5, 1, NOW - timedelta(hours=48))
    http = FakeHttp()
    report = rf.refresh(conn, ["y-a", "y-b", "y-a"], client=client(conn, http))
    assert report["refreshed"] == ["y-a", "y-b"] and report["failed"] == {}
    assert report["expired_deleted"] == 1 and len(http.calls) == 2
    assert ds.get_cache(conn, "y-a", NOW)["rating"] == 4.5


def test_failed_refresh_keeps_old_entry_which_still_expires(conn):
    ds.put_cache(conn, "y-a", "OLD", 3.0, 5, 1, NOW - timedelta(hours=1))
    report = rf.refresh(conn, ["y-a"], client=client(conn, FakeHttp(fail_ids={"y-a"})))
    assert "y-a" in report["failed"]
    assert ds.get_cache(conn, "y-a", NOW)["rating"] == 3.0
    assert ds.get_cache(conn, "y-a", NOW + timedelta(hours=23, minutes=1)) is None


def test_refresh_stops_on_429(conn):
    http = FakeHttp(status=429)
    report = rf.refresh(conn, ["y-a", "y-b"], client=client(conn, http))
    assert set(report["failed"]) == {"y-a", "y-b"} and len(http.calls) == 1


def test_refresh_without_key(conn):
    report = rf.refresh(conn, ["y-a"], client=client(conn, FakeHttp(), settings=yc.YelpSettings(api_key=None)))
    assert report["failed"] == {"y-a": "Yelp key missing or Yelp disabled"}


# ---------------------------------------------------------------- total budget

def test_limit_defaults_and_env_override(monkeypatch):
    monkeypatch.setattr(yc, "_secret", {"YELP_API_KEY": "k"}.get)
    s = yc.YelpSettings.load()
    assert (s.daily_limit, s.total_limit) == (300, 4500)
    monkeypatch.setattr(yc, "_secret", {"YELP_API_KEY": "k", "YELP_DAILY_LIMIT": "7",
                                        "YELP_TOTAL_LIMIT": "9"}.get)
    s = yc.YelpSettings.load()
    assert (s.daily_limit, s.total_limit) == (7, 9)
    monkeypatch.setattr(yc, "_secret", {"YELP_API_KEY": "k", "YELP_TOTAL_LIMIT": "abc"}.get)
    assert yc.YelpSettings.load().total_limit == 4500


def test_total_counter_persists_across_days(conn):
    ds.add_daily_call(conn, NOW)
    ds.add_daily_call(conn, NOW + timedelta(days=1))
    assert ds.get_total_calls(conn) == 2
    assert ds.get_daily_calls(conn, NOW) == 1


def test_total_budget_used_up_falls_back_with_message(conn):
    settings = yc.YelpSettings(api_key="test-key", daily_limit=10_000, total_limit=5)
    for _ in range(5):
        ds.add_daily_call(conn, NOW - timedelta(days=3))
    http = FakeHttp()
    res = recommend(make_dir(3), "R", Filters(), enricher=client(conn, http, settings).enrich, now=NOW)
    assert http.calls == []
    assert not res.yelp_available and len(res.results) == 3
    assert yc.BUDGET_USED_UP_MESSAGE in res.notices and YELP_UNAVAILABLE in res.notices


def test_total_budget_reached_mid_click_stops_calls(conn):
    settings = yc.YelpSettings(api_key="test-key", daily_limit=10_000, total_limit=3)
    http = FakeHttp()
    res = recommend(make_dir(5), "R", Filters(), enricher=client(conn, http, settings).enrich, now=NOW)
    assert len(http.calls) == 3 and ds.get_total_calls(conn) == 3
    assert not res.yelp_available and yc.BUDGET_USED_UP_MESSAGE in res.notices


def test_refresh_stops_when_total_budget_used_up(conn):
    settings = yc.YelpSettings(api_key="test-key", total_limit=1)
    http = FakeHttp()
    report = rf.refresh(conn, ["y-a", "y-b", "y-c"], client=client(conn, http, settings))
    assert report["refreshed"] == ["y-a"] and len(http.calls) == 1
    assert report["failed"]["y-b"] == yc.BUDGET_USED_UP_MESSAGE
