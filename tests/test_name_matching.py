import pytest

from name_matching import NameMatcher, normalize


@pytest.fixture
def matcher(directory, aliases):
    return NameMatcher(directory, aliases)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("  Don Angie  ", "don angie"),
        ("CAFÉ BORÉAL", "cafe boreal"),
        ("Joe's Shanghai, Inc.", "joes shanghai inc"),
        ("A&B", "a and b"),
        (None, ""),
    ],
)
def test_normalize(raw, expected):
    assert normalize(raw) == expected


def test_exact_duplicate_branches_require_choice(matcher):
    r = matcher.match("don angie")
    assert r.method == "exact"
    assert {c.nyc_id for c in r.candidates} == {"1", "2"}
    assert r.needs_choice
    assert all(c.address for c in r.candidates)


def test_exact_single_match_with_accents(matcher):
    r = matcher.match("cafe boreal")
    assert r.method == "exact" and [c.nyc_id for c in r.candidates] == ["4"]
    assert not r.needs_choice


def test_alias_match(matcher):
    r = matcher.match("the shanghai joe")
    assert r.method == "alias" and [c.nyc_id for c in r.candidates] == ["6"]


@pytest.mark.parametrize("query", ["Don Angie NYC", "don angee", "dom angie"])
def test_fuzzy_gives_suggestions_never_auto_selects(matcher, query):
    r = matcher.match(query)
    assert r.method == "fuzzy"
    assert r.needs_choice
    assert 0 < len(r.candidates) <= 5
    assert {"1", "2"} <= {c.nyc_id for c in r.candidates}


def test_fuzzy_typo_single_restaurant_still_needs_choice(matcher):
    r = matcher.match("sushi zenn")
    assert r.method == "fuzzy" and r.candidates[0].nyc_id == "5"
    assert r.needs_choice


def test_no_match_empty_state(matcher):
    r = matcher.match("qwxzv plmkj")
    assert r.is_empty and r.method == "none"
    assert "No restaurants" in r.message
    assert matcher.match("   ").is_empty


def test_candidate_label_shows_address(matcher):
    c = matcher.match("don angie").candidates[0]
    assert c.address in c.label and c.name in c.label


def test_real_directory_don_angie():
    from data_store import load_aliases, load_directory

    m = NameMatcher(load_directory(), load_aliases())
    for q in ("don angie", "Don Angie NYC", "don angi"):
        r = m.match(q)
        assert r.candidates and r.needs_choice
        assert any(c.name == "DON ANGIE" for c in r.candidates)
