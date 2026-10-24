"""Fair competitor benchmarks (pure functions: no DB, no I/O).

Fairness rules, from the Growth Studio design:
* views/day only on videos at least 7 days old ("mature"), so a video that is a
  few hours old cannot look viral just because its age was floored;
* Shorts and long-form are compared only with their own format;
* a video is judged against its own channel's median, never against raw views
  of a bigger or smaller channel;
* too little data gives an explicit "inconclusive", never a guess.
"""

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import mean, median

from app.analytics.audit import _topic_tokens  # read-only reuse of the audit's tokenizer

MATURE_DAYS = 7
BREAKOUT_MULTIPLE = 3.0
MIN_COMPARABLE = 10
EARLY_MULTIPLE = 5.0
EARLY_MIN_AGE_DAYS = 1.0
MIN_COMPARE = 5  # mature videos a channel needs per format to enter a comparison
RELIABLE_MIN_UPLOADS = 4
RELIABLE_MIN_SPAN_DAYS = 28.0


@dataclass(frozen=True)
class VideoStat:
    youtube_video_id: str
    title: str
    published_at: datetime | None
    view_count: int
    is_short: bool | None
    tags: list[str]
    hashtags: list[str]


@dataclass(frozen=True)
class Breakout:
    youtube_video_id: str
    title: str
    format: str
    view_count: int
    views_per_day: float
    channel_median_vpd: float
    multiple: float
    published_at: datetime | None


@dataclass(frozen=True)
class BreakoutResult:
    breakouts: list[Breakout]
    status: str  # "ok" | "inconclusive"
    note: str | None


@dataclass(frozen=True)
class UploadFrequency:
    uploads: int
    span_days: float | None
    per_30_days: float | None
    reliable: bool


@dataclass(frozen=True)
class TitlePatterns:
    n: int
    avg_length: float
    pct_with_number: float
    pct_question: float
    top_keywords: list[tuple[str, int]]


@dataclass(frozen=True)
class FormatComparison:
    fmt: str
    owner_median_vpd: float | None
    owner_videos: int
    watchlist_median_vpd: float | None
    competitors_counted: int
    owner_vpd_per_1k_subs: float | None
    watchlist_vpd_per_1k_subs: float | None


def _aware(dt: datetime) -> datetime:
    # SQLite returns naive datetimes; everything stored here is UTC.
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def age_days(published_at: datetime | None, now: datetime) -> float | None:
    if published_at is None:
        return None
    seconds = (_aware(now) - _aware(published_at)).total_seconds()
    return None if seconds < 0 else seconds / 86400


def views_per_day(v: VideoStat, now: datetime) -> float | None:
    age = age_days(v.published_at, now)
    if age is None or age < EARLY_MIN_AGE_DAYS:
        return None
    return v.view_count / age


def format_key(v: VideoStat) -> str | None:
    if v.is_short is None:
        return None
    return "short" if v.is_short else "long"


def _mature(videos: list[VideoStat], now: datetime, fmt: str) -> list[tuple[VideoStat, float]]:
    out = []
    for v in videos:
        if format_key(v) != fmt:
            continue
        age = age_days(v.published_at, now)
        if age is None or age < MATURE_DAYS:
            continue
        out.append((v, v.view_count / age))
    return out


def median_vpd(videos: list[VideoStat], now: datetime, *, fmt: str) -> float | None:
    rows = _mature(videos, now, fmt)
    return float(median(vpd for _, vpd in rows)) if rows else None


def find_breakouts(
    videos: list[VideoStat],
    now: datetime,
    *,
    multiple: float = BREAKOUT_MULTIPLE,
    min_comparable: int = MIN_COMPARABLE,
) -> BreakoutResult:
    breakouts: list[Breakout] = []
    evaluated = 0
    largest = 0
    for fmt in ("short", "long"):
        rows = _mature(videos, now, fmt)
        largest = max(largest, len(rows))
        if len(rows) < min_comparable:
            continue
        evaluated += 1
        med = float(median(vpd for _, vpd in rows))
        if med <= 0:
            continue  # nothing to compare against
        for v, vpd in rows:
            if vpd >= med * multiple:
                breakouts.append(
                    Breakout(v.youtube_video_id, v.title, fmt, v.view_count, vpd, med, vpd / med, v.published_at)
                )
    breakouts.sort(key=lambda x: x.multiple, reverse=True)
    if evaluated == 0:
        note = (
            f"Only {largest} comparable mature (7+ days old) videos of one format were found; "
            f"at least {min_comparable} are needed to judge breakouts."
        )
        return BreakoutResult([], "inconclusive", note)
    return BreakoutResult(breakouts, "ok", None)


def early_breakouts(
    videos: list[VideoStat],
    now: datetime,
    *,
    multiple: float = EARLY_MULTIPLE,
    min_comparable: int = MIN_COMPARABLE,
) -> list[Breakout]:
    """Videos 1-7 days old running far above their channel's normal pace.

    Provisional by nature: young videos are front-loaded, hence the higher bar.
    """
    found: list[Breakout] = []
    for fmt in ("short", "long"):
        rows = _mature(videos, now, fmt)
        if len(rows) < min_comparable:
            continue
        med = float(median(vpd for _, vpd in rows))
        if med <= 0:
            continue
        for v in videos:
            if format_key(v) != fmt:
                continue
            age = age_days(v.published_at, now)
            if age is None or age < EARLY_MIN_AGE_DAYS or age >= MATURE_DAYS:
                continue
            vpd = v.view_count / age
            if vpd >= med * multiple:
                found.append(Breakout(v.youtube_video_id, v.title, fmt, v.view_count, vpd, med, vpd / med, v.published_at))
    found.sort(key=lambda x: x.multiple, reverse=True)
    return found


def upload_frequency(videos: list[VideoStat], now: datetime) -> UploadFrequency:
    dated = sorted(
        _aware(v.published_at) for v in videos if v.published_at is not None and age_days(v.published_at, now) is not None
    )
    n = len(dated)
    if n < 2:
        return UploadFrequency(n, None, None, False)
    span = (dated[-1] - dated[0]).total_seconds() / 86400
    reliable = n >= RELIABLE_MIN_UPLOADS and span >= RELIABLE_MIN_SPAN_DAYS
    return UploadFrequency(n, span, (n - 1) / span * 30 if reliable else None, reliable)


def format_split(videos: list[VideoStat]) -> dict[str, int]:
    counts = {"short": 0, "long": 0, "unclassified": 0}
    for v in videos:
        counts[format_key(v) or "unclassified"] += 1
    return counts


def title_patterns(videos: list[VideoStat], *, top_n: int = 8) -> TitlePatterns | None:
    if not videos:
        return None
    n = len(videos)
    keywords: Counter[str] = Counter()
    for v in videos:
        keywords.update(set(_topic_tokens(v.title)))
    top = sorted(keywords.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]
    return TitlePatterns(
        n=n,
        avg_length=mean(len(v.title) for v in videos),
        pct_with_number=100 * sum(any(c.isdigit() for c in v.title) for v in videos) / n,
        pct_question=100 * sum("?" in v.title for v in videos) / n,
        top_keywords=top,
    )


def recurring(groups: list[list[str]], *, min_videos: int = 3, top_n: int = 10) -> list[tuple[str, int]]:
    counts: Counter[str] = Counter()
    for group in groups:
        counts.update({item.strip().lower() for item in group if item and item.strip()})
    kept = [(k, c) for k, c in counts.items() if c >= min_videos]
    return sorted(kept, key=lambda kv: (-kv[1], kv[0]))[:top_n]


def breakout_publish_times(breakouts: list[Breakout]) -> dict:
    weekday = {d: 0 for d in range(7)}
    hour = {h: 0 for h in range(24)}
    sample = 0
    for b in breakouts:
        if b.published_at is None:
            continue
        published = _aware(b.published_at).astimezone(timezone.utc)
        weekday[published.weekday()] += 1
        hour[published.hour] += 1
        sample += 1
    return {"sample": sample, "weekday_utc": weekday, "hour_utc": hour}


def compare_with_me(
    owner_videos: list[VideoStat],
    owner_subscribers: int | None,
    competitors: list[tuple[list[VideoStat], int | None]],
    now: datetime,
) -> dict[str, FormatComparison]:
    out: dict[str, FormatComparison] = {}
    for fmt in ("short", "long"):
        owner_rows = _mature(owner_videos, now, fmt)
        owner_med = float(median(vpd for _, vpd in owner_rows)) if len(owner_rows) >= MIN_COMPARE else None
        owner_per_1k = (
            owner_med / owner_subscribers * 1000 if owner_med is not None and owner_subscribers else None
        )
        medians: list[float] = []
        per_1k: list[float] = []
        for videos, subs in competitors:
            rows = _mature(videos, now, fmt)
            if len(rows) < MIN_COMPARE:
                continue
            med = float(median(vpd for _, vpd in rows))
            medians.append(med)
            if subs:
                per_1k.append(med / subs * 1000)
        out[fmt] = FormatComparison(
            fmt=fmt,
            owner_median_vpd=owner_med,
            owner_videos=len(owner_rows),
            watchlist_median_vpd=float(median(medians)) if medians else None,
            competitors_counted=len(medians),
            owner_vpd_per_1k_subs=owner_per_1k,
            watchlist_vpd_per_1k_subs=float(median(per_1k)) if per_1k else None,
        )
    return out
