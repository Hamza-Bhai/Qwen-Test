"""Shared pytest fixtures: isolated SQLite DB + fake YouTube API responses.

Tests never hit the real Google API; they monkeypatch httpx.Client.get with
realistic channels.list / playlistItems.list / videos.list payloads matching
actual API response shapes, then assert the pipeline stores/returns them
verbatim (stats must match the API response exactly).
"""

import pytest


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Point DATABASE_URL at a per-test SQLite file and set a dummy API key.

    ``chdir`` keeps the app's relative ``sqlite:///./...`` default (and any
    stray .env discovery) away from the developer's real database; the absolute
    env var below is what the app actually uses.
    """
    db_path = tmp_path / "test.db"
    workdir = tmp_path / "cwd"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("YOUTUBE_API_KEY", "test-api-key")
    monkeypatch.setenv("ENABLE_SCHEDULER", "false")
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# --------------------------------------------------------------------- payloads
CHANNEL_ITEM = {
    "kind": "youtube#channel",
    "etag": "abc",
    "id": "UC8-Th83bH_thdKZDJCrn88g",
    "snippet": {
        "title": "MKBHD",
        "description": "Tech reviews and more.",
        "publishedAt": "2010-07-03T01:09:50Z",
        "country": "US",
        "thumbnails": {
            "default": {"url": "https://img.example/default.jpg"},
            "high": {"url": "https://img.example/high.jpg"},
        },
    },
    "statistics": {
        "viewCount": "5000000000",
        "videoCount": "900",
        "subscriberCount": "40000000",
        "hiddenSubscriberCount": False,
    },
    "contentDetails": {
        "relatedPlaylists": {"uploads": "UU8-Th83bH_thdKZDJCrn88g"}
    },
}

PLAYLIST_ITEMS = {
    "items": [
        {"snippet": {"title": "v1"}, "contentDetails": {"videoId": "AAAAAAAAAA1"}}
    ]
}

VIDEOS_LIST = {
    "items": [
        {
            "id": "AAAAAAAAAA1",
            "snippet": {
                "title": "Cool Tech Video",
                "description": "desc",
                "publishedAt": "2024-01-01T00:00:00Z",
                "categoryId": "28",
                "tags": ["tech"],
                "thumbnails": {"high": {"url": "https://img.example/v1.jpg"}},
            },
            "statistics": {
                "viewCount": "1234567",
                "likeCount": "54321",
                "commentCount": "3210",
            },
            "contentDetails": {"duration": "PT12M34S"},
        }
    ]
}


class FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


def make_fake_get(monkeypatch, *, channel_item=None, error_status=None, error_message=None):
    """Replace httpx.Client.get with a recorder that returns canned payloads."""
    calls = []

    item = CHANNEL_ITEM if channel_item is None else channel_item

    def fake_get(self, url, params=None, **kwargs):  # noqa: ANN001
        calls.append({"url": url, "params": dict(params or {})})
        if error_status is not None:
            return FakeResponse(
                error_status,
                {"error": {"code": error_status, "message": error_message or "fake error"}},
            )
        if "/channels" in url:
            if item is None:
                return FakeResponse(200, {"items": []})
            return FakeResponse(200, {"items": [item]})
        if "/playlistItems" in url:
            return FakeResponse(200, PLAYLIST_ITEMS)
        if "/videos" in url:
            return FakeResponse(200, VIDEOS_LIST)
        return FakeResponse(404, {"error": {"message": "unexpected endpoint"}})

    monkeypatch.setattr("httpx.Client.get", fake_get)
    return calls
