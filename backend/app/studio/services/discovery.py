"""Competitor discovery: SerpApi finds candidates, the Data API verifies them.

Nothing is added to the watchlist here. Discovery only creates ``candidate``
rows (with the reason they were suggested) for the owner to approve or reject.
Rejected and existing channels, and the owner's own channel, never come back.
"""

import logging
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Video
from app.services.quota import QuotaExceededError
from app.services.url_resolver import parse_channel_reference
from app.services.youtube import YouTubeAPIError, YouTubeService, channel_payload_to_fields
from app.studio.models import StudioCompetitor
from app.studio.services.own_channel import get_own_channel
from app.studio.services.serpapi import SerpApiBudgetExceeded, SerpApiClient, SerpApiError, SerpApiNotConfigured
from app.studio.services.settings_store import get_keywords

logger = logging.getLogger(__name__)

_CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")

MAX_KEYWORD_SEARCHES = 5
MAX_RELATED_LOOKUPS = 3
MAX_ENRICHED = 25
RECENT_VIDEOS = 10
MIN_RECENT_VIDEOS = 3
MAX_DAYS_SINCE_UPLOAD = 30
SIZE_RANGE = (0.2, 20.0)  # allowed subscribers as a multiple of the owner's


@dataclass(frozen=True)
class Candidate:
    identifier: str  # "@handle" (lowercase) or a "UC..." channel id
    title: str | None
    seeds: tuple[str, ...]
    sources: tuple[str, ...]  # "search" | "related"
    appearances: int


@dataclass
class DiscoveryResult:
    added: int = 0
    considered: int = 0
    searches_used: int = 0
    stopped_reason: str | None = None  # "budget" | "youtube_quota"
    competitor_ids: list[int] = field(default_factory=list)


# ------------------------------------------------------------- extraction
def _identifier(channel: dict) -> str | None:
    channel_id = channel.get("channel_id") or channel.get("id")
    if isinstance(channel_id, str) and _CHANNEL_ID.match(channel_id):
        return channel_id
    link = channel.get("link")
    if isinstance(link, str):
        try:
            target = parse_channel_reference(link)
        except ValueError:
            return None
        return target.identifier if target.kind == "channel_id" else target.identifier.lower()
    return None


def candidates_from_search(payload: dict, seed: str) -> list[Candidate]:
    """Channels behind ``video_results`` (and ``channel_results`` when present)."""
    found: list[Candidate] = []
    entries = [r.get("channel") or {} for r in payload.get("video_results") or []]
    entries += [
        {"channel_id": r.get("channel_id"), "link": r.get("link"), "name": r.get("title")}
        for r in payload.get("channel_results") or []
    ]
    for channel in entries:
        identifier = _identifier(channel)
        if identifier:
            found.append(Candidate(identifier, channel.get("name"), (seed,), ("search",), 1))
    return found


def related_video_ids(payload: dict) -> list[str]:
    """Related videos only carry a channel *name*, so we collect video ids and
    resolve their channel through the Data API."""
    return [r["video_id"] for r in payload.get("related_videos") or [] if r.get("video_id")]


def candidates_from_video_items(items: list[dict], seed: str) -> list[Candidate]:
    found: list[Candidate] = []
    for item in items:
        snippet = item.get("snippet") or {}
        channel_id = snippet.get("channelId")
        if channel_id and _CHANNEL_ID.match(channel_id):
            found.append(Candidate(channel_id, snippet.get("channelTitle"), (seed,), ("related",), 1))
    return found


def _merge_two(a: Candidate, b: Candidate) -> Candidate:
    return replace(
        a,
        title=a.title or b.title,
        seeds=tuple(dict.fromkeys(a.seeds + b.seeds)),
        sources=tuple(dict.fromkeys(a.sources + b.sources)),
        appearances=a.appearances + b.appearances,
    )


def merge_candidates(groups: list[list[Candidate]]) -> list[Candidate]:
    merged: dict[str, Candidate] = {}
    for group in groups:
        for cand in group:
            key = cand.identifier if _CHANNEL_ID.match(cand.identifier) else cand.identifier.lower()
            merged[key] = _merge_two(merged[key], cand) if key in merged else cand
    return list(merged.values())


# --------------------------------------------------------------- the run
def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _owner_top_video_ids(db: Session, channel_id: int, now: datetime) -> list[str]:
    rows = db.scalars(
        select(Video)
        .where(Video.channel_id == channel_id, Video.removed_at.is_(None), Video.is_short.is_(False))
        .order_by(Video.view_count.desc())
    ).all()
    mature = [v for v in rows if v.published_at is None or (now - _utc(v.published_at)).days >= 7]
    return [v.youtube_video_id for v in mature[:MAX_RELATED_LOOKUPS]]


def _enrich(youtube: YouTubeService, candidates: list[Candidate]) -> dict[str, tuple[Candidate, dict]]:
    """Resolve candidates to Data API channel items, merged by real channel id."""
    ids = [c.identifier for c in candidates if _CHANNEL_ID.match(c.identifier)]
    by_identifier = {c.identifier: c for c in candidates}
    items: list[tuple[Candidate, dict]] = []
    if ids:
        for item in youtube.channels_list(id=",".join(ids)).get("items") or []:
            if item.get("id") in by_identifier:
                items.append((by_identifier[item["id"]], item))
    for cand in candidates:
        if cand.identifier.startswith("@"):
            found = (youtube.channels_list(handle=cand.identifier).get("items") or [None])[0]
            if found:
                items.append((cand, found))
    resolved: dict[str, tuple[Candidate, dict]] = {}
    for cand, item in items:
        key = item["id"]
        resolved[key] = (_merge_two(resolved[key][0], cand), item) if key in resolved else (cand, item)
    return resolved


def _recent_stats(youtube: YouTubeService, uploads_playlist_id: str) -> tuple[list[int], datetime | None]:
    playlist = youtube.playlist_items(uploads_playlist_id, max_results=RECENT_VIDEOS)
    ids = [i["contentDetails"]["videoId"] for i in playlist.get("items") or [] if i.get("contentDetails", {}).get("videoId")]
    items = youtube.videos_list(ids).get("items") or [] if ids else []
    views, latest = [], None
    for item in items:
        views.append(int((item.get("statistics") or {}).get("viewCount") or 0))
        published = (item.get("snippet") or {}).get("publishedAt")
        if published:
            when = datetime.fromisoformat(published.replace("Z", "+00:00"))
            latest = when if latest is None or when > latest else latest
    return views, latest


def _reason_text(seeds, appearances, median_views, subs, vps, days) -> str:
    where = f"Found via {', '.join(seeds)} ({appearances} time{'s' if appearances != 1 else ''})"
    if subs is None:
        size = f"typical recent video {median_views:,} views; subscriber count hidden"
    else:
        size = f"typical recent video {median_views:,} views on {subs:,} subscribers ({vps:.1f} views per subscriber)"
    return f"{where}; {size}; last upload {days} day{'s' if days != 1 else ''} ago."


def discover(
    db: Session,
    serp: SerpApiClient,
    youtube: YouTubeService,
    *,
    max_searches: int = 8,
    max_new: int = 15,
    now: datetime | None = None,
) -> DiscoveryResult:
    now = now or datetime.now(timezone.utc)
    own = get_own_channel(db)
    if own is None:
        raise ValueError("Pick your channel in Studio settings first")

    result = DiscoveryResult()
    used_before = serp.usage().used
    attempts = 0
    groups: list[list[Candidate]] = []

    def search(engine: str, params: dict) -> dict | None:
        nonlocal attempts
        attempts += 1
        try:
            return serp.get(engine, params)
        except SerpApiBudgetExceeded:
            result.stopped_reason = "budget"
            return None
        except SerpApiNotConfigured:
            raise
        except SerpApiError as exc:  # e.g. a query with no results: skip it, keep going
            logger.info("SerpApi search skipped (%s): %s", engine, exc)
            return {}

    for keyword in get_keywords(db)[:MAX_KEYWORD_SEARCHES]:
        if attempts >= max_searches or result.stopped_reason:
            break
        payload = search("youtube", {"search_query": keyword})
        if payload:
            groups.append(candidates_from_search(payload, keyword))

    related_ids: dict[str, str] = {}  # related video id -> the owner video it came from
    for video_id in _owner_top_video_ids(db, own.id, now):
        if attempts >= max_searches or result.stopped_reason:
            break
        payload = search("youtube_video", {"v": video_id})
        for rid in related_video_ids(payload or {}):
            related_ids.setdefault(rid, video_id)
    if related_ids:
        ids = list(related_ids)
        for start in range(0, len(ids), 50):
            chunk = ids[start:start + 50]
            groups.append(candidates_from_video_items(
                youtube.videos_list(chunk, parts="snippet").get("items") or [], "your top videos"
            ))
    result.searches_used = serp.usage().used - used_before

    known_ids = {own.youtube_channel_id} | set(db.scalars(select(StudioCompetitor.youtube_channel_id)).all())
    known_handles = {h.lower() for h in db.scalars(select(StudioCompetitor.handle)).all() if h}
    pool = [
        c for c in merge_candidates(groups)
        if c.identifier not in known_ids and c.identifier.lower() not in known_handles
    ]
    result.considered = len(pool)
    pool.sort(key=lambda c: -c.appearances)  # stable: first-seen order breaks ties
    try:
        enriched = _enrich(youtube, pool[:MAX_ENRICHED])
    except QuotaExceededError:
        result.stopped_reason = result.stopped_reason or "youtube_quota"
        return result

    scored: list[tuple[float, dict, Candidate, dict]] = []
    for channel_id, (cand, item) in enriched.items():
        if channel_id in known_ids:
            continue
        fields = channel_payload_to_fields(item)
        subs = fields["subscriber_count"]
        if subs is not None and own.subscriber_count:
            low, high = SIZE_RANGE[0] * own.subscriber_count, SIZE_RANGE[1] * own.subscriber_count
            if not low <= subs <= high:
                continue
        if not fields["uploads_playlist_id"]:
            continue
        try:
            views, latest = _recent_stats(youtube, fields["uploads_playlist_id"])
        except QuotaExceededError:
            result.stopped_reason = result.stopped_reason or "youtube_quota"
            break
        except YouTubeAPIError as exc:
            logger.info("Skipping %s: %s", channel_id, exc)
            continue
        if len(views) < MIN_RECENT_VIDEOS or latest is None:
            continue
        days = (now - _utc(latest)).days
        if days > MAX_DAYS_SINCE_UPLOAD:
            continue
        med = int(median(views))
        vps = med / subs if subs else None
        score = vps if vps is not None else 0.0
        reason = {
            "seeds": list(cand.seeds),
            "appearances": cand.appearances,
            "recent_median_views": med,
            "views_per_subscriber": round(vps, 2) if vps is not None else None,
            "days_since_last_upload": days,
            "text": _reason_text(cand.seeds, cand.appearances, med, subs, vps or 0.0, days),
        }
        scored.append((score, reason, cand, fields))

    scored.sort(key=lambda row: row[0], reverse=True)
    for _, reason, cand, fields in scored[:max_new]:
        comp = StudioCompetitor(
            youtube_channel_id=fields["youtube_channel_id"],
            title=fields["title"] or fields["youtube_channel_id"],
            handle=cand.identifier if cand.identifier.startswith("@") else None,
            description=fields["description"],
            thumbnail_url=fields["thumbnail_url"],
            country=fields["country"],
            uploads_playlist_id=fields["uploads_playlist_id"],
            subscriber_count=fields["subscriber_count"],
            video_count=fields["video_count"],
            view_count=fields["view_count"],
            status="candidate",
            source="search" if "search" in cand.sources else "related",
            discovery_reason=reason,
        )
        db.add(comp)
        db.flush()
        result.competitor_ids.append(comp.id)
    db.commit()
    result.added = len(result.competitor_ids)
    return result
