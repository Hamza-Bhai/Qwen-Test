"""Market signals for the evidence pack: YouTube-scoped Google Trends and YouTube autocomplete.

Both go through ``SerpApiClient`` (monthly cap + 7-day cache). A SerpApi problem
never breaks the pack: the section comes back empty with a note.
"""

from statistics import mean

from app.studio.services.serpapi import SerpApiError

TREND_KEYWORDS = 5  # Google Trends compares at most 5 terms per request
TREND_WINDOW = 28  # daily points: last 28 vs the 28 before
TREND_CHANGE = 0.15
AUTOCOMPLETE_SEEDS = 4
AUTOCOMPLETE_SUGGESTIONS = 8


def trend_directions(client, keywords: list[str]) -> dict:
    keywords = keywords[:TREND_KEYWORDS]
    if not keywords:
        return {"items": [], "note": None}
    try:
        payload = client.get("google_trends", {
            "q": ",".join(keywords), "gprop": "youtube", "data_type": "TIMESERIES", "date": "today 3-m",
        })
    except SerpApiError as exc:
        return {"items": [], "note": str(exc)}
    series: dict[str, list[float]] = {k: [] for k in keywords}
    for point in (payload.get("interest_over_time") or {}).get("timeline_data") or []:
        if point.get("partial_data"):
            continue
        for value in point.get("values") or []:
            if value.get("query") in series:
                series[value["query"]].append(float(value.get("extracted_value") or 0))
    items = []
    for keyword, values in series.items():
        if len(values) < 2 * TREND_WINDOW:
            continue
        last, prev = mean(values[-TREND_WINDOW:]), mean(values[-2 * TREND_WINDOW:-TREND_WINDOW])
        if prev and (last - prev) / prev >= TREND_CHANGE:
            direction = "rising"
        elif prev and (last - prev) / prev <= -TREND_CHANGE:
            direction = "falling"
        else:
            direction = "flat"
        items.append({"keyword": keyword, "direction": direction, "last_4w": round(last, 1), "prev_4w": round(prev, 1)})
    return {"items": items, "note": None if items else "Google Trends returned too little data."}


def autocomplete(client, keywords: list[str], *, max_seeds: int = AUTOCOMPLETE_SEEDS) -> dict:
    items = []
    for seed in keywords[:max_seeds]:
        try:
            payload = client.get("google_autocomplete", {"q": seed, "client": "youtube"})
        except SerpApiError as exc:
            return {"items": items, "note": str(exc)}
        suggestions = [s["value"] for s in payload.get("suggestions") or [] if s.get("value") and s["value"] != seed]
        items.append({"seed": seed, "suggestions": suggestions[:AUTOCOMPLETE_SUGGESTIONS]})
    return {"items": items, "note": None}
