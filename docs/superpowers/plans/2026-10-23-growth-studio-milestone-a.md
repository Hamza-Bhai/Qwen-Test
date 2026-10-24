# Growth Studio — Milestone A (foundations) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task, inline in this session (the owner forbids subagents). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the foundations the other Growth Studio milestones stand on: SerpApi settings and a budget-guarded cached client, editable niche keywords, a hashtag helper, `/api/v1/studio/*` status+keywords endpoints, and a `/studio` page shell with a working Settings tab.

**Architecture:** A new `backend/app/studio/` package (models, schemas, services, routes) wired into the app with the smallest possible edits; a new `frontend/src/app/studio/` page and `frontend/src/components/studio/` components. SerpApi usage reuses the existing `QuotaUsage` and `ApiCache` tables; the tiny key/value table `studio_setting` stores keywords (and later settings).

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic, pydantic-settings, httpx, pytest (backend, venv at `backend/.venv`, run `.venv/Scripts/python -m pytest` from `backend/`); Next.js 16, React 18, TanStack Query, Tailwind (frontend, `npm run typecheck`, `npm run lint`, `npm run build` from `frontend/`).

**Spec:** `docs/superpowers/specs/2026-10-23-growth-studio-design.md` (this plan implements its Milestone A and sections 2, 4 (budgets) and 6). Milestones B, C, D get their own plans later.

## Global Constraints

- The existing public-channel feature is not changed: no edits to its routes, tables, audit engine, tests or pages.
- Only these existing files may get a small wiring edit: `backend/app/main.py`, `backend/app/db/session.py`, `backend/app/core/config.py`, `backend/.env.example`, `frontend/src/components/site-header.tsx`. (`backend/app/jobs/scheduler.py` is not needed in this milestone.)
- New Python dependencies: none. Tests never hit the network.
- SerpApi: hard monthly cap, default 200 of the owner's 250 free searches; usage tracked in `QuotaUsage` with `service="serpapi"`; responses cached 7 days in `ApiCache` (`service="serpapi"`); the UI shows "used / cap". Env var name is the owner's existing `SERPAPIKEY`.
- Never fabricate numbers; SerpApi data is labelled estimated by later milestones. The API key never appears in logs, cached payloads, error messages or API responses.
- Route handlers are plain `def` (threadpool), like the existing routes. Windows dev machine: use forward slashes in shell commands (Git Bash).
- Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus

1. `SERPAPIKEY=` present but blank in `.env` → must count as "not configured", not as a key. (Task 1)
2. SerpApi answers HTTP 200 with an `{"error": ...}` body → raise `SerpApiError`, record **no** usage, cache nothing. (Task 4)
3. The API key leaking into a cached payload, an error message, or `/studio/status`. (Tasks 4, 6)
4. Hashtag false positives: URL fragments (`https://x.com/a#top`), `C#`, bare `#`, all-digit `#2024`. (Task 5)
5. Keyword input edges: whitespace-only, duplicates differing in case, > 60 chars, > 20 items, empty list. (Task 3)

---

### Task 1: SerpApi settings

**Files:**
- Modify: `backend/app/core/config.py` (add fields in the settings class)
- Modify: `backend/.env.example` (document the two vars)
- Test: `backend/tests/test_studio_config.py`

**Interfaces:**
- Produces: `Settings.serpapi_key: str | None` (env `SERPAPIKEY`, also accepts `SERPAPI_API_KEY`; blank string → `None`), `Settings.serpapi_monthly_cap: int = 200` (env `SERPAPI_MONTHLY_CAP`).

- [ ] **Step 1: Write failing tests** — `test_serpapi_key_read_from_serpapikey_env` (monkeypatch env `SERPAPIKEY=abc`, `get_settings.cache_clear()`, assert `get_settings().serpapi_key == "abc"`); `test_blank_serpapikey_means_not_configured` (env `SERPAPIKEY=""` → `None`); `test_monthly_cap_defaults_to_200_and_can_be_overridden` (unset → 200; `SERPAPI_MONTHLY_CAP=50` → 50).
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_studio_config.py -v` → FAIL (attribute missing).
- [ ] **Step 3: Implement** the two fields using `Field(default=None, validation_alias=AliasChoices("SERPAPIKEY", "SERPAPI_API_KEY"))` plus a `field_validator("serpapi_key")` that turns blank/whitespace into `None`. Add commented examples to `.env.example` (`# SERPAPIKEY=`, `# SERPAPI_MONTHLY_CAP=200`).
- [ ] **Step 4: Run** the same command → PASS; then run the whole suite `.venv/Scripts/python -m pytest -q` → all pass.
- [ ] **Step 5: Commit** `git add backend/app/core/config.py backend/.env.example backend/tests/test_studio_config.py && git commit -m "Add SerpApi settings for Growth Studio"`.

### Task 2: `studio_setting` table and migration

**Files:**
- Create: `backend/app/studio/__init__.py` (empty), `backend/app/studio/models.py`
- Create: `backend/alembic/versions/c9d1e2f3a4b5_add_studio_setting.py` (`down_revision = 'b8c4d0f3e6a2'`, the current head)
- Modify: `backend/app/db/session.py` (add `import app.studio.models  # noqa: F401` next to the existing models import)
- Test: `backend/tests/test_studio_migration.py`

**Interfaces:**
- Produces: `StudioSetting` ORM model — table `studio_setting`: `key: str` (String(64), primary key), `value: JSON`, `updated_at: DateTime(timezone=True)` (default and onupdate `utcnow` from `app.db.models`).

- [ ] **Step 1: Write failing tests** — `test_studio_setting_roundtrip` (insert `StudioSetting(key="k", value=["a"])` through `SessionLocal`, reload, assert value); `test_alembic_upgrade_head_creates_studio_setting` (run `[sys.executable, "-m", "alembic", "upgrade", "head"]` with `cwd=<backend dir>` and env `DATABASE_URL=sqlite:///<tmp_path>/mig.db`; assert returncode 0, then with `sqlite3` assert table `studio_setting` exists and `alembic_version` equals `c9d1e2f3a4b5`).
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_studio_migration.py -v` → FAIL.
- [ ] **Step 3: Implement** the model (own table only; do not touch existing tables), the migration (`op.create_table`, `downgrade` drops it) and the session import.
- [ ] **Step 4: Run** the tests → PASS; full suite → all pass.
- [ ] **Step 5: Commit** `git add backend/app/studio backend/alembic/versions/c9d1e2f3a4b5_add_studio_setting.py backend/app/db/session.py backend/tests/test_studio_migration.py && git commit -m "Add studio_setting table"`.

### Task 3: Niche keywords store

**Files:**
- Create: `backend/app/studio/services/__init__.py` (empty), `backend/app/studio/services/settings_store.py`
- Test: `backend/tests/test_studio_keywords.py`

**Interfaces:**
- Consumes: `StudioSetting` (Task 2).
- Produces: `DEFAULT_KEYWORDS: list[str] = ["sunday reset", "discipline", "morning routine", "self improvement"]`; `get_keywords(db: Session) -> list[str]` (defaults when no row); `set_keywords(db: Session, phrases: list[str]) -> list[str]` (stores under key `"niche_keywords"`, returns the normalised list; raises `ValueError` with a human message on invalid input).

- [ ] **Step 1: Write failing tests** — `test_defaults_when_nothing_saved`; `test_set_normalises` (`["  Sunday   RESET ", "sunday reset", "Discipline"]` → `["sunday reset", "discipline"]`, then `get_keywords` returns the same); `test_blank_entries_dropped` (`["", "   ", "x"]` → `["x"]`); `test_rejects_empty_result` (`[]` and `["  "]` raise `ValueError`); `test_rejects_phrase_over_60_chars`; `test_rejects_more_than_20_phrases`; `test_rejects_non_string_items` (`[1]` raises `ValueError`); `test_saved_empty_never_falls_back_to_defaults_silently` (a rejected save leaves the previous value intact).
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_studio_keywords.py -v` → FAIL.
- [ ] **Step 3: Implement** both functions: normalise = strip, collapse inner whitespace, lowercase, drop empties, de-duplicate preserving first position; validate after normalising (≥1, ≤20 phrases, each ≤60 chars).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `git add backend/app/studio/services backend/tests/test_studio_keywords.py && git commit -m "Add editable niche keywords store"`.

### Task 4: SerpApi client with budget guard and cache

**Files:**
- Create: `backend/app/studio/services/serpapi.py`
- Test: `backend/tests/test_studio_serpapi.py`

**Interfaces:**
- Consumes: `get_settings().serpapi_key` / `.serpapi_monthly_cap` (Task 1); existing `ResponseCache(db, service=...)` (`get/set`), `QuotaTracker(db, service="serpapi").record(endpoint, units)`, `QuotaUsage` model.
- Produces:
  - `class SerpApiError(RuntimeError)`, `class SerpApiNotConfigured(SerpApiError)`, `class SerpApiBudgetExceeded(SerpApiError)`.
  - `@dataclass(frozen=True) class SerpApiUsage: month: str  # "YYYY-MM"; used: int; cap: int; remaining: int` (property-free; `remaining = max(cap - used, 0)`).
  - `class SerpApiClient(db: Session, *, http: httpx.Client | None = None)` with `usage() -> SerpApiUsage` and `get(engine: str, params: dict[str, str | int], *, cache_days: int = 7) -> dict`.
- Behaviour pinned by the spec: `usage()` sums `QuotaUsage.units_used` for `service="serpapi"` over the current calendar month only (day column = Pacific date from `QuotaTracker`); `get` order is not-configured check → cache lookup (hit: return payload, no HTTP, no usage) → budget check (`used >= cap` raises `SerpApiBudgetExceeded`) → `GET https://serpapi.com/search.json` with `engine`, params, `api_key`, 30 s timeout → on success record exactly 1 unit and cache for `cache_days`. Cache key is `f"{engine}?{sha256(sorted params json).hexdigest()}"` (≤ 255 chars). Payload `search_parameters.api_key` is removed before returning/caching.

- [ ] **Step 1: Write failing tests** using a `FakeHttp` class (records calls; `get(url, params=None, timeout=None)` returns an object with `status_code` and `json()`):
  - `test_not_configured_raises_before_any_http` (key unset → `SerpApiNotConfigured`, zero calls).
  - `test_success_returns_payload_records_one_unit_and_caches` (second identical `get` makes no HTTP call and does not change usage; `usage().used == 1`).
  - `test_different_params_are_different_cache_entries`.
  - `test_http_200_with_error_body_is_not_charged_or_cached` (`{"error": "Google hasn't returned any results"}` → `SerpApiError`; `usage().used == 0`; same call again hits HTTP again).
  - `test_http_error_status_raises_and_is_not_charged` (status 401, body `{"error": "Invalid API key"}`; error text contains "Invalid API key" and not the key).
  - `test_network_failure_raises_serpapierror_not_charged` (fake raises `httpx.ConnectError`).
  - `test_budget_exceeded_blocks_without_http` (cap 2 via env, two successful distinct calls, third raises `SerpApiBudgetExceeded`, no third HTTP call; a cached repeat of call 1 still works).
  - `test_usage_counts_only_current_month` (insert `QuotaUsage(service="serpapi", day=<first day of previous month>, units_used=50)` and one for today with 3 → `used == 3`).
  - `test_api_key_never_cached_or_returned` (fake payload contains `search_parameters: {"api_key": "SECRET"}` → returned dict has no `api_key`; no `ApiCache.response_json` row contains `"SECRET"` when serialised).
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_studio_serpapi.py -v` → FAIL (module missing).
- [ ] **Step 3: Implement** the module per the pinned behaviour. Error messages are built from the response's `error` text only, never from request params. Do not log params.
- [ ] **Step 4: Run** → PASS; full suite → all pass.
- [ ] **Step 5: Commit** `git add backend/app/studio/services/serpapi.py backend/tests/test_studio_serpapi.py && git commit -m "Add budget-guarded cached SerpApi client"`.

### Task 5: Hashtag extraction helper

**Files:**
- Create: `backend/app/studio/text.py`
- Test: `backend/tests/test_studio_text.py`

**Interfaces:**
- Produces: `extract_hashtags(*texts: str | None) -> list[str]` — lowercase tags without the `#`, de-duplicated in first-seen order across all inputs.

- [ ] **Step 1: Write failing tests** — `test_basic` (`"Sunday reset #SundayReset #discipline"` → `["sundayreset", "discipline"]`); `test_multiple_texts_dedupe_in_order` (title + description, repeated tag appears once); `test_none_and_empty_inputs` (`extract_hashtags(None, "")` → `[]`); `test_ignores_url_fragments` (`"https://x.com/a#top"` → `[]`); `test_ignores_c_sharp_and_bare_hash` (`"I code in C# and # alone"` → `[]`); `test_ignores_all_digit_tags` (`"#2024 #2024goals"` → `["2024goals"]`); `test_trailing_punctuation_stripped` (`"#focus, #reset!"` → `["focus", "reset"]`); `test_unicode_tags` (`"#سکون"` → `["سکون"]`); `test_underscore_allowed` (`"#morning_routine"` → `["morning_routine"]`).
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_studio_text.py -v` → FAIL.
- [ ] **Step 3: Implement** with one compiled regex: a `#` that is at the start or follows whitespace, followed by word characters (`\w+`, unicode); reject matches that are digits only.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `git add backend/app/studio/text.py backend/tests/test_studio_text.py && git commit -m "Add hashtag extraction helper"`.

### Task 6: Studio status and keywords API

**Files:**
- Create: `backend/app/studio/schemas.py`, `backend/app/studio/routes/__init__.py` (empty), `backend/app/studio/routes/settings.py`
- Modify: `backend/app/main.py` (import the router and `app.include_router(studio_settings.router, prefix=API_PREFIX)` after the existing includes)
- Test: `backend/tests/test_studio_api.py` (defines its own `client` fixture exactly like `tests/test_channels_api.py`: `init_db()` then `with TestClient(app)`)

**Interfaces:**
- Consumes: `SerpApiClient.usage()` (Task 4), `get_keywords` / `set_keywords` (Task 3), `get_settings().serpapi_key`.
- Produces (router prefix `/studio`):
  - `GET /api/v1/studio/status` → `{"serpapi": {"configured": bool, "month": "YYYY-MM", "used": int, "cap": int, "remaining": int}, "keywords": [str]}`.
  - `GET /api/v1/studio/keywords` → `{"keywords": [str]}`.
  - `PUT /api/v1/studio/keywords` body `{"keywords": [str]}` → `{"keywords": [str]}` normalised; invalid input → 422 with `detail` = the `ValueError` message.
  - Pydantic models: `SerpApiStatus`, `StudioStatus`, `KeywordsPayload`.

- [ ] **Step 1: Write failing tests** — `test_status_without_key` (`configured` false, `used` 0, `cap` 200, default keywords); `test_status_with_key_never_exposes_it` (env `SERPAPIKEY=SECRETVALUE`; `configured` true; `"SECRETVALUE"` not in `response.text`); `test_status_reflects_cap_env`; `test_put_keywords_normalises_and_persists` (PUT then GET returns normalised list); `test_put_invalid_keywords_422_and_unchanged` (`{"keywords": []}` → 422; GET still returns previous list); `test_put_rejects_non_list_422`; `test_public_channel_routes_still_work` (`GET /api/v1/channels` → 200).
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_studio_api.py -v` → FAIL (404s).
- [ ] **Step 3: Implement** schemas, router and the `main.py` include.
- [ ] **Step 4: Run** → PASS; full suite → all pass.
- [ ] **Step 5: Commit** `git add backend/app/studio backend/app/main.py backend/tests/test_studio_api.py && git commit -m "Add studio status and keywords endpoints"`.

### Task 7: Studio page shell and Settings tab

**Files:**
- Create: `frontend/src/lib/studio-api.ts`, `frontend/src/components/studio/settings-panel.tsx`, `frontend/src/components/studio/coming-soon.tsx`, `frontend/src/app/studio/page.tsx`
- Modify: `frontend/src/components/site-header.tsx` (one `Link` to `/studio`, same style as the "Analyze" link, with a `Sparkles` icon and label "Studio")

**Interfaces:**
- Consumes: `API_BASE_URL`, `ApiError` from `@/lib/api` (import only — do not edit `api.ts`); `Tabs` from `@/components/ui/tabs`; `ErrorMessage`, `Card`, `Button`, `Input`, `Badge`, `Skeleton` existing components.
- Produces in `studio-api.ts`: types `StudioStatus` (`serpapi: {configured, month, used, cap, remaining}`, `keywords: string[]`); functions `getStudioStatus(): Promise<StudioStatus>`, `saveKeywords(keywords: string[]): Promise<string[]>`. They call `fetch` on `${API_BASE_URL}/studio/...`, and turn non-2xx responses into `ApiError` using the FastAPI `detail` string (network failure → `ApiError` with status 0 and the same "Cannot reach the backend" wording as `api.ts`).
- `StudioPage` (client component) renders a heading and `<Tabs urlParam="tab" defaultId="settings" tabs=[...]>` with tabs `dashboard` "Dashboard", `ideas` "Ideas & Plan", `my-channel` "My channel", `competitors` "Competitors" (each `<ComingSoon milestone="…" />` naming the milestone that delivers it: Dashboard/Ideas → D, My channel → C, Competitors → B) and `settings` "Settings" (`<SettingsPanel />`).
- `SettingsPanel`: react-query `["studio","status"]`; shows loading skeleton, an `ErrorMessage` on failure (backend down must not render blank), a SerpApi card ("not configured — add `SERPAPIKEY` to `backend/.env`" or "`used` / `cap` searches this month" with a simple progress bar), and a keywords editor (removable badges, an input + "Add" button that also submits on Enter, "Save" disabled when unchanged or empty, success/error feedback; Save calls `saveKeywords` then invalidates the status query).

- [ ] **Step 1: Implement** the files above following the existing component style (Tailwind, `"use client"`, dark-mode tokens as in `channel-overview.tsx`).
- [ ] **Step 2: Typecheck and lint** — from `frontend/`: `npm run typecheck` and `npm run lint` → no errors.
- [ ] **Step 3: Build** — `npm run build` → success and `/studio` appears in the route list.
- [ ] **Step 4: Verify in a real browser** with the backend running (`.venv/Scripts/python -m uvicorn app.main:app --port 8000` from `backend/`) and `npm run dev`: open `http://localhost:3000/studio` in headless Chrome (`chrome --headless=new --disable-gpu --virtual-time-budget=15000 --dump-dom <url>`) and confirm the DOM contains the five tab labels, "Settings" selected, the four default keywords, and the SerpApi usage line; open `?tab=competitors` and confirm "Coming in milestone B" appears; stop the backend and confirm the Settings tab shows the error message instead of a blank panel.
- [ ] **Step 5: Commit** `git add frontend/src/lib/studio-api.ts frontend/src/components/studio frontend/src/app/studio frontend/src/components/site-header.tsx && git commit -m "Add Studio page shell with Settings tab"`.

### Task 8: Milestone verification and snapshots finding

**Files:**
- Modify: none expected (report only).

- [ ] **Step 1: Run** the full backend suite `.venv/Scripts/python -m pytest -q` (from `backend/`) → all pass; run `npm run typecheck` and `npm run lint` (from `frontend/`) → clean.
- [ ] **Step 2: Confirm the public feature is untouched** — `git diff --stat main -- backend/app/api backend/app/services backend/app/analytics frontend/src/app/channels frontend/src/components/channel-overview.tsx frontend/src/components/channel-audit.tsx` shows no changes from this milestone (edits to those paths in earlier commits of this branch, e.g. tabs, are expected; this milestone's commits touch none of them).
- [ ] **Step 3: Snapshots finding** — no code change. Diagnosis already made while planning: the owner's database has 0 `video_snapshots` because the last sync (2026-10-23 05:39 UTC) ran before the commit that stores snapshots (`d159b61`, 06:34 UTC); the existing `TestVideoSnapshots` tests cover the behaviour. Verify: with the backend restarted, press **Re-sync** on the owner's channel in the UI, then `python -I -c "import sqlite3;print(sqlite3.connect('file:backend/yt_growth.db?mode=ro',uri=True).execute('select count(*) from video_snapshots').fetchone())"` → count equals the number of synced videos. If it is still 0 after a re-sync with a restarted backend, stop and report — that would be a real bug in the public sync path, which is outside this milestone and needs the owner's go-ahead.
- [ ] **Step 4: Report** to the owner: what shipped, the snapshot result, and that Milestone B's plan is next.
