"""Reusable YouTube Data API v3 service.

Responsibilities:
* Thin async HTTP client over https://www.googleapis.com/youtube/v3
* Response caching via ``ResponseCache`` (saves quota on repeated reads)
* Quota accounting via ``QuotaTracker`` (refuses requests over budget)
* Parsing helpers that only ever read fields actually returned by Google —
  missing values stay ``None``; nothing is fabricated (rule #10).

Business logic and persistence live in ``app.services.channel_service`` so this
module stays transport-level and reusable by later phases (analytics, AI).
"""

import json
import re
from urllib.parse import urlencode

import httpx

from app.core.config import get_settings
from app.services.cache import ResponseCache
from app.services.quota import QUOTA_COSTS, QuotaExceededError, QuotaTracker

BASE_URL = "https://www.googleapis.com/youtube/v3"

_HANDLE_RE = re.compile(r"^@?[A-Za-z0-9._-]{3,30}$")
_CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


class YouTubeAPIError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"YouTube API error {status_code}: {message}")
        self.status_code = status_code


class YouTubeService:
    """Stateless-per-call wrapper around the YouTube Data API v3."""

    def __init__(self, cache: ResponseCache, quota: QuotaTracker) -> None:
        self.cache = cache
        self.quota = quota

    @property
    def settings(self):
        """Resolve settings lazily so per-test env changes are always honored."""
        return get_settings()

    # ------------------------------------------------------------------ core
    def _require_key(self) -> str:
        if not self.settings.youtube_api_key:
            raise YouTubeAPIError(
                0,
                "YOUTUBE_API_KEY is not configured on the backend. "
                "Set it in backend/.env — never in frontend code.",
            )
        return self.settings.youtube_api_key

    def _request(self, endpoint: str, params: dict, *, cache_ttl: int | None) -> dict:
        """Blocking request helper with cache + quota guards."""
        key = self._require_key()
        cost = QUOTA_COSTS.get(endpoint, 1)
        cache_key = f"{endpoint}?{urlencode(sorted(params.items()))}"

        if cache_ttl:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return cached

        self.quota.check_available(cost)

        resource = {
            "channels.list": "channels",
            "channels.list_for_handle": "channels",
            "playlistItems.list": "playlistItems",
            "videos.list": "videos",
            "search.list": "search",
        }[endpoint]
        url = f"{BASE_URL}/{resource}"

        # Key goes in a header, never the query string: request URLs end up in
        # logs, error messages and proxies.
        with httpx.Client(timeout=30) as client:
            resp = client.get(url, params=params, headers={"X-Goog-Api-Key": key})

        if resp.status_code != 200:
            try:
                message = resp.json().get("error", {}).get("message", resp.text[:200])
            except (ValueError, AttributeError):
                message = resp.text[:200]
            raise YouTubeAPIError(resp.status_code, message)

        payload = resp.json()
        self.quota.record(endpoint, cost)
        if cache_ttl:
            self.cache.set(cache_key, payload, cache_ttl)
        return payload

    # ------------------------------------------------------------- endpoints
    def channels_list(self, *, id: str | None = None, handle: str | None = None,
                      parts: str = "snippet,statistics,contentDetails") -> dict:
        params: dict = {"part": parts}
        endpoint = "channels.list"
        if id:
            params["id"] = id
        elif handle:
            params["forHandle"] = handle
            endpoint = "channels.list_for_handle"
        else:
            raise ValueError("channels_list requires id or handle")
        return self._request(endpoint, params, cache_ttl=self.settings.cache_ttl_seconds)

    def playlist_items(self, playlist_id: str, *, max_results: int = 50,
                       page_token: str | None = None) -> dict:
        params = {
            "part": "snippet,contentDetails",
            "playlistId": playlist_id,
            "maxResults": str(min(max_results, 50)),
        }
        if page_token:
            params["pageToken"] = page_token
        # Playlist membership changes often; short TTL keeps data fresh-ish.
        return self._request("playlistItems.list", params, cache_ttl=min(300, self.settings.cache_ttl_seconds))

    def videos_list(self, video_ids: list[str],
                    parts: str = "snippet,statistics,contentDetails") -> dict:
        cleaned = [v for v in video_ids if _VIDEO_ID_RE.match(v)]
        if not cleaned:
            return {"items": []}
        params = {"part": parts, "id": ",".join(cleaned[:50])}
        return self._request("videos.list", params, cache_ttl=min(300, self.settings.cache_ttl_seconds))

    # ------------------------------------------------------------ resolution
    def resolve_channel(self, identifier: str) -> dict:
        """Resolve '@handle', 'UC...' channel id, or bare handle to channel data."""
        identifier = identifier.strip()
        if _CHANNEL_ID_RE.match(identifier):
            data = self.channels_list(id=identifier)
        elif identifier.startswith("@") or _HANDLE_RE.match(identifier):
            handle = identifier if identifier.startswith("@") else f"@{identifier}"
            data = self.channels_list(handle=handle)
        else:
            raise ValueError(
                "Identifier must be a YouTube channel ID (UC...) or handle (@name)."
            )
        items = data.get("items") or []
        if not items:
            raise YouTubeAPIError(404, f"No channel found for '{identifier}'.")
        return items[0]

    def fetch_recent_videos(self, upload_playlist_id: str, *, limit: int = 50) -> list[dict]:
        """Video IDs from the uploads playlist, then full stats via videos.list."""
        pl = self.playlist_items(upload_playlist_id, max_results=min(limit, 50))
        ids = [
            item["contentDetails"]["videoId"]
            for item in pl.get("items", [])
            if item.get("contentDetails", {}).get("videoId")
        ]
        if not ids:
            return []
        return self.videos_list(ids).get("items", []) or []


def parse_iso8601_duration(value: str | None) -> int | None:
    """PT#M#S style ISO-8601 duration -> seconds. Returns None if unparseable."""
    if not value:
        return None
    m = re.fullmatch(
        r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?", value
    )
    if not m:
        return None
    days, hours, minutes, seconds = (x or 0 for x in m.groups())
    return int(int(days) * 86400 + int(hours) * 3600 + int(minutes) * 60 + float(seconds))


def parse_content_dimension(value: str | None) -> dict:
    """Parse contentDetails.dimension ("1920x1080") into width/height ints.

    Defensive: missing or malformed values yield (None, None) so sync never
    crashes on unexpected API payloads."""
    m = re.fullmatch(r"(\d+)x(\d+)", (value or "").strip())
    return {
        "content_width": int(m.group(1)) if m else None,
        "content_height": int(m.group(2)) if m else None,
    }


# YouTube Shorts are capped at 3 minutes (60s before Oct 2024), so anything
# longer is a regular upload without needing any further check.
SHORTS_MAX_SECONDS = 180


def check_is_short(video_id: str, client: httpx.Client | None = None) -> bool | None:
    """Ask youtube.com whether a video is a Short (no Data API quota spent).

    ``https://www.youtube.com/shorts/<id>`` answers 200 for a Short and
    redirects to ``/watch?v=<id>`` for a regular video. Anything else (network
    error, consent page, rate limit) returns None so callers fall back to the
    duration heuristic instead of guessing."""
    url = f"https://www.youtube.com/shorts/{video_id}"
    try:
        if client is None:
            with httpx.Client(timeout=5.0) as c:
                resp = c.head(url, follow_redirects=False)
        else:
            resp = client.head(url, follow_redirects=False)
    except httpx.HTTPError:
        return None
    if resp.status_code == 200:
        return True
    if resp.is_redirect and "/watch" in resp.headers.get("location", ""):
        return False
    return None


def _to_int(value: str | None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def channel_payload_to_fields(item: dict) -> dict:
    """Map a channels.list item to Channel column values (no invented defaults)."""
    snippet = item.get("snippet", {})
    stats = item.get("statistics", {})
    thumbs = snippet.get("thumbnails", {})
    thumb = thumbs.get("high") or thumbs.get("medium") or thumbs.get("default")
    return {
        "youtube_channel_id": item.get("id"),
        "title": snippet.get("title"),
        "description": snippet.get("description"),
        "thumbnail_url": thumb.get("url") if thumb else None,
        "country": snippet.get("country"),
        "subscriber_count": _to_int(stats.get("subscriberCount")),
        "video_count": _to_int(stats.get("videoCount")),
        "view_count": _to_int(stats.get("viewCount")),
        "channel_created_at": snippet.get("publishedAt"),
        "uploads_playlist_id": (
            item.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
        ),
    }


def video_payload_to_fields(item: dict) -> dict:
    snippet = item.get("snippet", {})
    stats = item.get("statistics", {})
    content = item.get("contentDetails", {})
    thumbs = snippet.get("thumbnails", {})
    thumb = thumbs.get("high") or thumbs.get("medium") or thumbs.get("default")
    return {
        "youtube_video_id": item.get("id"),
        "title": snippet.get("title"),
        "description": snippet.get("description"),
        "thumbnail_url": thumb.get("url") if thumb else None,
        "published_at": snippet.get("publishedAt"),
        "live_broadcast_content": snippet.get("liveBroadcastContent"),
        "duration_seconds": parse_iso8601_duration(content.get("duration")),
        # contentDimension is a public field ("WxH"); parsed defensively so a
        # missing/malformed value stays None instead of crashing the sync.
        **parse_content_dimension(
            content.get("dimension") or content.get("contentDimension")),
        "tags": snippet.get("tags"),
        "category_id": snippet.get("categoryId"),
        "view_count": _to_int(stats.get("viewCount")) or 0,
        "like_count": _to_int(stats.get("likeCount")),
        "comment_count": _to_int(stats.get("commentCount")),
    }


__all__ = [
    "YouTubeService",
    "YouTubeAPIError",
    "QuotaExceededError",
    "channel_payload_to_fields",
    "video_payload_to_fields",
    "json_dumps_safe",
]


def json_dumps_safe(obj) -> str:
    return json.dumps(obj, default=str)
