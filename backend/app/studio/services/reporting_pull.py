"""Impressions and CTR from the YouTube Reporting API (bulk daily reach reports).

The Analytics API does not return impressions, so a reporting job of type
``channel_reach_basic_a1`` is created once per connected channel; Google then
generates one CSV per day (the first one can take up to ~48 hours).
"""

import csv
import io
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.studio.models import StudioGoogleConnection, StudioReachDaily

BASE = "https://youtubereporting.googleapis.com/v1"
REACH_TYPE = "channel_reach_basic_a1"
JOB_NAME = "YT Growth Studio reach"
KEEP_REPORT_IDS = 500
# Pinned to the documented reach report columns; verify against the first real file.
COL_DATE, COL_VIDEO, COL_IMPRESSIONS, COL_CTR = (
    "date", "video_id", "video_thumbnail_impressions", "video_thumbnail_impressions_ctr",
)


def ensure_reach_job(db: Session, connection: StudioGoogleConnection, session) -> str:
    if connection.reach_job_id:
        return connection.reach_job_id
    jobs = session.get(f"{BASE}/jobs").get("jobs") or []
    job_id = next((j["id"] for j in jobs if j.get("reportTypeId") == REACH_TYPE), None)
    if job_id is None:
        job_id = session.post(f"{BASE}/jobs", {"reportTypeId": REACH_TYPE, "name": JOB_NAME})["id"]
    connection.reach_job_id = job_id
    db.commit()
    return job_id


def parse_reach_csv(text: str) -> dict[tuple[date, str], tuple[int, float]]:
    """Sum impressions and clicks (impressions x CTR) per (day, video) over all other dimensions."""
    reader = csv.DictReader(io.StringIO(text))
    missing = [c for c in (COL_DATE, COL_VIDEO, COL_IMPRESSIONS, COL_CTR) if c not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f"Reach report is missing column(s): {', '.join(missing)}")
    totals: dict[tuple[date, str], tuple[int, float]] = {}
    for row in reader:
        video = (row[COL_VIDEO] or "").strip()
        if not video:
            continue
        day = datetime.strptime(row[COL_DATE], "%Y%m%d").date()
        impressions = int(float(row[COL_IMPRESSIONS] or 0))
        clicks = impressions * float(row[COL_CTR] or 0)
        seen_impressions, seen_clicks = totals.get((day, video), (0, 0.0))
        totals[(day, video)] = (seen_impressions + impressions, seen_clicks + clicks)
    return totals


def _list_reports(session, job_id: str) -> list[dict]:
    reports: list[dict] = []
    params: dict = {}
    while True:
        page = session.get(f"{BASE}/jobs/{job_id}/reports", params or None)
        reports += page.get("reports") or []
        token = page.get("nextPageToken")
        if not token:
            break
        params = {"pageToken": token}
    # Oldest first, so a regenerated (newer) file for the same day wins.
    return sorted(reports, key=lambda r: (r.get("startTime") or "", r.get("createTime") or ""))


def pull_reach(db: Session, connection: StudioGoogleConnection, session) -> dict:
    job_id = connection.reach_job_id or ensure_reach_job(db, connection, session)
    processed = list(connection.reach_report_ids or [])
    files = rows = 0
    for report in _list_reports(session, job_id):
        if report["id"] in processed:
            continue
        for (day, video), (impressions, clicks) in parse_reach_csv(session.get_text(report["downloadUrl"])).items():
            row = db.scalars(select(StudioReachDaily).filter_by(
                connection_id=connection.id, day=day, youtube_video_id=video
            )).first()
            if row is None:
                db.add(StudioReachDaily(connection_id=connection.id, day=day, youtube_video_id=video,
                                        impressions=impressions, clicks=clicks))
            else:
                row.impressions, row.clicks = impressions, clicks
            rows += 1
        processed.append(report["id"])
        connection.reach_report_ids = processed[-KEEP_REPORT_IDS:]
        db.commit()
        files += 1
    return {"files": files, "rows": rows}
