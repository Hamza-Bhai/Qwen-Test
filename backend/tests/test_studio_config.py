"""Growth Studio settings: SerpApi key and monthly search cap."""

from app.core.config import get_settings


def test_serpapi_key_read_from_serpapikey_env(monkeypatch):
    monkeypatch.setenv("SERPAPIKEY", "abc")
    get_settings.cache_clear()
    assert get_settings().serpapi_key == "abc"


def test_blank_serpapikey_means_not_configured(monkeypatch):
    monkeypatch.setenv("SERPAPIKEY", "")
    get_settings.cache_clear()
    assert get_settings().serpapi_key is None


def test_monthly_cap_defaults_to_200_and_can_be_overridden(monkeypatch):
    monkeypatch.delenv("SERPAPI_MONTHLY_CAP", raising=False)
    get_settings.cache_clear()
    assert get_settings().serpapi_monthly_cap == 200

    monkeypatch.setenv("SERPAPI_MONTHLY_CAP", "50")
    get_settings.cache_clear()
    assert get_settings().serpapi_monthly_cap == 50
