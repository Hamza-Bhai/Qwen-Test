"""Trends direction and YouTube autocomplete from SerpApi (real fixtures, no network)."""

import json
from pathlib import Path

import pytest

from app.studio.services.market import autocomplete, trend_directions
from app.studio.services.serpapi import SerpApiBudgetExceeded

FIX = Path(__file__).parent / "fixtures"
TRENDS = json.loads((FIX / "serpapi_google_trends.json").read_text(encoding="utf-8"))
AUTOCOMPLETE = json.loads((FIX / "serpapi_autocomplete.json").read_text(encoding="utf-8"))


class FakeSerp:
    def __init__(self, responses=None, error=None):
        self.responses = responses or {}
        self.error = error
        self.calls = []

    def get(self, engine, params, **kw):
        self.calls.append((engine, dict(params)))
        if self.error:
            raise self.error
        return self.responses[engine]


def _timeline(values_by_day):
    return {"interest_over_time": {"timeline_data": [
        {"date": f"d{i}", "values": [{"query": "a", "extracted_value": v}]} for i, v in enumerate(values_by_day)
    ]}}


def test_trend_direction_from_fixture():
    serp = FakeSerp({"google_trends": TRENDS})
    result = trend_directions(serp, ["sunday reset", "discipline", "morning routine", "self improvement"])
    assert result["note"] is None
    assert [i["keyword"] for i in result["items"]] == ["sunday reset", "discipline", "morning routine", "self improvement"]
    assert all(i["direction"] in {"rising", "falling", "flat"} for i in result["items"])
    engine, params = serp.calls[0]
    assert params["gprop"] == "youtube" and params["q"] == "sunday reset,discipline,morning routine,self improvement"


def test_trend_rising_falling_and_flat():
    rising = trend_directions(FakeSerp({"google_trends": _timeline([10] * 28 + [20] * 28)}), ["a"])["items"][0]
    assert rising["direction"] == "rising" and rising["last_4w"] == 20.0 and rising["prev_4w"] == 10.0
    falling = trend_directions(FakeSerp({"google_trends": _timeline([20] * 28 + [10] * 28)}), ["a"])["items"][0]
    assert falling["direction"] == "falling"
    flat = trend_directions(FakeSerp({"google_trends": _timeline([10] * 28 + [11] * 28)}), ["a"])["items"][0]
    assert flat["direction"] == "flat"


def test_partial_points_are_ignored():
    data = _timeline([10] * 28 + [20] * 28)
    data["interest_over_time"]["timeline_data"].append(
        {"date": "today", "partial_data": True, "values": [{"query": "a", "extracted_value": 0}]})
    assert trend_directions(FakeSerp({"google_trends": data}), ["a"])["items"][0]["last_4w"] == 20.0


def test_autocomplete_suggestions_capped():
    result = autocomplete(FakeSerp({"google_autocomplete": AUTOCOMPLETE}), ["sunday reset"])
    item = result["items"][0]
    assert item["seed"] == "sunday reset" and 1 <= len(item["suggestions"]) <= 8
    assert all(isinstance(s, str) and s for s in item["suggestions"])


def test_autocomplete_uses_at_most_4_seeds():
    serp = FakeSerp({"google_autocomplete": AUTOCOMPLETE})
    autocomplete(serp, ["a", "b", "c", "d", "e", "f"])
    assert [p["q"] for _, p in serp.calls] == ["a", "b", "c", "d"]
    assert all(p["client"] == "youtube" for _, p in serp.calls)


def test_serpapi_error_gives_empty_items_and_note():
    serp = FakeSerp(error=SerpApiBudgetExceeded("SerpApi monthly cap reached (200/200 searches in 2026-10)."))
    for result in (trend_directions(serp, ["a"]), autocomplete(serp, ["a"])):
        assert result["items"] == [] and "cap reached" in result["note"]


def test_no_keywords_no_search():
    serp = FakeSerp()
    assert trend_directions(serp, [])["items"] == [] and autocomplete(serp, [])["items"] == []
    assert serp.calls == []
