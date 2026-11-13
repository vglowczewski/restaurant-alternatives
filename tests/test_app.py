"""Smoke test: the app runs end to end in local mode (no Yelp key, no network)."""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")


@pytest.fixture
def local_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("YELP_API_KEY", "")
    monkeypatch.setenv("YELP_ENABLED", "false")
    import data_store

    monkeypatch.setattr(data_store, "DEFAULT_DB_PATH", tmp_path / "app.sqlite")


def test_app_local_mode_end_to_end(local_mode):
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    at.text_input(key="query").input("don angie").run()
    assert not at.exception
    radio = at.radio[0]
    assert radio.options  # suggestions shown with addresses
    radio.set_value(0).run()
    assert not at.exception
    [submit] = [b for b in at.button if b.label == "Find alternatives"]
    submit.click().run()
    assert not at.exception
    texts = " ".join(str(i.value) for i in at.info)
    assert "Yelp data unavailable" in texts
    assert any("Score reflects cuisine only" in str(c.value) for c in at.caption)


def test_app_no_match_empty_state(local_mode):
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.text_input(key="query").input("!!!").run()
    assert not at.exception
    assert any("No restaurants in Manhattan match" in str(i.value) for i in at.info)
