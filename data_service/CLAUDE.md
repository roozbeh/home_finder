# iPronto Home Finder — Claude Context

## Project Overview

A real estate home finder service for the San Francisco Bay Area peninsula and South Bay, built by **Roozbeh Zabihollahi** (DRE# 02225608). The product is branded **roozbeh.realtor** / **BayArea Home Finder**. Three Docker containers orchestrated via `docker-compose.yml`:

| Container | Port | Purpose |
|-----------|------|---------|
| `mls_mongo` | 27017 | MongoDB 7 — stores all data |
| `mls_app` | 8081 | MLS scraper (Selenium login + API fetch) + status dashboard |
| `mls_web` | 8080 | Flask web app — chat UI + listings browser |

There is also a native iOS app at `iOS/HomeFinder/` — see `iOS/CLAUDE.md` for its context.

The production server is at `ipronto.net` (Ubuntu, SSH as `ubuntu@ipronto.net`). Deploy with `bash deploy.sh` from the repo root.

## Directory Structure

```
data_service/
├── agentic/               ← agent logic, tools (imported by web/ AND used by tests)
│   ├── agent.py           ← run_agent() + run_agent_streaming() — the agentic loop
│   ├── prompts.py         ← SYSTEM_PROMPT + TOOLS schemas
│   └── tools/
│       ├── executor.py    ← dispatches tool calls by name
│       ├── search_listings.py
│       ├── get_school_info.py
│       └── save_contact.py
├── tests/                 ← pytest test suite (outside web/ and agentic/)
│   ├── conftest.py        ← fixtures: mock_db, ai_end_turn, ai_tool_then_end
│   ├── test_tools.py      ← 24 unit tests for all tools
│   ├── test_agent.py      ← 13 unit tests for the agentic loop
│   └── test_e2e.py        ← 3 e2e tests (real Anthropic API, mock MongoDB)
├── web/                   ← Flask app
│   ├── app.py             ← routes only; imports run_agent from agentic/
│   ├── Dockerfile
│   ├── favicon.png        ← AI-generated favicon; copied to static/ at build time
│   └── templates/
│       ├── index.html     ← standalone chat UI (Maya) — does NOT extend base.html
│       ├── search.html    ← listings browser (extends base.html)
│       ├── detail.html    ← listing detail + change history (extends base.html)
│       ├── base.html      ← base for search + detail only
│       ├── about.html     ← marketing/App Store landing page (standalone, not base.html)
│       ├── support.html   ← contact info page (extends base.html)
│       └── privacy_policy.html ← App Store privacy policy page (extends base.html)
├── mls_service/           ← scraper
│   ├── fetch_jsessionid.py  ← Selenium login → cookies
│   ├── search_and_store.py  ← MLS search API → MongoDB upsert
│   ├── entrypoint.sh        ← cron: login every 6h, search every 30min
│   └── Dockerfile
├── db/
│   └── init.js            ← MongoDB init script
├── pytest.ini             ← testpaths=tests, pythonpath=., addopts=-m "not e2e"
├── docker-compose.yml
├── deploy.sh              ← one-command deploy: SSH to ipronto.net, git pull, rebuild web
└── env                    ← secrets file (not .env — see Docker note below)
```

## Architecture

### Data flow
1. `mls_app` logs into ConnectMLS via Selenium → stores cookies in `auth_tokens` collection
2. `mls_app` fetches listings via MLS REST API (`bridge.connectmls.com/api/search/listing/list`) → upserts into `mls_listings`
3. `mls_web` serves the chat UI and listings browser, reads from MongoDB

### MongoDB collections
- `auth_tokens` — MLS session cookies
- `mls_listings` — property listings (LISTING_ID unique key, field-level change history in `_history`)
- `mls_runs` — job execution logs (type: "login" | "search")
- `mls_details` — per-property detail data (DCID unique) — populated by `fetch_details.py` (see TODO)
- `contacts` — user contact info captured by the chat agent
- `listing_feedback` — thumbs up/down on listings from chat sessions
- `users` — registered users (name, email, apple_user_id, user_id)
- `chat_sessions` — persisted conversation histories per user

### Key MLS listing fields
`LISTING_ID`, `STREET_ADDRESS`, `CITY` (ALL CAPS), `MLS_STATUS` (ACTV/NEW/AC/PCH/CS/BOMK), `LIST_PRICE`, `BEDROOMS_TOTAL`, `BATHROOMS_FULL`, `BATHS_DISPLAY`, `SQFT`, `LOT_SQFT`, `YEAR_BUILT`, `DAYS_ON_MARKET`, `LATITUDE`, `LONGITUDE`

**Photo fields:** `LPHOTOS` and `TINYPROPPHOTO_ONELINE` are stored in mls_listings. `TINYPROPPHOTO_ONELINE` may be a raw URL or an HTML `<img>` tag — `search_listings.py` parses both. Full-size photos come from the `details` sub-document (populated by `fetch_details.py`).

**Details sub-document:** The `details` field on each listing (populated separately) contains sub-fields `Baths` (string, e.g. `"2"`) and `Beds` (string). These are the **primary** source for bath/bed counts. `search_listings.py` pops `details` and extracts these before returning results to the agent.

## Web App (`web/app.py`)

### Routes
| Route | Description |
|-------|-------------|
| `GET /` | Chat UI — conversational home finder (Maya agent) |
| `GET /about` | Marketing landing page — App Store marketing URL |
| `GET /support` | Support page with contact info |
| `GET /privacy_policy` | Privacy policy — required for App Store |
| `GET /search` | MLS listings browser with filters |
| `GET /listing/<id>` | Listing detail + change history |
| `POST /api/chat` | Non-streaming: calls `run_agent()` from `agentic/agent.py` |
| `POST /api/chat/stream` | **Streaming SSE**: calls `run_agent_streaming()` — primary endpoint used by iOS and web |
| `POST /api/feedback` | Save thumbs up/down on a listing |
| `GET /api/sessions` | List user's chat sessions (requires user_id query param) |
| `GET /api/sessions/<id>` | Fetch full session with messages |
| `DELETE /api/sessions/<id>` | Delete a session |
| `POST /api/auth/login` | Create or look up user — accepts name, email, apple_user_id |
| `GET /api/auth/me` | Fetch user profile by user_id |

### Agent (Maya) — lives in `agentic/`
- Model: `claude-sonnet-4-6` via Anthropic API
- Agentic loop: up to 8 iterations (`agentic/agent.py`)
- Tools: `search_listings` (MongoDB), `get_school_info` (hardcoded), `save_contact` (MongoDB)
- Two entry points: `run_agent()` (blocking) and `run_agent_streaming()` (SSE generator)
- Session managed client-side (UUID in JS/Swift, sent with every request)
- Conversation history maintained client-side as `[{role, content}]` pairs; persisted server-side in `chat_sessions` after each response
- **Critical bug that was fixed:** the frontend initialises `history` with the assistant greeting. The Anthropic API rejects messages that start with `assistant` role. `_sanitize_messages()` in `agent.py` strips any leading assistant messages before every API call.
- Listings returned as structured JSON alongside text; rendered as cards in the UI
- Calendly booking: `https://calendly.com/ruzbeh-o0w7/new-meeting` — agent includes `OPEN_CALENDLY` in response text to trigger the UI to open Calendly

### Streaming (SSE) — `run_agent_streaming()`
The **primary** chat endpoint is `/api/chat/stream`. It uses Server-Sent Events (SSE) with `text/event-stream` MIME type.

Event types yielded:
```json
{"type": "status",  "text": "Searching for properties..."}
{"type": "text",    "text": "word "}
{"type": "done",    "listings": [...], "full_text": "complete assistant text"}
{"type": "error",   "text": "error message"}
```

- `status` events fire before each tool call (e.g. "Searching for properties...", "Looking up school info...")
- `text` events carry the final LLM response word-by-word (text split on spaces)
- `done` fires once with the full listings array and complete text — used to persist the session
- The Flask route sets `X-Accel-Buffering: no` and `Cache-Control: no-cache` headers
- Gunicorn is configured with `--worker-class gthread --threads 4` to handle concurrent SSE connections

### Auth (`/api/auth/login`)
Accepts `name`, `email`, `apple_user_id` (all optional except at least one identifier must be present). Lookup priority:
1. `apple_user_id` — stable across app reinstalls, always returned by Apple
2. `email` — fallback for web users or first-time Apple sign-in

On first Apple sign-in Apple sends email + name. On subsequent sign-ins (or after reinstall) Apple sends only `apple_user_id` — email is nil. The backend handles this by looking up existing user by `apple_user_id` and returning their stored email. Users collection stores both `email` and `apple_user_id`; missing fields are backfilled on subsequent logins.

### Logging
`agent.py` uses Python `logging` at INFO level. `app.py` calls `logging.basicConfig(level=logging.INFO)`. Logs visible via `docker compose logs -f web`. Each LLM call, tool call, and result is logged with `[agent]` or `[agent-stream]` prefix.

## Web Frontend (`web/templates/`)

- **`index.html`** — standalone (does NOT extend base.html). Full-page chat UI with sidebar. Uses `/api/chat/stream` (SSE). Two-bubble streaming pattern: text bubble fills word-by-word, dots bubble stays until `done` fires. Has favicon, About/Support links in input hint area.
- **`base.html`** — used by `search.html` and `detail.html`. Has navbar with Chat, Browse, About, Support links, login modal, and photo gallery overlay.
- **`about.html`** — standalone marketing page (does NOT extend base.html). Has its own navbar. Designed as App Store marketing URL: `https://ai.roozbeh.realtor/about`. Sections: hero, 6 feature cards, how-it-works steps, agent bio.
- **`support.html`** — extends base.html. Contact: `info@roozbeh.realtor`. URL: `/support`.
- **`privacy_policy.html`** — extends base.html. Required by App Store. URL: `/privacy_policy`. Covers data collected, AI/Anthropic disclosure, data deletion (30-day email request), children's privacy.

### Web frontend SSE streaming pattern (index.html JS)
```javascript
// Two bubbles appended immediately
appendStreamingBubble(textId)   // fills with words
appendStreamingBubble(dotsId)   // animated dots, removed on done

const res = await fetch('/api/chat/stream', { method: 'POST', body: JSON.stringify({...}) })
const reader = res.body.getReader()
// parse SSE lines, update text bubble on 'text' events,
// remove dots bubble and attach listing cards on 'done'
```

## Environment / Config

### `env` file (NOT `.env`)
The secrets file is named `env` (no dot). Docker Compose does **not** auto-detect it — `env_file: - env` is explicitly set in `docker-compose.yml` for both `app` and `web` services. Do not rename it to `.env` without updating docker-compose.

```
MLS_USERNAME=...
MLS_PASSWORD=...
ANTHROPIC_API_KEY=sk-ant-...
E2E_ANTHROPIC_API_KEY=sk-ant-...   ← separate key for e2e tests, tracked separately
HEADLESS=true
MONGO_USERNAME=...                  ← MongoDB root username (configurable)
MONGO_PASSWORD=...                  ← MongoDB root password (configurable)
MONGO_URI=mongodb://<user>:<pass>@localhost:27017/mls?authSource=admin
MONGO_DB=mls
```

### MongoDB credentials
Configured via `MONGO_USERNAME` / `MONGO_PASSWORD` in the `env` file — no longer hardcoded. **Important:** MongoDB only reads `MONGO_INITDB_ROOT_*` on first start with an empty data volume. To change credentials on an existing install: `docker compose down -v` (wipes volume) then `docker compose up`.

## Tests

```bash
# Unit tests only (default — no API calls, no real DB)
/Users/roozbeh/Library/Python/3.9/bin/pytest tests/ -v

# E2e tests (hits real Anthropic API using E2E_ANTHROPIC_API_KEY from env file)
/Users/roozbeh/Library/Python/3.9/bin/pytest tests/ -m e2e -v
```

- `python3` is the system Python at `/Library/Developer/CommandLineTools/usr/bin/python3` (Python 3.9)
- `pytest` installed at `/Users/roozbeh/Library/Python/3.9/bin/pytest`
- `anthropic` and `python-dotenv` also installed there
- Top-level `requirements.txt` at `home_finder/requirements.txt` tracks local dev deps (not Docker)
- `mls_service/requirements.txt` is used by the Docker container — do not remove it

## Docker

```bash
# Start everything
docker compose up

# Start only cloud services (mongo + web), skip the scraper
docker compose up mls_mongo mls_web

# Rebuild after code changes to web/ or agentic/
docker compose up --build web

# Rebuild everything
docker compose up --build

# Stop without wiping volumes
docker compose down

# Stop AND wipe MongoDB volume (needed when changing Mongo credentials)
docker compose down -v
```

### Docker build context — important
The `web` service build context is `.` (data_service root), not `./web`. This is so `agentic/` can be copied into the container alongside `web/`. The Dockerfile does:
```
COPY web/ .
COPY agentic/ ./agentic/
RUN mkdir -p static && cp favicon.png static/favicon.png
```
If you change the build context back to `./web`, `import agentic` will break at runtime.

### Gunicorn config (web/Dockerfile)
```
gunicorn --bind 0.0.0.0:5000 --workers 2 --timeout 120 --worker-class gthread --threads 4 app:app
```
- `--timeout 120` — needed because LLM calls can take 30-60s
- `--worker-class gthread --threads 4` — needed for concurrent SSE connections (streaming)

## Deployment

`deploy.sh` in the repo root:
```bash
ssh ubuntu@ipronto.net << 'EOF'
  cd /home/ubuntu/home_finder/data_service
  git pull
  docker-compose down
  docker compose up --build -d web
EOF
```
Run with: `bash deploy.sh`

The production URL is `https://ai.roozbeh.realtor` (or `http://ipronto.net:8080` directly).

## App Store Metadata

- **App name:** BayArea Home Finder
- **Subtitle:** Chat to Find Your Home (22 chars)
- **Category:** Real Estate (primary), Lifestyle (secondary)
- **Privacy Policy URL:** `https://ai.roozbeh.realtor/privacy_policy`
- **Marketing URL:** `https://ai.roozbeh.realtor/about`
- **Support URL:** `https://ai.roozbeh.realtor/support`
- **iPhone only** — iPad not supported (Info.plist `UIDeviceFamily = [1]`)
- **Build number** — increment `CFBundleVersion` in Info.plist for each TestFlight/App Store upload
- **Export compliance** — add `ITSAppUsesNonExemptEncryption = false` to Info.plist to suppress App Store warning (app only uses standard HTTPS)

## TODO / Next Features

- **Deploy backend fix for Sign in with Apple** — `apple_user_id` lookup is implemented in `app.py` but not yet deployed to ipronto.net. Run `bash deploy.sh` to deploy.
- **Listing photos:** `LPHOTOS` and `TINYPROPPHOTO_ONELINE` are stored. The full detail API endpoint (`bridge.connectmls.com/api/...`) is unknown — needs network inspection in browser DevTools. Once found, build a `fetch_details.py` script similar to `search_and_store.py` and store results in `mls_details` collection.
- **AWS deployment:** plan exists (t3.small EC2 for mongo+web, Mac Mini for scraper). Not yet executed.

## Conventions & Decisions

- **No framework in web frontend** — vanilla HTML/CSS/JS with Bootstrap 5 CDN; no build step
- **Cities stored in ALL CAPS** in MongoDB (e.g. `FOSTERCITY`, `HALFMO BAY`, `EASTPAALTO`)
- **School ratings** hardcoded in `agentic/tools/get_school_info.py` — no external API
- **Datetime fields** serialized to `YYYY-MM-DD` strings before returning to frontend
- **MongoDB logs suppressed** via `--logpath /dev/null` in docker-compose
- **`base.html`** used by `search.html` and `detail.html` only; `index.html` and `about.html` are standalone
- **`agentic/` is the source of truth** for all agent/tool logic — `web/app.py` only has routes
- **Baths/Beds primary source** is `details.Baths` / `details.Beds` (string values from fetch_details). Top-level `BATHS_DISPLAY` / `BATHROOMS_FULL` / `BEDROOMS_TOTAL` are fallbacks.
- **MongoDB stores integers as floats** (e.g. `BEDROOMS_TOTAL = 2.0`). iOS decoder must try `Double` fallback when `Int` decode fails.

## Known Issues / Gotchas

- **"variant selector cell index number could not be found"** — harmless iOS system log from the keyboard/emoji subsystem. Appears once per keystroke in the Xcode console on physical devices. Cannot be suppressed from app code. Safe to ignore.
- **Apple Sign In email only sent once** — `credential.email` is nil on all sign-ins after the first. Always pass `credential.user` (stable ID) to backend. Backend looks up by `apple_user_id` first. This backend fix is implemented but needs to be deployed.
- **SSE on Apache** — if using Apache as reverse proxy, add `flushpackets=on` to the ProxyPass directive for `/api/chat/stream`, otherwise Apache buffers the SSE stream.
