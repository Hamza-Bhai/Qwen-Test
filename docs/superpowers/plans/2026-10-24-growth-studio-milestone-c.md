# Growth Studio — Milestone C (my private channels) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task, inline in this session (the owner forbids subagents). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The owner presses "Connect with Google" once per own channel; each connected channel gets a daily pull of its private analytics (daily channel metrics, per-video totals, traffic sources, retention curves, impressions + CTR from the Reporting API), stored as time series, and the My channel tab shows progress and an evidence-backed diagnosis per channel.

**Architecture:** OAuth code flow implemented with `httpx` in `app/studio/services/google_oauth.py`; every Google call for a connected channel goes through one `GoogleSession` (token refresh, typed errors, injectable `httpx.Client`). Pull services write `studio_*` tables; diagnosis and progress are pure functions over plain rows, tested with fixed fixtures. Routes under `/api/v1/studio/oauth/*` and `/api/v1/studio/my-channels/*`. Frontend: the "My channel" tab replaces its `ComingSoon` placeholder.

**Tech Stack:** as Milestones A/B (FastAPI, SQLAlchemy 2, Alembic, httpx, pytest via `backend/.venv`; Next.js 16, React 18, TanStack Query, Tailwind, recharts 2).

**Spec:** `docs/superpowers/specs/2026-10-23-growth-studio-design.md` (Milestone C, sections 2, 4, 6, 7). Depends on Milestone B (head `985e04a`, alembic head `d2e3f4a5b6c7`).

## Global Constraints

- The existing public-channel feature is not changed. Wiring edits allowed outside `app/studio/`: `backend/app/main.py`, `backend/app/jobs/scheduler.py`, `backend/app/core/config.py`, `backend/.env.example`. Public `Channel` rows are only read (matched by `youtube_channel_id`).
- Scopes exactly `https://www.googleapis.com/auth/yt-analytics.readonly https://www.googleapis.com/auth/youtube.readonly`; `access_type=offline`, `prompt=consent` (always returns a refresh token). Redirect URI default `http://localhost:8000/api/v1/studio/oauth/callback`.
- Refresh token stored unencrypted in the local DB; never logged, never returned by any API response.
- No new Python or npm dependencies. Tests never hit the network: Google calls go through an injected `httpx.Client(transport=httpx.MockTransport(...))` and `GoogleSession` uses `client.request(...)` (conftest patches `httpx.Client.get` for the public feature; `request` is not patched).
- Never fabricate: CTR/impressions only from Reporting API files; a missing metric is `None`, never 0. Small samples → `"inconclusive"` with the reason. Publish-time advice only with ≥ 20 videos and a clear gap, else "no reliable data".
- One channel's failure (revoked token, API error) never stops other channels; a revoked/expired refresh token sets `status="needs_reconnect"` with a clear message.
- Handlers are plain `def`; Windows (Git Bash, forward slashes); commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. Refresh token revoked or expired (`invalid_grant`): connection becomes `needs_reconnect`, pull returns a clear 409, the daily job continues with other channels. (Tasks 2, 5, 8)
2. OAuth callback with `error=access_denied`, missing/expired/unknown `state`, or a Google account with no YouTube channel: redirect to the UI with `oauth_error`, nothing stored. (Tasks 2, 7)
3. A brand-new or tiny channel (no analytics rows, zero views, no long videos, no reach files yet): pull succeeds with zero rows, overview renders, every rule says "inconclusive" — no division by zero. (Tasks 4, 6)
4. Re-running a pull on the same day / overlapping windows: rows are upserted, never duplicated; already-processed reach report files are not downloaded again. (Tasks 4, 5)
5. Connecting the same channel twice (or reconnecting after disconnect): one row, token replaced, history kept. (Task 2)

---

### Task 1: Tables, migration and settings

**Files:**
- Modify: `backend/app/studio/models.py`, `backend/app/core/config.py`, `backend/.env.example`
- Create: `backend/alembic/versions/e3f4a5b6c7d8_add_my_channel_tables.py` (`down_revision = 'd2e3f4a5b6c7'`, every `create_table` guarded by `inspector.has_table`)
- Modify: `backend/tests/test_studio_competitor_models.py` (its head test pins `upgrade d2e3f4a5b6c7` instead of `head`, as Milestone B did for A)
- Test: `backend/tests/test_studio_my_channel_models.py`

**Interfaces — Produces (ORM in `app.studio.models`; all ids Integer pk; `connection_id` = FK `studio_google_connection.id`, indexed; child tables cascade-delete from the connection):**
- `StudioGoogleConnection` (`studio_google_connection`): `youtube_channel_id` String(64) unique; `title` String(255); `thumbnail_url` String(512) null; `refresh_token` Text null (None after disconnect); `scopes` String(512); `status` String(20) (`connected | disconnected | needs_reconnect`); `last_error` Text null; `connected_at` tz-DateTime default utcnow; `last_pulled_at` tz-DateTime null; `reach_job_id` String(64) null; `reach_report_ids` JSON default `[]`.
- `StudioMyVideo` (`studio_my_video`): `connection_id`; `youtube_video_id` String(32); `title` String(512); `published_at` tz null; `duration_seconds` Integer null; `is_short` Boolean null; `live_broadcast_content` String(16) null; unique (`connection_id`,`youtube_video_id`).
- `StudioChannelDaily` (`studio_analytics_channel_daily`): `connection_id`; `day` Date; `views`, `watch_minutes`, `avg_view_duration_s`, `subs_gained`, `subs_lost`, `likes`, `comments`, `shares` Integer; `avg_view_pct` Float; unique (`connection_id`,`day`).
- `StudioVideoTotals` (`studio_analytics_video_daily`): `connection_id`; `youtube_video_id` String(32); `as_of` Date; `views`, `watch_minutes`, `avg_view_duration_s`, `subs_gained` Integer; `avg_view_pct` Float; unique (`connection_id`,`youtube_video_id`,`as_of`). Lifetime totals as read on `as_of`.
- `StudioTrafficDaily` (`studio_analytics_traffic`): `connection_id`; `day` Date; `source` String(64); `views`, `watch_minutes` Integer; unique (`connection_id`,`day`,`source`).
- `StudioRetention` (`studio_analytics_retention`): `connection_id`; `youtube_video_id` String(32) index; `elapsed_ratio` Float; `watch_ratio` Float; `pulled_at` tz.
- `StudioReachDaily` (`studio_reach_daily`): `connection_id`; `day` Date; `youtube_video_id` String(32); `impressions` Integer; `clicks` Float (Σ impressions × ctr); unique (`connection_id`,`day`,`youtube_video_id`).
- Settings (`Settings`): `google_oauth_client_id: str | None = None`, `google_oauth_client_secret: str | None = None` (blank → None, like `serpapi_key`), `google_oauth_redirect_uri: str = "http://localhost:8000/api/v1/studio/oauth/callback"`, `studio_frontend_url: str = "http://localhost:3000"`. `.env.example` documents the first three (no values).

- [ ] **Step 1: Write failing tests** — `test_connection_roundtrip_with_children` (one row in each table, reload); `test_deleting_connection_cascades`; `test_connection_channel_id_unique` (`IntegrityError`); `test_channel_daily_unique_per_day`; `test_alembic_head_creates_my_channel_tables` (temp DB `upgrade head`: 7 tables, version `e3f4a5b6c7d8`); `test_my_channel_migration_safe_after_create_all`; `test_blank_oauth_client_id_is_none`.
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_studio_my_channel_models.py -v` → FAIL.
- [ ] **Step 3: Implement** models, migration, settings, `.env.example`; pin the B head test.
- [ ] **Step 4: Run** the file and the full suite → PASS.
- [ ] **Step 5: Commit** "Add my-channel tables and OAuth settings".

### Task 2: OAuth flow and GoogleSession

**Files:**
- Create: `backend/app/studio/services/google_oauth.py`
- Test: `backend/tests/test_studio_google_oauth.py`

**Interfaces — Produces:**
- Errors: `OAuthNotConfigured(RuntimeError)`; `OAuthStateError(ValueError)`; `GoogleAuthError(RuntimeError)` (refresh refused → reconnect needed); `GoogleApiError(RuntimeError)` with `.status_code`.
- `build_auth_url(db: Session) -> str` — new random `state` (`secrets.token_urlsafe(24)`) stored in `StudioSetting` key `"oauth_state"` as `{"value", "expires_at"}` (10 minutes); raises `OAuthNotConfigured` without client id/secret.
- `complete_oauth(db: Session, code: str, state: str, *, http: httpx.Client | None = None) -> StudioGoogleConnection` — checks and consumes state (`OAuthStateError` on mismatch/expiry/missing); POST `https://oauth2.googleapis.com/token`; then `GET https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true` with the new access token; no channel → `ValueError("This Google account has no YouTube channel.")`; upsert by `youtube_channel_id` (new token, `status="connected"`, `last_error=None`, history kept).
- `class GoogleSession(db, connection, *, http: httpx.Client | None = None)`: `get(url: str, params: dict | None = None) -> dict`, `post(url, json: dict) -> dict`, `get_text(url) -> str`. Lazily refreshes the access token (memory only); on 401 refreshes once and retries; token endpoint `400 invalid_grant` → sets `connection.status="needs_reconnect"`, `last_error`, commits, raises `GoogleAuthError("Google access was revoked or expired — connect this channel again.")`; other non-2xx → `GoogleApiError(status, google_message)`. Raises `GoogleAuthError` immediately when `refresh_token` is None.
- `disconnect(db, connection, *, http=None) -> None` — POST `https://oauth2.googleapis.com/revoke` (failure ignored), `refresh_token=None`, `status="disconnected"`.

- [ ] **Step 1: Write failing tests** (MockTransport handler records requests) — `test_auth_url_has_scopes_offline_consent_and_state`; `test_auth_url_without_client_raises`; `test_complete_creates_connection_and_consumes_state`; `test_complete_wrong_state_raises_and_stores_nothing`; `test_complete_expired_state_raises`; `test_complete_account_without_channel_raises`; `test_reconnect_same_channel_updates_one_row`; `test_session_refreshes_once_and_reuses_token`; `test_session_retries_once_on_401`; `test_invalid_grant_marks_needs_reconnect`; `test_api_error_carries_status_and_message`; `test_disconnect_revokes_and_clears_token`; `test_refresh_token_never_in_repr` (`repr(connection)` / error messages don't contain it).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** file + suite → PASS.
- [ ] **Step 5: Commit** "Add Google OAuth flow for own channels".

### Task 3: Own video list for a connection

**Files:**
- Create: `backend/app/studio/services/my_videos.py`
- Test: `backend/tests/test_studio_my_videos.py`

**Interfaces:**
- Consumes: `GoogleSession`; `app.services.youtube.video_payload_to_fields`, `parse_iso8601_duration`; `app.services.channel_service._resolve_shorts` (duck-typed, as competitor sync does).
- Produces: `sync_my_videos(db, connection, session: GoogleSession, *, max_videos: int = 200) -> int` — `channels?part=contentDetails&mine=true` → uploads playlist → `playlistItems` pages (50) up to `max_videos` → `videos.list` (`snippet,contentDetails`, 50 ids per call) → upsert `StudioMyVideo`; returns count. Also refreshes `connection.title`/`thumbnail_url`.

- [ ] **Step 1: Failing tests** — `test_sync_my_videos_upserts_and_flags_shorts` (3 videos: long, short by `_resolve_shorts` monkeypatch, live; run twice → still 3 rows); `test_sync_stops_at_max_videos`; `test_channel_without_uploads_returns_zero`.
- [ ] **Steps 2–4:** run → FAIL, implement, run file + suite → PASS.
- [ ] **Step 5: Commit** "Add own video list sync".

### Task 4: Analytics pull

**Files:**
- Create: `backend/app/studio/services/analytics_pull.py`
- Test: `backend/tests/test_studio_analytics_pull.py`

**Interfaces:**
- Consumes: `GoogleSession`, models from Task 1, `StudioMyVideo`.
- Produces (`REPORTS = "https://youtubeanalytics.googleapis.com/v2/reports"`, all with `ids=channel==MINE`, Analytics columns read by `columnHeaders` name, never by position):
  - `pull_window(db, connection, today: date) -> tuple[date, date]` — end = `today - 1 day`; start = `today - 90 days` on the first pull, else (latest stored `StudioChannelDaily.day` − 3 days), never after end.
  - `pull_channel_daily(db, connection, session, start, end) -> int` — `dimensions=day`, metrics `views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,subscribersGained,subscribersLost,likes,comments,shares`; upsert.
  - `pull_traffic(db, connection, session, start, end) -> int` — `dimensions=day,insightTrafficSourceType`, metrics `views,estimatedMinutesWatched`; upsert.
  - `pull_video_totals(db, connection, session, today) -> int` — `dimensions=video`, `startDate=2005-02-14`, `endDate=today-1`, metrics `views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,subscribersGained`, `sort=-views`, `maxResults=200`; upsert with `as_of=today`.
  - `pull_retention(db, connection, session, today, *, limit: int = 10) -> int` — latest `limit` own videos with `is_short is False`, not live, published ≥ 3 days ago; skip a video that already has rows and was published > 60 days ago; per video `dimensions=elapsedVideoTimeRatio`, `metrics=audienceWatchRatio`, `filters=video==<id>`, lifetime dates; replace that video's rows. Returns videos pulled.
- Each function commits; zero rows from Google is a valid result.

- [ ] **Step 1: Failing tests** (fixtures shaped like the real spike responses: `columnHeaders` + `rows`) — `test_first_window_is_90_days_ending_yesterday`; `test_next_window_overlaps_three_days`; `test_channel_daily_upsert_no_duplicates_on_rerun`; `test_columns_read_by_header_name` (headers in a different order still map correctly); `test_empty_rows_is_ok`; `test_traffic_upsert`; `test_video_totals_as_of_today`; `test_retention_only_long_mature_videos_and_replaces_rows`; `test_retention_skips_old_video_already_pulled`.
- [ ] **Steps 2–4:** run → FAIL, implement, run file + suite → PASS.
- [ ] **Step 5: Commit** "Add private analytics pull".

### Task 5: Reach (impressions + CTR) from the Reporting API

**Files:**
- Create: `backend/app/studio/services/reporting_pull.py`, `backend/tests/fixtures/reach_basic_a1.csv`
- Test: `backend/tests/test_studio_reporting_pull.py`

**Interfaces:**
- Produces (`BASE = "https://youtubereporting.googleapis.com/v1"`, `REACH_TYPE = "channel_reach_basic_a1"`):
  - `ensure_reach_job(db, connection, session) -> str` — reuse `connection.reach_job_id`; else `GET /jobs` and reuse one with `reportTypeId == REACH_TYPE`; else `POST /jobs {"reportTypeId": REACH_TYPE, "name": "YT Growth Studio reach"}`; store the id.
  - `parse_reach_csv(text: str) -> dict[tuple[date, str], tuple[int, float]]` — header names pinned: `date` (YYYYMMDD), `video_id`, `video_thumbnail_impressions`, `video_thumbnail_impressions_ctr`; sums impressions and impressions × ctr per (day, video) across the other dimension columns; rows with empty video id are skipped. Missing required column → `ValueError` naming it.
  - `pull_reach(db, connection, session) -> dict` (`{"files": int, "rows": int}`) — `GET /jobs/{id}/reports` (follow `nextPageToken`), skip ids in `connection.reach_report_ids`, download `downloadUrl` via `session.get_text`, upsert `StudioReachDaily` (a later file for the same day/video replaces it), append the id (keep the last 500).
- Fixture CSV uses the pinned headers plus `channel_id,traffic_source_type`-style extra columns. **When the first real report exists for any connected channel (Google needs up to ~48 h after job creation), download it once, compare headers, and if they differ: rule it in the ledger, fix the pinned names and the fixture.**

- [ ] **Step 1: Failing tests** — `test_ensure_job_reuses_existing_job`; `test_ensure_job_creates_when_missing`; `test_parse_sums_across_extra_dimensions`; `test_parse_missing_column_raises`; `test_pull_skips_processed_reports`; `test_pull_no_reports_yet_is_ok`; `test_later_file_replaces_same_day_video`.
- [ ] **Steps 2–4:** run → FAIL, implement, run file + suite → PASS.
- [ ] **Step 5: Commit** "Add Reporting API reach pull".

### Task 6: Diagnosis rules and progress

**Files:**
- Create: `backend/app/studio/diagnosis.py` (pure, no DB)
- Test: `backend/tests/test_studio_diagnosis.py`

**Interfaces — Produces:**
- Inputs as dataclasses: `DayRow(day, views, watch_minutes, avg_view_pct, subs_gained, subs_lost)`, `VideoRow(video_id, title, published_at, is_short, views, avg_view_pct, subs_gained)`, `TrafficRow(day, source, views)`, `ReachRow(day, video_id, impressions, clicks)`, `retention: dict[str, list[tuple[float, float]]]`.
- `Finding(code: str, title: str, status: Literal["issue","good","info","inconclusive"], confidence: Literal["high","medium","low"], summary: str, evidence: dict, action: str | None)`.
- `compute_progress(days, reach, today) -> dict` — for `views`, `watch_minutes`, `subs_net`, `avg_view_pct` (views-weighted), `impressions`, `ctr` (Σclicks/Σimpressions): `{"last_7","prev_7","change_7_pct","last_28","prev_28","change_28_pct"}`; a window without data → `None`, change `None` when either side is `None` or 0.
- `diagnose(days, videos, traffic, reach, retention, today) -> list[Finding]`, one finding per code, in this order, thresholds as constants at the top:
  1. `low_ctr` — videos with ≥ 500 impressions in the last 28 days; need ≥ 5; issue for each video with CTR < 0.7 × their median (list up to 5).
  2. `ctr_trend` — last 28 vs previous 28 days, each ≥ 2 000 impressions; issue if relative drop ≥ 15 %, good if rise ≥ 15 %, else info.
  3. `early_dropoff` — retention at 10/30/50 % (nearest point) for long videos with curves; need ≥ 3; issue for videos whose 30 % value < 0.8 × median 30 %.
  4. `view_pct_by_format` — median `avg_view_pct` for Shorts and long-form, each format needs ≥ 3 videos (info).
  5. `traffic_mix` — last 28 days shares, needs ≥ 500 views per window; issue "dependence" if top source > 60 %; evidence lists sources whose share moved ≥ 10 points vs previous 28 days.
  6. `sub_conversion` — net subs per 1 000 views last 28 vs previous 28 (each ≥ 1 000 views); evidence: top/bottom 3 videos by subs per 1 000 views among videos with ≥ 300 views.
  7. `cadence` — last 26 full weeks; median weekly views in weeks with ≥ 1 upload vs without, each group ≥ 4 weeks (info, ratio in evidence).
  8. `title_keywords` — long videos ≥ 7 days old, need ≥ 10; tokens via `app.analytics.audit._topic_tokens` in ≥ 3 videos; median views/day vs channel median: beat ≥ 1.5×, lag ≤ 0.67× (top 3 each).
  9. `publish_timing` — needs ≥ 20 videos ≥ 7 days old; weekday groups with ≥ 3 videos; good only if best median views/day ≥ 1.5 × worst; else inconclusive with summary "No reliable data on the best publish day yet."
- Every inconclusive finding says what is missing ("needs 5 videos with ≥ 500 impressions; have 2").

- [ ] **Step 1: Failing tests** — progress: `test_progress_changes_and_none_windows`, `test_ctr_is_weighted_not_averaged`; rules: one fixture per rule for the issue path and the inconclusive path (`test_low_ctr_flags_videos_below_70pct_of_median`, `test_low_ctr_inconclusive_with_few_videos`, … `test_publish_timing_inconclusive_under_20_videos`); `test_empty_channel_all_inconclusive_no_crash`.
- [ ] **Steps 2–4:** run → FAIL, implement, run file + suite → PASS.
- [ ] **Step 5: Commit** "Add own-channel diagnosis and progress".

### Task 7: Pull orchestration, routes and daily job

**Files:**
- Create: `backend/app/studio/services/my_channel.py`, `backend/app/studio/routes/my_channel.py`
- Modify: `backend/app/studio/schemas.py`, `backend/app/studio/jobs.py`, `backend/app/main.py`, `backend/app/jobs/scheduler.py`, `backend/app/studio/routes/errors.py` (map the new errors)
- Test: `backend/tests/test_studio_my_channel_api.py`, extend `backend/tests/test_studio_jobs.py`

**Interfaces:**
- `pull_connection(db, connection, *, session: GoogleSession | None = None, today: date | None = None) -> dict` — order: my videos → channel daily → traffic → video totals → retention → reach (`ensure_reach_job` + `pull_reach`); reach failure is recorded in the result (`"reach_error"`) without failing the pull; sets `last_pulled_at`, clears `last_error`; `GoogleAuthError` propagates.
- `pull_all_connected(db) -> list[dict]` — each `connected` channel; one failure never stops others.
- `get_overview(db, connection, today) -> dict` — `{"channel": {...}, "progress": compute_progress(...), "daily": [last 90 days], "traffic": [last 28 days shares], "videos": [up to 50 newest: id, title, published_at, is_short, views, avg_view_pct, ctr_28d|None, impressions_28d|None, retention_30|None], "findings": [Finding as dict], "public_channel_id": int|None, "reach_status": "waiting_for_first_report"|"ok"|"no_job"}`.
- Routes (router prefix `/studio`): `GET /oauth/start` → 307 to Google (409 JSON if not configured); `GET /oauth/callback?code&state&error` → 307 to `{studio_frontend_url}/studio?tab=my-channel&connected=<id>` and schedules a first `pull_connection` as a FastAPI `BackgroundTask` with its own session; on any error 307 to `...&oauth_error=<urlencoded message>`; `GET /my-channels` → list (never the token); `POST /my-channels/{id}/pull` → summary (404 unknown, 409 not connected or `GoogleAuthError`, 502 `GoogleApiError`); `GET /my-channels/{id}/overview`; `DELETE /my-channels/{id}` → disconnect (204).
- Job: `MY_CHANNELS_JOB = "studio_my_channels_pull"`, `my_channels_pull_job() -> dict` like `competitor_sync_job`; scheduler registers it every 24 h with the same catch-up.

- [ ] **Step 1: Failing tests** — `test_start_redirects_to_google`; `test_start_409_without_client`; `test_callback_success_redirects_with_connected_id` (patch `complete_oauth` and the background pull); `test_callback_error_param_redirects_with_message`; `test_callback_bad_state_redirects_with_message`; `test_list_never_contains_refresh_token`; `test_pull_409_when_needs_reconnect`; `test_pull_reach_failure_does_not_fail_pull`; `test_overview_empty_channel_renders`; `test_delete_disconnects`; job: `test_my_channels_job_continues_after_one_failure`.
- [ ] **Steps 2–4:** run → FAIL, implement, run file + suite → PASS.
- [ ] **Step 5: Commit** "Add my-channel API and daily pull job".

### Task 8: My channel tab (frontend)

**Files:**
- Create: `frontend/src/components/studio/my-channel/my-channel-panel.tsx`, `.../channel-overview.tsx`, `.../findings-list.tsx`
- Modify: `frontend/src/lib/studio-api.ts`, `frontend/src/app/studio/page.tsx`

**Behaviour:** panel lists connected channels as a switcher (status badge; "Reconnect" for `needs_reconnect`), a **Connect a channel** button that navigates to `${API_BASE_URL}/studio/oauth/start`, a banner from `connected` / `oauth_error` URL params, and an empty state explaining the unverified-app warning (Advanced → Go to YT Growth Studio). Overview: KPI cards (views, watch hours, net subs, avg view %, impressions, CTR) with 7-day and 28-day change (`—` for `None`); recharts line of daily views; traffic sources table; findings grouped issue → good → info → inconclusive, each with evidence and action; videos table with CTR/retention (`—` when missing) and a note "CTR appears 1–2 days after connecting" while `reach_status="waiting_for_first_report"`; buttons Pull now and Disconnect (confirm in-page, no `window.confirm`).

- [ ] **Step 1:** add types and fetchers to `studio-api.ts` matching Task 7's payloads.
- [ ] **Step 2:** build the components; replace the My channel `ComingSoon`.
- [ ] **Step 3: Run** `npm run typecheck && npm run lint && npm run build` in `frontend/` → clean.
- [ ] **Step 4: Commit** "Add My channel tab".

### Task 9: Real end-to-end check

- [ ] **Step 1:** Copy `backend/yt_growth.db` to the scratchpad; run a backend on port 8001 against the copy with `alembic upgrade head`.
- [ ] **Step 2:** Insert a connection for the spike's channel using the spike refresh token (scratchpad `spike_token.json`), call `POST /my-channels/{id}/pull` and `GET /overview`; record row counts, units and any errors. Confirm the reach job exists.
- [ ] **Step 3:** If a real reach report is already available, verify Task 5's pinned headers.
- [ ] **Step 4:** Run the full backend suite and the frontend checks once more; report. The owner connects AXIOM MINDSET themselves with the Connect button on their own backend (port 8000, the registered redirect).
