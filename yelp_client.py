"""All Yelp Fusion API access lives here.

- Called only from the "Find alternatives" click (via `make_enricher`) and from
  refresh.py for a fixed set of IDs. Never on keystrokes, reruns or schedules.
- Cache-first: Yelp content is stored at most 24 hours (fetched_at on every
  record); only Yelp business IDs are stored permanently (mappings table).
- Missing key, YELP_ENABLED=false, auth errors (401/403) and 429 make the
  enricher return None so the recommender falls back to local mode.
- Hard cap of 45 Yelp calls per click; calls are also counted per session,
  per UTC day (YELP_DAILY_LIMIT) and in total (YELP_TOTAL_LIMIT). When the
  total budget is used up, Yelp is not called and the click runs in local mode
  with BUDGET_USED_UP_MESSAGE.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import requests
from rapidfuzz import fuzz

import data_store as ds
from name_matching import normalize

API_BASE = "https://api.yelp.com/v3"
CALLS_PER_CLICK = 45
DEFAULT_DAILY_LIMIT = 300
DEFAULT_TOTAL_LIMIT = 4500  # free trial is capped at ~5,000 calls
BUDGET_USED_UP_MESSAGE = "Yelp trial call budget used up"
TIMEOUT_S = 10
MATCH_NAME_MIN = 85  # business-match name similarity needed to accept a match
NOT_ENRICHED = "not enriched"


# ---------------------------------------------------------------- errors

class YelpError(Exception):
    """Base class. `fatal` errors abort the whole click (fallback to local mode)."""
    fatal = False


class YelpUnavailable(YelpError):
    fatal = True


class YelpAuthError(YelpUnavailable):
    pass


class YelpRateLimited(YelpUnavailable):
    pass


class YelpBudgetExhausted(YelpUnavailable):
    """Lifetime call budget (YELP_TOTAL_LIMIT) reached: no more Yelp calls."""
    user_message = BUDGET_USED_UP_MESSAGE

    def __init__(self, msg: str = BUDGET_USED_UP_MESSAGE):
        super().__init__(msg)


class YelpCallCapReached(YelpError):
    """Per-click cap or daily limit hit: remaining restaurants stay not enriched."""


# ---------------------------------------------------------------- settings

def _secret(name: str) -> str | None:
    """Environment / .env first, then Streamlit secrets (if running under Streamlit)."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    val = os.environ.get(name)
    if val:
        return val
    try:
        import streamlit as st
        return str(st.secrets[name]) if name in st.secrets else None
    except Exception:
        return None


@dataclass
class YelpSettings:
    api_key: str | None
    enabled: bool = True
    daily_limit: int = DEFAULT_DAILY_LIMIT
    total_limit: int = DEFAULT_TOTAL_LIMIT

    @property
    def usable(self) -> bool:
        return bool(self.enabled and self.api_key and self.api_key.strip()
                    and not self.api_key.startswith("your-"))

    @classmethod
    def load(cls) -> "YelpSettings":
        enabled = (_secret("YELP_ENABLED") or "true").strip().lower() not in ("0", "false", "no", "off")
        return cls(api_key=_secret("YELP_API_KEY"), enabled=enabled,
                   daily_limit=_int_setting("YELP_DAILY_LIMIT", DEFAULT_DAILY_LIMIT),
                   total_limit=_int_setting("YELP_TOTAL_LIMIT", DEFAULT_TOTAL_LIMIT))


def _int_setting(name: str, default: int) -> int:
    try:
        return int(_secret(name) or default)
    except ValueError:
        return default


def price_level(price) -> int | None:
    """Yelp '$'..'$$$$' -> 1..4; anything else is unknown."""
    if isinstance(price, str) and price and set(price) == {"$"} and len(price) <= 4:
        return len(price)
    return None


# ---------------------------------------------------------------- client

class YelpClient:
    """One instance per user session (keep it in st.session_state)."""

    def __init__(self, conn, settings: YelpSettings, http=None, now=None):
        self.conn = conn
        self.settings = settings
        self.http = http or requests.Session()
        self._now = now  # callable for tests
        self.session_calls = 0
        self._budget: int | None = None  # remaining calls in the current click

    def now(self):
        return self._now() if self._now else ds.utcnow()

    # -- low level -------------------------------------------------------

    def _get(self, path: str, params: dict | None = None) -> dict | None:
        """One counted API call. Returns JSON, or None for 'not found'-like results."""
        if not self.settings.usable:
            raise YelpUnavailable("Yelp key missing or Yelp disabled")
        if self.budget_used_up():
            raise YelpBudgetExhausted()
        if self._budget is not None and self._budget <= 0:
            raise YelpCallCapReached(f"per-click cap of {CALLS_PER_CLICK} calls reached")
        if ds.get_daily_calls(self.conn, self.now()) >= self.settings.daily_limit:
            raise YelpCallCapReached("daily Yelp call limit reached")

        if self._budget is not None:
            self._budget -= 1
        self.session_calls += 1
        ds.add_daily_call(self.conn, self.now())
        try:
            resp = self.http.get(f"{API_BASE}{path}", params=params,
                                 headers={"Authorization": f"Bearer {self.settings.api_key}"},
                                 timeout=TIMEOUT_S)
        except requests.RequestException as e:
            raise YelpError(f"network error: {e}") from e
        if resp.status_code in (401, 403):
            raise YelpAuthError(f"Yelp authentication failed ({resp.status_code})")
        if resp.status_code == 429:
            raise YelpRateLimited("Yelp rate limit reached (429)")
        if resp.status_code in (400, 404):
            return None
        if resp.status_code >= 300:
            raise YelpError(f"Yelp error {resp.status_code}")
        return resp.json()

    def budget_used_up(self) -> bool:
        return ds.get_total_calls(self.conn) >= self.settings.total_limit

    # -- API wrappers ----------------------------------------------------

    def business_match(self, row: dict) -> str | None:
        """Resolve a directory row to a Yelp ID via Business Match (name + address).
        Returns None when the match is missing or uncertain."""
        data = self._get("/businesses/matches", {
            "name": str(row["name"])[:64],
            "address1": str(row["address"])[:64],
            "city": "New York",
            "state": "NY",
            "country": "US",
            "postal_code": str(row.get("zip") or "")[:5] or None,
            "limit": 1,
        })
        businesses = (data or {}).get("businesses") or []
        if len(businesses) != 1:
            return None
        b = businesses[0]
        name_ok = fuzz.token_set_ratio(normalize(row["name"]), normalize(b.get("name"))) >= MATCH_NAME_MIN
        zip_ok = not row.get("zip") or str((b.get("location") or {}).get("zip_code", "")) == str(row["zip"])
        return b.get("id") if (name_ok and zip_ok and b.get("id")) else None

    def fetch_details(self, yelp_id: str) -> dict | None:
        """Fetch details, store them in the 24 h cache, return the cache record."""
        data = self._get(f"/businesses/{yelp_id}")
        if not data:
            return None
        fetched = self.now()
        ds.put_cache(self.conn, yelp_id, data.get("name"), data.get("rating"),
                     data.get("review_count"), price_level(data.get("price")), fetched)
        return ds.get_cache(self.conn, yelp_id, fetched)

    # -- cache-first lookup ----------------------------------------------

    def record_for(self, row: dict) -> dict | None:
        """Fresh Yelp record for a directory row (cache-first), or None if not enriched."""
        nyc_id = str(row.get("nyc_id") or row.get("restaurant_id"))
        mapping = ds.get_mapping(self.conn, nyc_id)
        if mapping:
            cached = ds.get_cache(self.conn, mapping["yelp_id"], self.now())
            if cached:
                return cached
            yelp_id = mapping["yelp_id"]
        else:
            yelp_id = self.business_match(row)
            if not yelp_id:
                return None
            ds.upsert_mapping(self.conn, nyc_id, yelp_id, "business_match", self.now())
        return self.fetch_details(yelp_id)

    def enrich(self, reference: dict, candidates: list[dict]) -> dict | None:
        """Enricher for recommender.recommend: {nyc_id: record} or None (local mode).
        Reference first, then candidates in rank order, until the per-click cap."""
        if not self.settings.usable:
            return None
        if self.budget_used_up():
            raise YelpBudgetExhausted()  # recommender falls back and shows the message
        self._budget = CALLS_PER_CLICK
        records: dict[str, dict] = {}
        rows = [dict(reference, nyc_id=str(reference["nyc_id"]))] + [
            dict(c, nyc_id=c["restaurant_id"]) for c in candidates]
        try:
            for row in rows:
                try:
                    rec = self.record_for(row)
                except YelpCallCapReached:
                    # cache hits still work without calls; keep going cache-only
                    rec = self._cached_only(row)
                except YelpError as e:
                    if e.fatal:
                        raise
                    rec = None  # this restaurant stays "not enriched"
                if rec:
                    records[row["nyc_id"]] = rec
        except YelpBudgetExhausted:
            raise
        except YelpUnavailable:
            return None
        finally:
            self._budget = None
        # nothing fresh at all (e.g. only expired data) -> local mode
        return records or None

    def _cached_only(self, row: dict) -> dict | None:
        mapping = ds.get_mapping(self.conn, str(row["nyc_id"]))
        return ds.get_cache(self.conn, mapping["yelp_id"], self.now()) if mapping else None


def make_enricher(conn, settings: YelpSettings | None = None, client: YelpClient | None = None):
    """Return an enricher callable, or None when Yelp cannot be used."""
    client = client or YelpClient(conn, settings or YelpSettings.load())
    return client.enrich if client.settings.usable else None
