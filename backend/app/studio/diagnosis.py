"""Own-channel diagnosis and progress (pure functions, no DB).

Every rule returns one ``Finding`` with its evidence, a confidence and, when
something should change, an action. Thin data gives ``"inconclusive"`` with
what is missing — never a guess. CTR values are percentages (0-100).
"""

import calendar
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import Literal

from app.analytics.audit import _topic_tokens

# --- thresholds ---------------------------------------------------------------------------
WINDOW = 28
LOW_CTR_MIN_IMPRESSIONS = 500
LOW_CTR_MIN_VIDEOS = 5
LOW_CTR_RATIO = 0.7
CTR_TREND_MIN_IMPRESSIONS = 2000
CTR_TREND_CHANGE = 15.0
DROPOFF_MIN_VIDEOS = 3
DROPOFF_RATIO = 0.8
FORMAT_MIN_VIDEOS = 3
TRAFFIC_MIN_VIEWS = 500
TRAFFIC_DEPENDENCE_SHARE = 60.0
TRAFFIC_SHIFT_POINTS = 10.0
SUBS_MIN_VIEWS = 1000
SUBS_VIDEO_MIN_VIEWS = 300
CADENCE_WEEKS = 26
CADENCE_MIN_WEEKS = 4
MATURE_DAYS = 7
KEYWORD_MIN_VIDEOS = 10
KEYWORD_MIN_REPEATS = 3
KEYWORD_BEAT = 1.5
KEYWORD_LAG = 0.67
TIMING_MIN_VIDEOS = 20
TIMING_MIN_GROUP = 3
TIMING_GAP = 1.5
NO_TIMING_DATA = "No reliable data on the best publish day yet."


@dataclass
class DayRow:
    day: date
    views: int
    watch_minutes: int
    avg_view_pct: float
    subs_gained: int
    subs_lost: int


@dataclass
class VideoRow:
    video_id: str
    title: str
    published_at: datetime | None
    is_short: bool | None
    views: int
    avg_view_pct: float | None
    subs_gained: int


@dataclass
class TrafficRow:
    day: date
    source: str
    views: int


@dataclass
class ReachRow:
    day: date
    video_id: str
    impressions: int
    clicks: float


Status = Literal["issue", "good", "info", "inconclusive"]


@dataclass
class Finding:
    code: str
    title: str
    status: Status
    confidence: Literal["high", "medium", "low"]
    summary: str
    evidence: dict = field(default_factory=dict)
    action: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _inconclusive(code: str, title: str, summary: str, **evidence) -> Finding:
    return Finding(code, title, "inconclusive", "low", summary, evidence)


def _confidence(have: int, need: int) -> Literal["high", "medium"]:
    return "high" if have >= 2 * need else "medium"


def _window(today: date, n: int, offset: int = 0) -> tuple[date, date]:
    """Inclusive (start, end) of the n days before ``today``, shifted back ``offset`` windows."""
    end = today - timedelta(days=1 + offset * n)
    return end - timedelta(days=n - 1), end


def _in(day: date, window: tuple[date, date]) -> bool:
    return window[0] <= day <= window[1]


def _pct_change(last, prev) -> float | None:
    if last is None or prev is None or prev == 0:
        return None
    return round((last - prev) / prev * 100.0, 1)


def _ctr(impressions: float, clicks: float) -> float | None:
    return clicks / impressions * 100.0 if impressions else None


def _age_days(video: VideoRow, today: date) -> int | None:
    if video.published_at is None:
        return None
    published = video.published_at if video.published_at.tzinfo else video.published_at.replace(tzinfo=timezone.utc)
    return (today - published.date()).days


def _views_per_day(video: VideoRow, today: date) -> float | None:
    age = _age_days(video, today)
    return video.views / age if age is not None and age >= MATURE_DAYS else None


# --- progress -----------------------------------------------------------------------------

def _window_metrics(days: list[DayRow], reach: list[ReachRow], window) -> dict:
    rows = [r for r in days if _in(r.day, window)]
    hits = [r for r in reach if _in(r.day, window)]
    views = sum(r.views for r in rows)
    impressions = sum(r.impressions for r in hits)
    return {
        "views": views if rows else None,
        "watch_minutes": sum(r.watch_minutes for r in rows) if rows else None,
        "subs_net": sum(r.subs_gained - r.subs_lost for r in rows) if rows else None,
        "avg_view_pct": sum(r.avg_view_pct * r.views for r in rows) / views if views else None,
        "impressions": impressions if hits else None,
        "ctr": _ctr(impressions, sum(r.clicks for r in hits)) if hits else None,
    }


def compute_progress(days: list[DayRow], reach: list[ReachRow], today: date) -> dict:
    windows = {
        "last_7": _window(today, 7), "prev_7": _window(today, 7, 1),
        "last_28": _window(today, WINDOW), "prev_28": _window(today, WINDOW, 1),
    }
    values = {name: _window_metrics(days, reach, w) for name, w in windows.items()}
    result = {}
    for metric in ("views", "watch_minutes", "subs_net", "avg_view_pct", "impressions", "ctr"):
        entry = {name: values[name][metric] for name in windows}
        entry["change_7_pct"] = _pct_change(entry["last_7"], entry["prev_7"])
        entry["change_28_pct"] = _pct_change(entry["last_28"], entry["prev_28"])
        result[metric] = entry
    return result


# --- rules --------------------------------------------------------------------------------

def _low_ctr(videos, reach, today) -> Finding:
    code, title = "low_ctr", "Videos with a low click-through rate"
    window = _window(today, WINDOW)
    per_video: dict[str, list[float]] = defaultdict(lambda: [0, 0.0])
    for r in reach:
        if _in(r.day, window):
            per_video[r.video_id][0] += r.impressions
            per_video[r.video_id][1] += r.clicks
    qualifying = {vid: _ctr(imp, clicks) for vid, (imp, clicks) in per_video.items() if imp >= LOW_CTR_MIN_IMPRESSIONS}
    if len(qualifying) < LOW_CTR_MIN_VIDEOS:
        return _inconclusive(code, title, f"Needs {LOW_CTR_MIN_VIDEOS} videos with ≥ {LOW_CTR_MIN_IMPRESSIONS} "
                             f"impressions in the last {WINDOW} days; have {len(qualifying)}.")
    mid = median(qualifying.values())
    titles = {v.video_id: v.title for v in videos}
    low = sorted(((vid, c) for vid, c in qualifying.items() if c < LOW_CTR_RATIO * mid), key=lambda x: x[1])[:5]
    evidence = {"median_ctr": round(mid, 2), "videos": [
        {"video_id": vid, "title": titles.get(vid), "ctr": round(c, 2), "impressions": per_video[vid][0]} for vid, c in low
    ]}
    if not low:
        return Finding(code, title, "good", _confidence(len(qualifying), LOW_CTR_MIN_VIDEOS),
                       f"No video is far below your median CTR of {mid:.1f}%.", evidence)
    return Finding(code, title, "issue", _confidence(len(qualifying), LOW_CTR_MIN_VIDEOS),
                   f"{len(low)} video(s) get under {LOW_CTR_RATIO:.0%} of your median CTR ({mid:.1f}%).", evidence,
                   "Test a new thumbnail and a clearer title on these videos; they are shown but rarely clicked.")


def _ctr_trend(reach, today) -> Finding:
    code, title = "ctr_trend", "Click-through rate trend"
    totals = []
    for offset in (0, 1):
        w = _window(today, WINDOW, offset)
        hits = [r for r in reach if _in(r.day, w)]
        totals.append((sum(r.impressions for r in hits), sum(r.clicks for r in hits)))
    (imp_last, clk_last), (imp_prev, clk_prev) = totals
    if min(imp_last, imp_prev) < CTR_TREND_MIN_IMPRESSIONS:
        return _inconclusive(code, title, f"Needs ≥ {CTR_TREND_MIN_IMPRESSIONS} impressions in each of the last two "
                             f"{WINDOW}-day windows; have {imp_last} and {imp_prev}.")
    last, prev = _ctr(imp_last, clk_last), _ctr(imp_prev, clk_prev)
    change = _pct_change(last, prev)
    evidence = {"last_28_ctr": round(last, 2), "prev_28_ctr": round(prev, 2), "change_pct": change,
                "last_28_impressions": imp_last, "prev_28_impressions": imp_prev}
    summary = f"CTR {last:.1f}% vs {prev:.1f}% in the previous {WINDOW} days."
    if change is not None and change <= -CTR_TREND_CHANGE:
        return Finding(code, title, "issue", "medium", summary, evidence,
                       "Compare recent thumbnails/titles with the ones from the stronger period and go back to what worked.")
    if change is not None and change >= CTR_TREND_CHANGE:
        return Finding(code, title, "good", "medium", summary, evidence)
    return Finding(code, title, "info", "medium", summary, evidence)


def _at(curve: list[tuple[float, float]], ratio: float) -> float:
    return min(curve, key=lambda p: abs(p[0] - ratio))[1]


def _early_dropoff(videos, retention) -> Finding:
    code, title = "early_dropoff", "Where viewers leave long videos"
    titles = {v.video_id: v.title for v in videos}
    long_ids = {v.video_id for v in videos if v.is_short is False}
    curves = {vid: c for vid, c in retention.items() if vid in long_ids and c}
    if len(curves) < DROPOFF_MIN_VIDEOS:
        return _inconclusive(code, title, f"Needs retention curves for {DROPOFF_MIN_VIDEOS} long videos; have {len(curves)}.")
    points = {vid: {"r10": _at(c, 0.1), "r30": _at(c, 0.3), "r50": _at(c, 0.5)} for vid, c in curves.items()}
    mid = median(p["r30"] for p in points.values())
    weak = sorted((vid for vid, p in points.items() if p["r30"] < DROPOFF_RATIO * mid), key=lambda v: points[v]["r30"])
    evidence = {"median_at_30": round(mid, 3), "videos": [
        {"video_id": vid, "title": titles.get(vid), **{k: round(v, 3) for k, v in points[vid].items()}} for vid in weak
    ]}
    if not weak:
        return Finding(code, title, "good", _confidence(len(curves), DROPOFF_MIN_VIDEOS),
                       f"Retention at 30% is steady across videos (median {mid:.0%}).", evidence)
    return Finding(code, title, "issue", _confidence(len(curves), DROPOFF_MIN_VIDEOS),
                   f"{len(weak)} video(s) lose viewers early: under {DROPOFF_RATIO:.0%} of your median retention at 30%.",
                   evidence, "Rewatch the first third of these videos: cut slow intros and deliver the promise of the title sooner.")


def _view_pct_by_format(videos) -> Finding:
    code, title = "view_pct_by_format", "Average view percentage by format"
    groups = {"long": [v.avg_view_pct for v in videos if v.is_short is False and v.avg_view_pct is not None],
              "short": [v.avg_view_pct for v in videos if v.is_short is True and v.avg_view_pct is not None]}
    evidence = {name: round(median(vals), 1) for name, vals in groups.items() if len(vals) >= FORMAT_MIN_VIDEOS}
    if not evidence:
        return _inconclusive(code, title, f"Needs {FORMAT_MIN_VIDEOS} videos of a format; have "
                             f"{len(groups['long'])} long and {len(groups['short'])} Shorts.")
    parts = [f"{'long-form' if k == 'long' else 'Shorts'} {v:.0f}%" for k, v in evidence.items()]
    return Finding(code, title, "info", "medium", "Median average view percentage: " + ", ".join(parts) + ".", evidence)


def _shares(traffic, window) -> tuple[int, dict[str, float]]:
    by_source: dict[str, int] = defaultdict(int)
    for r in traffic:
        if _in(r.day, window):
            by_source[r.source] += r.views
    total = sum(by_source.values())
    return total, {s: v / total * 100.0 for s, v in by_source.items()} if total else {}


def _traffic_mix(traffic, today) -> Finding:
    code, title = "traffic_mix", "Where your views come from"
    total, shares = _shares(traffic, _window(today, WINDOW))
    if total < TRAFFIC_MIN_VIEWS:
        return _inconclusive(code, title, f"Needs ≥ {TRAFFIC_MIN_VIEWS} views in the last {WINDOW} days; have {total}.")
    prev_total, prev_shares = _shares(traffic, _window(today, WINDOW, 1))
    shifts = []
    if prev_total >= TRAFFIC_MIN_VIEWS:
        for source in sorted(set(shares) | set(prev_shares)):
            moved = shares.get(source, 0.0) - prev_shares.get(source, 0.0)
            if abs(moved) >= TRAFFIC_SHIFT_POINTS:
                shifts.append({"source": source, "points": round(moved, 1)})
    top = max(shares, key=shares.get)
    evidence = {"shares": {s: round(v, 1) for s, v in sorted(shares.items(), key=lambda x: -x[1])},
                "top_source": top, "shifts": shifts, "views": total}
    if shares[top] > TRAFFIC_DEPENDENCE_SHARE:
        return Finding(code, title, "issue", "medium",
                       f"{shares[top]:.0f}% of views come from {top}; one change there moves the whole channel.",
                       evidence, "Grow a second source: search-friendly titles for search, end screens and playlists for suggested.")
    return Finding(code, title, "info", "medium", f"Top source is {top} with {shares[top]:.0f}% of views.", evidence)


def _sub_conversion(days, videos, today) -> Finding:
    code, title = "sub_conversion", "Subscribers per 1,000 views"
    rates = []
    for offset in (0, 1):
        rows = [r for r in days if _in(r.day, _window(today, WINDOW, offset))]
        views = sum(r.views for r in rows)
        rates.append((views, sum(r.subs_gained - r.subs_lost for r in rows) / views * 1000 if views else None))
    (views_last, last), (views_prev, prev) = rates
    if min(views_last, views_prev) < SUBS_MIN_VIEWS:
        return _inconclusive(code, title, f"Needs ≥ {SUBS_MIN_VIEWS} views in each of the last two {WINDOW}-day "
                             f"windows; have {views_last} and {views_prev}.")
    ranked = sorted(
        ({"video_id": v.video_id, "title": v.title, "per_1000": round(v.subs_gained / v.views * 1000, 2)}
         for v in videos if v.views >= SUBS_VIDEO_MIN_VIEWS),
        key=lambda x: -x["per_1000"],
    )
    evidence = {"last_28": round(last, 2), "prev_28": round(prev, 2), "change_pct": _pct_change(last, prev),
                "best_videos": ranked[:3], "worst_videos": ranked[-3:][::-1] if len(ranked) > 3 else []}
    return Finding(code, title, "info", "medium",
                   f"{last:.1f} net subscribers per 1,000 views (previous {WINDOW} days: {prev:.1f}).", evidence)


def _cadence(days, videos, today) -> Finding:
    code, title = "cadence", "Upload weeks vs quiet weeks"
    weekly_views: dict[int, int] = defaultdict(int)
    for r in days:
        week = ((today - r.day).days - 1) // 7
        if 0 <= week < CADENCE_WEEKS:
            weekly_views[week] += r.views
    upload_weeks = set()
    for v in videos:
        age = _age_days(v, today)
        if age is not None and age >= 1 and (age - 1) // 7 < CADENCE_WEEKS:
            upload_weeks.add((age - 1) // 7)
    with_upload = [views for w, views in weekly_views.items() if w in upload_weeks]
    without = [views for w, views in weekly_views.items() if w not in upload_weeks]
    if len(with_upload) < CADENCE_MIN_WEEKS or len(without) < CADENCE_MIN_WEEKS:
        return _inconclusive(code, title, f"Needs {CADENCE_MIN_WEEKS} weeks with and without uploads; have "
                             f"{len(with_upload)} and {len(without)}.")
    med_with, med_without = median(with_upload), median(without)
    ratio = med_with / med_without if med_without else None
    evidence = {"median_views_upload_weeks": med_with, "median_views_quiet_weeks": med_without,
                "ratio": round(ratio, 2) if ratio else None,
                "weeks_with_upload": len(with_upload), "weeks_without": len(without)}
    summary = (f"Weeks with an upload get {ratio:.1f}× the views of quiet weeks." if ratio
               else "Quiet weeks get no views, so the ratio cannot be computed.")
    return Finding(code, title, "info", _confidence(min(len(with_upload), len(without)), CADENCE_MIN_WEEKS), summary, evidence)


def _title_keywords(videos, today) -> Finding:
    code, title = "title_keywords", "Title keywords that beat or lag your baseline"
    mature = [(v, _views_per_day(v, today)) for v in videos if v.is_short is False]
    mature = [(v, vpd) for v, vpd in mature if vpd is not None]
    if len(mature) < KEYWORD_MIN_VIDEOS:
        return _inconclusive(code, title, f"Needs {KEYWORD_MIN_VIDEOS} long videos older than {MATURE_DAYS} days; "
                             f"have {len(mature)}.")
    baseline = median(vpd for _, vpd in mature)
    if baseline <= 0:
        return _inconclusive(code, title, "Your long videos have no views per day yet.")
    groups: dict[str, list[float]] = defaultdict(list)
    for v, vpd in mature:
        for token in set(_topic_tokens(v.title)):
            groups[token].append(vpd)
    scored = [{"keyword": k, "videos": len(vals), "vs_baseline": round(median(vals) / baseline, 2)}
              for k, vals in groups.items() if len(vals) >= KEYWORD_MIN_REPEATS]
    beat = sorted((s for s in scored if s["vs_baseline"] >= KEYWORD_BEAT), key=lambda s: -s["vs_baseline"])[:3]
    lag = sorted((s for s in scored if s["vs_baseline"] <= KEYWORD_LAG), key=lambda s: s["vs_baseline"])[:3]
    evidence = {"baseline_views_per_day": round(baseline, 1), "beat": beat, "lag": lag}
    if not beat and not lag:
        return Finding(code, title, "info", "medium", "No repeated title keyword clearly beats or lags your baseline.", evidence)
    parts = []
    if beat:
        parts.append("beat: " + ", ".join(f"“{s['keyword']}” {s['vs_baseline']}×" for s in beat))
    if lag:
        parts.append("lag: " + ", ".join(f"“{s['keyword']}” {s['vs_baseline']}×" for s in lag))
    return Finding(code, title, "info", _confidence(len(mature), KEYWORD_MIN_VIDEOS), "; ".join(parts) + ".", evidence,
                   "Make more videos around the keywords that beat your baseline; rethink the angle of the lagging ones.")


def _publish_timing(videos, today) -> Finding:
    code, title = "publish_timing", "Best publish day"
    mature = [(v, _views_per_day(v, today)) for v in videos]
    mature = [(v, vpd) for v, vpd in mature if vpd is not None]
    if len(mature) < TIMING_MIN_VIDEOS:
        return _inconclusive(code, title, NO_TIMING_DATA, videos=len(mature), needed=TIMING_MIN_VIDEOS)
    by_day: dict[int, list[float]] = defaultdict(list)
    for v, vpd in mature:
        by_day[v.published_at.weekday()].append(vpd)
    medians = {day: median(vals) for day, vals in by_day.items() if len(vals) >= TIMING_MIN_GROUP}
    if len(medians) < 2:
        return _inconclusive(code, title, NO_TIMING_DATA, videos=len(mature), needed=TIMING_MIN_VIDEOS)
    best, worst = max(medians, key=medians.get), min(medians, key=medians.get)
    if medians[worst] <= 0 or medians[best] / medians[worst] < TIMING_GAP:
        return _inconclusive(code, title, NO_TIMING_DATA, videos=len(mature), needed=TIMING_MIN_VIDEOS)
    evidence = {"best_day": calendar.day_name[best], "worst_day": calendar.day_name[worst],
                "ratio": round(medians[best] / medians[worst], 2),
                "median_views_per_day": {calendar.day_name[d]: round(m, 1) for d, m in sorted(medians.items())}}
    return Finding(code, title, "good", "medium",
                   f"{calendar.day_name[best]} uploads get {evidence['ratio']}× the views/day of {calendar.day_name[worst]} uploads.",
                   evidence, f"Prefer publishing on {calendar.day_name[best]}.")


def diagnose(days: list[DayRow], videos: list[VideoRow], traffic: list[TrafficRow], reach: list[ReachRow],
             retention: dict[str, list[tuple[float, float]]], today: date) -> list[Finding]:
    return [
        _low_ctr(videos, reach, today),
        _ctr_trend(reach, today),
        _early_dropoff(videos, retention),
        _view_pct_by_format(videos),
        _traffic_mix(traffic, today),
        _sub_conversion(days, videos, today),
        _cadence(days, videos, today),
        _title_keywords(videos, today),
        _publish_timing(videos, today),
    ]
