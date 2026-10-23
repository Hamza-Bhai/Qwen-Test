"""Phase 2 — Channel audit engine.

Pure, deterministic rules over *stored public data only* (videos already synced
from the YouTube Data API v3). No network calls happen here, so audits cost
zero quota and reuse PostgreSQL/SQLite records (architecture rule #8/#10).

Honesty constraints baked into this module:
* Public data cannot reveal CTR, impressions, watch time or audience geography.
  The engine never claims those metrics exist.
* Every finding carries its evidence, a confidence level and explicit data
  limitations. When evidence is insufficient the finding is marked
  ``confidence="inconclusive"`` instead of inventing a diagnosis.
* Underperformance is always stated against an explicit baseline (the channel's
  own median views-per-day), never on raw totals alone.
* Age normalization: videos are compared by *views per day since publish*, not
  total views, so a 3-day-old upload is not judged against a 3-year-old one.

Formulas (all documented in FORMULAS below and exposed in the API response):

  age_days(v)          = max(1, (now - published_at).days)   [floor of 1 day]
  vpd(v)               = view_count(v) / age_days(v)         [views/day baseline metric]
  avg_views            = mean(view_count over analyzed videos)
  median_views         = median(view_count over analyzed videos)
  total_views          = sum(view_count over analyzed videos)
  like_to_view_ratio   = sum(likes where available) / sum(views of those same videos)
                         -> reported as "like/view ratio", NOT full engagement rate
  comment_to_view_ratio= sum(comments where available) / sum(views of those same videos)
  uploads_per_month    = (analyzed_video_count - 1) / months_spanned
                         months_spanned = (last_pub - first_pub).days / 30.44, min 1
  days_since_last      = (now - most recent published_at).days
  outlier              |vpd| > median(vpd) + 3 * MAD-ish spread (robust z via IQR fence)
  concentration        top-5 videos' share of total views of analyzed set
  format split         youtube.com/shorts/<id> check, falling back to duration;
                       NEVER an official Shorts flag

Health score (0-100) is computed ONLY from components whose data exists;
missing components are excluded and their weight renormalized — a channel is
never penalized for unavailable metrics. Weights are listed in WEIGHTS.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from statistics import mean, median
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Channel, Video, analyzable_video_filter
from app.services.youtube import (
    ANALYSIS_MAX_VIDEOS,
    ANALYSIS_MIN_VIDEOS,
    ANALYSIS_WINDOW_DAYS,
    SHORTS_MAX_SECONDS,
)

# --------------------------------------------------------------------- config
SHORT_MAX_SECONDS = SHORTS_MAX_SECONDS  # Shorts are capped at 3 min; fallback only
MIN_VIDEOS_FOR_FINDINGS = 5         # below this: findings are inconclusive
RECENT_WINDOW_DAYS = 90             # "recent performance" window
STALE_UPLOAD_DAYS = 60              # gap threshold for "long publishing pause"
CONCENTRATION_HIGH = 0.60           # top-5 share above which dependence flagged
OUTLIER_IQR_K = 3.0                 # Tukey-fence multiplier for vpd outliers
TOPIC_TOKEN_MIN_LEN = 4             # ignore tiny words when grouping topics
TOPIC_MIN_DIFF_PCT = 20.0           # keyword must beat/trail the baseline by
FORMAT_BASELINE_MIN_VIDEOS = 3      # mature videos a format needs for its own
                                    # baseline; fewer => its videos are not judged
                                    # >= 20% to be listed as strong/weak
MIN_AGE_DAYS_FOR_RANKING = 7        # videos younger than this have provisional
                                    # traction only (excluded from top/under/outliers)
MIN_OBSERVATION_SPAN_DAYS = 30      # below this span, monthly cadence is not a
                                    # defensible long-term estimate (inconclusive)
MIN_HISTORY_DAYS_FOR_TREND = 90     # trend needs history predating the recent
                                    # window so cohorts are genuinely separate
FORMAT_SPLIT_MIN_PER_GROUP = 8      # >=8 videos per format for any detected split
FORMAT_SPLIT_MAX_IMBALANCE = 5.0    # ratio of group sizes above which medians
                                    # compare different creator behaviours
STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "your", "mine", "how",
    "why", "what", "when", "from", "into", "about", "video", "shorts",
    "official", "trailer", "full", "new", "you", "are", "was", "will",
}


@dataclass(frozen=True)
class AuditConfig:
    window_days: int = ANALYSIS_WINDOW_DAYS   # analyze uploads from the last N days,
    min_videos: int = ANALYSIS_MIN_VIDEOS     # ...but at least the latest N uploads
    max_videos: int = ANALYSIS_MAX_VIDEOS     # ...and at most N uploads
    short_max_seconds: int = SHORT_MAX_SECONDS
    recent_window_days: int = RECENT_WINDOW_DAYS
    stale_upload_days: int = STALE_UPLOAD_DAYS
    concentration_high: float = CONCENTRATION_HIGH


FORMULAS: dict[str, str] = {
    "activity_score": "weighted mean of recency (0.25) and consistency (0.20): is the creator publishing regularly? Needs >= 1 scored component",
    "performance_score": "weighted mean of recent_trend (0.25, vs the channel's own same-format history), distribution (0.15, top-5 view share) and engagement (0.15, like/view): how is the audience responding? Needs >= 2 scored components. Views/day and views per subscriber are shown as context, never scored (no fair public benchmark)",
    "analysis_set": f"uploads published in the last {ANALYSIS_WINDOW_DAYS} days; if fewer than {ANALYSIS_MIN_VIDEOS}, the latest {ANALYSIS_MIN_VIDEOS} uploads instead; never more than {ANALYSIS_MAX_VIDEOS} (newest first). Scheduled premieres and live streams excluded. The real date span is reported in `coverage`",
    "age_days": "max(1, floor((now_utc - published_at) / 1 day)); undated videos have age unknown and are excluded from mature rankings",
    "views_per_day": "view_count / age_days  (age-normalized comparison metric; for videos under 7 days old this is PROVISIONAL early traction only)",
    "avg_views": "mean(view_count) over the analyzed video set",
    "median_views": "median(view_count) over the analyzed video set",
    "total_views": "sum(view_count) over the analyzed video set",
    "like_to_view_ratio": "sum(like_count) / sum(view_count) restricted to videos that expose likes (NOT the full engagement rate)",
    "comment_to_view_ratio": "sum(comment_count) / sum(view_count) restricted to videos that expose comments",
    "uploads_per_month": f"(count(analyzed) - 1) / span_months, because n uploads span only n-1 publishing intervals; span_months = span_days / 30.44, measured from the first to the last analyzed upload. Only reported when the observed span >= {MIN_OBSERVATION_SPAN_DAYS} days (cadence_reliable=true); shorter windows report the raw count + span instead of extrapolating a monthly rate.",
    "days_since_last_upload": "floor((now_utc - max(published_at)) / 1 day)",
    "mature_video": f"a video with a known publish date at least {MIN_AGE_DAYS_FOR_RANKING} days old; only mature videos enter the baseline median, top/underperformer lists, outlier detection, distribution buckets and topic grouping",
    "fresh_video_provisional": f"published < {MIN_AGE_DAYS_FOR_RANKING} days ago (or undated): shown separately as provisional early traction, never ranked or compared against settled performance",
    "top_performers": "mature videos ranked by views_per_day, top 10 (fresh uploads excluded — their floored age exaggerates vpd)",
    "format_baseline": f"median views/day of MATURE videos of the same format (Shorts vs long-form); a format needs >= {FORMAT_BASELINE_MIN_VIDEOS} mature videos, otherwise its videos get no baseline and are not judged",
    "underperformers": f"mature videos at least {MIN_AGE_DAYS_FOR_RANKING} days old whose views/day is >40% below their own format's baseline",
    "outlier": "views_per_day beyond Q3 + 3*IQR computed per format over MATURE videos only (robust Tukey fence; requires >= 8 mature videos of that format; no assumption of normality)",
    "top5_share_of_views": f"sum(views of top-5 MATURE videos by view_count) / total views of MATURE videos; needs >= 10 videos at least {MIN_AGE_DAYS_FOR_RANKING} days old",
    "short_form": ("is_short from the youtube.com/shorts/<id> check (200 => Short, redirect to /watch => regular video); videos longer than " + str(SHORT_MAX_SECONDS) + "s are never Shorts; unchecked videos fall back to duration_seconds <= " + str(SHORT_MAX_SECONDS) + "s"),
    "recent_vs_history": f"per format: median views/day of MATURE uploads in the last {RECENT_WINDOW_DAYS} days / median of that format's older MATURE uploads; a format is compared only with >= 2 recent and >= 3 older videos; formats are never compared with each other; combined ratio = per-format ratios weighted by recent videos",
    "topic_groups": f"title words (>= {TOPIC_TOKEN_MIN_LEN} chars, not stopwords) shared by >= 3 MATURE videos; median of each video's views/day relative to its own format's baseline. Strong = >= +{TOPIC_MIN_DIFF_PCT:.0f}%, weak = <= -{TOPIC_MIN_DIFF_PCT:.0f}%; words in between are listed in neither",
    "distribution_buckets": "counts of MATURE videos' views/day relative to the mature median of their own format (<50%, 50–80%, 80–120%, 120–200%, >200%); fresh uploads listed separately as excluded",
    "format_split": f"median views/day per estimated format group over MATURE videos (>= {MIN_AGE_DAYS_FOR_RANKING} days old); needs >= {FORMAT_SPLIT_MIN_PER_GROUP} mature videos per group and a group-size imbalance <= {FORMAT_SPLIT_MAX_IMBALANCE}:1, else inconclusive; differences < 15% yield no claimed advantage",
}

WEIGHTS: dict[str, float] = {
    "recency": 0.25,           # publishing cadence / days since last upload
    "consistency": 0.20,       # upload-gap regularity
    "recent_trend": 0.25,      # recent views/day vs channel's own history
    "distribution": 0.15,      # healthy spread (not one-hit-wonder dependent)
    "engagement": 0.15,        # like+comment ratios vs channel median
}

# Two separate scores so regular uploading can never hide a weak audience
# response (or the other way round):
#   activity    — what the creator controls: publishing recency and rhythm
#   performance — how the audience responds: trend vs own history, view
#                 spread across videos, like/view ratio
SCORE_GROUPS: dict[str, tuple[str, ...]] = {
    "activity": ("recency", "consistency"),
    "performance": ("recent_trend", "distribution", "engagement"),
}
SCORE_MIN_COMPONENTS = {"activity": 1, "performance": 2}


# --------------------------------------------------------------------- DTOs
@dataclass
class PerformanceStats:
    videos_analyzed: int
    analyzed_from_total_synced: int
    total_views: int
    avg_views: float | None
    median_views: float | None
    stdev_views: float | None
    total_likes: int | None
    total_comments: int | None
    like_to_view_ratio: float | None
    comment_to_view_ratio: float | None
    likes_available_on: int
    comments_available_on: int
    hidden_stats_videos: int
    uploads_per_month: float | None
    observed_span_days: int | None
    cadence_reliable: bool
    days_since_last_upload: int | None
    first_published_at: datetime | None
    last_published_at: datetime | None
    avg_duration_seconds: float | None
    median_duration_seconds: float | None
    duration_known_on: int
    shorts_count: int
    longform_count: int
    format_unclassified: int
    shorts_avg_views: float | None
    longform_avg_views: float | None
    shorts_median_vpd: float | None
    longform_median_vpd: float | None
    distribution_buckets: list[dict[str, Any]]
    monthly_uploads: list[dict[str, Any]]


@dataclass
class VideoRanking:
    youtube_video_id: str
    title: str
    thumbnail_url: str | None
    published_at: datetime | None
    view_count: int
    like_count: int | None
    comment_count: int | None
    duration_seconds: int | None
    age_days: int
    views_per_day: float
    vs_baseline_pct: float | None  # relative to channel median vpd
    form: str                      # "estimated-shorts" | "estimated-longform" | "unknown"
    last_synced_at: datetime | None = None  # when stats were observed (freshness)
    mature: bool = True            # age >= MIN_AGE_DAYS_FOR_RANKING; fresh videos
                                   # have provisional traction only, never ranked


@dataclass
class TopicGroup:
    topic: str
    videos: int
    median_views_per_day: float
    vs_channel_baseline_pct: float | None
    example_titles: list[str]


@dataclass
class Finding:
    key: str
    title: str
    severity: str            # high | medium | low | info
    status: str              # detected | ok | inconclusive
    explanation: str
    recommended_action: str
    confidence: str          # high | medium | low | inconclusive
    evidence: dict[str, Any]
    data_limitations: list[str]
    kind: str                # fact | heuristic


@dataclass
class HealthScore:
    score: int | None
    band: str | None
    components: list[dict[str, Any]]
    method: str
    caveats: list[str]
    kind: str = ""                      # "activity" | "performance"
    context: list[dict[str, Any]] = field(default_factory=list)  # shown, never scored


@dataclass
class ChannelAudit:
    channel_id: int
    youtube_channel_id: str
    title: str
    audited_at: datetime
    computed_from: str
    config: dict[str, Any]
    formulas: dict[str, str]
    performance: PerformanceStats
    activity_score: HealthScore
    performance_score: HealthScore
    findings: list[Finding]
    top_performers: list[VideoRanking]
    underperformers: list[VideoRanking]
    outliers: list[VideoRanking]
    fresh_videos_provisional: list[VideoRanking]
    strong_topics: list[TopicGroup]
    weak_topics: list[TopicGroup]
    insufficient_data: bool
    notes: list[str]
    provenance: dict[str, str] | None = None
    unavailable_metrics: list[str] | None = None
    confidence_rules: dict[str, str] | None = None
    data_freshness: dict[str, Any] | None = None
    coverage: dict[str, Any] | None = None


# --------------------------------------------------------------------- helpers
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        # SQLite (and some PG drivers under legacy timezones) hand back naive
        # datetimes even for timezone=True columns. The app stores everything
        # in UTC, so interpret naive values as UTC instead of letting them
        # crash comparisons with aware datetimes or sort inconsistently.
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _gap_days(a: datetime, b: datetime) -> float:
    """Exact signed distance between two timestamps in fractional days.

    Never use timedelta.days here: it truncates toward minus infinity, so a
    burst of uploads spaced 6–20 hours apart would yield all-zero gaps and the
    engine would falsely claim every upload shares one publish date.
    """
    return (b - a).total_seconds() / 86400.0


def _safe_div(num: float | None, den: float) -> float | None:
    """Zero-denominator / missing-numerator guard: returns None, never raises."""
    if num is None or den == 0:
        return None
    return num / den


def _quantiles(sorted_vals: list[float]) -> tuple[float, float]:
    n = len(sorted_vals)
    def q(p: float) -> float:
        if n == 1:
            return sorted_vals[0]
        idx = p * (n - 1)
        lo = int(idx)
        hi = min(lo + 1, n - 1)
        frac = idx - lo
        return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac
    return q(0.25), q(0.75)


def classify_format(
    duration_seconds: int | None,
    short_max: int,
    is_short: bool | None = None,
) -> str:
    """Shorts vs long-form label for one video.

      - is_short known (youtube.com/shorts/<id> check at sync) -> use it
      - otherwise duration missing                            -> unknown
      - otherwise duration <= short_max (180s)                -> estimated-shorts
      - otherwise                                             -> estimated-longform

    Labels keep the 'estimated-' prefix because the Data API itself has no
    Shorts flag; the URL check is YouTube's own routing, not an API field.
    """
    if is_short is not None:
        return "estimated-shorts" if is_short else "estimated-longform"
    if duration_seconds is None:
        return "unknown"
    if duration_seconds <= short_max:
        return "estimated-shorts"
    return "estimated-longform"


# ------------------------------------------------------------------ provenance
PROVENANCE_LABELS = {
    "observed": "Observed — retrieved directly from the YouTube Data API",
    "calculated": "Calculated — deterministic math on observed data",
    "heuristic": "Heuristic — rule/threshold interpretation, not a verified fact",
    "unavailable": "Unavailable — cannot be obtained from public data",
    "inconclusive": "Inconclusive — insufficient evidence for a useful judgment",
}

UNAVAILABLE_METRICS = [
    "Click-through rate (CTR)",
    "Impression counts",
    "Watch time / average view duration",
    "Audience retention",
    "Traffic sources",
    "Subscriber source / audience geography",
]

CONFIDENCE_RULES = {
    "high": "Directly measured from complete stored data; arithmetic only.",
    "medium": "Derived from real data but subject to stated limitations "
              "(small cohorts, age effects, or missing context).",
    "low": "Lexical/heuristic signal only; treat as a question to investigate, not a verdict.",
    "inconclusive": "Evidence insufficient — deliberately no judgement made.",
}


def _topic_tokens(title: str) -> list[str]:
    toks: list[str] = []
    for raw in title.lower().replace("|", " ").replace(":", " ").split():
        w = "".join(ch for ch in raw if ch.isalnum())
        if len(w) >= TOPIC_TOKEN_MIN_LEN and w not in STOPWORDS and not w.isdigit():
            toks.append(w)
    return toks


# --------------------------------------------------------------------- core math
def compute_performance(videos: list[Video], cfg: AuditConfig, now: datetime) -> PerformanceStats:
    views = [v.view_count or 0 for v in videos]
    total_views = sum(views)
    avg_views = mean(views) if views else None
    med_views = median(views) if views else None
    stdev = (
        (sum((x - avg_views) ** 2 for x in views) / (len(views) - 1)) ** 0.5
        if len(views) >= 2 and avg_views is not None
        else None
    )

    # Engagement: restrict numerator/denominator to the SAME videos that expose
    # the stat, so hidden-stats videos never distort the ratio.
    lv_pairs = [(v.like_count, v.view_count or 0) for v in videos if v.like_count is not None]
    cv_pairs = [(v.comment_count, v.view_count or 0) for v in videos if v.comment_count is not None]
    total_likes = sum(x for x, _ in lv_pairs) if lv_pairs else None
    total_comments = sum(x for x, _ in cv_pairs) if cv_pairs else None
    like_ratio = _safe_div(total_likes or 0, sum(y for _, y in lv_pairs)) if lv_pairs else None
    comment_ratio = _safe_div(total_comments or 0, sum(y for _, y in cv_pairs)) if cv_pairs else None
    hidden = sum(1 for v in videos if v.like_count is None and v.comment_count is None)

    pubs = sorted(_aware(v.published_at) for v in videos if v.published_at)
    days_since_last = (
        (now - pubs[-1]).days if pubs else None
    )
    # Monthly cadence is only a defensible *long-term* estimate when the
    # observed window spans enough time. Over shorter windows we report the
    # raw observed count and span instead of extrapolating (e.g. 50 uploads
    # in 4 days must never be presented as "50 videos/month" publishing rate).
    uploads_per_month = None
    observed_span_days: int | None = None
    cadence_reliable = False
    if len(pubs) >= 2:
        observed_span_days = max(round(_gap_days(pubs[0], pubs[-1])), 1)
        if observed_span_days >= MIN_OBSERVATION_SPAN_DAYS:
            months = observed_span_days / 30.44
            # n uploads between the first and last date make n-1 intervals.
            uploads_per_month = (len(pubs) - 1) / months
            cadence_reliable = True
    elif len(pubs) == 1:
        uploads_per_month = None  # single upload: cadence unknowable

    durs = [v.duration_seconds for v in videos if v.duration_seconds is not None]

    shorts_ids, long_ids, unknown_ids = [], [], []
    shorts_views, long_views, shorts_vpd, long_vpd = [], [], [], []
    mature_by_form: dict[str, list[float]] = {}
    for v in videos:
        form = classify_format(v.duration_seconds, cfg.short_max_seconds,
                               getattr(v, "is_short", None))
        {"estimated-shorts": shorts_ids, "estimated-longform": long_ids, "unknown": unknown_ids}[form].append(v)
        pub = _aware(v.published_at)
        age = max(1, (now - pub).days) if pub else None
        # Views/day of uploads under MIN_AGE_DAYS_FOR_RANKING is provisional
        # early traction; only mature videos enter the per-format medians.
        mature_vpd = (v.view_count or 0) / age if age and age >= MIN_AGE_DAYS_FOR_RANKING else None
        if mature_vpd is not None:
            mature_by_form.setdefault(form, []).append(mature_vpd)
        if form == "estimated-shorts":
            shorts_views.append(v.view_count or 0)
            if mature_vpd is not None:
                shorts_vpd.append(mature_vpd)
        elif form == "estimated-longform":
            long_views.append(v.view_count or 0)
            if mature_vpd is not None:
                long_vpd.append(mature_vpd)

    # Distribution buckets: each MATURE video's views/day relative to the median
    # of its OWN format (Shorts vs long-form differ by orders of magnitude).
    # Fresh uploads have provisional vpd and are listed separately.
    mature_count = sum(len(v) for v in mature_by_form.values())
    excluded_fresh = len(videos) - mature_count
    ratios: list[float] = []
    no_baseline = 0
    for vals in mature_by_form.values():
        fmed = median(vals)
        if len(vals) >= FORMAT_BASELINE_MIN_VIDEOS and fmed > 0:
            ratios.extend(x / fmed for x in vals)
        else:
            no_baseline += len(vals)
    buckets = []
    if len(ratios) >= 3:
        ranges = [(0.0, 0.5), (0.5, 0.8), (0.8, 1.2), (1.2, 2.0), (2.0, float("inf"))]
        labels = ["<50%", "50–80%", "80–120%", "120–200%", ">200%"]
        for (lo, hi), label in zip(ranges, labels):
            count = sum(1 for x in ratios if lo <= x < hi)
            buckets.append({"label": label, "videos": count})
    else:
        buckets = [{"label": "insufficient mature data", "videos": len(ratios)}]
    if no_baseline:
        buckets.append({
            "label": f"not bucketed (format has < {FORMAT_BASELINE_MIN_VIDEOS} mature videos)",
            "videos": no_baseline,
        })
    if excluded_fresh:
        buckets.append({
            "label": "excluded (<7 days old)",
            "videos": excluded_fresh,
            "note": f"Videos under {MIN_AGE_DAYS_FOR_RANKING} days have provisional traction only.",
        })

    monthly: dict[str, int] = {}
    for p in pubs:
        key = f"{p.year:04d}-{p.month:02d}"
        monthly[key] = monthly.get(key, 0) + 1

    return PerformanceStats(
        videos_analyzed=len(videos),
        analyzed_from_total_synced=len(videos),
        total_views=total_views,
        avg_views=avg_views,
        median_views=med_views,
        stdev_views=stdev,
        total_likes=total_likes,
        total_comments=total_comments,
        like_to_view_ratio=like_ratio,
        comment_to_view_ratio=comment_ratio,
        likes_available_on=len(lv_pairs),
        comments_available_on=len(cv_pairs),
        hidden_stats_videos=hidden,
        uploads_per_month=uploads_per_month,
        observed_span_days=observed_span_days,
        cadence_reliable=cadence_reliable,
        days_since_last_upload=days_since_last,
        first_published_at=pubs[0] if pubs else None,
        last_published_at=pubs[-1] if pubs else None,
        avg_duration_seconds=mean(durs) if durs else None,
        median_duration_seconds=median(durs) if durs else None,
        duration_known_on=len(durs),
        shorts_count=len(shorts_ids),
        longform_count=len(long_ids),
        format_unclassified=len(unknown_ids),
        shorts_avg_views=mean(shorts_views) if shorts_views else None,
        longform_avg_views=mean(long_views) if long_views else None,
        shorts_median_vpd=median(shorts_vpd) if shorts_vpd else None,
        longform_median_vpd=median(long_vpd) if long_vpd else None,
        distribution_buckets=buckets,
        monthly_uploads=[{"month": k, "uploads": monthly[k]} for k in sorted(monthly)],
    )




def rank_videos(videos: list[Video], cfg: AuditConfig, now: datetime) -> tuple[list[VideoRanking], float | None]:
    rows: list[VideoRanking] = []
    for v in videos:
        pub = _aware(v.published_at)
        if pub is not None:
            age = max(1, (now - pub).days)
        else:
            # Undated video: age is UNKNOWN, not 1. Mark it immature so it is
            # excluded from mature rankings/baselines instead of pretending a
            # floor of one day reflects settled performance.
            age = 0
        vpd = (v.view_count or 0) / age if age > 0 else 0.0
        rows.append(
            VideoRanking(
                youtube_video_id=v.youtube_video_id,
                title=v.title,
                thumbnail_url=v.thumbnail_url,
                published_at=pub,
                view_count=v.view_count or 0,
                like_count=v.like_count,
                comment_count=v.comment_count,
                duration_seconds=v.duration_seconds,
                age_days=age,
                views_per_day=round(vpd, 2),
                vs_baseline_pct=None,
                form=classify_format(v.duration_seconds, cfg.short_max_seconds,
                                     getattr(v, "is_short", None)),
                last_synced_at=_aware(v.last_synced_at),
                mature=pub is not None and age >= MIN_AGE_DAYS_FOR_RANKING,
            )
        )
    # Baselines use MATURE videos only. Including a hours-old upload whose
    # views/day is floored at 1 day would drag the median around and make its
    # own provisional traction look comparable to settled performance.
    vpds = sorted(r.views_per_day for r in rows if r.mature)
    base = median(vpds) if vpds else None
    # Each video is compared with its OWN format: Shorts and long-form views/day
    # differ by orders of magnitude, so one mixed median would brand a whole
    # format as under- or over-performing.
    by_format = format_baselines(rows)
    for r in rows:
        fb = by_format.get(r.form)
        if fb and fb > 0:
            r.vs_baseline_pct = round((r.views_per_day / fb - 1.0) * 100.0, 1)
    return rows, base


def format_baselines(rows: list[VideoRanking]) -> dict[str, float]:
    """Median mature views/day per format, for formats with enough videos."""
    groups: dict[str, list[float]] = {}
    for r in rows:
        if r.mature:
            groups.setdefault(r.form, []).append(r.views_per_day)
    return {form: median(v) for form, v in groups.items() if len(v) >= FORMAT_BASELINE_MIN_VIDEOS}


def detect_outliers(rows: list[VideoRanking]) -> list[VideoRanking]:
    """Tukey fence per format, so a hit long-form video is not hidden among
    Shorts (or every Short flagged against long-form numbers)."""
    found: list[VideoRanking] = []
    for form in {r.form for r in rows}:
        mature = [r for r in rows if r.mature and r.form == form]
        if len(mature) < 8:  # fences meaningless on tiny samples
            continue
        q1, q3 = _quantiles(sorted(r.views_per_day for r in mature))
        iqr = q3 - q1
        if iqr <= 0:
            continue
        upper = q3 + OUTLIER_IQR_K * iqr
        found.extend(r for r in mature if r.views_per_day > upper)
    return sorted(found, key=lambda r: -(r.vs_baseline_pct or 0))[:5]


def group_topics(rows: list[VideoRanking]) -> tuple[list[TopicGroup], list[TopicGroup]]:
    groups: dict[str, list[VideoRanking]] = {}
    # Topic strength uses MATURE videos with a format baseline only — a fresh
    # upload's provisional views/day must not decide whether a keyword looks
    # "strong" or "weak", and each video counts relative to its own format.
    for r in rows:
        if not r.mature or r.vs_baseline_pct is None:
            continue
        for tok in set(_topic_tokens(r.title)):
            groups.setdefault(tok, []).append(r)
    scored: list[TopicGroup] = []
    for topic, vids in groups.items():
        if len(vids) < 3:  # need repetition before calling something a "topic"
            continue
        med = median(v.views_per_day for v in vids)
        rel = median(1.0 + v.vs_baseline_pct / 100.0 for v in vids)
        pct = round((rel - 1.0) * 100.0, 1)
        scored.append(
            TopicGroup(
                topic=topic,
                videos=len(vids),
                median_views_per_day=round(med, 2),
                vs_channel_baseline_pct=pct,
                example_titles=[v.title for v in sorted(vids, key=lambda x: -x.views_per_day)[:3]],
            )
        )
    # A keyword is "strong"/"weak" only when it is clearly above/below the
    # baseline, so the lists are disjoint and a below-baseline word can never
    # be shown as strong. No baseline => no claim either way.
    strong = sorted((g for g in scored if g.vs_channel_baseline_pct >= TOPIC_MIN_DIFF_PCT),
                    key=lambda g: -g.vs_channel_baseline_pct)[:5]
    weak = sorted((g for g in scored if g.vs_channel_baseline_pct <= -TOPIC_MIN_DIFF_PCT),
                  key=lambda g: g.vs_channel_baseline_pct)[:5]
    return strong, weak


# --------------------------------------------------------------------- findings
def _f(key, title, severity, status, explanation, action, confidence, evidence, limits, kind="heuristic") -> Finding:
    return Finding(key, title, severity, status, explanation, action, confidence, evidence, limits, kind)


def build_findings(
    videos: list[Video], rows: list[VideoRanking], perf: PerformanceStats,
    baseline: float | None, outliers: list[VideoRanking], cfg: AuditConfig, now: datetime,
) -> list[Finding]:
    findings: list[Finding] = []
    n = perf.videos_analyzed
    small_sample = n < MIN_VIDEOS_FOR_FINDINGS
    limits_small = [f"Only {n} videos analyzed; heuristics need >= {MIN_VIDEOS_FOR_FINDINGS}."]

    # ---- FACTS (measured) ----
    if perf.days_since_last_upload is not None:
        if perf.uploads_per_month is not None and perf.cadence_reliable:
            cadence_line = (
                f"The channel published {perf.uploads_per_month:.2f} videos/month across the "
                f"observed {perf.observed_span_days}-day window; the newest analyzed upload is "
                f"{perf.days_since_last_upload} day(s) old."
            )
        elif perf.observed_span_days is not None:
            cadence_line = (
                f"{n} uploads observed within a span of only {perf.observed_span_days} day(s). "
                "This window is too short to extrapolate a monthly publishing rate, so no "
                "videos/month figure is reported. The newest analyzed upload is "
                f"{perf.days_since_last_upload} day(s) old."
            )
        else:
            cadence_line = f"Newest analyzed upload is {perf.days_since_last_upload} day(s) old."
        findings.append(_f(
            "upload_cadence", "Upload cadence (measured)", "info", "fact",
            cadence_line,
            "No action — measured fact.", "high",
            {"uploads_per_month": perf.uploads_per_month,
             "observed_span_days": perf.observed_span_days,
             "cadence_reliable": perf.cadence_reliable,
             "min_span_days_for_monthly_rate": MIN_OBSERVATION_SPAN_DAYS,
             "days_since_last_upload": perf.days_since_last_upload,
             "first_published_at": perf.first_published_at.isoformat() if perf.first_published_at else None,
             "last_published_at": perf.last_published_at.isoformat() if perf.last_published_at else None},
            ["Cadence is computed over the analyzed set only (see analysis coverage), "
             "not the channel's whole history.",
             "A monthly rate requires an observation span >= "
             f"{MIN_OBSERVATION_SPAN_DAYS} days; shorter bursts are reported as raw counts only."],
            kind="fact",
        ))

    # ---- 1. Long publishing pause ----
    if small_sample or perf.days_since_last_upload is None:
        findings.append(_f("upload_pause", "Publishing pause", "low", "inconclusive",
                           "Not enough dated uploads to judge publishing gaps.",
                           "Re-sync more videos, then re-run the audit.", "inconclusive", {}, limits_small))
    elif perf.days_since_last_upload >= cfg.stale_upload_days:
        sev = "high" if perf.days_since_last_upload >= cfg.stale_upload_days * 2 else "medium"
        findings.append(_f("upload_pause", "Long period without publishing", sev, "detected",
                           f"No upload in the analyzed set for {perf.days_since_last_upload} days "
                           f"(threshold {cfg.stale_upload_days}).",
                           "Plan a realistic release schedule; even one well-prepared video restores momentum.",
                           "high",
                           {"days_since_last_upload": perf.days_since_last_upload, "threshold_days": cfg.stale_upload_days},
                           ["Only the analyzed set is checked; videos newer than the last sync would not appear here."]))
    else:
        findings.append(_f("upload_pause", "Publishing pause", "low", "ok",
                           f"Last upload was {perf.days_since_last_upload} days ago (below the {cfg.stale_upload_days}-day threshold).",
                           "Keep current cadence.", "high",
                           {"days_since_last_upload": perf.days_since_last_upload}, []))

    # ---- 2. Upload consistency (gap variance) ----
    pubs = sorted(_aware(v.published_at) for v in videos if v.published_at)
    # A burst ending "now" also means most of its uploads are themselves too
    # fresh for any rhythm judgement; count them so the rule can refuse.
    fresh_in_window = sum(1 for p in pubs if (now - p).days < MIN_AGE_DAYS_FOR_RANKING)
    if len(pubs) < 6:
        findings.append(_f("upload_consistency", "Upload consistency", "low", "inconclusive",
                           f"Only {len(pubs)} dated uploads available; gap statistics need at least 6.",
                           "Sync more videos and re-run.", "inconclusive", {"dated_uploads": len(pubs)}, limits_small))
    elif pubs and _gap_days(pubs[0], pubs[-1]) < MIN_OBSERVATION_SPAN_DAYS:
        # Whole analyzed window is a short burst: even with fractional gaps it
        # cannot represent a publishing rhythm, and extrapolating from it (or
        # scoring consistency off it) would fabricate coverage.
        gaps = [_gap_days(a, b) for a, b in zip(pubs, pubs[1:])]
        med_gap = median(gaps)
        span_days = _gap_days(pubs[0], pubs[-1])
        findings.append(_f("upload_consistency", "Upload consistency", "low", "inconclusive",
                           f"The latest {len(pubs)} analyzed uploads were published within a single "
                           f"{span_days:.1f}-day burst (median gap {med_gap*24:.1f} hours), which is far "
                           f"short of the {MIN_OBSERVATION_SPAN_DAYS}-day observation needed to judge a "
                           "publishing rhythm — this window says nothing about the channel's normal cadence.",
                           "Re-audit after a wider spread of uploads accumulates.", "inconclusive",
                           {"median_gap_days": round(med_gap, 2), "observed_span_days": round(span_days, 2),
                            "gaps_days": [round(g, 2) for g in gaps],
                            "min_observation_days": MIN_OBSERVATION_SPAN_DAYS},
                           ["A bulk import or mass-scheduled release can compress publish dates; "
                            "gap regularity cannot be judged from one burst."]))
    else:
        # Fractional-day gaps: timedelta.days truncation used to report every
        # gap of a same-day burst as 0 and falsely claim one shared date.
        gaps = [_gap_days(a, b) for a, b in zip(pubs, pubs[1:])]
        med_gap = median(gaps)
        big_gaps = sum(1 for g in gaps if med_gap > 0 and g > med_gap * 3)
        span_days = _gap_days(pubs[0], pubs[-1])
        ev_gaps = [round(g, 2) for g in gaps]
        if fresh_in_window >= max(2, len(pubs) // 2):
            findings.append(_f("upload_consistency", "Upload consistency", "low", "inconclusive",
                               f"{fresh_in_window} of the {len(pubs)} analyzed uploads are under "
                               f"{MIN_AGE_DAYS_FOR_RANKING} days old; the observable history is too young "
                               "to establish a publishing rhythm.",
                               "Re-audit once more of the recent uploads are at least a week old.", "inconclusive",
                               {"median_gap_days": round(med_gap, 2), "observed_span_days": round(span_days, 2),
                                "gaps_days": ev_gaps},
                               ["Fresh-upload windows cannot distinguish a real rhythm from a burst."]))
        elif med_gap <= 0.05:  # <= ~72 minutes between consecutive uploads
            findings.append(_f("upload_consistency", "Upload consistency", "low", "inconclusive",
                               f"The latest {len(pubs)} analyzed uploads were published within a single "
                               f"{span_days:.1f}-day burst (median gap {med_gap*24:.1f} hours), so this window "
                               "says nothing about the channel's normal publishing rhythm.",
                               "Re-audit after a wider spread of uploads accumulates.", "inconclusive",
                               {"median_gap_days": round(med_gap, 2), "observed_span_days": round(span_days, 2),
                                "gaps_days": ev_gaps},
                               ["A bulk import or mass-scheduled release can compress publish dates; "
                                "gap regularity cannot be judged from one burst."]))
        elif big_gaps >= max(2, len(gaps) // 4):
            findings.append(_f("upload_consistency", "Inconsistent upload schedule", "medium", "detected",
                               f"{big_gaps} of {len(gaps)} upload gaps exceed 3× the median gap ({med_gap:.1f} days).",
                               "Batch-produce content or use a content calendar to smooth gaps.", "medium",
                               {"median_gap_days": round(med_gap, 2), "gaps_over_3x_median": big_gaps,
                                "total_gaps": len(gaps), "observed_span_days": round(span_days, 2)},
                               ["Gap analysis uses only the analyzed window."]))
        else:
            findings.append(_f("upload_consistency", "Upload consistency", "low", "ok",
                               f"Median gap between uploads is {med_gap:.1f} days with few outliers.",
                               "Maintain the rhythm.", "medium",
                               {"median_gap_days": round(med_gap, 2), "total_gaps": len(gaps),
                                "observed_span_days": round(span_days, 2)}, []))

    # ---- 3. Recent performance vs channel's own history ----
    if small_sample or baseline is None or baseline <= 0:
        findings.append(_f("recent_trend", "Recent performance trend", "low", "inconclusive",
                           "Insufficient data for an age-normalized recent-vs-history comparison.",
                           "Analyze more videos first.", "inconclusive", {}, limits_small))
    else:
        cutoff = now - timedelta(days=cfg.recent_window_days)
        recent = [r for r in rows if r.published_at and r.published_at >= cutoff]
        older = [r for r in rows if r.published_at and r.published_at < cutoff]
        mature_recent = [r for r in recent if r.mature]
        immature = len(recent) - len(mature_recent)
        # Independent-history requirement: with no video predating the recent
        # window (or fewer than MIN_HISTORY_DAYS_FOR_TREND of them), the
        # "historical baseline" would be drawn from the SAME cohort being
        # tested — comparing a set against itself proves nothing. In that case
        # any ratio ≈ 1.0 ("stable") is an artifact, so we refuse to judge.
        hist_cutoff = now - timedelta(days=max(cfg.recent_window_days, MIN_HISTORY_DAYS_FOR_TREND))
        independent_hist = [r for r in rows if r.published_at and r.published_at < hist_cutoff and r.mature]
        ev = {"recent_videos": len(recent), "window_days": cfg.recent_window_days,
              "new_uploads_excluded_from_decline_call": immature,
              "independent_history_videos": len(independent_hist)}
        if len(mature_recent) < 2:
            reason = (
                f"The recent cohort ({cfg.recent_window_days}-day window) has {len(recent)} upload(s), "
                f"{immature} of which are under {MIN_AGE_DAYS_FOR_RANKING} days old; at least 2 mature "
                "recent videos are needed before any trend can be judged."
                if recent else
                f"No uploads within the last {cfg.recent_window_days} days to build a recent cohort."
            )
            findings.append(_f("recent_trend", "Recent performance trend", "low", "inconclusive",
                               reason,
                               "Re-audit once recent uploads are at least a week old.", "inconclusive", ev,
                               ["New-upload view accumulation is incomplete; treating low vpd as failure "
                                "would be misleading."]))
        elif len(independent_hist) < 3:
            findings.append(_f("recent_trend", "Recent performance trend", "low", "inconclusive",
                               f"All {len(rows)} analyzed videos were published within roughly the same "
                               f"{cfg.recent_window_days}-day window; there is no independent earlier "
                               "cohort to compare against. A near-identical recent/baseline ratio here "
                               "is expected and says nothing about a trend.",
                               "Sync a wider slice of the channel's back-catalogue, then re-run.",
                               "inconclusive", ev,
                               ["Trend detection requires history that does not overlap the recent cohort."]))
        else:
            # Compare each format only with its own history: a channel that
            # recently added Shorts would otherwise look like a huge uptrend.
            per_format: dict[str, dict[str, Any]] = {}
            for form in sorted({r.form for r in mature_recent}):
                rec_f = [r.views_per_day for r in mature_recent if r.form == form]
                older_f = [r.views_per_day for r in older if r.form == form and r.mature]
                indep_f = [r.views_per_day for r in independent_hist if r.form == form]
                if len(rec_f) < 2 or len(indep_f) < 3:
                    continue
                rec_med_f = median(rec_f)
                hist_med_f = median(older_f if len(older_f) >= 3 else indep_f)
                ratio_f = _safe_div(rec_med_f, hist_med_f)
                if ratio_f is None:
                    continue
                per_format[form] = {"recent_median_vpd": round(rec_med_f, 2),
                                    "history_median_vpd": round(hist_med_f, 2),
                                    "ratio": round(ratio_f, 3),
                                    "recent_videos": len(rec_f), "history_videos": len(indep_f)}
            ratio = None
            if per_format:
                weight = sum(p["recent_videos"] for p in per_format.values())
                ratio = sum(p["ratio"] * p["recent_videos"] for p in per_format.values()) / weight
            ev.update({"ratio": round(ratio, 3) if ratio is not None else None,
                       "formats_compared": sorted(per_format),
                       "per_format": per_format,
                       "mature_recent_videos": len(mature_recent)})
            detail = "; ".join(
                f"{f.replace('estimated-', '')} {p['recent_median_vpd']:.1f} vs {p['history_median_vpd']:.1f} views/day"
                for f, p in per_format.items())
            effective = ratio
            if ratio is None:
                findings.append(_f("recent_trend", "Recent performance trend", "low", "inconclusive",
                                   "No format has both >= 2 mature recent uploads and >= 3 older uploads of the "
                                   "same format, and different formats are never compared with each other.",
                                   "Re-audit once recent uploads have older same-format history to compare against.",
                                   "inconclusive", ev,
                                   ["Shorts and long-form earn views at very different rates; mixing them "
                                    "would show a format shift as a performance trend."]))
            elif effective < 0.6:
                findings.append(_f("recent_trend", "Weak recent performance vs own history", "high", "detected",
                                   f"Mature videos from the last {cfg.recent_window_days} days earn {effective*100:.0f}% of the "
                                   f"channel's own historical median views/day for the same format ({detail}). "
                                   f"Baseline: the channel's own videos. No statistical significance test is claimed.",
                                   "Review recent titles/topics/thumbnails against the channel's best-performing patterns.",
                                   "medium", ev,
                                   [f"Views/day keeps falling as videos age slowly; uploads under "
                                    f"{MIN_AGE_DAYS_FOR_RANKING} days old were excluded from this call.",
                                    "Public data cannot show whether the drop is demand-side or content-side — no causal claim is made."]))
            elif effective > 1.5:
                findings.append(_f("recent_trend", "Recent uploads outperform history", "info", "detected",
                                   f"Recent videos earn {ratio*100:.0f}% of the same format's historical "
                                   f"views/day ({detail}).",
                                   "Identify what changed and repeat it deliberately.", "medium", ev, []))
            else:
                findings.append(_f("recent_trend", "Recent performance stable", "low", "ok",
                                   f"Mature recent views/day sits within the channel's own same-format "
                                   f"history ({ratio*100:.0f}%; {detail}).",
                                   "No action needed.", "medium", ev, []))

    # ---- 4. Dependence on a few popular videos ----
    # Mature videos only: a days-old upload's view total is still climbing, so
    # it can neither prove nor disprove dependence on a few hits.
    mature_views = [r.view_count for r in rows if r.mature]
    if len(mature_views) < 10:
        findings.append(_f("concentration", "Dependence on top videos", "low", "inconclusive",
                           f"Need >=10 videos at least {MIN_AGE_DAYS_FOR_RANKING} days old to measure view "
                           f"concentration meaningfully (have {len(mature_views)} of {n}).",
                           "Re-audit once more uploads are at least a week old.", "inconclusive",
                           {"mature_videos": len(mature_views), "analyzed": n}, limits_small))
    else:
        mature_total = sum(mature_views)
        top5 = sorted(mature_views, reverse=True)[:5]
        share = _safe_div(sum(top5), mature_total)
        ev = {"top5_share_of_views": round(share, 3) if share is not None else None,
              "total_views_analyzed": mature_total,
              "mature_videos": len(mature_views)}
        if share is not None and share > cfg.concentration_high:
            findings.append(_f("concentration", "Heavy dependence on a few popular videos", "medium", "detected",
                               f"The top 5 videos hold {share*100:.0f}% of all views in the analyzed set "
                               f"(threshold {cfg.concentration_high*100:.0f}%).",
                               "Build series/playlists around the hits so traffic spreads across related uploads.",
                               "high", ev, ["Old evergreen videos naturally accumulate views; share alone is not failure."]))
        else:
            findings.append(_f("concentration", "View concentration", "low", "ok",
                               f"Top-5 share of analyzed views is {(share or 0)*100:.0f}% (below threshold).",
                               "No action needed.", "high", ev, []))

    # ---- 5. Repetitive title keywords (lexical, NOT semantic topics) ----
    if small_sample:
        findings.append(_f("topic_repetition", "Repeated title keywords", "low", "inconclusive",
                           "Too few videos to detect recurring title keywords.", "Analyze more videos.", "inconclusive", {}, limits_small))
    else:
        token_counts: dict[str, int] = {}
        for r in rows:
            for tok in set(_topic_tokens(r.title)):
                token_counts[tok] = token_counts.get(tok, 0) + 1
        dominant = sorted(token_counts.items(), key=lambda kv: -kv[1])[:3]
        coverage = [(t, c, round(c / n, 2)) for t, c in dominant if n and c / n >= 0.5]
        if coverage:
            findings.append(_f("topic_repetition", "One repeated title keyword dominates uploads", "low", "detected",
                               "One word appears in half or more of analyzed titles: "
                               + ", ".join(f"'{t}' ({c}/{n} videos)" for t, c, _ in coverage)
                               + ". This measures WORD overlap only — it is not a verified list of semantic topics.",
                               "Consider adjacent angles or formats to broaden topical reach.", "low",
                               {"dominant_tokens": coverage},
                               ["Lexical matching cannot tell apart different meanings of the same word, "
                                "nor group synonyms; real topic clustering needs NLP (later phase)."]))
        else:
            findings.append(_f("topic_repetition", "Repeated title keywords", "low", "ok",
                              "No single title keyword appears in >=50% of analyzed videos.", "No action needed.", "low",
                              {"checked_tokens": len(token_counts)}, ["Lexical proxy only."]))

    # ---- 6. Title & description optimization opportunity ----
    no_desc = sum(1 for v in videos if not (v.description or "").strip())
    very_short_titles = sum(1 for r in rows if len(r.title.strip()) < 15)
    ev = {"videos_without_description": no_desc, "titles_under_15_chars": very_short_titles, "analyzed": n}
    if no_desc > n * 0.3 or very_short_titles > n * 0.3:
        findings.append(_f("metadata_quality", "Titles/descriptions could carry more context", "low", "detected",
                           f"{no_desc}/{n} analyzed videos have empty descriptions and {very_short_titles}/{n} have titles under 15 characters.",
                           "Add a 1–2 sentence keyword-rich description and front-load important words in titles.",
                           "medium", ev,
                           ["We cannot measure SEO impact without CTR/search data (unavailable via public API)."]))
    else:
        findings.append(_f("metadata_quality", "Metadata completeness", "low", "ok",
                          "Descriptions and title lengths look adequately populated.", "No action needed.", "medium", ev, []))

    # ---- 7. Shorts vs long-form ----
    fmt_limits = [f"Format comes from YouTube's /shorts/<id> URL check where it succeeded, otherwise "
                  f"from duration <= {cfg.short_max_seconds}s; the Data API itself exposes no Shorts flag.",
                  f"Only videos at least {MIN_AGE_DAYS_FOR_RANKING} days old are compared; newer uploads have "
                  "provisional views/day."]
    # Mature counts: the per-format medians are computed over mature videos only.
    s_n = sum(1 for r in rows if r.mature and r.form == "estimated-shorts")
    l_n = sum(1 for r in rows if r.mature and r.form == "estimated-longform")
    if s_n < FORMAT_SPLIT_MIN_PER_GROUP or l_n < FORMAT_SPLIT_MIN_PER_GROUP:
        findings.append(_f("format_split", "Shorts vs long-form comparison", "low", "inconclusive",
                           f"Need >= {FORMAT_SPLIT_MIN_PER_GROUP} videos per format, each at least "
                           f"{MIN_AGE_DAYS_FOR_RANKING} days old, to compare medians "
                           f"(mature estimated shorts={s_n}, mature estimated long-form={l_n}).",
                           "Publish (or sync) more of each format, then re-audit.", "inconclusive",
                           {"shorts": s_n, "long": l_n,
                            "min_per_group": FORMAT_SPLIT_MIN_PER_GROUP},
                           fmt_limits))
    else:
        s_vpd, l_vpd = perf.shorts_median_vpd, perf.longform_median_vpd
        imbalance = max(s_n, l_n) / max(1, min(s_n, l_n))
        ev = {"shorts_median_vpd": s_vpd, "longform_median_vpd": l_vpd,
              "shorts_count": s_n, "longform_count": l_n,
              "group_size_imbalance": round(imbalance, 1),
              "max_supported_imbalance": FORMAT_SPLIT_MAX_IMBALANCE}
        both = [x for x in (s_vpd, l_vpd) if x and x > 0]
        if len(both) != 2:
            findings.append(_f("format_split", "Shorts vs long-form comparison", "low", "inconclusive",
                               "Duration metadata missing on too many videos.", "Re-sync to refresh durations.",
                               "inconclusive", ev, fmt_limits))
        elif imbalance > FORMAT_SPLIT_MAX_IMBALANCE:
            smaller = "Shorts" if s_n < l_n else "long-form"
            findings.append(_f("format_split", "Shorts vs long-form comparison", "low", "inconclusive",
                               f"The formats are too imbalanced to compare fairly "
                               f"({s_n} estimated Shorts vs {l_n} estimated "
                               f"long-form, a {imbalance:.0f}:1 split). The median of a {smaller}-only "
                               f"{min(s_n, l_n)}-video sample reflects a few "
                               "uploads, not a format trend, so no production-allocation recommendation "
                               "is made.",
                               f"Treat the current numbers as descriptive only. Re-audit once at least "
                               f"{FORMAT_SPLIT_MIN_PER_GROUP} {smaller} uploads exist, or run your own "
                               "controlled tests.", "inconclusive", ev,
                               fmt_limits + ["Imbalanced groups compare different creator behaviours, "
                                             "not formats."]))
        else:
            ratio = max(both) / min(both)
            winner = "Shorts" if s_vpd == max(both) else "Long-form"
            loser = "long-form" if winner == "Shorts" else "Shorts"
            diff_pct = abs(s_vpd - l_vpd) / max(both) * 100.0
            if diff_pct < 15.0:
                findings.append(_f("format_split", "Shorts vs long-form comparison", "info", "ok",
                                   f"Median views/day differ by only {diff_pct:.0f}% between formats "
                                   f"(Shorts {s_vpd:.1f} vs long-form {l_vpd:.1f}); that gap is small and "
                                   "the samples are modest, so no format advantage is claimed.",
                                   "No action needed based on this data.", "medium", ev,
                                   fmt_limits + ["Small cohorts swing wildly; treat cautiously once data grows."]))
            else:
                findings.append(_f("format_split", f"{winner} outperform {loser} per day (directional)",
                                   "low", "detected",
                                   f"Median views/day: Shorts {s_vpd:.1f} vs long-form {l_vpd:.1f} "
                                   f"({ratio:.1f}× apart). This is a directional observation from public "
                                   "totals, not a proven format effect.",
                                   f"Optionally test it: two {winner}-style and two {loser}-style uploads, "
                                   "then compare your own results.", "medium", ev,
                                   fmt_limits + ["Views/day favors Shorts partly due to shelf-life differences; treat as directional."]))

    # ---- 8. Format-mismatch opportunity ----
    if (not small_sample and baseline and baseline > 0
            and s_n >= FORMAT_SPLIT_MIN_PER_GROUP
            and l_n >= FORMAT_SPLIT_MIN_PER_GROUP):
        weaker_is_shorts = (perf.shorts_median_vpd or 0) < (perf.longform_median_vpd or 0)
        weaker_fmt = "Shorts" if weaker_is_shorts else "Long-form"
        stronger = "long-form" if weaker_is_shorts else "Shorts"
        findings.append(_f("format_mismatch", "Possible content-format mismatch", "low", "inconclusive",
                           f"{weaker_fmt} trail {stronger} in views/day. A shift could help, but audience intent "
                           "(search vs feed) cannot be observed from public data, so we do not assert a mismatch.",
                           f"Run your own A/B: two {stronger}-style and two {weaker_fmt}-style uploads, then compare.",
                           "low", {"shorts_median_vpd": perf.shorts_median_vpd, "longform_median_vpd": perf.longform_median_vpd},
                           ["Requires creator-side experiments; public stats alone cannot prove format-audience fit."]))

    # ---- 9. Engagement ----
    if perf.likes_available_on == 0:
        findings.append(_f("engagement", "Engagement ratios unavailable", "low", "inconclusive",
                           "Like counts are hidden on every analyzed video.", "Nothing to fix — some channels hide stats.",
                           "inconclusive", {"likes_available_on": 0},
                           ["Hidden likes/comments make engagement comparison impossible."]))
    else:
        ev = {"like_to_view_ratio": perf.like_to_view_ratio, "comment_to_view_ratio": perf.comment_to_view_ratio,
              "likes_available_on": perf.likes_available_on, "comments_available_on": perf.comments_available_on,
              "videos_with_hidden_stats": perf.hidden_stats_videos}
        lr = perf.like_to_view_ratio
        if lr is not None and lr < 0.01:
            findings.append(_f("engagement", "Low like/view ratio", "medium", "detected",
                               f"Likes are {lr*100:.2f}% of views across videos exposing likes (reference threshold 1%). "
                               "This is a like/view ratio only — not the full engagement rate.",
                               "Strengthen value delivery and clear calls-to-action.", "low", ev,
                               ["No channel-specific historical baseline for likes exists yet; threshold is a general reference.",
                                "Comments/likes can be disabled independently, biasing availability."]))
        else:
            findings.append(_f("engagement", "Engagement ratios (measured)", "info", "fact",
                               f"Like/view ratio: {(lr or 0)*100:.2f}%; comment/view ratio: "
                               f"{(perf.comment_to_view_ratio or 0)*100:.2f}% (over videos exposing these stats).",
                               "No action — measured fact. Note: not the full engagement rate.", "high", ev,
                               ["Computed only over videos whose like/comment counts are public."], kind="fact"))

    # ---- 10. Outliers ----
    if outliers:
        findings.append(_f("outliers", "Unusually high performers detected", "info", "detected",
                           f"{len(outliers)} video(s) exceed the robust views/day fence (Q3 + {OUTLIER_IQR_K}×IQR): "
                           + "; ".join(f"'{o.title[:40]}' ({o.views_per_day:.0f} vpd)" for o in outliers[:3]),
                           "Study packaging/topic of these outliers; consider deliberate follow-ups.", "medium",
                           {"outlier_ids": [o.youtube_video_id for o in outliers]},
                           ["Outlier ≠ reproducible strategy; causality requires controlled tests."]))
    return findings


# --------------------------------------------------------------------- scores
def score_components(findings: list[Finding], perf: PerformanceStats,
                     rows: list[VideoRanking], cfg: AuditConfig) -> list[dict[str, Any]]:
    """All scoreable components. Components with insufficient data are left
    out (or marked excluded) — missing data never costs points."""
    comps: list[dict[str, Any]] = []

    def add(name: str, raw: float | None, note: str) -> None:
        if raw is not None:
            comps.append({"name": name, "weight": WEIGHTS[name], "score": round(raw * 100, 1), "note": note})

    # recency
    if perf.days_since_last_upload is not None:
        d = perf.days_since_last_upload
        raw = 1.0 if d <= 30 else max(0.0, 1.0 - (d - 30) / (cfg.stale_upload_days * 2))
        add("recency", raw, f"{d} days since last upload")
    # consistency
    cons = next((f for f in findings if f.key == "upload_consistency"), None)
    if cons and cons.status != "inconclusive":
        med_gap = cons.evidence.get("median_gap_days")
        outliers_ct = cons.evidence.get("gaps_over_3x_median", 0)
        total_gaps = cons.evidence.get("total_gaps", 1)
        span = cons.evidence.get("observed_span_days") or 0
        note = f"median gap {med_gap}d, {outliers_ct}/{total_gaps} irregular gaps"
        if span < MIN_OBSERVATION_SPAN_DAYS:
            # A short burst window cannot represent a publishing rhythm; scoring
            # consistency from it would silently fabricate coverage. Excluded.
            comps.append({"name": "consistency", "weight": WEIGHTS["consistency"], "score": None,
                          "excluded": True,
                          "note": f"excluded — only {span:.0f} day(s) of uploads observed "
                                  f"(needs >= {MIN_OBSERVATION_SPAN_DAYS})"})
        else:
            raw = 1.0 - min(1.0, outliers_ct / max(1, total_gaps))
            add("consistency", raw, note)
    # recent trend
    tr = next((f for f in findings if f.key == "recent_trend"), None)
    if tr and tr.status != "inconclusive":
        ratio = tr.evidence.get("ratio")
        if ratio is not None:
            raw = min(1.0, max(0.0, ratio / 1.2))  # 120%+ of own history => full marks
            add("recent_trend", raw, f"recent/history views-per-day ratio {ratio:.2f} (same format)")
    # distribution (concentration)
    cc = next((f for f in findings if f.key == "concentration"), None)
    if cc and cc.status != "inconclusive" and cc.evidence.get("top5_share_of_views") is not None:
        share = cc.evidence["top5_share_of_views"]
        raw = max(0.0, min(1.0, 1.0 - max(0.0, share - 0.35) / 0.5))
        add("distribution", raw, f"top-5 view share {share*100:.0f}%")
    # engagement
    if perf.like_to_view_ratio is not None and rows:
        # Soft linear scale: 5% like/view ratio maps to full marks.
        raw = min(1.0, perf.like_to_view_ratio / 0.05)
        add("engagement", raw, f"like/view ratio {perf.like_to_view_ratio*100:.2f}% (soft cap 5%)")
    return comps


def _performance_context(perf: PerformanceStats, rows: list[VideoRanking],
                         subscriber_count: int | None) -> list[dict[str, Any]]:
    """Plain numbers shown next to the performance score. No fair benchmark
    exists in public data, so they are never turned into points."""
    ctx = []
    if perf.shorts_median_vpd is not None:
        ctx.append({"name": "shorts_median_views_per_day", "value": round(perf.shorts_median_vpd, 2),
                    "note": f"median views/day of Shorts at least {MIN_AGE_DAYS_FOR_RANKING} days old"})
    if perf.longform_median_vpd is not None:
        ctx.append({"name": "longform_median_views_per_day", "value": round(perf.longform_median_vpd, 2),
                    "note": f"median views/day of long-form videos at least {MIN_AGE_DAYS_FOR_RANKING} days old"})
    mature_views = [r.view_count for r in rows if r.mature]
    if mature_views and subscriber_count:
        ctx.append({"name": "median_views_per_subscriber",
                    "value": round(median(mature_views) / subscriber_count, 4),
                    "note": "median views of mature videos ÷ subscribers (subscriber count is rounded by YouTube)"})
    return ctx


def _build_score(kind: str, comps: list[dict[str, Any]],
                 context: list[dict[str, Any]] | None = None) -> HealthScore:
    names = SCORE_GROUPS[kind]
    weights = {k: WEIGHTS[k] for k in names}
    group = [dict(c) for c in comps if c["name"] in names]
    caveats = [
        "Score excludes components lacking data instead of penalizing them.",
        "Weights: " + ", ".join(f"{k}={v}" for k, v in weights.items()),
        "Based only on public YouTube Data API v3 fields; CTR, impression counts and audience retention are private Analytics metrics that do not exist in public data.",
        "Thresholds (e.g. 5% like/view cap, 30-day recency window, 35%/60% concentration bands) are internal heuristics — NOT official YouTube standards.",
        "The score is a relative self-assessment against the channel's own history plus soft fixed caps; it is not comparable across different channels.",
        "No statistical significance testing is performed; small samples reduce which components can be scored at all.",
    ]
    method = "score = Σ(component × weight) / Σ(weights present); each component ∈ [0,100]"
    scorable = [c for c in group if c.get("score") is not None]
    min_needed = SCORE_MIN_COMPONENTS[kind]
    if len(scorable) < min_needed:
        reason = ("No component had enough data to score." if not scorable else
                  f"Needs at least {min_needed} scored components; only {len(scorable)} had enough data.")
        return HealthScore(None, "insufficient-data", group, method, caveats + [reason], kind, context or [])

    wsum = sum(c["weight"] for c in scorable)
    score = sum(c["score"] * c["weight"] for c in scorable) / wsum
    band = "healthy" if score >= 75 else "watch" if score >= 55 else "at-risk"

    # Coverage: never present partial data as a comprehensive assessment.
    total_w = sum(weights.values())
    coverage_pct = round(wsum / total_w * 100) if total_w else 0
    if coverage_pct < 50:
        caveats.insert(0, f"PROVISIONAL SCORE: only {coverage_pct}% of this score's component weight "
                          "could be computed from available data. Treat this as a limited snapshot.")
    elif coverage_pct < 80:
        caveats.insert(0, f"PARTIAL COVERAGE: components worth {100 - coverage_pct}% of this score's "
                          "weighting were excluded for lack of data; see the component list.")
    group.append({"name": "coverage", "weight": None, "score": coverage_pct,
                  "note": f"{round(wsum, 2)} of {round(total_w, 2)} weight points had enough data to score"})
    return HealthScore(round(score), band, group, method, caveats, kind, context or [])


def compute_scores(findings: list[Finding], perf: PerformanceStats, rows: list[VideoRanking],
                   cfg: AuditConfig, subscriber_count: int | None = None) -> tuple[HealthScore, HealthScore]:
    """(activity, performance) — see SCORE_GROUPS."""
    comps = score_components(findings, perf, rows, cfg)
    return (_build_score("activity", comps),
            _build_score("performance", comps, _performance_context(perf, rows, subscriber_count)))


# --------------------------------------------------------------------- analysis set
def select_analysis_set(candidates: list[Video], cfg: AuditConfig, now: datetime,
                        channel_video_count: int | None = None) -> tuple[list[Video], dict[str, Any]]:
    """Uploads from the last ``window_days``; if fewer than ``min_videos`` fall
    in the window, the latest ``min_videos`` instead; never more than
    ``max_videos``. Returns the chosen videos and their actual date coverage."""
    cutoff = now - timedelta(days=cfg.window_days)
    oldest_dt = datetime.min.replace(tzinfo=timezone.utc)
    ordered = sorted(candidates, key=lambda v: _aware(v.published_at) or oldest_dt,
                     reverse=True)[:cfg.max_videos]
    in_window = [v for v in ordered if v.published_at and _aware(v.published_at) >= cutoff]
    if len(in_window) >= cfg.min_videos:
        chosen, mode = in_window, "window"
    else:
        chosen, mode = ordered[:cfg.min_videos], "min_videos_fallback"

    pubs = [_aware(v.published_at) for v in chosen if v.published_at]
    oldest, newest = (min(pubs), max(pubs)) if pubs else (None, None)
    covered_days = max(1, round(_gap_days(oldest, now))) if oldest else None
    cap_reached = len(chosen) >= cfg.max_videos and oldest is not None and oldest > cutoff
    # Stored uploads stop short of the window start while the channel has more
    # uploads than we hold: the window is not fully covered (stale/partial sync).
    has_older = any(v.published_at and _aware(v.published_at) < cutoff for v in candidates)
    window_incomplete = bool(
        oldest is not None and oldest > cutoff and not has_older
        and channel_video_count is not None and channel_video_count > len(candidates)
    )
    return chosen, {
        "mode": mode,
        "window_days": cfg.window_days,
        "window_start": cutoff.isoformat(),
        "min_videos": cfg.min_videos,
        "max_videos": cfg.max_videos,
        "videos_analyzed": len(chosen),
        "oldest_analyzed": oldest.isoformat() if oldest else None,
        "newest_analyzed": newest.isoformat() if newest else None,
        "covered_days": covered_days,
        "cap_reached": cap_reached,
        "window_incomplete": window_incomplete,
    }


def load_analysis_set(db: Session, channel: Channel, cfg: AuditConfig | None = None,
                      now: datetime | None = None) -> tuple[list[Video], dict[str, Any]]:
    """The one video set every metric (Audit AND Overview) is computed from."""
    cfg = cfg or AuditConfig()
    now = now or _utcnow()
    candidates = list(
        db.execute(
            select(Video)
            .where(Video.channel_id == channel.id, analyzable_video_filter())
            .order_by(Video.published_at.desc())
            .limit(cfg.max_videos)
        ).scalars().all()
    )
    return select_analysis_set(candidates, cfg, now, channel.video_count)


def _coverage_notes(cov: dict[str, Any], cfg: AuditConfig, channel: Channel) -> list[str]:
    n = cov["videos_analyzed"]
    total = channel.video_count or "unknown"
    if not n:
        return ["No analyzable uploads stored yet — sync the channel first."]
    span = (f"{cov['oldest_analyzed'][:10]} – {cov['newest_analyzed'][:10]}"
            if cov["oldest_analyzed"] else "undated")
    if cov["cap_reached"]:
        return [f"{cfg.max_videos}-video cap reached: the analysis covers only the last "
                f"{cov['covered_days']} of {cfg.window_days} days ({span}), "
                f"{n} of {total} uploads on the channel."]
    if cov["window_incomplete"]:
        return [f"Stored uploads cover only the last {cov['covered_days']} of {cfg.window_days} days "
                f"({span}; {n} of {total} on the channel). Sync the channel to fetch the full window."]
    if cov["mode"] == "min_videos_fallback":
        return [f"Fewer than {cfg.min_videos} uploads in the last {cfg.window_days} days, so the "
                f"latest {n} uploads are analyzed instead ({span}; {total} on the channel)."]
    return [f"Analysis window: {n} uploads from the last {cfg.window_days} days ({span}), "
            f"of {total} on the channel."]


# --------------------------------------------------------------------- entry point
def run_channel_audit(db: Session, channel: Channel, cfg: AuditConfig | None = None,
                      now: datetime | None = None) -> ChannelAudit:
    cfg = cfg or AuditConfig()
    now = now or _utcnow()

    videos, coverage = load_analysis_set(db, channel, cfg, now)
    perf = compute_performance(videos, cfg, now)
    rows, baseline = rank_videos(videos, cfg, now)
    outliers = detect_outliers(rows)
    strong, weak = group_topics(rows)
    findings = build_findings(videos, rows, perf, baseline, outliers, cfg, now)
    activity_score, performance_score = compute_scores(findings, perf, rows, cfg,
                                                       channel.subscriber_count)

    notes: list[str] = []
    insufficient = perf.videos_analyzed < MIN_VIDEOS_FOR_FINDINGS
    if insufficient:
        notes.append(f"Fewer than {MIN_VIDEOS_FOR_FINDINGS} videos analyzed — most findings are inconclusive.")
    if perf.format_unclassified:
        notes.append(f"{perf.format_unclassified} video(s) lack duration metadata and were left unclassified.")
    if perf.hidden_stats_videos:
        notes.append(f"{perf.hidden_stats_videos} video(s) hide like AND comment counts.")
    notes.extend(_coverage_notes(coverage, cfg, channel))
    notes.append("Format labels ('estimated-shorts'/'estimated-longform') come from YouTube's /shorts/<id> "
                 f"URL check, falling back to duration <= {SHORT_MAX_SECONDS}s when the check was unavailable; "
                 "YouTube's Data API does not expose an official Shorts flag.")
    notes.append("Topic groups are repeated title WORDS (lexical), not semantic topics.")

    # ---- data freshness / staleness (provenance requirement) ----
    sync_times = [r.last_synced_at for r in rows if r.last_synced_at]
    last_video_sync = max(sync_times) if sync_times else _aware(channel.last_synced_at)
    stats_age_days = (now - last_video_sync).days if last_video_sync else None
    freshness: dict[str, Any] = {
        "last_video_stats_sync": last_video_sync.isoformat() if last_video_sync else None,
        "stats_age_days": stats_age_days,
        "sample_size_analyzed": perf.videos_analyzed,
        "stale_data": bool(stats_age_days is not None and stats_age_days > 7),
    }
    if freshness["stale_data"]:
        notes.append(f"Video statistics are {stats_age_days} days old (last sync {last_video_sync:%Y-%m-%d}); "
                     "re-sync before relying on recent-performance findings.")
    elif last_video_sync is None:
        notes.append("No recorded sync timestamp; video statistics may be outdated.")

    ranked_desc = sorted(rows, key=lambda r: -r.views_per_day)
    # Top performers are MATURE videos only. A video published hours ago has its
    # age floored at 1 day, so its views/day is provisional early traction that
    # can dwarf settled performance; ranking it as a "top performer" would be
    # misleading. Fresh uploads stay visible in fresh_videos_provisional instead.
    mature_desc = [r for r in ranked_desc if r.mature]
    fresh_rows = [r for r in ranked_desc if not r.mature]
    if fresh_rows:
        notes.append(
            f"{len(fresh_rows)} upload(s) under {MIN_AGE_DAYS_FOR_RANKING} days old (or undated) were "
            "excluded from top/underperformer/outlier rankings and shown separately as provisional "
            "early traction — their views/day is not yet a settled performance rate."
        )

    return ChannelAudit(
        channel_id=channel.id,
        youtube_channel_id=channel.youtube_channel_id,
        title=channel.title,
        audited_at=now,
        computed_from="Stored public video records (YouTube Data API v3); zero quota spent by this endpoint.",
        config=asdict(cfg),
        formulas=dict(FORMULAS),
        performance=perf,
        activity_score=activity_score,
        performance_score=performance_score,
        findings=findings,
        top_performers=mature_desc[:10],
        # Underperformance requires >=7 days of observation AND a computable
        # baseline. Fresh uploads are excluded rather than labelled failures.
        underperformers=[
            r for r in mature_desc
            if r.vs_baseline_pct is not None and r.vs_baseline_pct < -40
        ][-10:],
        outliers=outliers,
        fresh_videos_provisional=fresh_rows[:10],
        strong_topics=strong,
        weak_topics=weak,
        insufficient_data=insufficient,
        notes=notes,
        provenance=dict(PROVENANCE_LABELS),
        unavailable_metrics=list(UNAVAILABLE_METRICS),
        confidence_rules=dict(CONFIDENCE_RULES),
        data_freshness=freshness,
        coverage=coverage,
    )
