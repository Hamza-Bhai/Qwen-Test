# Growth Studio — Milestone D (advisor, ideas, planner) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task, inline in this session (the owner forbids subagents). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each day the owner opens the Studio, picks a connected channel and sees (1) answers to the six questions with evidence and confidence, (2) 3–5 fresh video ideas that don't repeat their past titles, (3) a planner to approve/schedule/mark published, and (4) at day 7 and 28 whether each published idea worked, aggregated into "what has worked for me".

**Architecture:** A deterministic evidence pack (`app/studio/evidence.py`, every item has an id) is built from Milestone B/C data plus cached SerpApi Trends/autocomplete. An OpenAI-compatible client (`services/ai_client.py`, `httpx`, daily cap) sends only the pack; `advisor.py` and `ideas.py` validate the JSON reply with pydantic and reject any unknown evidence id. Results are cached per evidence hash in `studio_advisor_report`; ideas live in `studio_idea`, outcomes in `studio_idea_outcome`. Routes under `/api/v1/studio/advisor/*` and `/api/v1/studio/ideas/*`; Dashboard and Ideas & Plan tabs replace their `ComingSoon` placeholders.

**Tech Stack:** as Milestones A–C.

**Spec:** `docs/superpowers/specs/2026-10-23-growth-studio-design.md` (Milestone D; sections 2, 3, 4, 6; "Calls I made" incl. the 2026-10-24 lines). Depends on main at `6b53284` (alembic head `e3f4a5b6c7d8`).

## Global Constraints

- Public feature untouched; wiring edits only in `app/main.py`, `app/jobs/scheduler.py`, `app/core/config.py`, `.env.example`.
- AI receives **only** the evidence pack (no tokens, no raw DB rows). Every claim/idea must cite ≥ 1 evidence id from the pack; an unknown id rejects the whole reply (nothing stored, clear error). AI calls capped per day: `ai_daily_cap: int = 3` (`QuotaTracker(db, service="ai")`); identical evidence (same SHA-256 of the canonical pack JSON) on the same day reuses the stored result without a call.
- No AI key (`ai_api_key` None) → advice/ideas endpoints return 409 "Add AI_API_KEY to backend/.env"; the Dashboard still shows findings and evidence.
- Never fabricate: numbers in the UI come from the pack/DB, not from the model; upload time only from the `publish_timing` finding of the idea's format (status `good`), else "no reliable data".
- Duplicate guard: tokens = `_topic_tokens(title without #hashtags)`; an idea is dropped when overlap `|A∩B| / |A|` ≥ 0.6 between its (topic + first title) tokens and any of the channel's own titles or any earlier idea of that channel.
- SerpApi: Trends (1 search) + autocomplete (≤ 4 seeds) per evidence build, all through `SerpApiClient` (cache 7 days, monthly cap). SerpApi failure/cap → those evidence sections are empty with a note; never fails the pack.
- Outcomes: views/day of the published video at day ≥ 7 and ≥ 28 vs the stored baseline (owner's median views/day for that format at publish time): worked ≥ 1.25×, didn't ≤ 0.8×, else neutral; caveat "single video — noisy" always shown; "what has worked" groups need ≥ 3 outcomes.
- Handlers plain `def`; tests never hit the network; commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. Model returns invalid JSON, wrong shape, extra/unknown evidence ids, 4 answers instead of 6, or titles outside 3–5: rejected with a readable error, counted against the cap, nothing stored. (Tasks 4, 5, 6)
2. AI provider down / 401 / 429 / timeout: 502 with the provider message, cap not consumed on network failure. (Task 4)
3. Channel with no competitors, no reach, SerpApi off: pack still builds with empty sections and notes; ideas still possible from own data. (Tasks 1, 3)
4. Marking a published video that doesn't belong to the channel, or twice; outcome before day 7; video without totals yet: clear 422 / "pending", never a fake verdict. (Task 7)
5. Two "generate" clicks at once or the daily job overlapping a click: one AI call, not two (per-channel lock, same pattern as the pull lock). (Tasks 5, 6, 8)

---

### Task 1: Trends and autocomplete (market signals)

**Files:** Create `backend/app/studio/services/market.py`, `backend/tests/fixtures/serpapi_google_trends.json`, `backend/tests/fixtures/serpapi_autocomplete.json`; Test `backend/tests/test_studio_market.py`.

**Interfaces — Produces:**
- `trend_directions(client: SerpApiClient, keywords: list[str]) -> dict` → `{"items": [{"keyword", "direction": "rising"|"falling"|"flat", "last_4w": float, "prev_4w": float}], "note": str | None}`; one `google_trends` search (`q` = up to 5 keywords comma-joined, `gprop=youtube`, `data_type=TIMESERIES`, `date="today 3-m"`); direction by mean of last 4 points vs the 4 before: ≥ +15 % rising, ≤ −15 % falling.
- `autocomplete(client, keywords: list[str], *, max_seeds: int = 4) -> dict` → `{"items": [{"seed", "suggestions": [str, ...≤ 8]}], "note"}`; `google_autocomplete` with `client=youtube`.
- Any `SerpApiError` → empty items and `note` = its message.

- [ ] **Step 1:** Capture real fixtures once (2 searches, key never printed): trends for the owner's 4 seed keywords, autocomplete for "sunday reset"; strip `api_key`; trim. Ledger the real field paths (`interest_over_time.timeline_data[*].values[*].extracted_value`, `suggestions[*].value` — rule if they differ).
- [ ] **Step 2: Failing tests** — `test_trend_direction_from_fixture`, `test_trend_flat_when_change_small`, `test_autocomplete_suggestions_capped`, `test_serpapi_error_gives_empty_items_and_note`, `test_autocomplete_uses_at_most_4_seeds`.
- [ ] **Steps 3–5:** run → FAIL, implement, run → PASS. **Commit** "Add Trends and autocomplete signals".

### Task 2: Tables, migration, settings

**Files:** Modify `app/studio/models.py`, `app/core/config.py`, `.env.example`, pin the C head test (`tests/test_studio_my_channel_models.py` → `upgrade e3f4a5b6c7d8`); Create `alembic/versions/f4a5b6c7d8e9_add_advisor_tables.py`; Test `tests/test_studio_advisor_models.py`.

**Interfaces — Produces (connection FK cascade as in C):**
- `StudioAdvisorReport` (`studio_advisor_report`): `connection_id`; `kind` String(16) (`advice | ideas`); `evidence_hash` String(64) index; `created_at`; `payload` JSON; `model` String(100).
- `StudioIdea` (`studio_idea`): `connection_id`; `report_id` FK null; `created_at`; `status` String(16) (`new | saved | approved | rejected | scheduled | published`) index; `format` String(8) (`short | long`); `topic` String(255); `payload` JSON (titles, hook, outline, duration_minutes, description, hashtags, tags, competitor_refs, evidence_ids, reason, confidence, priority, upload_time); `planned_date` Date null; `published_video_id` String(32) null; `published_at` tz null; `baseline` JSON null.
- `StudioIdeaOutcome` (`studio_idea_outcome`): `idea_id` FK cascade; `checkpoint` Integer (7 | 28); unique (`idea_id`,`checkpoint`); `measured_at`; `views_per_day` Float; `baseline_vpd` Float; `ratio` Float; `verdict` String(10).
- Settings: `ai_daily_cap: int = 3`; `ai_api_key` blank → None (add to the blank validator).

- [ ] Failing tests (roundtrip, cascade from connection and from idea, outcome unique per checkpoint, alembic head = `f4a5b6c7d8e9`, safe after create_all, blank AI key is None) → FAIL → implement → PASS. **Commit** "Add advisor, idea and outcome tables".

### Task 3: Evidence pack

**Files:** Create `app/studio/evidence.py`; Test `tests/test_studio_evidence.py`.

**Interfaces:**
- Consumes: `my_channel.get_overview` data sources (reuse its DB reads by calling `get_overview(db, connection, today)`), `insights.build_insights(db, now)`, `market.trend_directions/autocomplete`, `settings_store.get_keywords`, `StudioMyVideo` titles, earlier `StudioIdea` topics.
- Produces: `build_evidence(db, connection, *, serpapi: SerpApiClient | None, today: date) -> dict` →
  `{"channel": {"title", "format_mix": {"short": n, "long": n}}, "items": [{"id", "kind", "text", "data"}], "own_titles": [str], "earlier_ideas": [str], "notes": [str]}` with ids: `F:<finding code>` (non-inconclusive findings + inconclusive ones as context), `P:<metric>` progress lines, `TOP:<video_id>` / `LOW:<video_id>` (top/bottom 5 own videos by views/day ≥ 7 days old, per format), `B:<video_id>` competitor breakouts (≤ 10, highest multiple), `GAP:<token>` topic gaps (token in ≥ 2 competitor breakout titles and in none of the own titles, hashtags removed), `TR:<keyword>`, `AC:<seed>`. Items sorted by id; `evidence_hash(pack) -> str` = SHA-256 of `json.dumps(pack["items"], sort_keys=True)`.
- [ ] Failing tests — `test_pack_ids_unique_and_prefixed`, `test_gap_tokens_exclude_own_titles_and_hashtags`, `test_top_low_per_format`, `test_pack_without_competitors_or_serpapi_has_notes`, `test_hash_stable_and_changes_with_items` → FAIL → implement → PASS. **Commit** "Add evidence pack".

### Task 4: AI client

**Files:** Create `app/studio/services/ai_client.py`; Test `tests/test_studio_ai_client.py`.

**Interfaces — Produces:** `AINotConfigured`, `AIBudgetExceeded`, `AIError(status, message)`; `class AIClient(db, *, http=None)`: `complete_json(system: str, user: str) -> tuple[dict, str]` (parsed JSON, model name) — POST `{ai_base_url}/chat/completions`, `model=ai_model`, `temperature=0.4`, `response_format={"type": "json_object"}`, timeout 120 s; checks cap before calling; records 1 unit only when the provider answered (any HTTP status from the provider); non-JSON content → `AIError(502, "The model did not return JSON.")`; `used_today() -> int`, `cap -> int`.
- [ ] Failing tests — `test_not_configured`, `test_sends_json_mode_and_model`, `test_cap_blocks_fourth_call`, `test_network_error_not_counted`, `test_provider_401_is_error_and_counted`, `test_non_json_content_rejected` → FAIL → implement → PASS. **Commit** "Add OpenAI-compatible AI client".

### Task 5: Advisor (six questions)

**Files:** Create `app/studio/services/advisor.py`; Test `tests/test_studio_advisor.py`.

**Interfaces — Produces:** `QUESTIONS = ["why_not_growing", "mistakes", "how_to_fix", "competitors_better", "topics_next", "focus_this_week"]` (with English labels); `generate_advice(db, connection, ai: AIClient, pack: dict, *, force: bool = False) -> StudioAdvisorReport` — reuse today's report with the same hash unless `force`; validates `{"answers": [{"question": <one of QUESTIONS>, "answer": str ≤ 1200 chars, "evidence_ids": [≥1 known id], "confidence": "high"|"medium"|"low"}]}` with exactly one answer per question; rejection → `AdviceRejected(ValueError)` naming the problem; per-channel lock → `AdviceInProgress`. `latest_advice(db, connection) -> StudioAdvisorReport | None`. System prompt states: answer only from the pack, cite ids, no invented numbers, say "not enough data" when the pack lacks it, compare formats separately.
- [ ] Failing tests — `test_valid_reply_stored`, `test_unknown_evidence_id_rejected`, `test_missing_question_rejected`, `test_same_hash_same_day_reuses_without_call`, `test_force_calls_again`, `test_prompt_contains_only_pack` → FAIL → implement → PASS. **Commit** "Add advisor answers".

### Task 6: Daily ideas with duplicate guard

**Files:** Create `app/studio/services/ideas.py`; Test `tests/test_studio_ideas.py`.

**Interfaces — Produces:** `overlap(a: str, b: str) -> float`; `is_duplicate(idea_text: str, existing: list[str]) -> bool` (≥ 0.6); `generate_ideas(db, connection, ai, pack, *, force=False) -> list[StudioIdea]` — asks for 5 ideas; validates each `{"topic", "reason", "titles": 3–5 str, "hook", "outline": 3–8 str, "duration_minutes": number > 0, "format": "short"|"long", "description", "hashtags": ≤ 8, "tags": ≤ 15, "competitor_refs": [B:… ids], "evidence_ids": [≥1], "confidence", "priority": 1–5}`; unknown id anywhere → `IdeasRejected`; drops duplicates (counts them in the report payload `skipped_duplicates`); fills `upload_time` from the pack's `F:publish_timing` when `good` and the format matches, else `"no reliable data"`; stores a report + `StudioIdea(status="new")` rows; same-hash-same-day reuse and lock as Task 5.
- [ ] Failing tests — `test_overlap_ignores_hashtags_and_stopwords`, `test_duplicate_of_own_title_skipped`, `test_duplicate_of_earlier_idea_skipped`, `test_titles_count_validated`, `test_competitor_ref_must_be_breakout_id`, `test_upload_time_only_from_good_finding_same_format`, `test_ideas_stored_with_status_new` → FAIL → implement → PASS. **Commit** "Add daily ideas with duplicate guard".

### Task 7: Planner and outcomes

**Files:** Create `app/studio/services/planner.py`; Test `tests/test_studio_planner.py`.

**Interfaces — Produces:**
- `set_status(db, idea, status)` (allowed: new→saved/approved/rejected; saved→approved/rejected; approved→scheduled/rejected; scheduled→approved; any non-published → rejected); `schedule(db, idea, day: date)` (status `scheduled`); `mark_published(db, idea, youtube_video_id, *, today)` — video must be a `StudioMyVideo` of the idea's channel (else `ValueError`), not already linked to another idea; stores `published_at` from the video and `baseline = {"format", "median_vpd", "videos"}` (own median views/day of that format, videos ≥ 7 days old, from latest totals; `median_vpd` None if < 5 videos).
- `update_outcomes(db, connection, *, today) -> int` — for published ideas at age ≥ 7 / ≥ 28 days without that checkpoint and with totals: vpd = lifetime views / age; verdict by the constraint thresholds; baseline None → verdict `"no_baseline"`.
- `what_worked(db, connection) -> dict` — groups by `format`, first topic token, title pattern (`number` / `question` / `plain` of the first title); each `{group, worked, neutral, didnt, n}`, groups with n < 3 listed under `"too_few"`.
- [ ] Failing tests — `test_status_transitions`, `test_schedule_sets_date`, `test_mark_published_rejects_foreign_video`, `test_mark_published_twice_rejected`, `test_baseline_per_format`, `test_outcome_waits_for_day_7`, `test_outcome_verdicts`, `test_no_baseline_verdict`, `test_what_worked_groups_and_too_few` → FAIL → implement → PASS. **Commit** "Add planner and outcome tracking".

### Task 8: Routes and daily job

**Files:** Create `app/studio/routes/advisor.py`; Modify `app/main.py`, `app/studio/jobs.py`, `app/jobs/scheduler.py`; Test `tests/test_studio_advisor_api.py`, extend `tests/test_studio_jobs.py`.

**Interfaces:**
- `GET /studio/advisor/{connection_id}` → `{"ai": {"configured", "used_today", "cap"}, "advice": report payload + created_at | null, "findings": [...], "evidence": pack items}`; `POST /studio/advisor/{connection_id}/generate?force=false` (409 no key / busy, 429 cap, 422 rejected, 502 provider).
- `GET /studio/ideas/{connection_id}?status=` → ideas newest first; `POST /studio/ideas/{connection_id}/generate`; `PATCH /studio/ideas/item/{idea_id}` body `{"status"?, "planned_date"?}`; `POST /studio/ideas/item/{idea_id}/published` body `{"youtube_video_id"}`; `GET /studio/ideas/{connection_id}/what-worked`.
- Job `ADVISOR_JOB = "studio_advisor_daily"`: per connected channel → `update_outcomes`; if AI configured and no ideas report today → build pack, advice + ideas (each failure recorded, others continue). Scheduled every 24 h with catch-up, registered after the my-channels job.
- [ ] Failing tests — endpoint shapes; 409 without key; 422 on rejected reply (patched AI); 429 at cap; PATCH invalid transition 422; published foreign video 422; job skips AI without key but still updates outcomes → FAIL → implement → PASS (file + full suite). **Commit** "Add advisor and ideas API and daily job".

### Task 9: Dashboard tab

**Files:** Create `frontend/src/components/studio/dashboard/dashboard-panel.tsx`; reuse the channel switcher (extract `ChannelSwitcher` from `my-channel-panel.tsx` into `frontend/src/components/studio/channel-switcher.tsx`); Modify `frontend/src/lib/studio-api.ts`, `frontend/src/app/studio/page.tsx`.
**Behaviour:** six answer cards (question label, answer, confidence badge, evidence chips that expand to the item text), "Generate advice" / "Refresh" (force) with AI usage `used/cap`; without key a clear card explaining `AI_API_KEY`; below, the rule-based findings (`FindingsList`).
- [ ] Implement; `npm run typecheck && npm run lint && npm run build` clean. **Commit** "Add Studio dashboard".

### Task 10: Ideas & Plan tab

**Files:** Create `frontend/src/components/studio/ideas/ideas-panel.tsx`, `idea-card.tsx`, `planner-week.tsx`, `what-worked.tsx`; Modify `studio-api.ts`, `page.tsx`.
**Behaviour:** "Generate ideas"; idea cards (topic, reason, titles, hook, outline, format/duration, description, hashtags/tags with copy buttons, competitor refs linking to YouTube, upload time or "no reliable data", confidence/priority) with Save / Approve / Reject / Schedule (date input); a 7-day planner strip of scheduled ideas; "Mark published" choosing from the channel's own videos (newest 20); outcomes badges (pending / worked / neutral / didn't + caveat); "What has worked for me" table.
- [ ] Implement; frontend checks clean. **Commit** "Add Ideas and Plan tab".

### Task 11: Real end-to-end check

- [ ] On a DB copy (port 8001): build the evidence pack for AXIOM and print item counts per kind (2 SerpApi searches at most, cached afterwards).
- [ ] If `AI_API_KEY` is set: generate advice and ideas once each (≤ 2 AI calls), check every cited id exists and duplicates were skipped. If not set: verify the 409 path and the Dashboard's no-key state.
- [ ] Full backend suite + frontend checks; report.
