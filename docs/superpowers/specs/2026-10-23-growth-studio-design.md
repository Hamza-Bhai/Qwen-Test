# Growth Studio — design

Personal tool, one user (the owner of the AXIOM MINDSET channel). Not a SaaS: no multi-user, no
accounts, no billing, no new infrastructure.

## 1. Intent

The owner's channel is not growing and they don't know why. The existing app shows *public* numbers
and a rule-based audit. Growth Studio adds the three things that actually change what they publish:

1. **Competitors** — who in the niche is winning with small channels, with which topics/titles/formats.
2. **Their own private analytics** — CTR, retention, traffic sources, subscriber conversion — the real
   "why" behind views.
3. **An advisor** that turns (1) and (2) into a short list of mistakes, fixes, and daily video ideas,
   then tracks whether the advice worked.

Success = each day the owner opens one page and knows *what to make next and why*, with evidence they
can check, and later sees whether last week's advice moved the numbers.

Niche (confirmed by owner): calm / self-improvement — "Sunday reset", discipline, morning routine
style content. Seed keywords are editable in the app.

## 2. Hard constraints

- **The existing public-channel feature is not changed.** Its routes, tables, pages, audit engine and
  tests stay as they are. Growth Studio is a separate area (`/studio`, `/api/v1/studio/*`,
  `studio_*` tables, `app/studio/` package, `src/app/studio/` pages).
- Reuse by **importing** existing code (YouTube client, URL resolver, quota tracker, cache, parsing
  helpers, `channel_service` sync for the owner's own channel), not by editing it.
- Files outside the new packages that may receive a small wiring edit, and nothing else:
  `app/main.py` (register router), `app/db/session.py` (import studio models), `app/jobs/scheduler.py`
  (register the daily job), `app/core/config.py` (new settings), `backend/.env.example`,
  `frontend/src/components/site-header.tsx` (one nav link). A new alembic migration adds the tables.
- Never fabricate. Competitor CTR / retention / impressions do not exist publicly and are never shown
  or estimated. SerpApi numbers (rounded subscribers, relative dates) are labelled "estimated" and are
  replaced by official YouTube Data API values as soon as a channel is enriched.
- Fair comparisons only: age-normalised (views/day on videos ≥ 7 days old), per format
  (Shorts vs long-form), and relative to the channel's own median or size (views per subscriber) —
  never raw views across different-sized channels.
- Small samples produce "inconclusive", the same convention the existing audit uses.
- No Redis/Celery/queues/containers. SQLite stays; in-process scheduler stays.

## 3. Goals / non-goals / anti-goals

**Goals:** everything in section 5. **Non-goals:** SaaS features, login for the app itself, a public
deployment, auto-posting to YouTube, thumbnail generation, competitor private metrics, changing the
public channel pages. **Anti-goals (failure even if it "works"):** generic advice ("post consistently"),
invented numbers, a dashboard that looks rich but recommends nothing, silently burning the SerpApi or
YouTube quota, ideas that repeat what the owner already published.

## 4. External services and what they can really do

| Need | Source | Cost |
|---|---|---|
| Competitor videos, tags, descriptions, stats, dates | YouTube Data API v3 (existing client) | 1 unit/call |
| Competitor **discovery** (keyword search, related channels) | SerpApi `youtube`, `youtube_video` (related videos) | 1 search each |
| Trend interest for niche keywords | SerpApi `google_trends` with `gprop=youtube` | 1 search |
| Title/keyword ideas from real searches | SerpApi `google_autocomplete`, `client=youtube` | 1 search |
| Competitor hook / outline | SerpApi `youtube_video_transcript` (10-second segments) | 1 search |
| Owner: views, watch time, avg view duration/percentage, subs gained/lost, traffic sources, retention curve, top videos | YouTube Analytics API (`yt-analytics.readonly`) | separate quota |
| Owner: **impressions and CTR** | YouTube **Reporting API** bulk reports `channel_reach_basic_a1` (per date/video) — *not* the Analytics API | separate quota |

Verified on 2026-10-23: SerpApi autocomplete (`client=youtube`), transcript and YouTube-scoped Trends
all return data on the owner's key (Free plan, 250 searches/month). Reach reports were added to the
Reporting API in Jan 2026; whether the owner's channel can use them is **not confirmed** — milestone C
starts with a verification step. Fallback if unavailable: CTR/impressions are simply not shown (an
import of the YouTube Studio CSV export is a possible later add-on, not part of this scope).
Reporting API reports are daily files generated after a job is created, so CTR history starts
shortly after connecting (Google provides some backfill; to be confirmed in the spike).

Budgets: SerpApi hard monthly cap (default 200 of the 250 free), tracked with the existing
`QuotaUsage` table (`service="serpapi"`), responses cached 7 days in `ApiCache`; the UI shows
"used / cap". When the cap is hit, SerpApi features pause with a clear message; nothing else breaks.
YouTube Data API units are tracked by the existing tracker (competitor sync ≈ 2–3 units per channel).
AI calls are capped per day (default 3) and cached by evidence hash.

## 5. Features

### Milestone A — foundations (small)
- Settings: `SERPAPIKEY` (the owner's existing env name), `SERPAPI_MONTHLY_CAP`, `STUDIO_NICHE_KEYWORDS`
  default seeded: sunday reset, discipline, morning routine, self improvement (editable in UI, stored in DB).
- SerpApi client service: budget guard, cache, typed errors, `json_restrictor` to keep payloads small.
- Hashtag extraction (`#word` from title/description) as a studio helper (the public audit is untouched).
- Diagnose why `video_snapshots` is empty for the owner's channel (sync is supposed to store them) —
  report the cause to the owner; fix only if it is a bug in code that studio depends on and the owner
  agrees, because that code belongs to the public feature.
- Studio shell: nav link, `/studio` page with tabs: Dashboard · Ideas & Plan · My channel · Competitors · Settings.

### Milestone B — competitors (public data only)
- **Watchlist**: add by URL/@handle/ID (existing URL resolver), remove, notes; statuses
  `candidate → watching → rejected`. Rejected channels are never suggested again.
- **Discovery** (weekly job + "Find more" button, ≈5–8 searches per run):
  1. SerpApi `youtube` search per seed keyword → channels and the channels behind result videos.
  2. SerpApi `youtube_video` on the owner's top 3 videos → channels that appear repeatedly in related videos.
  3. Enrich candidates with Data API `channels.list` (exact subscribers, country, video count).
  4. Rank: prefer small and growing — subscribers between 0.2× and 20× the owner's, recent-video views
     per subscriber high, active in the last 30 days. Show **why** each was suggested.
  The owner approves/rejects; nothing is added to the watchlist automatically.
- **Sync** (daily, only `watching` channels): uploads playlist + `videos.list`; store title, description,
  tags, hashtags, duration, Shorts flag, publish time, stats; keep a stats snapshot each sync for velocity.
- **Benchmarks & patterns** per competitor and for the whole watchlist:
  breakout videos (views/day ≥ 3× that channel's own same-format median, needing ≥ 10 comparable videos),
  upload frequency, format split, title patterns (length, numbers, questions, keywords), recurring
  tags and hashtags, when breakouts were published (shown only with sample size).
- **Monitoring**: new competitor uploads since last visit, with "early breakout" flag when a video is
  well above its channel's normal pace for its age.
- **Hook study** (on demand, 1 search): fetch the transcript of a breakout video, store the first
  ~60 s as the hook text and the segment list as an outline source.
- **Compare with me**: owner's median views/day and views per subscriber vs the watchlist, per format.

### Milestone C — my private channels
- **Connect with Google** (OAuth web flow, redirect to the local backend, scopes
  `yt-analytics.readonly` + `youtube.readonly`). Refresh token stored in the local DB (unencrypted — the
  DB is local and gitignored; revisit before any deployment). Connect / disconnect buttons.
- **Several own channels** (owner's request, 2026-10-24): every press of "Connect" adds one channel
  (Google asks which account / brand channel); each connected channel keeps its own token, data and
  diagnosis, and the My channel tab has a channel switcher. Connecting the same channel again only
  refreshes its token. Disconnect revokes the token at Google and keeps the stored history.
- A connected channel's *public* data keeps using the existing `Channel`/`Video` rows through the
  existing sync when such a row exists (matched by YouTube channel id, read only); it is never required.
  The own videos needed for diagnosis (title, publish time, duration, Shorts flag) are read with the
  channel's own OAuth token and stored in studio tables.
- Spike result (2026-10-24): OAuth works; the Reporting API lists `channel_reach_basic_a1` and
  `channel_reach_combined_a1` for a connected channel, so impressions/CTR are in scope. The Analytics API
  rejects impression metrics (400), as expected. The app is "In production" (unverified, personal use).
- **Pull** (daily + button), stored as time series so improvement is trackable:
  channel daily (views, watch minutes, avg view duration/percentage, subs gained/lost, likes, comments,
  shares); per-video totals; traffic sources (type, share, trend); retention curve per video for the
  latest N long videos (`elapsedVideoTimeRatio` → `audienceWatchRatio`, one query per video);
  impressions + CTR from the Reporting API reach report (if the spike passes).
- **Own-channel diagnosis** (rules with evidence, confidence, action; "inconclusive" on thin data):
  low CTR videos vs own median, CTR/impression trend, early drop-off (retention at 10 / 30 / 50 %),
  average view percentage by format, traffic-source dependence and shift, subscriber conversion per 1 000
  views, cadence vs results, which title keywords/topics beat or lag the channel's own baseline,
  publish day/hour effect (only with ≥ 20 videos and a clear gap, otherwise "no reliable data").
- **Progress tracking**: week-over-week and 28-day-over-28-day change for the key metrics; a "baseline
  at the time" snapshot is stored when advice is accepted (that part ships with milestone D's advice).

### Milestone D — advisor, ideas, planner
- **Evidence pack** (deterministic, code-built): own diagnosis findings, own top/bottom videos,
  competitor breakouts, topic gaps (what competitors win with that the owner hasn't covered), Trends
  direction for seed keywords, autocomplete suggestions. Every item has an id.
- **AI step** (existing OpenAI-compatible `AI_*` settings): receives only the pack, returns validated JSON.
  Claims must cite evidence ids; unknown ids reject the output. If no AI key is set, the dashboard still
  shows the rule-based findings and the idea generator is disabled with a clear message.
- **Dashboard answers the six questions**: why not growing, mistakes, how to fix, what competitors do
  better, which topics next, what to focus on this week — each answer lists its evidence and a
  confidence level.
- **Daily ideas** (once a day + "generate now"), each with: topic and reason, 3–5 titles, hook, outline,
  recommended duration and format, description, hashtags and tags, competitor references with links,
  suggested upload time *only when reliable* (see diagnosis rule), confidence, priority.
- **Duplicate guard**: skip ideas whose title keywords overlap ≥ 60 % with the owner's published titles
  or earlier ideas (token similarity in code, plus an instruction to the model).
- **Planner**: save / approve / reject ideas, schedule onto a daily/weekly calendar, mark published and
  link the actual video.
- **Outcome tracking**: at day 7 and day 28 after publish compare views/day (and CTR/retention when
  available) with the owner's baseline at publish time and mark worked / neutral / didn't, with sample
  caveat; aggregate by idea attributes (format, topic cluster, title pattern) → "what has worked for me".

## 6. Architecture (the parts the builder should follow)

- Backend package `app/studio/`: `models.py`, `schemas.py`, `routes/*.py`, `services/` (serpapi,
  discovery, competitor_sync, google_oauth, analytics_pull, reporting_pull, diagnosis, evidence,
  advisor, ideas, planner), `jobs.py`. Sync handlers (`def`), like the existing routes.
- Tables (all `studio_*`): `google_connection` (one per connected own channel), `my_video`, `competitor`, `competitor_video`, `competitor_video_snapshot`,
  `keyword`, `analytics_channel_daily`, `analytics_video_daily`, `analytics_traffic`, `analytics_retention`,
  `trend_point`, `idea`, `idea_outcome`, `advisor_report`. Alembic migration `studio_*` only; do not
  touch existing tables.
- Google calls and SerpApi calls through `httpx`; **no new Python dependencies expected** (OAuth code
  flow is implemented directly). Tests never hit the network; they patch `httpx` like the existing
  conftest does, and cover: budget guard, ranking, benchmark maths with fixed fixtures, diagnosis rules
  incl. "inconclusive", duplicate guard, evidence-id validation, outcome verdicts.
- Scheduler: one `studio_daily` job (competitor sync → analytics pull → ideas), a weekly discovery job;
  on app start, run anything that was missed (the app is only up when the owner's PC is on). Every job
  also has a manual button and writes `JobRun` rows.
- Frontend: `src/app/studio/**` pages and `src/components/studio/**`, same stack and UI kit
  (tabs, cards, react-query). Charts with the existing recharts.

## 7. What the owner has to do (one-time)

1. Google Cloud (same project as the YouTube key): enable **YouTube Analytics API** and **YouTube
   Reporting API**; OAuth consent screen *External*, add themselves as test user and later set it to
   "In production" (otherwise Google expires the connection every 7 days; an "unverified app" warning is
   expected and fine for personal use); create an OAuth **Web** client with redirect URI
   `http://localhost:8000/api/v1/studio/oauth/callback`; put client id/secret in `backend/.env`.
2. For milestone D: an AI key for an OpenAI-compatible provider in `backend/.env`.
3. Review/edit the niche keywords once in Settings.

## 8. Calls I made (owner can overturn)

- Studio is a separate area; the owner's own channel is linked to the existing public row instead of duplicated.
- Several own channels can be connected; competitor features keep using the single "own channel"
  chosen in Settings (milestone B), private analytics are per connected channel.
- Competitor discovery suggests, the owner approves; no automatic watchlist additions.
- OAuth refresh token stored unencrypted in the local DB; revisit before deploying anywhere.
- No new dependencies; OAuth implemented with `httpx`.
- Breakout threshold 3× own median, ≥ 10 comparable videos; SerpApi cap 200/month; AI cap 3 calls/day.
- The stashed "API token + rate limit" work stays stashed (not needed for a local personal tool).
- Build order A → B → C → D, each shippable and tested before the next.

## 9. Left to the builder

Exact column names and indexes, ranking weights for discovery, UI layout details, prompt wording and
JSON schema for the AI step, how the "missed job" catch-up is detected, and the precise Reporting API
job lifecycle once the spike shows what the owner's channel returns.
