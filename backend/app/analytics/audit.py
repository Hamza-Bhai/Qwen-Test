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
  uploads_per_month    = analyzed_video_count / months_spanned
                         months_spanned = (last_pub - first_pub).days / 30.44, min 1
  days_since_last      = (now - most recent published_at).days
  outlier              |vpd| > median(vpd) + 3 * MAD-ish spread (robust z via IQR fence)
  concentration        top-5 videos' share of total views of analyzed set
  format split         short := duration_seconds <= SHORT_MAX_SECONDS (300s default)

Health score (0-100) is computed ONLY from components whose data exists;
missing components are excluded and their weight renormalized — a channel is
never penalized for unavailable metrics. Weights are listed in WEIGHTS.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from statistics import mean, median
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Channel, Video

# --------------------------------------------------------------------- config
DEFAULT_ANALYZE_LIMIT = 50          # latest N videos by publish date
SHORT_MAX_SECONDS = 300             # <= 5 min treated as short-form heuristic
MIN_VIDEOS_FOR_FINDINGS = 5         # below this: findings are inconclusive
RECENT_WINDOW_DAYS = 90             # "recent performance" window
STALE_UPLOAD_DAYS = 60              # gap threshold for "long publishing pause"
CONCENTRATION_HIGH = 0.60           # top-5 share above which dependence flagged
OUTLIER_IQR_K = 3.0                 # Tukey-fence multiplier for vpd outliers
TOPIC_TOKEN_MIN_LEN = 4             # ignore tiny words when grouping topics
STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "your", "mine", "how",
    "why", "what", "when", "from", "into", "about", "video", "shorts",
    "official", "trailer", "full", "new", "you", "are", "was", "will",
}


@dataclass(frozen=True)
class AuditConfig:
    analyze_limit: int = DEFAULT_ANALYZE_LIMIT
    short_max_seconds: int = SHORT_MAX_SECONDS
    recent_window_days: int = RECENT_WINDOW_DAYS
    stale_upload_days: int = STALE_UPLOAD_DAYS
    concentration_high: float = CONCENTRATION_HIGH


FORMULAS: dict[str, str] = {
    "age_days": "max(1, floor((now_utc - published_at) / 1 day))",
    "views_per_day": "view_count / age_days  (age-normalized comparison metric)",
    "avg_views": "mean(view_count) over the analyzed video set",
    "median_views": "median(view_count) over the analyzed video set",
    "total_views": "sum(view_count) over the analyzed video set",
    "like_to_view_ratio": "sum(like_count) / sum(view_count) restricted to videos that expose likes (NOT the full engagement rate)",
    "comment_to_view_ratio": "sum(comment_count) / sum(view_count) restricted to videos that expose comments",
    "uploads_per_month": "count(analyzed) / max(1, span_months); span_months = (last_publish - first_publish).days / 30.44",
    "days_since_last_upload": "floor((now_utc - max(published_at)) / 1 day)",
    "outlier": "views_per_day beyond Q3 + 3*IQR (robust Tukey fence; no assumption of normality)",
    "top5_share_of_views": "sum(views of top-5 videos by view_count) / total_views",
    "short_form": f"duration_seconds <= {SHORT_MAX_SECONDS} (public-metadata heuristic, not an official flag)",
    "recent_vs_history": "median(views_per_day of last 90 days) / median(views_per_day of all analyzed)",
}

WEIGHTS: dict[str, float] = {
    "recency": 0.25,           # publishing cadence / days since last upload
    "consistency": 0.20,       # upload-gap regularity
    "recent_trend": 0.25,      # recent views/day vs channel's own history
    "distribution": 0.15,      # healthy spread (not one-hit-wonder dependent)
    "engagement": 0.15,        # like+comment ratios vs channel median
}


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
    form: str                      # "shorts" | "long" | "unknown"


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
    health_score: HealthScore
    findings: list[Finding]
    top_performers: list[VideoRanking]
    underperformers: list[VideoRanking]
    outliers: list[VideoRanking]
    strong_topics: list[TopicGroup]
    weak_topics: list[TopicGroup]
    insufficient_data: bool
    notes: list[str]


# --------------------------------------------------------------------- helpers
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _safe_div(num: float, den: float) -> float | None:
    """Zero-denominator guard: returns None, never raises."""
    if den == 0:
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


def classify_format(duration_seconds: int | None, short_max: int) -> str:
    if duration_seconds is None:
        return "unknown"
    return "shorts" if duration_seconds <= short_max else "long"


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
    uploads_per_month = None
    if len(pubs) >= 2:
        span_days = max((pubs[-1] - pubs[0]).days, 1)
        months = max(span_days / 30.44, 1.0)
        uploads_per_month = len(pubs) / months
    elif len(pubs) == 1:
        uploads_per_month = None  # single upload: cadence unknowable

    durs = [v.duration_seconds for v in videos if v.duration_seconds is not None]

    shorts_ids, long_ids, unknown_ids = [], [], []
    shorts_views, long_views, shorts_vpd, long_vpd = [], [], [], []
    for v in videos:
        form = classify_format(v.duration_seconds, cfg.short_max_seconds)
        {"shorts": shorts_ids, "long": long_ids, "unknown": unknown_ids}[form].append(v)
        age = max(1, (now - (_aware(v.published_at) or now)).days)
        vpd = (v.view_count or 0) / age
        if form == "shorts":
            shorts_views.append(v.view_count or 0)
            shorts_vpd.append(vpd)
        elif form == "long":
            long_views.append(v.view_count or 0)
            long_vpd.append(vpd)

    # Distribution buckets by views/day vs the channel's own median views/day.
    ages = [max(1, (now - (_aware(v.published_at) or now)).days) for v in videos]
    vpds_all = [
        ((v.view_count or 0) / a) for v, a in zip(videos, ages)
    ]
    med_vpd = median(vpds_all) if vpds_all else None
    buckets = []
    if med_vpd and med_vpd > 0:
        ranges = [(0.0, 0.5), (0.5, 0.8), (0.8, 1.2), (1.2, 2.0), (2.0, float("inf"))]
        labels = ["<50%", "50–80%", "80–120%", "120–200%", ">200%"]
        for (lo, hi), label in zip(ranges, labels):
            count = sum(1 for x in vpds_all if lo * med_vpd <= x < hi * med_vpd)
            buckets.append({"label": label, "videos": count})
    else:
        buckets = [{"label": "insufficient data", "videos": len(videos)}]

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
        age = max(1, (now - pub).days) if pub else max(1, (now - now).days or 1)
        vpd = (v.view_count or 0) / age
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
                form=classify_format(v.duration_seconds, cfg.short_max_seconds),
            )
        )
    vpds = sorted(r.views_per_day for r in rows)
    base = median(vpds) if vpds else None
    for r in rows:
        if base and base > 0:
            r.vs_baseline_pct = round((r.views_per_day / base - 1.0) * 100.0, 1)
    return rows, base


def detect_outliers(rows: list[VideoRanking]) -> list[VideoRanking]:
    if len(rows) < 8:  # fences meaningless on tiny samples
        return []
    vpds = sorted(r.views_per_day for r in rows)
    q1, q3 = _quantiles(vpds)
    iqr = q3 - q1
    if iqr <= 0:
        return []
    upper = q3 + OUTLIER_IQR_K * iqr
    return sorted((r for r in rows if r.views_per_day > upper), key=lambda r: -r.views_per_day)[:5]


def group_topics(rows: list[VideoRanking], baseline: float | None) -> tuple[list[TopicGroup], list[TopicGroup]]:
    groups: dict[str, list[VideoRanking]] = {}
    for r in rows:
        for tok in set(_topic_tokens(r.title)):
            groups.setdefault(tok, []).append(r)
    scored: list[TopicGroup] = []
    for topic, vids in groups.items():
        if len(vids) < 3:  # need repetition before calling something a "topic"
            continue
        med = median(v.views_per_day for v in vids)
        pct = round((med / baseline - 1.0) * 100.0, 1) if baseline and baseline > 0 else None
        scored.append(
            TopicGroup(
                topic=topic,
                videos=len(vids),
                median_views_per_day=round(med, 2),
                vs_channel_baseline_pct=pct,
                example_titles=[v.title for v in sorted(vids, key=lambda x: -x.views_per_day)[:3]],
            )
        )
    strong = sorted((g for g in scored if g.videos >= 3), key=lambda g: -(g.median_views_per_day))[:5]
    weak = sorted((g for g in scored if g.videos >= 3), key=lambda g: g.median_views_per_day)[:5]
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
        findings.append(_f(
            "upload_cadence", "Upload cadence (measured)", "info", "fact",
            f"The channel published {perf.uploads_per_month:.2f} videos/month across the analyzed "
            f"window; the newest analyzed upload is {perf.days_since_last_upload} day(s) old."
            if perf.uploads_per_month is not None else
            f"Newest analyzed upload is {perf.days_since_last_upload} day(s) old.",
            "No action — measured fact.", "high",
            {"uploads_per_month": perf.uploads_per_month, "days_since_last_upload": perf.days_since_last_upload,
             "first_published_at": perf.first_published_at.isoformat() if perf.first_published_at else None,
             "last_published_at": perf.last_published_at.isoformat() if perf.last_published_at else None},
            ["Cadence is computed over the analyzed set only (latest "
             f"{cfg.analyze_limit} synced videos), not the channel's whole history."],
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
                           ["The latest 50 synced videos are analyzed; videos newer than the sync snapshot would not appear here."]))
    else:
        findings.append(_f("upload_pause", "Publishing pause", "low", "ok",
                           f"Last upload was {perf.days_since_last_upload} days ago (below the {cfg.stale_upload_days}-day threshold).",
                           "Keep current cadence.", "high",
                           {"days_since_last_upload": perf.days_since_last_upload}, []))

    # ---- 2. Upload consistency (gap variance) ----
    pubs = sorted(_aware(v.published_at) for v in videos if v.published_at)
    if len(pubs) < 6:
        findings.append(_f("upload_consistency", "Upload consistency", "low", "inconclusive",
                           f"Only {len(pubs)} dated uploads available; gap statistics need at least 6.",
                           "Sync more videos and re-run.", "inconclusive", {"dated_uploads": len(pubs)}, limits_small))
    else:
        gaps = [(b - a).days for a, b in zip(pubs, pubs[1:])]
        med_gap = median(gaps)
        big_gaps = sum(1 for g in gaps if med_gap > 0 and g > med_gap * 3)
        if med_gap <= 0:
            findings.append(_f("upload_consistency", "Upload consistency", "low", "inconclusive",
                               "All analyzed uploads share the same publish date; gaps are degenerate.",
                               "Check the sync snapshot.", "inconclusive", {"gaps": gaps}, []))
        elif big_gaps >= max(2, len(gaps) // 4):
            findings.append(_f("upload_consistency", "Inconsistent upload schedule", "medium", "detected",
                               f"{big_gaps} of {len(gaps)} upload gaps exceed 3× the median gap ({med_gap:.0f} days).",
                               "Batch-produce content or use a content calendar to smooth gaps.", "medium",
                               {"median_gap_days": med_gap, "gaps_over_3x_median": big_gaps, "total_gaps": len(gaps)},
                               ["Gap analysis uses only the analyzed window."]))
        else:
            findings.append(_f("upload_consistency", "Upload consistency", "low", "ok",
                               f"Median gap between uploads is {med_gap:.0f} days with few outliers.",
                               "Maintain the rhythm.", "medium",
                               {"median_gap_days": med_gap, "total_gaps": len(gaps)}, []))

    # ---- 3. Recent performance vs channel's own history ----
    if small_sample or baseline is None or baseline <= 0:
        findings.append(_f("recent_trend", "Recent performance trend", "low", "inconclusive",
                           "Insufficient data for an age-normalized recent-vs-history comparison.",
                           "Analyze more videos first.", "inconclusive", {}, limits_small))
    else:
        cutoff = now - timedelta(days=cfg.recent_window_days)
        recent = [r for r in rows if r.published_at and r.published_at >= cutoff]
        older = [r for r in rows if r.published_at and r.published_at < cutoff]
        if len(recent) < 2:
            findings.append(_f("recent_trend", "Recent performance trend", "low", "inconclusive",
                               f"Fewer than 2 uploads in the last {cfg.recent_window_days} days — the recent cohort is too small.",
                               "Compare again after more uploads accumulate.", "inconclusive",
                               {"recent_videos": len(recent)}, ["Small cohorts swing wildly; treat cautiously once data grows."]))
        else:
            rec_med = median(r.views_per_day for r in recent)
            hist_base = median([r.views_per_day for r in (older if len(older) >= 3 else rows)])
            ratio = _safe_div(rec_med, hist_base)
            ev = {"recent_median_vpd": round(rec_med, 2), "baseline_median_vpd": round(hist_base, 2),
                  "recent_videos": len(recent), "window_days": cfg.recent_window_days}
            if ratio is not None and ratio < 0.6:
                findings.append(_f("recent_trend", "Weak recent performance vs own history", "high", "detected",
                                   f"Videos from the last {cfg.recent_window_days} days earn {ratio*100:.0f}% of the channel's "
                                   f"own historical median views/day ({rec_med:.1f} vs {hist_base:.1f}). Baseline: channel's own videos.",
                                   "Review recent titles/topics/thumbnails against the channel's best-performing patterns.",
                                   "medium", ev,
                                   ["Views/day keeps falling as videos age slowly; very fresh uploads (<1 week) can read low.",
                                    "Public data cannot show whether the drop is demand-side (impression volume) or content-side."]))
            elif ratio is not None and ratio > 1.5:
                findings.append(_f("recent_trend", "Recent uploads outperform history", "info", "detected",
                                   f"Recent videos average {ratio*100:.0f}% of the historical views/day baseline.",
                                   "Identify what changed and repeat it deliberately.", "medium", ev, []))
            else:
                findings.append(_f("recent_trend", "Recent performance stable", "low", "ok",
                                   f"Recent views/day sits within ±40% of the channel's own baseline ({(ratio or 0)*100:.0f}%).",
                                   "No action needed.", "medium", ev, []))

    # ---- 4. Dependence on a few popular videos ----
    if n < 10:
        findings.append(_f("concentration", "Dependence on top videos", "low", "inconclusive",
                           f"Need >=10 videos to measure view concentration meaningfully (have {n}).",
                           "Analyze more videos.", "inconclusive", {}, limits_small))
    else:
        top5 = sorted((r.view_count for r in rows), reverse=True)[:5]
        share = _safe_div(sum(top5), perf.total_views)
        ev = {"top5_share_of_views": round(share, 3) if share is not None else None,
              "total_views_analyzed": perf.total_views}
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

    # ---- 5. Repetitive topics ----
    if small_sample:
        findings.append(_f("topic_repetition", "Topic repetition", "low", "inconclusive",
                           "Too few videos to detect recurring topics.", "Analyze more videos.", "inconclusive", {}, limits_small))
    else:
        token_counts: dict[str, int] = {}
        for r in rows:
            for tok in set(_topic_tokens(r.title)):
                token_counts[tok] = token_counts.get(tok, 0) + 1
        dominant = sorted(token_counts.items(), key=lambda kv: -kv[1])[:3]
        coverage = [(t, c, round(c / n, 2)) for t, c in dominant if n and c / n >= 0.5]
        if coverage:
            findings.append(_f("topic_repetition", "Repetitive topics in titles", "low", "detected",
                               "One word appears in half or more of analyzed titles: "
                               + ", ".join(f"'{t}' ({c}/{n} videos)" for t, c, _ in coverage)
                               + ". This is a lexical signal, not a semantic-cluster analysis.",
                               "Consider adjacent angles or formats to broaden topical reach.", "low",
                               {"dominant_tokens": coverage},
                               ["Title keyword overlap is a crude proxy; real topical similarity needs deeper NLP (later phase)."]))
        else:
            findings.append(_f("topic_repetition", "Topic repetition", "low", "ok",
                              "No single title token dominates >=50% of analyzed videos.", "No action needed.", "low",
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
    if perf.shorts_count < 3 or perf.longform_count < 3:
        findings.append(_f("format_split", "Shorts vs long-form comparison", "low", "inconclusive",
                           f"Need >=3 videos per format (shorts={perf.shorts_count}, long-form={perf.longform_count}).",
                           "Publish (or sync) more of each format, then re-audit.", "inconclusive",
                           {"shorts": perf.shorts_count, "long": perf.longform_count},
                           [f"Format is inferred from duration <= {cfg.short_max_seconds}s; YouTube's own Shorts flag is not exposed publicly."]))
    else:
        s_vpd, l_vpd = perf.shorts_median_vpd, perf.longform_median_vpd
        ev = {"shorts_median_vpd": s_vpd, "longform_median_vpd": l_vpd,
              "shorts_count": perf.shorts_count, "longform_count": perf.longform_count}
        both = [x for x in (s_vpd, l_vpd) if x and x > 0]
        if len(both) == 2:
            ratio = max(both) / min(both)
            winner = "Shorts" if s_vpd == max(both) else "Long-form"
            loser = "long-form" if winner == "Shorts" else "Shorts"
            findings.append(_f("format_split", f"{winner} outperform {loser} per day", "low", "detected",
                               f"Median views/day: Shorts {s_vpd:.1f} vs long-form {l_vpd:.1f} ({ratio:.1f}× apart).",
                               f"Allocate production effort proportionally; test whether {loser} ideas can be reframed.",
                               "medium", ev,
                               ["Views/day favors Shorts partly due to shelf-life differences; treat as directional."]))
        else:
            findings.append(_f("format_split", "Shorts vs long-form comparison", "low", "inconclusive",
                               "Duration metadata missing on too many videos.", "Re-sync to refresh durations.", "inconclusive", ev, []))

    # ---- 8. Format-mismatch opportunity ----
    if not small_sample and baseline and baseline > 0 and perf.shorts_count >= 3 and perf.longform_count >= 3:
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


# --------------------------------------------------------------------- health score
def compute_health_score(findings: list[Finding], perf: PerformanceStats, baseline: float | None,
                         rows: list[VideoRanking], now: datetime, cfg: AuditConfig) -> HealthScore:
    """Transparent weighted score. Components with insufficient data are
    EXCLUDED and weights renormalized — missing data never costs points."""
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
        raw = 1.0 - min(1.0, outliers_ct / max(1, total_gaps))
        add("consistency", raw, f"median gap {med_gap}d, {outliers_ct}/{total_gaps} irregular gaps")
    # recent trend
    tr = next((f for f in findings if f.key == "recent_trend"), None)
    if tr and tr.status != "inconclusive" and tr.evidence.get("recent_median_vpd") is not None:
        ratio = tr.evidence["recent_median_vpd"] / tr.evidence["baseline_median_vpd"] if tr.evidence.get("baseline_median_vpd") else None
        if ratio is not None:
            raw = min(1.0, max(0.0, ratio / 1.2))  # 120%+ of baseline => full marks
            add("recent_trend", raw, f"recent/history views-per-day ratio {ratio:.2f}")
    # distribution (concentration)
    cc = next((f for f in findings if f.key == "concentration"), None)
    if cc and cc.status != "inconclusive" and cc.evidence.get("top5_share_of_views") is not None:
        share = cc.evidence["top5_share_of_views"]
        raw = max(0.0, min(1.0, 1.0 - max(0.0, share - 0.35) / 0.5))
        add("distribution", raw, f"top-5 view share {share*100:.0f}%")
    # engagement (relative to channel's own videos — avoids arbitrary global bar)
    if perf.like_to_view_ratio is not None and rows:
        # Soft linear scale: 5% like/view ratio maps to full marks.
        raw = min(1.0, perf.like_to_view_ratio / 0.05)
        add("engagement", raw, f"like/view ratio {perf.like_to_view_ratio*100:.2f}% (soft cap 5%)")

    caveats = [
        "Score excludes components lacking data instead of penalizing them.",
        "Weights: " + ", ".join(f"{k}={v}" for k, v in WEIGHTS.items()),
        "Based only on public YouTube Data API v3 fields; CTR, impression counts and audience retention are private Analytics metrics that do not exist in public data.",
    ]
    if not comps:
        return HealthScore(None, "insufficient-data", [], "weighted mean of available components (renormalized weights)",
                           caveats + ["No component had enough data to score."])
    wsum = sum(c["weight"] for c in comps)
    score = sum(c["score"] * c["weight"] for c in comps) / wsum
    band = "healthy" if score >= 75 else "watch" if score >= 55 else "at-risk"
    return HealthScore(round(score), band, comps,
                       "score = Σ(component × weight) / Σ(weights present); each component ∈ [0,100]",
                       caveats)


# --------------------------------------------------------------------- entry point
def run_channel_audit(db: Session, channel: Channel, cfg: AuditConfig | None = None,
                      now: datetime | None = None) -> ChannelAudit:
    cfg = cfg or AuditConfig()
    now = now or _utcnow()

    videos = list(
        db.execute(
            select(Video)
            .where(Video.channel_id == channel.id)
            .order_by(Video.published_at.desc())
            .limit(cfg.analyze_limit)
        ).scalars().all()
    )
    # Videos without publish dates sort last but still count toward totals.
    perf = compute_performance(videos, cfg, now)
    rows, baseline = rank_videos(videos, cfg, now)
    outliers = detect_outliers(rows)
    strong, weak = group_topics(rows, baseline)
    findings = build_findings(videos, rows, perf, baseline, outliers, cfg, now)
    score = compute_health_score(findings, perf, baseline, rows, now, cfg)

    notes: list[str] = []
    insufficient = perf.videos_analyzed < MIN_VIDEOS_FOR_FINDINGS
    if insufficient:
        notes.append(f"Fewer than {MIN_VIDEOS_FOR_FINDINGS} videos analyzed — most findings are inconclusive.")
    if perf.format_unclassified:
        notes.append(f"{perf.format_unclassified} video(s) lack duration metadata and were left unclassified.")
    if perf.hidden_stats_videos:
        notes.append(f"{perf.hidden_stats_videos} video(s) hide like AND comment counts.")
    notes.append("Analysis window: latest "
                 f"{min(cfg.analyze_limit, perf.videos_analyzed)} synced videos (of {channel.video_count or 'unknown'} total on channel).")

    ranked_desc = sorted(rows, key=lambda r: -r.views_per_day)
    return ChannelAudit(
        channel_id=channel.id,
        youtube_channel_id=channel.youtube_channel_id,
        title=channel.title,
        audited_at=now,
        computed_from="Stored public video records (YouTube Data API v3); zero quota spent by this endpoint.",
        config=asdict(cfg),
        formulas=dict(FORMULAS),
        performance=perf,
        health_score=score,
        findings=findings,
        top_performers=ranked_desc[:10],
        underperformers=[r for r in ranked_desc if r.vs_baseline_pct is not None and r.vs_baseline_pct < -40][-10:],
        outliers=outliers,
        strong_topics=strong,
        weak_topics=weak,
        insufficient_data=insufficient,
        notes=notes,
    )
