"""Local restaurant-name matching (no network, no LLM).

Pipeline: normalize -> exact match -> alias match -> fuzzy top 5.
A fuzzy score never selects a restaurant: the user always chooses from
candidates shown with addresses. Duplicate names/branches always need a choice.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

import pandas as pd
from rapidfuzz import fuzz, process

FUZZY_LIMIT = 5
FUZZY_CUTOFF = 60  # below this a name is not even suggested

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize(text) -> str:
    """Lowercase, strip accents, turn punctuation into spaces, collapse whitespace."""
    if text is None:
        return ""
    s = unicodedata.normalize("NFKD", str(text))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower().replace("&", " and ").replace("'", "").replace("’", "")
    return _NON_ALNUM.sub(" ", s).strip()


@dataclass
class Candidate:
    nyc_id: str
    name: str
    address: str
    zip: str
    cuisine: str
    method: str  # "exact", "alias" or "fuzzy"
    score: float  # 100 for exact/alias; never used to auto-select

    @property
    def label(self) -> str:
        loc = f"{self.address}, {self.zip}".strip(", ")
        return f"{self.name} — {loc}" if loc else self.name


@dataclass
class MatchResult:
    query: str
    method: str  # "exact", "alias", "fuzzy" or "none"
    candidates: list[Candidate] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.candidates

    @property
    def needs_choice(self) -> bool:
        """True unless there is exactly one exact/alias hit. Fuzzy always needs a choice."""
        return self.method == "fuzzy" or len(self.candidates) != 1

    @property
    def message(self) -> str:
        if self.is_empty:
            return f'No restaurants in Manhattan match "{self.query}". Check the spelling or try a shorter name.'
        if self.method == "fuzzy":
            return "No exact match. Did you mean one of these? Choose the right restaurant."
        if len(self.candidates) > 1:
            return f"{len(self.candidates)} locations share this name. Choose the right one by address."
        return "Please confirm this is the restaurant you mean."


class NameMatcher:
    def __init__(self, directory: pd.DataFrame, aliases: pd.DataFrame | None = None):
        self.directory = directory.reset_index(drop=True)
        self._rows = self.directory.to_dict("records")
        self._by_id = {str(r["nyc_id"]): r for r in self._rows}
        self._norm = [normalize(n) for n in self.directory["name"]]
        self._exact: dict[str, list[int]] = {}
        for i, n in enumerate(self._norm):
            if n:
                self._exact.setdefault(n, []).append(i)
        self._alias: dict[str, list[str]] = {}
        if aliases is not None:
            for a in aliases.itertuples(index=False):
                key = normalize(a.alias)
                if key and str(a.nyc_id) in self._by_id:
                    ids = self._alias.setdefault(key, [])
                    if str(a.nyc_id) not in ids:
                        ids.append(str(a.nyc_id))

    def _candidate(self, row, method: str, score: float) -> Candidate:
        return Candidate(
            str(row["nyc_id"]), row["name"], row["address"], row["zip"], row["cuisine"], method, round(float(score), 1)
        )

    def match(self, query: str) -> MatchResult:
        q = normalize(query)
        if not q:
            return MatchResult(query, "none")

        if q in self._exact:
            rows = [self._rows[i] for i in self._exact[q]]
            cands = [self._candidate(r, "exact", 100) for r in rows]
            return MatchResult(query, "exact", sorted(cands, key=lambda c: (c.address, c.nyc_id)))

        if q in self._alias:
            cands = [self._candidate(self._by_id[i], "alias", 100) for i in self._alias[q]]
            return MatchResult(query, "alias", cands)

        hits = process.extract(
            q, self._norm, scorer=fuzz.WRatio, limit=FUZZY_LIMIT, score_cutoff=FUZZY_CUTOFF
        )
        cands = [self._candidate(self._rows[idx], "fuzzy", score) for _, score, idx in hits]
        return MatchResult(query, "fuzzy" if cands else "none", cands)

    def get(self, nyc_id: str):
        """Return the directory row for a user-chosen id (or None)."""
        return self._by_id.get(str(nyc_id))
