"""Pydantic schemas for channels and videos (API DTOs)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ChannelCreate(BaseModel):
    """Add a channel by URL, handle or channel ID.

    Examples: 'https://www.youtube.com/@mkbhd',
    'https://youtube.com/channel/UC...', '@mkbhd', 'UC...'.
    Legacy /c/ and /user/ URLs are detected and reported as unsupported.

    The backend resolves it against the YouTube Data API; no client-supplied
    channel statistics are ever accepted (rule #10).
    """

    identifier: str = Field(
        min_length=2,
        max_length=300,
        description="YouTube channel URL, handle ('@name'), or channel ID ('UC...').",
    )


class ChannelRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    youtube_channel_id: str
    title: str
    description: str | None = None
    thumbnail_url: str | None = None
    country: str | None = None
    subscriber_count: int | None = None
    video_count: int | None = None
    view_count: int | None = None
    channel_created_at: datetime | None = None
    last_synced_at: datetime | None = None


class VideoRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    youtube_video_id: str
    title: str
    thumbnail_url: str | None = None
    published_at: datetime | None = None
    duration_seconds: int | None = None
    view_count: int
    like_count: int | None = None
    comment_count: int | None = None
    last_synced_at: datetime | None = None


class SyncResult(BaseModel):
    channel_id: int
    videos_upserted: int
    quota_units_used: int


class ChannelAnalysisRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    channel_id: int
    created_at: datetime
    trigger: str
    subscriber_count: int | None = None
    video_count: int | None = None
    view_count: int | None = None
    videos_synced: int
    quota_units_used: int


class ChannelOverview(BaseModel):
    """Everything the Phase-1 dashboard needs in one call."""

    channel: ChannelRead
    summary: dict
    recent_videos: list[VideoRead]
    history: list[ChannelAnalysisRead]


# ---------------------------------------------------------------- Phase 2 audit
class AuditVideoRanking(BaseModel):
    youtube_video_id: str
    title: str
    thumbnail_url: str | None = None
    published_at: datetime | None = None
    view_count: int
    like_count: int | None = None
    comment_count: int | None = None
    duration_seconds: int | None = None
    age_days: int
    views_per_day: float
    vs_baseline_pct: float | None = None
    form: str
    mature: bool = True


class AuditTopicGroup(BaseModel):
    topic: str
    videos: int
    median_views_per_day: float
    vs_channel_baseline_pct: float | None = None
    example_titles: list[str]


class AuditFinding(BaseModel):
    key: str
    title: str
    severity: str            # high | medium | low | info
    status: str              # detected | ok | inconclusive | fact
    explanation: str
    recommended_action: str
    confidence: str          # high | medium | low | inconclusive
    evidence: dict
    data_limitations: list[str]
    kind: str                # fact | heuristic


class AuditPerformance(BaseModel):
    videos_analyzed: int
    analyzed_from_total_synced: int
    total_views: int
    avg_views: float | None = None
    median_views: float | None = None
    stdev_views: float | None = None
    total_likes: int | None = None
    total_comments: int | None = None
    like_to_view_ratio: float | None = None
    comment_to_view_ratio: float | None = None
    likes_available_on: int
    comments_available_on: int
    hidden_stats_videos: int
    uploads_per_month: float | None = None
    observed_span_days: int | None = None
    cadence_reliable: bool = False
    days_since_last_upload: int | None = None
    first_published_at: datetime | None = None
    last_published_at: datetime | None = None
    avg_duration_seconds: float | None = None
    median_duration_seconds: float | None = None
    duration_known_on: int
    shorts_count: int
    longform_count: int
    format_unclassified: int
    shorts_avg_views: float | None = None
    longform_avg_views: float | None = None
    shorts_median_vpd: float | None = None
    longform_median_vpd: float | None = None
    distribution_buckets: list[dict]
    monthly_uploads: list[dict]


class AuditHealthScore(BaseModel):
    score: int | None = None
    band: str | None = None
    components: list[dict]
    method: str
    caveats: list[str]
    kind: str = ""
    context: list[dict] = []


class ChannelAuditRead(BaseModel):
    """Response DTO for GET /channels/{id}/audit (mirrors analytics.audit)."""

    channel_id: int
    youtube_channel_id: str
    title: str
    audited_at: datetime
    computed_from: str
    config: dict
    formulas: dict
    performance: AuditPerformance
    activity_score: AuditHealthScore
    performance_score: AuditHealthScore
    findings: list[AuditFinding]
    top_performers: list[AuditVideoRanking]
    underperformers: list[AuditVideoRanking]
    outliers: list[AuditVideoRanking]
    fresh_videos_provisional: list[AuditVideoRanking] = []
    strong_topics: list[AuditTopicGroup]
    weak_topics: list[AuditTopicGroup]
    insufficient_data: bool
    notes: list[str]
    # Reliability-transparency fields (mirrored from analytics.audit.ChannelAudit).
    provenance: dict = {}
    unavailable_metrics: list[str] = []
    confidence_rules: dict = {}
    data_freshness: dict = {}
    coverage: dict = {}
