"""The evidence pack: everything the advisor may talk about, built by code, every item with an id.

The AI step only ever sees this pack, and every claim it makes must cite these ids.
Ids: ``F:`` diagnosis findings, ``P:`` progress, ``TOP:``/``LOW:`` own videos (per format),
``B:`` competitor breakouts, ``GAP:`` topics competitors win with that the owner never used,
``TR:`` YouTube search trend direction, ``AC:`` YouTube autocomplete suggestions.
"""

import hashlib
import json
import re
from collections import defaultdict
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.audit import _topic_tokens
from app.studio.models import StudioGoogleConnection, StudioIdea, StudioMyVideo
from app.studio.services.insights import build_insights
from app.studio.services.market import autocomplete, trend_directions
from app.studio.services.my_channel import _latest_totals, get_overview
from app.studio.services.settings_store import get_keywords

MATURE_DAYS = 7
TOP_N = 5
MAX_BREAKOUTS = 10
GAP_MIN_BREAKOUTS = 2
MAX_GAPS = 10
PROGRESS_METRICS = ("views", "watch_minutes", "subs_net", "avg_view_pct", "impressions", "ctr")


def title_tokens(title: str) -> set[str]:
    """Topic words of a title; hashtags are labels, not topics."""
    return set(_topic_tokens(re.sub(r"#\S+", " ", title or "")))


def _item(id_: str, kind: str, text: str, data: dict | None = None) -> dict:
    return {"id": id_, "kind": kind, "text": text, "data": data or {}}


def _fmt(value) -> str:
    if value is None:
        return "no data"
    return f"{value:,.1f}" if isinstance(value, float) else f"{value:,}"


def _progress_items(progress: dict) -> list[dict]:
    items = []
    for metric in PROGRESS_METRICS:
        m = progress[metric]
        change = f"{m['change_28_pct']:+.1f}%" if m["change_28_pct"] is not None else "n/a"
        items.append(_item(f"P:{metric}", "progress",
                           f"{metric.replace('_', ' ')}: last 28 days {_fmt(m['last_28'])}, previous 28 days "
                           f"{_fmt(m['prev_28'])} (change {change}); last 7 days {_fmt(m['last_7'])}.", m))
    return items


def _own_video_items(db: Session, connection: StudioGoogleConnection, today: date) -> tuple[list[dict], dict, list[str]]:
    videos = db.scalars(select(StudioMyVideo).where(StudioMyVideo.connection_id == connection.id)
                        .order_by(StudioMyVideo.published_at.desc())).all()
    totals = _latest_totals(db, connection.id)
    format_mix = {"short": sum(1 for v in videos if v.is_short is True),
                  "long": sum(1 for v in videos if v.is_short is False)}
    by_format: dict[str, list[tuple[StudioMyVideo, float]]] = defaultdict(list)
    for v in videos:
        if v.is_short is None or v.published_at is None or v.youtube_video_id not in totals:
            continue
        published = v.published_at if v.published_at.tzinfo else v.published_at.replace(tzinfo=timezone.utc)
        age = (today - published.date()).days
        if age >= MATURE_DAYS:
            by_format["short" if v.is_short else "long"].append((v, totals[v.youtube_video_id].views / age))
    items = []
    for fmt, rows in by_format.items():
        rows.sort(key=lambda r: -r[1])
        for prefix, chosen in (("TOP", rows[:TOP_N]), ("LOW", rows[-TOP_N:][::-1])):
            for v, vpd in chosen:
                t = totals[v.youtube_video_id]
                items.append(_item(
                    f"{prefix}:{v.youtube_video_id}", "own_video",
                    f"{'Best' if prefix == 'TOP' else 'Weakest'} own {fmt} video: \"{v.title}\" — "
                    f"{vpd:.1f} views/day, {t.avg_view_pct:.0f}% viewed on average.",
                    {"format": fmt, "title": v.title, "views_per_day": round(vpd, 2), "views": t.views,
                     "avg_view_pct": t.avg_view_pct, "published_at": v.published_at.isoformat()},
                ))
    return items, format_mix, [v.title for v in videos]


def _competitor_items(db: Session, today: date, own_titles: list[str]) -> tuple[list[dict], list[str]]:
    insights = build_insights(db, datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc))
    if not insights["competitors"]:
        return [], ["No competitors are being watched yet, so there are no competitor breakouts or topic gaps."]
    breakouts = [
        {**b, "competitor": c["title"]} for c in insights["competitors"] for b in c["breakouts"]
    ]
    breakouts.sort(key=lambda b: -b["multiple"])
    items = []
    for b in breakouts[:MAX_BREAKOUTS]:
        items.append(_item(
            f"B:{b['youtube_video_id']}", "competitor_breakout",
            f"{b['competitor']}'s {b['format']} video \"{b['title']}\" got {b['multiple']:.1f}× that channel's "
            f"usual views/day ({b['views_per_day']:.1f}/day).",
            {"competitor": b["competitor"], "title": b["title"], "format": b["format"],
             "multiple": round(b["multiple"], 2), "views_per_day": round(b["views_per_day"], 2),
             "url": f"https://www.youtube.com/watch?v={b['youtube_video_id']}"},
        ))
    own = set().union(*(title_tokens(t) for t in own_titles)) if own_titles else set()
    counts: dict[str, list[str]] = defaultdict(list)
    for b in breakouts:
        for token in title_tokens(b["title"]):
            counts[token].append(b["youtube_video_id"])
    gaps = sorted(((t, v) for t, v in counts.items() if len(v) >= GAP_MIN_BREAKOUTS and t not in own),
                  key=lambda x: (-len(x[1]), x[0]))[:MAX_GAPS]
    for token, video_ids in gaps:
        items.append(_item(f"GAP:{token}", "topic_gap",
                           f"\"{token}\" appears in {len(video_ids)} competitor breakout titles and in none of yours.",
                           {"token": token, "breakouts": [f"B:{v}" for v in video_ids]}))
    notes = [] if breakouts else ["Watched competitors have no breakout videos yet (or too few videos to judge)."]
    return items, notes


def _market_items(serpapi, keywords: list[str]) -> tuple[list[dict], list[str]]:
    if serpapi is None:
        return [], ["SerpApi was not used for this pack (trends and autocomplete left out)."]
    items, notes = [], []
    trends = trend_directions(serpapi, keywords)
    for t in trends["items"]:
        items.append(_item(f"TR:{t['keyword']}", "trend",
                           f"YouTube search interest for \"{t['keyword']}\" is {t['direction']} "
                           f"(last 4 weeks {t['last_4w']} vs {t['prev_4w']} before, Google Trends 0–100).", t))
    suggestions = autocomplete(serpapi, keywords)
    for s in suggestions["items"]:
        if s["suggestions"]:
            items.append(_item(f"AC:{s['seed']}", "autocomplete",
                               f"YouTube suggests these searches for \"{s['seed']}\": " + "; ".join(s["suggestions"]) + ".", s))
    notes += [n for n in (trends["note"], suggestions["note"]) if n]
    return items, notes


def build_evidence(db: Session, connection: StudioGoogleConnection, *, serpapi, today: date) -> dict:
    overview = get_overview(db, connection, today)
    items = [
        _item(f"F:{f['code']}", "finding", f"{f['title']}: {f['summary']}",
              {"status": f["status"], "confidence": f["confidence"], "evidence": f["evidence"], "action": f["action"]})
        for f in overview["findings"]
    ]
    items += _progress_items(overview["progress"])
    own_items, format_mix, own_titles = _own_video_items(db, connection, today)
    items += own_items
    competitor_items, notes = _competitor_items(db, today, own_titles)
    items += competitor_items
    market_items, market_notes = _market_items(serpapi, get_keywords(db))
    items += market_items
    notes += market_notes
    earlier = db.scalars(select(StudioIdea).where(StudioIdea.connection_id == connection.id)
                         .order_by(StudioIdea.id)).all()
    return {
        "channel": {"title": connection.title, "format_mix": format_mix},
        "items": sorted(items, key=lambda i: i["id"]),
        "own_titles": own_titles,
        "earlier_ideas": [f"{i.topic} — {(i.payload.get('titles') or [''])[0]}" for i in earlier],
        "notes": notes,
    }


def evidence_hash(pack: dict) -> str:
    return hashlib.sha256(json.dumps(pack["items"], sort_keys=True, default=str).encode()).hexdigest()
