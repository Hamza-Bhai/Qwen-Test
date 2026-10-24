"""Hook study: the first ~60 seconds of a competitor video's transcript.

Costs one SerpApi search (cached for 30 days). The transcript text is public
caption data; nothing is invented when it is missing.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.studio.models import StudioCompetitorVideo
from app.studio.services.serpapi import SerpApiClient, SerpApiNoResults

HOOK_WINDOW_MS = 60_000
TRANSCRIPT_CACHE_DAYS = 30


class UnknownVideo(ValueError):
    """The video is not one of the stored competitor videos."""


class NoTranscript(ValueError):
    """SerpApi returned no transcript segments for the video."""


def _find_video(db: Session, youtube_video_id: str) -> StudioCompetitorVideo | None:
    return db.scalars(
        select(StudioCompetitorVideo).where(StudioCompetitorVideo.youtube_video_id == youtube_video_id)
    ).first()


def study_hook(db: Session, serp: SerpApiClient, youtube_video_id: str) -> dict:
    video = _find_video(db, youtube_video_id)
    if video is None:
        raise UnknownVideo("Unknown competitor video")

    try:
        payload = serp.get(
            "youtube_video_transcript", {"v": youtube_video_id}, cache_days=TRANSCRIPT_CACHE_DAYS
        )
    except SerpApiNoResults:
        raise NoTranscript("No transcript available for this video") from None
    segments = [
        {"start_ms": int(s["start_ms"]), "text": str(s["snippet"]).strip()}
        for s in payload.get("transcript") or []
        if s.get("snippet") and s.get("start_ms") is not None
    ]
    if not segments:
        raise NoTranscript("No transcript available for this video")

    hook = [s for s in segments if s["start_ms"] < HOOK_WINDOW_MS] or segments[:1]
    result = {
        "youtube_video_id": youtube_video_id,
        "hook_text": " ".join(s["text"] for s in hook),
        "segments": segments,
        "seconds_covered": max(s["start_ms"] for s in hook) // 1000,
    }
    video.hook_text = result["hook_text"]
    video.hook_segments = segments
    db.commit()
    return result


def stored_hook(db: Session, youtube_video_id: str) -> dict | None:
    video = _find_video(db, youtube_video_id)
    if video is None or video.hook_text is None:
        return None
    segments = video.hook_segments or []
    hook = [s for s in segments if s["start_ms"] < HOOK_WINDOW_MS] or segments[:1]
    return {
        "youtube_video_id": youtube_video_id,
        "hook_text": video.hook_text,
        "segments": segments,
        "seconds_covered": max((s["start_ms"] for s in hook), default=0) // 1000,
    }
