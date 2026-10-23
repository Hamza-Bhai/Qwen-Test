"""Hashtag extraction used by Growth Studio (competitor tags, own titles)."""

from app.studio.text import extract_hashtags


def test_basic():
    assert extract_hashtags("Sunday reset #SundayReset #discipline") == ["sundayreset", "discipline"]


def test_multiple_texts_dedupe_in_order():
    assert extract_hashtags("Title #focus", "desc #reset #focus") == ["focus", "reset"]


def test_none_and_empty_inputs():
    assert extract_hashtags(None, "") == []
    assert extract_hashtags() == []


def test_ignores_url_fragments():
    assert extract_hashtags("see https://x.com/a#top now") == []


def test_ignores_c_sharp_and_bare_hash():
    assert extract_hashtags("I code in C# and # alone") == []


def test_ignores_all_digit_tags():
    assert extract_hashtags("#2024 #2024goals") == ["2024goals"]


def test_trailing_punctuation_stripped():
    assert extract_hashtags("#focus, #reset!") == ["focus", "reset"]


def test_unicode_tags():
    assert extract_hashtags("#سکون") == ["سکون"]


def test_underscore_allowed():
    assert extract_hashtags("#morning_routine") == ["morning_routine"]
