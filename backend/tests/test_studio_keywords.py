"""Niche keywords store (editable in the Studio Settings tab)."""

import pytest

from app.db.session import SessionLocal, init_db
from app.studio.services.settings_store import DEFAULT_KEYWORDS, get_keywords, set_keywords


@pytest.fixture()
def db():
    init_db()
    with SessionLocal() as session:
        yield session


def test_defaults_when_nothing_saved(db):
    assert get_keywords(db) == ["sunday reset", "discipline", "morning routine", "self improvement"]
    assert get_keywords(db) == DEFAULT_KEYWORDS


def test_set_normalises(db):
    saved = set_keywords(db, ["  Sunday   RESET ", "sunday reset", "Discipline"])
    assert saved == ["sunday reset", "discipline"]
    assert get_keywords(db) == ["sunday reset", "discipline"]


def test_blank_entries_dropped(db):
    assert set_keywords(db, ["", "   ", "x"]) == ["x"]


@pytest.mark.parametrize("bad", [[], ["  "], ["", ""]])
def test_rejects_empty_result(db, bad):
    with pytest.raises(ValueError):
        set_keywords(db, bad)


def test_rejects_phrase_over_60_chars(db):
    with pytest.raises(ValueError):
        set_keywords(db, ["a" * 61])
    assert set_keywords(db, ["a" * 60]) == ["a" * 60]


def test_rejects_more_than_20_phrases(db):
    with pytest.raises(ValueError):
        set_keywords(db, [f"k{i}" for i in range(21)])
    assert len(set_keywords(db, [f"k{i}" for i in range(20)])) == 20


def test_rejects_non_string_items(db):
    with pytest.raises(ValueError):
        set_keywords(db, [1])  # type: ignore[list-item]


def test_rejected_save_leaves_previous_value_intact(db):
    set_keywords(db, ["keep me"])
    with pytest.raises(ValueError):
        set_keywords(db, [])
    assert get_keywords(db) == ["keep me"]
