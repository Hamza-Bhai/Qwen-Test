# Growth Studio — Milestone B (competitors) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task, inline in this session (the owner forbids subagents). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the owner keep a competitor watchlist (manual add + SerpApi-assisted discovery they approve), sync those channels daily from the official Data API, and see fair, evidence-backed benchmarks: breakout videos, upload frequency, formats, title/tag/hashtag patterns, new uploads with early-breakout flags, hook text from breakout transcripts, and a comparison with their own channel.

**Architecture:** New `studio_competitor*` tables and services inside `backend/app/studio/`; pure benchmark functions (no DB) tested with fixed fixtures; routes under `/api/v1/studio/competitors`; reuse (import, never edit) the existing `YouTubeService`, URL resolver, `channel_service.get_service`, `_resolve_shorts`, audit's `_topic_tokens`, and Milestone A's `SerpApiClient`, `extract_hashtags`, settings store. Frontend: the Studio "Competitors" tab replaces its `ComingSoon` placeholder.

**Tech Stack:** as Milestone A (FastAPI, SQLAlchemy 2, Alembic, httpx, pytest with `backend/.venv`; Next.js 16, React 18, TanStack Query, Tailwind).

**Spec:** `docs/superpowers/specs/2026-10-23-growth-studio-design.md` (Milestone B, sections 2, 4, 6). Depends on Milestone A (commits up to `9cb7176`).

## Global Constraints

- The existing public-channel feature is not changed (no edits to its routes, tables, audit engine, tests or pages). Wiring edits allowed outside `app/studio/`: `backend/app/main.py`, `backend/app/db/session.py`, `backend/alembic/env.py` (already done), `backend/app/jobs/scheduler.py`, `backend/app/core/config.py`, `frontend/src/components/site-header.tsx`.
- Fair comparisons only: views/day on videos **≥ 7 days old**, per format (Shorts vs long-form), relative to the channel's own median; never raw views across channels of different size. Small samples → "inconclusive", never a guess.
- Breakout = views/day ≥ **3.0×** the channel's own same-format median with **≥ 10** comparable mature videos. Upload frequency is only "reliable" with ≥ 4 uploads over ≥ 28 days.
- Never fabricate: no competitor CTR/retention/impressions. SerpApi values are estimates and are replaced by Data API values on enrichment.
- Rejected channels are never suggested again. Discovery never adds to the watchlist by itself (it creates `candidate` rows the owner approves).
- Budgets: one discovery run uses **≤ 8** SerpApi searches (≤ 5 keyword searches + ≤ 3 related-video lookups); competitor sync reads **at most 100** recent videos per channel (min 20, window 90 days); everything goes through `SerpApiClient` (monthly cap, cache) and `QuotaTracker`. Tests never hit the network.
- Handlers are plain `def`; Windows dev machine (Git Bash, forward slashes); commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Network note: Task 1 and Task 14 call SerpApi for real (≈ 3–10 searches of the owner's budget). If `serpapi.com` does not resolve on the machine, do Tasks 2–8, 10, 11 first and return to Tasks 1, 9, 12, 14 when it does.

## Review Focus

1. A competitor with a hidden/`None` subscriber count (or 0): no division by zero; excluded from per-subscriber numbers and from the discovery size filter. (Tasks 6, 7, 9)
2. Fewer than 10 comparable mature videos (or a format with too few): result is "inconclusive" with the reason, not "no breakouts". (Task 6)
3. Videos with no `published_at`, a future date, or < 1 day old: excluded from vpd maths, never divide by ~0. (Task 6)
4. Candidate duplicates and exclusions: the same channel found as `@handle` and as `UC…` id, the owner's own channel, existing/rejected competitors. (Task 9)
5. SerpApi budget exhausted mid-run, empty/no transcript, YouTube quota exceeded: partial result or clear 4xx/429/502 message, no crash, nothing charged for failures. (Tasks 8, 9, 4)

---

### Task 1: Capture real SerpApi fixtures (spike)

**Files:**
- Create: `backend/tests/fixtures/serpapi_youtube_search.json`, `backend/tests/fixtures/serpapi_youtube_video.json`, `backend/tests/fixtures/serpapi_youtube_transcript.json`

- [ ] **Step 1:** With the owner's key (never printed), call SerpApi once each: `engine=youtube` `search_query=sunday reset`; `engine=youtube_video` `v=<the owner's most-viewed long video id from the dev DB>`; `engine=youtube_video_transcript` `v=<same id>`. Remove `search_parameters.api_key`, trim each file to ≤ 8 results per list, keep real field names and values.
- [ ] **Step 2:** Print and record (in the ledger) the real field paths for: channel result (name, link/handle/id), video result's `channel` object, `related_videos[*].channel`, transcript segments. These pin the parsing in Tasks 8–9; if a field name differs from the plan, rule it in the ledger and use the real one.
- [ ] **Step 3: Commit** `git add backend/tests/fixtures && git commit -m "Add real SerpApi response fixtures"`.

### Task 2: Own-channel selection

**Files:**
- Create: `backend/app/studio/services/own_channel.py`
- Modify: `backend/app/studio/routes/settings.py`, `backend/app/studio/schemas.py` (add fields), `backend/app/studio/services/settings_store.py` only if a helper is shared
- Test: `backend/tests/test_studio_own_channel.py`

**Interfaces:**
- Consumes: `StudioSetting`, public `Channel` model (read only).
- Produces: `get_own_channel(db: Session) -> Channel | None` (saved id if that channel exists and is not archived; else the single non-archived channel if exactly one exists; else `None`); `set_own_channel(db: Session, channel_id: int) -> Channel` (`ValueError` if missing/archived; stored under key `"own_channel_id"`). API: `GET /api/v1/studio/own-channel` → `{"channel": {"id": int, "title": str, "subscriber_count": int | None} | None, "candidates": [{"id", "title"}]}`; `PUT /api/v1/studio/own-channel` body `{"channel_id": int}` → same shape, 422 on `ValueError`. `GET /studio/status` gains `own_channel: {"id","title"} | None`.

- [ ] **Step 1: Write failing tests** — `test_single_channel_is_picked_automatically`; `test_two_channels_and_nothing_saved_returns_none`; `test_set_then_get`; `test_archived_saved_channel_is_ignored`; `test_set_unknown_channel_raises_valueerror`; API: `test_put_own_channel_422_for_unknown_id`, `test_status_includes_own_channel`.
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_studio_own_channel.py -v` → FAIL.
- [ ] **Step 3: Implement** the two functions, schemas and the two endpoints.
- [ ] **Step 4: Run** the file and the full suite → PASS.
- [ ] **Step 5: Commit** `git add backend/app/studio backend/tests/test_studio_own_channel.py && git commit -m "Add own-channel selection for Studio"`.

### Task 3: Competitor tables and migration

**Files:**
- Modify: `backend/app/studio/models.py`
- Create: `backend/alembic/versions/d2e3f4a5b6c7_add_competitor_tables.py` (`down_revision = 'c9d1e2f3a4b5'`; each `create_table` guarded by `inspector.has_table`, as in `c9d1e2f3a4b5`)
- Test: `backend/tests/test_studio_competitor_models.py`

**Interfaces — Produces (ORM, all in `app.studio.models`):**
- `StudioCompetitor` (`studio_competitor`): `id` pk; `youtube_channel_id` String(64) unique index; `title` String(255); `handle` String(100) null; `description` Text null; `thumbnail_url` String(512) null; `country` String(8) null; `uploads_playlist_id` String(64) null; `subscriber_count`/`video_count`/`view_count` Integer null; `channel_created_at` tz-DateTime null; `status` String(16) index (`"candidate" | "watching" | "rejected"`); `source` String(16) (`"manual" | "search" | "related"`); `discovery_reason` JSON null; `notes` Text null; `added_at`, `status_changed_at` tz-DateTime (default `utcnow`); `last_synced_at`, `last_viewed_at` tz-DateTime null; relationship `videos` (cascade `all, delete-orphan`).
- `StudioCompetitorVideo` (`studio_competitor_video`): `id`; `competitor_id` FK index; `youtube_video_id` String(32) unique index; `title` String(512); `description` Text null; `thumbnail_url` String(512) null; `published_at` tz-DateTime null; `duration_seconds` Integer null; `is_short` Boolean null; `live_broadcast_content` String(16) null; `tags` JSON null; `hashtags` JSON null; `view_count` Integer default 0; `like_count`/`comment_count` Integer null; `first_seen_at`, `last_synced_at` tz-DateTime; `hook_text` Text null; `hook_segments` JSON null; relationship `snapshots` (cascade).
- `StudioCompetitorVideoSnapshot` (`studio_competitor_video_snapshot`): `id`; `video_id` FK index; `captured_at` tz-DateTime; `view_count` Integer; `like_count`/`comment_count` Integer null.

- [ ] **Step 1: Write failing tests** — `test_competitor_roundtrip_with_videos_and_snapshots` (create all three, reload, assert relationships); `test_deleting_competitor_cascades_to_videos_and_snapshots`; `test_youtube_channel_id_is_unique` (second insert raises `IntegrityError`); `test_alembic_head_creates_competitor_tables` (subprocess `alembic upgrade head` on a temp DB: three tables exist, version `d2e3f4a5b6c7`); `test_competitor_migration_safe_after_create_all` (same pattern as the `studio_setting` test).
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_studio_competitor_models.py -v` → FAIL.
- [ ] **Step 3: Implement** the models and the migration.
- [ ] **Step 4: Run** → PASS; full suite → pass.
- [ ] **Step 5: Commit** `git add backend/app/studio/models.py backend/alembic/versions/d2e3f4a5b6c7_add_competitor_tables.py backend/tests/test_studio_competitor_models.py && git commit -m "Add competitor tables"`.

### Task 4: Watchlist service and API

**Files:**
- Create: `backend/app/studio/services/competitors.py`, `backend/app/studio/routes/competitors.py`, `backend/app/studio/routes/errors.py`
- Modify: `backend/app/studio/schemas.py`, `backend/app/main.py` (include the new router)
- Test: `backend/tests/test_studio_competitors_api.py` (own `client` fixture; use `make_fake_get` from `tests/conftest.py` to fake the Data API)

**Interfaces:**
- Consumes: `parse_channel_reference`, `channel_service.get_service(db)`, `channel_payload_to_fields` (existing, import only).
- Produces in `competitors.py`: `add_competitor(db, identifier: str, *, notes: str | None = None, status: str = "watching", source: str = "manual", reason: dict | None = None) -> StudioCompetitor` (resolves via Data API; if the channel already exists it is updated, and a `rejected`/`candidate` row becomes `watching` when added manually; stores `handle` when the identifier was a handle); `list_competitors(db, status: str | None = None) -> list[StudioCompetitor]` (newest `added_at` first); `get_competitor(db, competitor_id: int) -> StudioCompetitor | None`; `set_status(db, competitor_id: int, status: str) -> StudioCompetitor` (`ValueError` for an unknown status or id; updates `status_changed_at`); `update_notes(db, competitor_id: int, notes: str | None) -> StudioCompetitor`; `remove_competitor(db, competitor_id: int) -> None` (sets `rejected` and deletes that channel's stored videos — a removed channel is never suggested again; adding it manually later restores it).
- In `routes/errors.py`: `raise_http(exc: Exception) -> NoReturn` mapping `UnsupportedURLError`/`InvalidURLError`/`ValueError` → 422 (message), `YouTubeAPIError` 404 → 404, 403/5xx → 502, others → 400, `QuotaExceededError` → 429, `SerpApiNotConfigured` → 409, `SerpApiBudgetExceeded` → 429, `SerpApiError` → 502 (all with the exception's message as `detail`).
- API (prefix `/studio/competitors`): `GET ""?status=` → list of `CompetitorRead` (`id, youtube_channel_id, title, handle, thumbnail_url, country, subscriber_count (int|null), video_count, status, source, discovery_reason, notes, added_at, last_synced_at, videos_stored: int`); `POST ""` body `{"identifier": str, "notes": str | null}` → 201 `CompetitorRead`; `PATCH "/{id}"` body `{"status": str | null, "notes": str | null}` → `CompetitorRead` (404 if unknown id); `DELETE "/{id}"` → 204.

- [ ] **Step 1: Write failing tests** — add by `@handle` → 201 with `status == "watching"` and the Data API title/subscribers; add by channel URL; adding twice returns one row; `UnsupportedURLError` URL (`youtube.com/c/foo`) → 422 with the resolver's message; unknown channel → 404; quota exhausted → 429; `PATCH` notes and status (candidate → watching) persists; `PATCH` bad status → 422; `DELETE` → 204, row now `rejected`, its videos gone, and `GET ?status=watching` no longer lists it; re-adding a removed channel manually makes it `watching` again; `GET ?status=candidate` filters; a competitor with `subscriber_count` `None` is returned as `null`.
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_studio_competitors_api.py -v` → FAIL.
- [ ] **Step 3: Implement** service, error mapper, schemas, router and the `main.py` include.
- [ ] **Step 4: Run** the file and the full suite → PASS.
- [ ] **Step 5: Commit** `git add backend/app/studio backend/app/main.py backend/tests/test_studio_competitors_api.py && git commit -m "Add competitor watchlist service and API"`.

### Task 5: Competitor sync

**Files:**
- Create: `backend/app/studio/services/competitor_sync.py`
- Modify: `backend/app/studio/routes/competitors.py` (add the two sync endpoints)
- Test: `backend/tests/test_studio_competitor_sync.py`

**Interfaces:**
- Consumes: `get_service(db)` → `YouTubeService` (`bypass_cache = True`, `resolve_channel`, `fetch_recent_videos(playlist_id, window_days=90, min_videos=20, max_videos=100)`), `video_payload_to_fields`, `channel_service._resolve_shorts` (duck-typed: reads/sets `live_broadcast_content`, `duration_seconds`, `is_short`, `youtube_video_id`), `extract_hashtags` (Milestone A).
- Produces: `sync_competitor(db: Session, competitor: StudioCompetitor, *, service: YouTubeService | None = None) -> dict` returning `{"competitor": youtube_channel_id, "videos_upserted": int, "quota_units_used": int}`; refreshes channel fields, upserts `StudioCompetitorVideo` rows (title, description, tags, `hashtags = extract_hashtags(title, description)`, duration, Shorts flag, publish time, stats, `last_synced_at`), writes one `StudioCompetitorVideoSnapshot` per video per sync, sets `last_synced_at`; `sync_all_watching(db: Session) -> list[dict]` (only `status == "watching"`; one failing channel records `{"competitor", "error"}` and does not stop the others). Endpoints: `POST /studio/competitors/{id}/sync` → the dict above (404 unknown, 409 if not `watching`); `POST /studio/competitors/sync` → `{"results": [...]}`.

- [ ] **Step 1: Write failing tests** (fake Data API via `make_fake_get`) — `test_sync_stores_videos_with_hashtags_and_tags`; `test_sync_records_one_snapshot_per_video_per_sync` (two syncs → two snapshots each); `test_resync_updates_stats_without_duplicating_videos`; `test_sync_sets_last_synced_at_and_channel_stats`; `test_hidden_subscriber_count_is_stored_as_none` (payload with `hiddenSubscriberCount: true`/no `subscriberCount`); `test_sync_all_skips_candidates_and_rejected`; `test_one_failing_channel_does_not_stop_the_rest`; `test_sync_endpoint_409_for_candidate`.
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_studio_competitor_sync.py -v` → FAIL.
- [ ] **Step 3: Implement** service and endpoints.
- [ ] **Step 4: Run** → PASS; full suite → pass.
- [ ] **Step 5: Commit** `git add backend/app/studio backend/tests/test_studio_competitor_sync.py && git commit -m "Add competitor sync with snapshots"`.

### Task 6: Benchmark maths (pure functions)

**Files:**
- Create: `backend/app/studio/benchmarks.py`
- Test: `backend/tests/test_studio_benchmarks.py` (no DB; fixed `now` and hand-built `VideoStat` lists)

**Interfaces — Produces** (all in `app.studio.benchmarks`):
- Constants `MATURE_DAYS = 7`, `BREAKOUT_MULTIPLE = 3.0`, `MIN_COMPARABLE = 10`, `EARLY_MULTIPLE = 5.0`, `EARLY_MIN_AGE_DAYS = 1.0`.
- `@dataclass(frozen=True) VideoStat`: `youtube_video_id: str`, `title: str`, `published_at: datetime | None`, `view_count: int`, `is_short: bool | None`, `tags: list[str]`, `hashtags: list[str]`.
- `age_days(published_at: datetime | None, now: datetime) -> float | None` (`None` for missing or future dates); `views_per_day(v: VideoStat, now: datetime) -> float | None` (`None` when age is `None` or < `EARLY_MIN_AGE_DAYS`); `format_key(v: VideoStat) -> str | None` (`"short"`, `"long"`, or `None` when `is_short` is `None`); `median_vpd(videos, now, *, fmt: str) -> float | None` (mature, same-format videos only; `None` when none).
- `@dataclass(frozen=True) Breakout`: `youtube_video_id, title, format, view_count, views_per_day, channel_median_vpd, multiple, published_at`. `@dataclass(frozen=True) BreakoutResult`: `breakouts: list[Breakout]` (sorted by `multiple` desc), `status: str` (`"ok"` or `"inconclusive"`), `note: str | None`. `find_breakouts(videos, now, *, multiple=BREAKOUT_MULTIPLE, min_comparable=MIN_COMPARABLE) -> BreakoutResult` (a format is evaluated only with ≥ `min_comparable` mature videos of that format; if no format qualifies → `inconclusive` with a note saying how many mature videos were found); `early_breakouts(videos, now, *, multiple=EARLY_MULTIPLE, min_comparable=MIN_COMPARABLE) -> list[Breakout]` (videos aged ≥ 1 and < 7 days whose vpd ≥ `multiple` × that format's mature median; needs the same ≥ 10 comparable).
- `@dataclass(frozen=True) UploadFrequency`: `uploads: int`, `span_days: float | None`, `per_30_days: float | None`, `reliable: bool`; `upload_frequency(videos, now) -> UploadFrequency` (`reliable` only with ≥ 4 dated uploads over ≥ 28 days; `per_30_days = (n - 1) / span_days * 30`, `None` when not reliable).
- `format_split(videos) -> dict[str, int]` (`short`, `long`, `unclassified`); `@dataclass(frozen=True) TitlePatterns`: `n, avg_length, pct_with_number, pct_question, top_keywords: list[tuple[str, int]]`; `title_patterns(videos, *, top_n: int = 8) -> TitlePatterns | None` (`None` for no videos; keywords via `app.analytics.audit._topic_tokens`, counted once per title); `recurring(groups: list[list[str]], *, min_videos: int = 3, top_n: int = 10) -> list[tuple[str, int]]` (each value counted once per video, lowercase, sorted by count desc then alphabetically); `breakout_publish_times(breakouts: list[Breakout]) -> dict` → `{"sample": int, "weekday_utc": {0..6: int}, "hour_utc": {0..23: int}}`.
- `@dataclass(frozen=True) FormatComparison`: `fmt, owner_median_vpd, owner_videos, watchlist_median_vpd, competitors_counted, owner_vpd_per_1k_subs, watchlist_vpd_per_1k_subs` (all numbers `float | None`, counts `int`); `compare_with_me(owner_videos: list[VideoStat], owner_subscribers: int | None, competitors: list[tuple[list[VideoStat], int | None]], now: datetime) -> dict[str, FormatComparison]` keyed `"short"`/`"long"`; a channel contributes to a format only with ≥ 5 mature videos of it; watchlist numbers are the median of the competitors' medians; per-1k-subs values are `None` when subscribers are `None`/0.

- [ ] **Step 1: Write failing tests** with exact numbers: `test_age_days_none_for_missing_or_future`; `test_views_per_day_excludes_under_one_day`; `test_median_vpd_uses_only_mature_same_format` (mix of ages/formats, expected median hand-computed); `test_breakout_detected_at_3x_and_not_below` (a video at 3.0× is in, 2.9× is out); `test_breakouts_inconclusive_below_10_comparable` (9 mature videos → `status == "inconclusive"`, empty list, note contains "9"); `test_breakouts_evaluate_each_format_separately` (12 long + 4 shorts → only long evaluated); `test_early_breakouts_need_5x_and_age_1_to_7_days`; `test_upload_frequency_unreliable_when_short_span_or_few_uploads`; `test_upload_frequency_uses_n_minus_1`; `test_format_split_counts_unclassified`; `test_title_patterns_none_for_empty_and_counts_numbers_questions`; `test_recurring_counts_once_per_video_and_applies_min`; `test_breakout_publish_times_reports_sample_size`; `test_compare_with_me_handles_none_subscribers` (no division error, per-1k `None`); `test_compare_requires_5_mature_videos`.
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_studio_benchmarks.py -v` → FAIL.
- [ ] **Step 3: Implement** the module (pure functions, no DB, no I/O).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `git add backend/app/studio/benchmarks.py backend/tests/test_studio_benchmarks.py && git commit -m "Add fair competitor benchmark maths"`.

### Task 7: Insights, new uploads and compare-with-me API

**Files:**
- Create: `backend/app/studio/services/insights.py`
- Modify: `backend/app/studio/routes/competitors.py`, `backend/app/studio/schemas.py`
- Test: `backend/tests/test_studio_insights.py`

**Interfaces:**
- Consumes: Task 6 functions; `get_own_channel` (Task 2); public `Video` rows of the own channel (read only, excluding `removed_at`/live via the existing `video_is_analyzable`-style filter — use `Video.removed_at.is_(None)`); competitors' stored videos.
- Produces: `build_insights(db: Session, now: datetime | None = None) -> dict` with `{"competitors": [{"id", "title", "subscriber_count", "videos_analyzed": int, "breakouts": [Breakout-as-dict], "breakout_status": str, "breakout_note": str | None, "upload_frequency": {...}, "format_split": {...}, "title_patterns": {...} | None, "top_tags": [[str, int]], "top_hashtags": [[str, int]], "breakout_publish_times": {...}}], "watchlist": {"top_tags": [...], "top_hashtags": [...], "title_patterns": {...} | None, "format_split": {...}}, "compare": {"short": FormatComparison-as-dict, "long": ...} | None, "own_channel": {"id","title"} | None}` (only `watching` competitors; `compare` is `None` when there is no own channel); `new_uploads(db, now=None) -> list[dict]` (videos from `watching` competitors published after that competitor's `last_viewed_at`, or in the last 7 days when it is `None`; each `{"competitor_id", "competitor_title", "youtube_video_id", "title", "published_at", "view_count", "early_breakout": bool, "early_multiple": float | None}`, newest first); `mark_seen(db, now=None) -> int` (sets `last_viewed_at` on all `watching` competitors, returns how many). Endpoints: `GET /studio/competitors/insights`, `GET /studio/competitors/new-uploads`, `POST /studio/competitors/mark-seen` → `{"marked": int}`.

- [ ] **Step 1: Write failing tests** (DB rows built directly with fixed dates; patch nothing) — `test_insights_only_include_watching_competitors`; `test_breakout_and_status_reported_per_competitor` (one with 12 mature videos incl. a 4× outlier → listed, one with 6 → `breakout_status == "inconclusive"` and a note); `test_hidden_subscribers_do_not_break_insights`; `test_compare_none_without_own_channel`; `test_compare_uses_own_channel_videos_and_ignores_removed`; `test_new_uploads_since_last_viewed_and_default_7_days`; `test_early_breakout_flag_on_new_upload`; `test_mark_seen_hides_old_uploads_and_returns_count`; `test_insights_endpoint_shape` (API call).
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_studio_insights.py -v` → FAIL.
- [ ] **Step 3: Implement** the three functions and endpoints.
- [ ] **Step 4: Run** → PASS; full suite → pass.
- [ ] **Step 5: Commit** `git add backend/app/studio backend/tests/test_studio_insights.py && git commit -m "Add competitor insights, new uploads and compare-with-me"`.

### Task 8: Hook study (transcript)

**Files:**
- Create: `backend/app/studio/services/hooks.py`
- Modify: `backend/app/studio/routes/competitors.py`
- Test: `backend/tests/test_studio_hooks.py` (builds a `SerpApiClient` around a fake HTTP object like the `FakeHttp` in `tests/test_studio_serpapi.py`, replaying `tests/fixtures/serpapi_youtube_transcript.json`)

**Interfaces:**
- Consumes: `SerpApiClient.get("youtube_video_transcript", {"v": id}, cache_days=30)`; segment field names pinned in Task 1 (`start_ms`, `snippet` expected).
- Produces: `study_hook(db: Session, serp: SerpApiClient, youtube_video_id: str) -> dict` returning `{"youtube_video_id", "hook_text": str, "segments": [{"start_ms": int, "text": str}], "seconds_covered": int}` where `hook_text` joins the segments starting before 60 000 ms; stores `hook_text` and `hook_segments` on the matching `StudioCompetitorVideo` (`ValueError("Unknown competitor video")` if it is not stored); raises `ValueError("No transcript available for this video")` when the response has no segments (nothing stored). Endpoints: `POST /studio/competitors/videos/{youtube_video_id}/hook` (spends ≤ 1 SerpApi search; 404 unknown video, 404 no transcript, 409 not configured, 429 budget) and `GET` of the same path returning the stored hook or 404.

- [ ] **Step 1: Write failing tests** — `test_hook_text_uses_only_first_60_seconds` (segments at 0/10/…/70 s); `test_hook_is_stored_and_returned_by_get`; `test_second_call_uses_cache_and_costs_nothing`; `test_empty_transcript_raises_and_stores_nothing`; `test_unknown_video_raises`; `test_budget_exceeded_maps_to_429` and `test_not_configured_maps_to_409` (API).
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_studio_hooks.py -v` → FAIL.
- [ ] **Step 3: Implement** service and endpoints (SerpApi client injected through a FastAPI dependency `get_serpapi(db)` so tests can override it).
- [ ] **Step 4: Run** → PASS; full suite → pass.
- [ ] **Step 5: Commit** `git add backend/app/studio backend/tests/test_studio_hooks.py && git commit -m "Add competitor hook study from transcripts"`.

### Task 9: Competitor discovery

**Files:**
- Create: `backend/app/studio/services/discovery.py`
- Modify: `backend/app/studio/routes/competitors.py`
- Test: `backend/tests/test_studio_discovery.py` (fixtures from Task 1; fake `SerpApiClient` and a fake Data API through `make_fake_get`)

**Interfaces:**
- Consumes: `SerpApiClient`, `YouTubeService`, `get_keywords`, `get_own_channel`, `parse_channel_reference` (to turn a channel link into `@handle`/`UC…`).
- Produces: `@dataclass(frozen=True) Candidate`: `identifier: str` (`@handle` lowercased, or `UC…` id), `title: str | None`, `seeds: tuple[str, ...]`, `sources: tuple[str, ...]` (`"search"`/`"related"`), `appearances: int`; `candidates_from_search(payload: dict, seed: str) -> list[Candidate]` (channels in `channel_results` plus the channel behind each `video_results` entry); `candidates_from_related(payload: dict, seed: str) -> list[Candidate]` (channels behind `related_videos`); `merge_candidates(groups: list[list[Candidate]]) -> list[Candidate]` (de-duplicate by identifier, sum `appearances`, union seeds/sources); `@dataclass DiscoveryResult`: `added: int`, `considered: int`, `searches_used: int`, `stopped_reason: str | None` (`"budget"` when the SerpApi cap ended the run early), `competitor_ids: list[int]`; `discover(db: Session, serp: SerpApiClient, youtube: YouTubeService, *, max_searches: int = 8, max_new: int = 15, now: datetime | None = None) -> DiscoveryResult`.
- Rules pinned by the spec: seeds = the first 5 saved keywords (one `engine="youtube"` search each) plus the owner's 3 most-viewed mature long videos (one `engine="youtube_video"` each), never more than `max_searches` searches; drop the owner's own channel and every channel already in `studio_competitor` (any status, so `rejected` never returns); enrich at most 25 remaining candidates with the Data API (`channels_list` with comma-joined ids for `UC…`, single handle lookups otherwise); keep a candidate only if its subscribers are unknown **or** within 0.2×–20× the owner's subscribers (skip this filter when the owner's count is `None`), its latest upload is ≤ 30 days old and ≥ 3 recent videos were read (`playlist_items(max_results=10)` + `videos_list`); score = median views of those recent videos ÷ max(subscribers, 1) (unknown subscribers → score 0 and listed last); store the top `max_new` as `candidate` rows with `source`, and `discovery_reason = {"seeds": [...], "appearances": int, "recent_median_views": int, "views_per_subscriber": float | None, "days_since_last_upload": int, "text": "<one readable sentence>"}`. `ValueError("Pick your channel in Studio settings first")` when there is no own channel. A `SerpApiBudgetExceeded` mid-run ends the run with `stopped_reason == "budget"` and keeps what was found; `SerpApiNotConfigured` propagates. Endpoint: `POST /studio/competitors/discover` → `DiscoveryResult` as JSON, using the same `get_serpapi(db)` dependency as Task 8 so tests can override it.

- [ ] **Step 1: Write failing tests** — `test_candidates_from_search_fixture` (every identifier is `@handle` or matches `^UC[A-Za-z0-9_-]{22}$`; none empty); `test_candidates_from_related_fixture`; `test_merge_dedupes_handle_case_and_sums_appearances`; `test_discover_excludes_own_existing_and_rejected` (own channel, a `watching` one and a `rejected` one appear in the fake results and are not added); `test_discover_applies_size_range_and_recency` (too big, too small, stale last upload dropped); `test_discover_unknown_subscribers_kept_but_ranked_last`; `test_discover_owner_with_hidden_subscribers_skips_size_filter`; `test_discover_stores_reason_and_source_and_never_watching`; `test_discover_never_exceeds_max_searches` (counts fake calls ≤ 8); `test_discover_budget_exhausted_returns_partial_with_reason`; `test_discover_without_own_channel_raises`; `test_discover_endpoint_409_when_serpapi_not_configured`.
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_studio_discovery.py -v` → FAIL.
- [ ] **Step 3: Implement** the module and endpoint.
- [ ] **Step 4: Run** → PASS; full suite → pass.
- [ ] **Step 5: Commit** `git add backend/app/studio backend/tests/test_studio_discovery.py && git commit -m "Add SerpApi-assisted competitor discovery"`.

### Task 10: Jobs, scheduler wiring and auto-discovery toggle

**Files:**
- Create: `backend/app/studio/jobs.py`
- Modify: `backend/app/jobs/scheduler.py`, `backend/app/studio/services/settings_store.py`, `backend/app/studio/routes/settings.py`, `backend/app/studio/schemas.py`
- Test: `backend/tests/test_studio_jobs.py`

**Interfaces:**
- Produces in `jobs.py`: `competitor_sync_job() -> dict` (opens its own session, writes a `JobRun` named `studio_competitor_sync`, runs `sync_all_watching`, status `success`/`failed`, same style as `sync_all_channels_job`); `competitor_discovery_job() -> dict` (`JobRun` `studio_competitor_discovery`; does nothing and records `{"skipped": "auto_discovery is off"}` when the setting is off; otherwise runs `discover` and stores the `DiscoveryResult`); `job_overdue(db: Session, job_name: str, max_age: timedelta, now: datetime | None = None) -> bool` (true when there is no successful `JobRun` of that name newer than `max_age`).
- In `settings_store.py`: `get_auto_discovery(db) -> bool` (default `False`), `set_auto_discovery(db, enabled: bool) -> bool` (key `"auto_discovery"`). API: `PUT /api/v1/studio/auto-discovery` body `{"enabled": bool}` → `{"enabled": bool}`; `GET /studio/status` gains `auto_discovery: bool`.
- Scheduler: add `competitor_sync_job` (`IntervalTrigger(hours=24)`, id `studio_competitor_sync`) and `competitor_discovery_job` (`IntervalTrigger(weeks=1)`, id `studio_competitor_discovery`) to `start_scheduler`; when `job_overdue(..., timedelta(hours=24))` is true for the sync job at startup, give it `next_run_time = now + 60 s` (missed-run catch-up); discovery gets the same catch-up with `timedelta(days=7)` but, because the job itself skips when the toggle is off, nothing is spent unless the owner enabled it.

- [ ] **Step 1: Write failing tests** — `test_sync_job_logs_jobrun_success_and_failure`; `test_discovery_job_skips_when_auto_discovery_off_and_spends_nothing`; `test_discovery_job_runs_when_on` (patch `discover`); `test_job_overdue_cases` (never ran, ran 2 h ago, ran 30 h ago, only failed runs); `test_auto_discovery_default_false_and_toggle_via_api`; `test_scheduler_registers_studio_jobs` (start scheduler with `ENABLE_SCHEDULER=true` in a test, assert job ids present, then stop it).
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_studio_jobs.py -v` → FAIL.
- [ ] **Step 3: Implement** jobs, setting helpers, endpoint and the scheduler edit.
- [ ] **Step 4: Run** → PASS; full suite → pass (the existing scheduler/health tests must still pass).
- [ ] **Step 5: Commit** `git add backend/app/studio backend/app/jobs/scheduler.py backend/tests/test_studio_jobs.py && git commit -m "Add Studio competitor jobs and auto-discovery toggle"`.

### Task 11: Frontend — client, Watchlist tab

**Files:**
- Modify: `frontend/src/lib/studio-api.ts` (add types and functions), `frontend/src/app/studio/page.tsx` (Competitors tab renders `<CompetitorsPanel />`)
- Create: `frontend/src/components/studio/competitors/competitors-panel.tsx` (sub-tabs `Tabs size="sm" urlParam="comp"`: Watchlist · Candidates · New uploads · Insights), `watchlist-tab.tsx`

**Interfaces:**
- Produces in `studio-api.ts` (all through `studioRequest`): types `Competitor` (fields of `CompetitorRead`), `Breakout`, `CompetitorInsight`, `Insights`, `NewUpload`, `HookResult`, `DiscoveryResult`, `OwnChannelInfo`; functions `listCompetitors(status?: string)`, `addCompetitor(identifier: string, notes?: string)`, `updateCompetitor(id: number, patch: { status?: string; notes?: string | null })`, `removeCompetitor(id: number)` (handles the 204 with no body), `syncCompetitor(id: number)`, `syncAllCompetitors()`, `getInsights()`, `getNewUploads()`, `markSeen()`, `studyHook(youtubeVideoId: string)`, `getHook(youtubeVideoId: string)`, `discoverCompetitors()`, `getOwnChannel()`, `setOwnChannel(channelId: number)`, `setAutoDiscovery(enabled: boolean)`.
- Watchlist tab behaviour: add form (URL/@handle/ID, optional notes) with the server's message on failure; cards for `watching` competitors (thumbnail, title, subscribers or "hidden", videos stored, last synced, notes with inline edit, **Sync now**, **Remove** with a confirm); a **Sync all** button; empty state explaining how to add one; loading skeleton and `ErrorMessage` on failure; mutations invalidate `["studio","competitors"]` and `["studio","insights"]`.

- [ ] **Step 1: Implement** the client functions and the Watchlist tab following the existing component style.
- [ ] **Step 2: Typecheck and lint** — from `frontend/`: `npm run typecheck`, `npm run lint` → clean.
- [ ] **Step 3: Verify in a real browser** (temp DB via `alembic upgrade head`, backend on 8000, `npm run dev`, Chrome extension): add a real public competitor by `@handle`, press Sync now, see videos count > 0; edit notes; remove it and confirm it disappears; stop the backend and confirm an error panel, not a blank one. Remove `frontend/AGENTS.md` if Next recreates it.
- [ ] **Step 4: Commit** `git add frontend/src && git commit -m "Add Studio competitors watchlist UI"`.

### Task 12: Frontend — Candidates and discovery

**Files:**
- Create: `frontend/src/components/studio/competitors/candidates-tab.tsx`
- Modify: `frontend/src/components/studio/settings-panel.tsx` (own-channel picker card + auto-discovery toggle), `competitors-panel.tsx`

**Behaviour:** the Candidates tab lists `candidate` competitors with their `discovery_reason.text`, subscribers, appearances and **Approve** (→ `watching`, then offers Sync) / **Reject** buttons, plus **Find more competitors** (calls `discoverCompetitors`, shows `added`, `searches_used`, and a clear message for `stopped_reason == "budget"`, "SerpApi not configured" and "pick your channel first") with the current SerpApi usage beside it. Settings gains: an own-channel `select` fed by `getOwnChannel().candidates` and a labelled checkbox "Discover competitors automatically every week (uses SerpApi searches)" bound to `setAutoDiscovery`.

- [ ] **Step 1: Implement** the tab and the settings additions.
- [ ] **Step 2: Typecheck and lint** → clean.
- [ ] **Step 3: Verify in a real browser** (needs Task 1's network): choose the own channel, press **Find more** once (≤ 8 searches), see candidates with reasons, Approve one and Reject one, reload and confirm the rejected one does not come back after another **Find more** (do not run a second discovery unless searches remain; otherwise confirm via the API test).
- [ ] **Step 4: Commit** `git add frontend/src && git commit -m "Add candidates and discovery UI"`.

### Task 13: Frontend — New uploads, Insights, hooks

**Files:**
- Create: `frontend/src/components/studio/competitors/new-uploads-tab.tsx`, `insights-tab.tsx`
- Modify: `competitors-panel.tsx`

**Behaviour:** **New uploads** lists `getNewUploads()` (competitor, title linking to YouTube, views, published date, an "early signal — provisional" badge with the multiple for `early_breakout`), with **Mark all as seen**. **Insights** shows the compare-with-me card per format (owner vs watchlist median views/day, per 1 000 subscribers, "not enough data" text for `null`), the watchlist-wide patterns (title patterns, top tags, top hashtags, format split), and one card per competitor: breakouts with their multiple and publish time (or the inconclusive note), upload frequency (or "not enough uploads to judge"), and a **Study hook** button per breakout that calls `studyHook`, shows the first ~60 s of transcript text, and displays the SerpApi budget message when blocked. Every number states its sample size.

- [ ] **Step 1: Implement** both tabs.
- [ ] **Step 2: Typecheck and lint** → clean.
- [ ] **Step 3: Verify in a real browser** with a synced real competitor: Insights renders without errors, the inconclusive path shows its note for a small channel, Study hook returns text for a real breakout (1 search) and a second click costs nothing, New uploads and Mark all as seen behave.
- [ ] **Step 4: Commit** `git add frontend/src && git commit -m "Add new uploads, insights and hook study UI"`.

### Task 14: Milestone verification

**Files:** none expected.

- [ ] **Step 1:** Full backend suite `.venv/Scripts/python -m pytest -q` (from `backend/`) → all pass; `npm run typecheck`, `npm run lint`, `npm run build` (from `frontend/`) → clean, `/studio` in the route list.
- [ ] **Step 2: Public feature untouched** — `git diff --stat <milestone-A-head>..HEAD -- backend/app/api backend/app/services backend/app/analytics backend/app/db/models.py frontend/src/app/channels frontend/src/components/channel-overview.tsx frontend/src/components/channel-audit.tsx frontend/src/lib/api.ts` shows nothing.
- [ ] **Step 3: End-to-end on a temp DB** with the real keys: add 2 real competitors, sync (record Data API units used, expected ≈ 4–10 each), open Insights, study one hook, run one discovery; record SerpApi searches used (≤ 9) and confirm `GET /studio/status` matches.
- [ ] **Step 4: Report** to the owner: what shipped, units and searches spent, any inconclusive results shown for small channels, and that Milestone C (private analytics) is next, starting with its Reporting-API verification spike.
