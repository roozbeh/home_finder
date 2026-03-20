# iPronto Home Finder — Claude Context

## Project Overview

A real estate home finder service for the San Francisco Bay Area peninsula and South Bay. Three Docker containers orchestrated via `docker-compose.yml`:

| Container | Port | Purpose |
|-----------|------|---------|
| `mls_mongo` | 27017 | MongoDB 7 — stores all data |
| `mls_app` | 8081 | MLS scraper (Selenium login + API fetch) + status dashboard |
| `mls_web` | 8080 | Flask web app — chat UI + listings browser |

There is also a native iOS app at `iOS/HomeFinder/` — see `iOS/CLAUDE.md` for its context.

## Directory Structure

```
data_service/
├── agentic/               ← agent logic, tools (imported by web/ AND used by tests)
│   ├── agent.py           ← run_agent() — the agentic loop
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
│   └── templates/
│       ├── index.html     ← standalone chat UI (Maya)
│       ├── search.html    ← listings browser
│       ├── detail.html    ← listing detail + history
│       └── base.html      ← base for search + detail only
├── mls_service/           ← scraper
│   ├── fetch_jsessionid.py  ← Selenium login → cookies
│   ├── search_and_store.py  ← MLS search API → MongoDB upsert
│   ├── entrypoint.sh        ← cron: login every 6h, search every 30min
│   └── Dockerfile
├── db/
│   └── init.js            ← MongoDB init script
├── pytest.ini             ← testpaths=tests, pythonpath=., addopts=-m "not e2e"
├── docker-compose.yml
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
- `mls_details` — per-property detail data (DCID unique) — not yet populated (see TODO below)
- `contacts` — user contact info captured by the chat agent
- `listing_feedback` — thumbs up/down on listings from chat sessions

### Key MLS listing fields
`LISTING_ID`, `STREET_ADDRESS`, `CITY` (ALL CAPS), `MLS_STATUS` (ACTV/NEW/AC/PCH/CS/BOMK), `LIST_PRICE`, `BEDROOMS_TOTAL`, `BATHROOMS_FULL`, `BATHS_DISPLAY`, `SQFT`, `LOT_SQFT`, `YEAR_BUILT`, `DAYS_ON_MARKET`, `LATITUDE`, `LONGITUDE`

**Note on photo fields:** `LPHOTOS` and `TINYPROPPHOTO_ONELINE` are already returned by the search API but currently listed in `SKIP_FIELDS` and not stored. Removing them from `SKIP_FIELDS` would be the quickest way to start capturing thumbnail photos. A separate detail API call (unknown endpoint — needs network inspection) would give full-size photos.

## Web App (`web/app.py`)

### Routes
| Route | Description |
|-------|-------------|
| `GET /` | Chat UI — conversational home finder (Maya agent) |
| `GET /search` | MLS listings browser with filters |
| `GET /listing/<id>` | Listing detail + change history |
| `POST /api/chat` | Calls `run_agent()` from `agentic/agent.py` |
| `POST /api/feedback` | Save thumbs up/down on a listing |

### Agent (Maya) — lives in `agentic/`
- Model: `claude-sonnet-4-6` via Anthropic API
- Agentic loop: up to 8 iterations (`agentic/agent.py`)
- Tools: `search_listings` (MongoDB), `get_school_info` (hardcoded), `save_contact` (MongoDB)
- Session managed client-side (UUID in JS, sent with every request)
- Conversation history maintained client-side as `[{role, content}]` pairs
- **Critical bug that was fixed:** the frontend initialises `history` with the assistant greeting. The Anthropic API rejects messages that start with `assistant` role. `_sanitize_messages()` in `agent.py` strips any leading assistant messages before every API call.
- Listings returned as structured JSON alongside text; rendered as cards in the UI
- Calendly booking: `https://calendly.com/ruzbeh-o0w7/new-meeting` — agent includes `OPEN_CALENDLY` in response text to trigger the UI

### Logging
`agent.py` uses Python `logging` at INFO level. `app.py` calls `logging.basicConfig(level=logging.INFO)`. Logs visible via `docker compose logs -f web`. Each LLM call, tool call, and result is logged with `[agent]` prefix.

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
```
If you change the build context back to `./web`, `import agentic` will break at runtime.

## AWS Deployment Plan (not yet done)

The plan is to run `mls_mongo` + `mls_web` on a 2GB EC2 instance (t3.small), and run `mls_app` (Selenium scraper) on a local Mac Mini pointing at the cloud MongoDB.

**EC2 Security Group ports:**
| Port | Source | Purpose |
|------|--------|---------|
| 8080 | 0.0.0.0/0 | Flask web (iOS app connects here) |
| 27017 | Mac Mini IP only | MongoDB (scraper connects here) |
| 22 | Your IP only | SSH |

**Mac Mini `env` file change for cloud MongoDB:**
```
MONGO_URI=mongodb://<user>:<pass>@<ec2-public-ip>:27017/mls?authSource=admin
```

## TODO / Next Features

- **Listing photos:** `LPHOTOS` and `TINYPROPPHOTO_ONELINE` are already in search results but skipped. The detail API endpoint (`bridge.connectmls.com/api/...`) is unknown — needs network inspection in browser DevTools while clicking a listing detail page in ConnectMLS. Once found, build a `fetch_details.py` script similar to `search_and_store.py` and store results in `mls_details` collection.
- **AWS deployment:** set up EC2, deploy `mls_mongo` + `mls_web`, configure Mac Mini scraper to point at cloud MongoDB.

## Conventions & Decisions

- **No framework in web frontend** — vanilla HTML/CSS/JS with Bootstrap 5 CDN; no build step
- **Cities stored in ALL CAPS** in MongoDB (e.g. `FOSTERCITY`, `HALFMO BAY`, `EASTPAALTO`)
- **School ratings** hardcoded in `agentic/tools/get_school_info.py` — no external API
- **Datetime fields** serialized to `YYYY-MM-DD` strings before returning to frontend
- **MongoDB logs suppressed** via `--logpath /dev/null` in docker-compose
- **`base.html`** used by `search.html` and `detail.html` only; chat `index.html` is standalone
- **`agentic/` is the source of truth** for all agent/tool logic — `web/app.py` only has routes
