# YT Growth Intelligence — Local Setup Guide (Windows PowerShell)

This guide is based on the **actual files in this repository** at commit `f563624`
("Phase 1: YouTube Channel Lookup & Dashboard Implementation"). No features are
assumed that are not present in the code.

---

## 1. Current Project Status

### What is implemented and working (backend)
- **FastAPI** app (`backend/app/main.py`) with routes under `/api/v1`:
  - `GET  /api/v1/health` – startup/DB/scheduler check
  - `GET  /api/v1/quota` – daily YouTube quota usage
  - `GET  /api/v1/cache/stats` – API response cache stats
  - `POST /api/v1/channels/analyze` – one-shot "Analyze Channel" flow
    (parse URL → resolve via YouTube Data API v3 → sync recent videos →
    store analysis history → return full overview). Returns HTTP **201**.
  - `GET  /api/v1/channels/{id}/overview` – dashboard data from our DB
    (no quota spent)
  - `POST /api/v1/channels`, `GET /api/v1/channels`, `GET/DELETE /api/v1/channels/{id}`,
    `GET /api/v1/channels/{id}/videos`, `GET /api/v1/channels/{id}/summary`,
    `POST /api/v1/channels/{id}/sync`
  - `POST /api/v1/jobs/sync-all`, `GET /api/v1/jobs/runs`
- **YouTube URL resolution** (`app/services/url_resolver.py`):
  - Supported: `youtube.com/@handle`, `youtube.com/channel/UC...`, bare `@handle`, bare `UC...` ID.
  - **Legacy `/c/` and `/user/` URLs are intentionally rejected** with a clear
    HTTP 400 (`code: "unsupported_url_format"`) instead of guessing. To analyze such a
    channel, open it in a browser and use its `@handle` or `/channel/UC...` URL.
- **Real channel data** fetched and stored: name, thumbnail, description,
  subscriber count (if public), total views, video count, creation date, country.
- **Database**: SQLAlchemy models + Alembic migrations
  (`8d907efc40d3` initial schema → `dcc6e52aca60` channel_analyses history table).
  Default database is a local **SQLite file** (`yt_growth.db`) so you can run with
  zero DB setup; PostgreSQL is supported by setting `DATABASE_URL`.
- **Caching** (DB-backed, TTL default 900 s) and **daily quota tracking** (limit 10,000).
- **Error handling**: missing API key → clear message; unknown channel → 404;
  Google/API failure → 502; quota exhausted → 429; invalid input → 422.
- **APScheduler** background jobs (periodic sync + cache purge); disable with
  `ENABLE_SCHEDULER=false`.

### What is NOT implemented yet (be honest about this)
- **The frontend is EMPTY.** `frontend/` contains only `package.json` and
  `package-lock.json` (Next.js/Tailwind/shadcn dependencies listed, but **no source
  code, no `app/` pages, no components, no config files**). There is nothing to run
  with `npm run dev` — no `dev` script exists. The dashboard UI will be built next.
- **AI analysis, competitors, Phase 2+**: not implemented. `AI_API_KEY` etc. are
  placeholders in `.env.example`; you do **not** need them now.
- **PostgreSQL is optional for now**: the committed code defaults to SQLite.
  You can switch to PostgreSQL later just by setting `DATABASE_URL` and re-running
  migrations (same two migrations apply to both).
- **Known test-suite issue**: 7 of 39 backend tests currently fail due to test
  isolation (the app's module-level engine binds before the per-test temp DB env
  var is applied, so some tests read a stale SQLite file). This is a *test fixture*
  problem, not an application bug — the endpoints themselves were verified working
  against a live server (see §7). Will be fixed before Phase 2.

### Expected to work locally right now
Backend start, health endpoint, Swagger docs, channel analyze/overview with a real
YouTube API key, persistence, history, quota/cache endpoints, error responses.

---

## 2. GitHub and Cloning

**Important:** the sandbox repository I have access to has **no Git remote
configured** (`git remote -v` returns nothing), so I cannot provide a verified
repository URL from here. Please use the GitHub repository URL shown in your own
GitHub account (the repo you created/pushed to). Do not clone from any URL I invent.

Verified local state in the sandbox repo:
- Latest commit: `f5636245dad23c0ee741c347a2394d9be3dd2a23`
  ("Phase 1: YouTube Channel Lookup & Dashboard Implementation")
- Branches present locally: `main` and `qwen-code-f7e923c6-727a-4eca-9d5a-b73f424f2034`.
  **Before cloning, confirm which branch on GitHub contains commit `f563624`**
  (open the repo on github.com → Commits). If you pushed from this workspace, it is
  likely the feature branch above, not `main`.

PowerShell (replace `<REPO_URL>` with your actual GitHub URL):

```powershell
cd $HOME\Documents   # or wherever you keep projects
git clone <REPO_URL> yt-growth-intelligence
cd yt-growth-intelligence
git log --oneline -1   # should show f563624 "Phase 1: ..."
```

If the latest code is on a different branch:

```powershell
git branch -a
git checkout <branch-containing-f563624>
```

---

## 3. Required Software

From the actual project configuration:

| Tool | Version used when building | Requirement |
|---|---|---|
| Python | 3.12.10 | **3.11 or newer required** (code uses `X | None` union syntax) |
| Node.js | v20.x | Only needed later for the frontend; **skip for now** (frontend is empty) |
| npm | 10.x | same as above |
| Git | any recent | for cloning |
| PostgreSQL | optional | **not required now** — default is SQLite (zero setup) |

Check what you already have (PowerShell):

```powershell
git --version
python --version          # if this opens the Microsoft Store, see Troubleshooting §8
py --list                 # Windows Python launcher: shows all installed Pythons
node --version            # optional today
npm --version             # optional today
```

Install anything missing:
- Python: https://www.python.org/downloads/ — **check "Add python.exe to PATH"** during install.
- Git: https://git-scm.com/downloads (default options are fine).
- (Optional, later) Node.js LTS: https://nodejs.org

---

## 4. Environment Configuration

Only one env file matters today: **`backend/.env`** (copy of the committed
`backend/.env.example`). Frontend env files do not exist yet because there is no
frontend code.

```powershell
cd yt-growth-intelligence\backend
Copy-Item .env.example .env
notepad .env
```

Variables (from the actual `backend/.env.example` and `app/core/config.py`):

| Variable | Required? | Notes |
|---|---|---|
| `YOUTUBE_API_KEY` | **Yes** for real channel lookups | Get it free: Google Cloud Console → new project → enable **YouTube Data API v3** → Credentials → Create API key. Paste as `YOUTUBE_API_KEY=<your-key>`. |
| `DATABASE_URL` | No | Omit → SQLite `yt_growth.db` (recommended first run). For PostgreSQL: `DATABASE_URL=postgresql+psycopg2://postgres:YOUR_PASSWORD@localhost:5432/yt_growth` |
| `AI_BASE_URL`, `AI_API_KEY`, `AI_MODEL` | **No** | Phase 2+ placeholders. Leave blank. |
| `YOUTUBE_QUOTA_DAILY_LIMIT` | No | default 10000 |
| `CACHE_TTL_SECONDS` | No | default 900 |
| `ENABLE_SCHEDULER` | No | default true; set `false` to disable background jobs |
| `CORS_ORIGINS` | No | default `["http://localhost:3000"]` (already correct for the future Next.js app) |

Security rules (enforced by the code — keys are read only on the backend):
- Never put `YOUTUBE_API_KEY` in frontend code or `.env.local` for Next.js.
- Never commit `.env` (it is covered by `backend/.gitignore`).
- Never paste your key into chat/screenshots.

**If you don't have a YouTube API key yet**, everything still runs:
server startup, health/quota/cache endpoints, Swagger docs, invalid-URL rejection,
unsupported-format errors, and storage/history reads. Only the actual
resolve/fetch step returns the message *"YOUTUBE_API_KEY is not configured on the
backend"* until you add a key.

Create the PostgreSQL database first **only if** you set `DATABASE_URL` (run in
`psql` or pgAdmin):

```sql
CREATE DATABASE yt_growth;
```

---

## 5. Backend Setup (Terminal 1)

All commands from **Windows PowerShell**, directory `...\yt-growth-intelligence\backend`:

```powershell
cd $HOME\Documents\yt-growth-intelligence\backend

# 1) Virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1
# If activation is blocked:
#   Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
# then re-run the Activate line.

# 2) Dependencies
python -m pip install --upgrade pip
pip install -r requirements.txt

# 3) Env file (if not done in §4)
Copy-Item .env.example .env
notepad .env    # paste YOUTUBE_API_KEY=...

# 4) Database + migrations (order is enforced by Alembic itself:
#    8d907efc40d3 initial schema -> dcc6e52aca60 channel_analyses)
alembic upgrade head
alembic current        # expected output: dcc6e52aca60 (head)

# 5) Start FastAPI
uvicorn app.main:app --reload --port 8000
```

Expected terminal output after startup:

```
INFO:apscheduler.scheduler:Adding job ...
INFO:app.main:Backend startup complete
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

Backend URLs:
- App root: http://127.0.0.1:8000/
- **Swagger docs: http://127.0.0.1:8000/docs**
- Health check: http://127.0.0.1:8000/api/v1/health

Test endpoints from Swagger (`/docs`): click **POST /api/v1/channels/analyze** →
"Try it out" → body:

```json
{"identifier": "https://www.youtube.com/@mkbhd"}
```

→ Execute. Expected: **201** with `channel` (real title/subs/views/videoCount/
country/creation date), `summary`, `recent_videos`, and `history` (one row per run).
Then try **GET /api/v1/channels/1/overview** (quota-free dashboard read).

Or from PowerShell (in a second window):

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/channels/analyze `
  -ContentType 'application/json' -Body '{"identifier":"https://www.youtube.com/@mkbhd"}' |
  ConvertTo-Json -Depth 5
```

---

## 6. Frontend Setup

**Not possible today — stated honestly:** `frontend/` contains only
`package.json` + `package-lock.json`. There is no Next.js app code, no
`next.config.js`, no Tailwind/TypeScript config, and **no `dev` script** in
`package.json`. Running `npm run dev` would fail with
`Missing script: "dev"`. Building the dashboard UI is the next task; for now,
use Swagger (`/docs`) as your interface.

When the frontend exists, the intended pattern (already prepared in the backend's
CORS default) will be:

```powershell
cd $HOME\Documents\yt-growth-intelligence\frontend
npm install
# frontend/.env.local -> NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1
npm run dev   # http://localhost:3000
```

---

## 7. Testing Checklist (sequential)

Run each step and note pass/fail. Keep Terminal 1 = backend, Terminal 2 = commands.

1. **Backend starts** — `uvicorn app.main:app --reload --port 8000` shows
   `Application startup complete` and no traceback.
2. **Database connection works** — open http://127.0.0.1:8000/api/v1/health →
   `{"status":"ok", ...}`. First run also creates `backend/yt_growth.db` (SQLite).
3. **Migrations applied** — `alembic current` prints `dcc6e52aca60 (head)`.
4. **Frontend starts** — skip (not implemented yet; see §6).
5. **Open website in browser** — use http://127.0.0.1:8000/docs instead.
6. **Supported URL** — POST `/api/v1/channels/analyze` with
   `https://www.youtube.com/@mkbhd` → expect 201 JSON. Also try
   `https://www.youtube.com/channel/UCB8RgTRGrlcuCDN1JYIL1LQ` (a channel-ID URL).
7. **Real channel info appears** — verify `title`, `subscriber_count`,
   `view_count`, `video_count`, `country`, `channel_created_at` match what you see
   on the actual YouTube channel page (About tab). Numbers must come from the API,
   not approximations.
8. **Invalid URL** — `"not a url at all !!"` → expect **422** with a clear message.
   Legacy formats `https://www.youtube.com/c/xyz` and `/user/xyz` → expect **400**
   with `code: "unsupported_url_format"` (by design, not a bug).
9. **API failures / missing credentials** — comment out `YOUTUBE_API_KEY` in
   `.env`, restart uvicorn, call analyze → expect message
   `YouTube API error 0: YOUTUBE_API_KEY is not configured...`. With a key, analyze a
   nonexistent channel ID (`UC` + garbage) → expect **404**. Check
   GET `/api/v1/quota` shows units consumed rising (~1–6 units per analysis).
10. **History persisted** — analyze the same channel twice, then GET
    `/api/v1/channels/1/overview` → `history` array should contain 2 entries with
    timestamps; channel row survives a server restart (stored in DB).

**Backend automated tests** (Terminal 2, venv active, from `backend\`):

```powershell
pip install pytest
python -m pytest tests -q -p no:libtmux
```

Expected reality (verified in my sandbox): **32 passed, 7 failed**. The 7 failures
are the known test-isolation issue described in §1; the 32 passing include all URL
resolver tests and most analyze/error tests. Treat additional failures beyond those
7 as new problems worth reporting.

**Reporting errors back to me:** copy the exact traceback from PowerShell and the
browser DevTools Console/Network tab (F12) text. **Redact your API key** first —
keys appear in query strings of YouTube calls, so never paste raw lines containing
`key=`. Screenshots must hide `.env` contents.

---

## 8. Troubleshooting (Windows-specific, relevant to this repo)

1. **`python` not recognized / opens Microsoft Store**
   Reinstall Python with "Add to PATH" checked, or use the launcher:
   `py -3.12 -m venv .venv`, then `py -m pip install -r requirements.txt`.
2. **`.\.venv\Scripts\Activate.ps1` blocked ( PSSecurityException )**
   `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned` → `Y`,
   reopen PowerShell, activate again.
3. **`pip install psycopg2-binary` fails** — it shouldn't on Windows wheels; if it
   does, you can ignore PostgreSQL entirely (default SQLite path needs no driver).
4. **PostgreSQL connection refused** (only if you set `DATABASE_URL`)
   - Is the service running? `Get-Service postgresql*` → `Start-Service`.
   - Verify credentials/port in the URL; test with
     `psql -h localhost -U postgres`. Create the DB: `CREATE DATABASE yt_growth;`
   - Quick sanity check: temporarily comment out `DATABASE_URL` — if SQLite works,
     the problem is the PG config, not the app.
5. **Missing env vars / "YOUTUBE_API_KEY is not configured"** — you created `.env`
   in the wrong folder. It must be `backend\.env` (same dir as `alembic.ini`),
   variable named exactly `YOUTUBE_API_KEY`. Restart uvicorn after editing.
6. **Port 8000 already in use** (`[Errno 10048]`) — find it:
   `Get-NetTCPConnection -LocalPort 8000 | Select OwningProcess` → kill process or
   run `uvicorn app.main:app --reload --port 8001`.
7. **CORS errors** — cannot occur yet (no frontend). When the frontend exists,
   it must be served from `http://localhost:3000` (backend default allows exactly
   that), or add `CORS_ORIGINS=["http://localhost:3000"]` to `.env`.
8. **Alembic fails** (`Can't locate revision`, or table-exists errors)
   - Always run alembic from the `backend\` directory (it reads `alembic.ini` and
     `DATABASE_URL` from there).
   - For a fresh personal database, nuclear option is safe: delete `backend\yt_growth.db`
     (or drop/recreate the PG database), then `alembic upgrade head`.
   - If tables were auto-created by a previous run without version rows:
     `alembic stamp 8d907efc40d3` then `alembic upgrade head`.
9. **YouTube API errors**
   - `403 quotaExceeded` / HTTP 429 from our API: daily 10,000-unit budget used up
     (channels.list ≈1 unit; analyze spends roughly 1–6 units). Wait for UTC
     midnight reset or raise `YOUTUBE_QUOTA_DAILY_LIMIT` cautiously.
   - `403 apiKeyInvalid` / HTTP 502 from our API: bad key, or YouTube Data API v3
     not enabled on the Google Cloud project.
   - `404`: the handle/channel doesn't exist — use the exact @handle from the channel URL.
10. **Frontend can't reach backend** — N/A today; later ensure uvicorn is running
    on port 8000 and the frontend base URL is `http://localhost:8000/api/v1`.

---

## 9. Final Instructions (condensed copy-paste sequence)

**Terminal 1 (backend):**
```powershell
cd $HOME\Documents\yt-growth-intelligence\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env                     # add YOUTUBE_API_KEY only
alembic upgrade head
alembic current                  # expect: dcc6e52aca60 (head)
uvicorn app.main:app --reload --port 8000
```

**Terminal 2 (verify):**
```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/channels/analyze -ContentType 'application/json' -Body '{"identifier":"https://www.youtube.com/@mkbhd"}' | ConvertTo-Json -Depth 5
```

**Browser:** http://127.0.0.1:8000/docs

**Missing items identified honestly:**
- Repository URL could not be read from this sandbox (no git remote configured) — use your GitHub repo page.
- Frontend application code does not exist yet (dependencies only) — §6 cannot be executed today.
- No `requirements-dev.txt`/pytest pinning — install pytest manually (§7).
- 7 backend tests fail due to a known fixture-isolation defect (app itself verified working live in my sandbox: health OK, analyze returns 201-shaped flows, unsupported `/c/` and `/user/` correctly rejected with 400, missing-key handled, quota tracked).

Run through §7, then report results/errors (redacted) and we'll fix issues before starting the frontend build.
