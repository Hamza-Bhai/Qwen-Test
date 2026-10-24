"""Own uploads of a connected channel, read with that channel's OAuth token.

Diagnosis needs title, publish time, duration and the Shorts flag for every own
video; the public ``Video`` rows may not exist for a connected channel, so the
studio keeps its own copy.
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.channel_service import _resolve_shorts
from app.services.youtube import video_payload_to_fields
from app.studio.models import StudioGoogleConnection, StudioMyVideo

DATA_API = "https://www.googleapis.com/youtube/v3"
PAGE_SIZE = 50


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _upload_ids(session, connection: StudioGoogleConnection, max_videos: int) -> list[str]:
    channel = (session.get(f"{DATA_API}/channels", {"part": "snippet,contentDetails", "mine": "true"}).get("items") or [{}])[0]
    snippet = channel.get("snippet") or {}
    if snippet.get("title"):
        connection.title = snippet["title"]
        thumbs = snippet.get("thumbnails") or {}
        connection.thumbnail_url = (thumbs.get("medium") or thumbs.get("default") or {}).get("url")
    uploads = ((channel.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
    if not uploads:
        return []
    ids: list[str] = []
    page_token = None
    while len(ids) < max_videos:
        params = {"part": "contentDetails", "playlistId": uploads, "maxResults": PAGE_SIZE}
        if page_token:
            params["pageToken"] = page_token
        page = session.get(f"{DATA_API}/playlistItems", params)
        ids += [i["contentDetails"]["videoId"] for i in page.get("items") or [] if i.get("contentDetails")]
        page_token = page.get("nextPageToken")
        if not page_token:
            break
    return list(dict.fromkeys(ids))[:max_videos]


def sync_my_videos(db: Session, connection: StudioGoogleConnection, session, *, max_videos: int = 200) -> int:
    ids = _upload_ids(session, connection, max_videos)
    synced: list[StudioMyVideo] = []
    for start in range(0, len(ids), PAGE_SIZE):
        chunk = ids[start:start + PAGE_SIZE]
        payload = session.get(f"{DATA_API}/videos", {"part": "snippet,contentDetails", "id": ",".join(chunk)})
        for item in payload.get("items") or []:
            fields = video_payload_to_fields(item)
            video = db.scalars(
                select(StudioMyVideo).where(
                    StudioMyVideo.connection_id == connection.id,
                    StudioMyVideo.youtube_video_id == fields["youtube_video_id"],
                )
            ).first()
            if video is None:
                video = StudioMyVideo(connection_id=connection.id, youtube_video_id=fields["youtube_video_id"])
                db.add(video)
            video.title = fields["title"] or ""
            video.published_at = _parse_dt(fields["published_at"])
            video.duration_seconds = fields["duration_seconds"]
            video.live_broadcast_content = fields["live_broadcast_content"]
            synced.append(video)
    _resolve_shorts(synced)  # duck-typed: reads/sets duration, live flag, is_short
    db.commit()
    return len(synced)
