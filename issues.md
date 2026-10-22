# Issues — YT Growth Intelligence

Reliability / accuracy / security review of the current code (2026-10-22).
Evidence comes from the code and from running the audit read-only against the
local database (channels: AXIOM MINDSET, DPCcars).

Work through these one at a time. Tick the box and note the commit when fixed.

**Legend:** P0 = security / data integrity · P1 = wrong numbers or conclusions · P2 = minor

| # | Pri | Issue | Status |
|---|-----|-------|--------|
| 0 | — | Shorts classification fix not committed | ✅ `95e26d1` |
| 1 | P0 | YouTube API key written to logs | ✅ `6301d2c` |
| 2 | P0 | GET `/audit` writes history rows (duplicates) | ✅ `0a83ba1` |
| 3 | P0 | No auth / rate limit on mutating endpoints | ☐ |
| 4 | P1 | Same keyword in Strong AND Weak topics | ☐ |
| 5 | P1 | Fresh videos leak into format split & concentration | ☐ |
| 6 | P1 | Upcoming premieres / live streams not filtered | ☐ |
| 7 | P1 | Analysis limited to latest 50 videos | ☐ |
| 8 | P1 | Shorts and long-form share one baseline | ☐ |
| 9 | P1 | Health score does not measure views | ☐ |
| 10 | P1 | Views/day is biased against older videos | ☐ |
| 11 | P1 | Overview and Audit use different video sets | ☐ |
| 12 | P2 | Duplicated "coverage" label in Health score UI | ☐ |
| 13 | P2 | Subscriber count shown as exact (API rounds it) | ☐ |
| 14 | P2 | Uploads/month uses n/span instead of (n−1)/span | ☐ |
| 15 | P2 | Quota day uses UTC; YouTube resets at Pacific midnight | ☐ |
| 16 | P2 | Cached data reported with a fresh "last synced" time | ☐ |
| 17 | P2 | Dead `content_width` / `content_height` columns | ☐ |

---

## 0. Shorts classification fix not committed — ✅ fixed in `95e26d1`

- **State:** `/shorts/<id>` URL check, `is_short` column, migration
  `b7e2d4f19a30`, updated tests and UI text are in the working tree only.
- **Fix:** commit it separately before starting the issues below.

## 1. [P0] YouTube API key written to logs — ✅ fixed in `6301d2c`

- **Done:** key sent as `X-Goog-Api-Key` header (verified live: 200), httpx/httpcore loggers at WARNING.
- **Note:** the old key was already printed in earlier local logs; rotate it if any log/screenshot was shared.

- **Where:** [backend/app/main.py:15](backend/app/main.py#L15) — `logging.basicConfig(level=logging.INFO)`.
- **Evidence:** httpx logs every request URL at INFO, and the key is a query param:
  `INFO:httpx:HTTP Request: GET https://www.googleapis.com/youtube/v3/channels?key=FAKE_SECRET_KEY`
- **Impact:** key visible in the uvicorn terminal and any log file / screenshot.
- **Fix:** set the `httpx` / `httpcore` loggers to WARNING, or send the key as
  the `X-Goog-Api-Key` header instead of a query param (removes it from URLs entirely).
- **Test:** capture logs during a mocked request; assert the key string never appears.

## 2. [P0] GET `/audit` writes history rows (duplicates) — ✅ fixed in `0a83ba1`

- **Done:** GET is read-only; `POST /audit` (Re-run) → one `audit` row; `POST /sync` → one `sync` row;
  Analyze → one `analyze` row. Scheduled syncs log JobRuns only.
- **Leftover:** 24 old duplicate `audit` rows still in the local DB (not deleted).

- **Where:** [backend/app/api/routes/channels.py:131](backend/app/api/routes/channels.py#L131) (`persist=True` default),
  [backend/app/services/audit_service.py](backend/app/services/audit_service.py).
- **Evidence:** 19 `audit` rows for AXIOM, 5 for DPCcars; two rows 1 second apart
  (11:06:54 / 11:06:55) — page load + React StrictMode double render in dev.
- **Impact:** history table filled with identical rows; GET has a side effect.
- **Fix:** default `persist=False`; save an audit snapshot only on Analyze/Sync
  (or an explicit POST), optionally skip if the last snapshot is identical.
- **Test:** call GET `/audit` twice → `channel_analyses` row count unchanged.

## 3. [P0] No auth / rate limit on mutating endpoints

- **Where:** `POST /jobs/sync-all`, `POST /channels/{id}/sync`, `DELETE /channels/{id}`, `POST /channels/analyze`.
- **Impact:** fine on localhost; on any public deploy anyone can burn quota or delete data.
- **Fix:** before SaaS — user auth, per-user ownership checks (`owner_id` already exists), rate limiting.
- **Test:** unauthenticated requests rejected; user A cannot touch user B's channel.

## 4. [P1] Same keyword in Strong AND Weak topics

- **Where:** [backend/app/analytics/audit.py:546-547](backend/app/analytics/audit.py#L546-L547) — both lists are top-5/bottom-5 of the same `scored` list.
- **Evidence (AXIOM):** Strong = `rest (+4.2%)`, `protect (−74.8%)`, `sunday (−89.9%)`;
  Weak = the same three. A −89.9% keyword is shown as "strong".
- **Fix:** Strong = groups clearly above baseline (e.g. `pct > +20`), Weak = clearly
  below (e.g. `pct < −20`); lists must be disjoint.
- **Test:** with ≤ 5 groups, no topic appears in both lists; strong never has negative pct.

## 5. [P1] Fresh videos leak into format split & concentration

- **Where:** format vpd in `compute_performance` ([audit.py:389](backend/app/analytics/audit.py#L389)) and
  concentration finding ([audit.py:765](backend/app/analytics/audit.py#L765)) use all videos, not mature ones.
- **Evidence (DPCcars):** all 50 videos are < 5 days old, yet "Shorts outperform
  long-form" and "Heavy dependence on top 5" are both **detected**.
- **Fix:** use mature videos (≥ 7 days) only; inconclusive if not enough.
- **Test:** all-fresh video set → format_split and concentration are `inconclusive`.

## 6. [P1] Upcoming premieres / live streams not filtered

- **Where:** [backend/app/services/youtube.py](backend/app/services/youtube.py) `video_payload_to_fields` ignores `snippet.liveBroadcastContent`.
- **Impact (from code):** scheduled premieres have 0 views and duration `P0D` = 0 s →
  counted as a Short with 0 views, dragging averages down. Not present in current data.
- **Fix:** store `live_broadcast_content`; exclude `upcoming` / `live` from analysis.
- **Test:** payload with `liveBroadcastContent: "upcoming"` is not counted.

## 7. [P1] Analysis limited to latest 50 videos

- **Where:** `fetch_recent_videos` fetches one page; `DEFAULT_ANALYZE_LIMIT = 50`.
- **Evidence (DPCcars):** 28,000 videos on channel; the 50 analyzed span only 4.5 days →
  every video is "fresh", top performers empty, trend inconclusive.
- **Fix:** paginate `playlistItems` to cover a fixed window (proposal: last 90 days,
  max 500 videos ≈ 20 quota units).
- **Test:** pagination follows `nextPageToken`, stops at date window / cap.

## 8. [P1] Shorts and long-form share one baseline

- **Where:** `rank_videos` baseline, underperformers, outliers, topic groups.
- **Evidence (AXIOM):** Shorts median 0.75 views/day vs long-form 0.04; 4 of 10
  "underperformers" are long-form videos judged against the Shorts median.
- **Fix:** compute baseline (and rankings/outliers/topics) per format.
- **Test:** mixed set where each format is internally uniform → no underperformers.

## 9. [P1] Health score does not measure views

- **Where:** `compute_health_score` — components are recency, consistency, trend, distribution, engagement.
- **Evidence (AXIOM):** score 79 "Healthy" while videos get ~1 view/day.
- **Fix (decide):** add a performance component, or rename to an "activity" score so it isn't read as channel success.

## 10. [P1] Views/day is biased against older videos

- **Where:** views_per_day = lifetime views / age, used for rankings, trend, topics, outliers.
- **Impact:** views decay with age, so older videos look worse and recent cohorts look
  better than they are (acknowledged in [audit.py:746](backend/app/analytics/audit.py#L746), not handled).
- **Fix:** store per-sync view snapshots (new table) → compare "views in first 7/30 days";
  short-term: only compare videos within similar age bands.

## 11. [P1] Overview and Audit use different video sets

- **Where:** [backend/app/analytics/metrics.py:36](backend/app/analytics/metrics.py#L36) reads ALL stored videos; audit reads latest 50.
- **Impact:** numbers diverge over time; videos deleted/privated on YouTube stay forever with stale stats.
- **Fix:** one shared "analysis set" query; mark videos missing from the latest sync as removed.

## 12. [P2] Duplicated "coverage" label in Health score UI

- **Where:** [frontend/src/components/channel-audit.tsx:475-492](frontend/src/components/channel-audit.tsx#L475-L492).
- **Evidence:** "Coverage" component name + "score coverage" badge; amber banner and
  "PARTIAL COVERAGE" caveat repeat the same message.
- **Fix:** render coverage once (separate line, not as a component); one warning.

## 13. [P2] Subscriber count shown as exact

- **Where:** [frontend/src/components/channel-overview.tsx:153](frontend/src/components/channel-overview.tsx#L153).
- **Impact:** API rounds to 3 significant figures; "430,000" looks exact.
- **Fix:** show "≈ 430K" with a tooltip.

## 14. [P2] Uploads/month uses n/span

- **Where:** [audit.py:376](backend/app/analytics/audit.py#L376).
- **Fix:** `(n − 1) / span_months` (n uploads have n−1 gaps). Matters for small n.

## 15. [P2] Quota day uses UTC

- **Where:** [backend/app/services/quota.py](backend/app/services/quota.py) `today` property.
- **Fix:** use `America/Los_Angeles` date (YouTube resets at Pacific midnight).

## 16. [P2] Cached data reported as freshly synced

- **Where:** `_request` cache (videos 5 min, channels 15 min) + `last_synced_at = now` in `sync_channel`.
- **Fix:** bypass cache on explicit sync, or record the cached payload's fetch time.

## 17. [P2] Dead `content_width` / `content_height` columns

- **Where:** `Video` model + migration `864e8963ab7d`; API `contentDetails.dimension` is `"2d"`/`"3d"`, never `"WxH"`.
- **Fix:** drop columns and `parse_content_dimension` in a migration (low priority).

---

## Decisions

1. GET audit is read-only. Only explicit Analyze / Re-run audit / Sync create
   their history record, exactly once per action.
2. Analysis window (#7): rolling 90 days, max 500 videos with pagination. If the
   cap is hit, clearly disclose the actual date coverage.
3. Shorts fix committed separately first.
