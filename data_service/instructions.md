# MLS Scraper — Quick Start

## Setup

```bash
# Activate the Python environment
source agentic_env/bin/activate

# Install dependencies
pip install -r mls_service/requirements.txt
```

## Credentials

Credentials are stored in `env` (git-ignored). Load them before running any script:

```bash
source env
```

To update credentials, edit `env` directly. Never hardcode them in scripts or commit them to git.

## Run locally (Mac)

```bash
source env

# Step 1 — log in and capture session cookies → saved to MongoDB
python mls_service/get_cookies.py

# Step 2 — fetch listings and upsert to MongoDB
python mls_service/search_and_store.py
```

Set `HEADLESS=false` in `env` to watch the browser window during login.

## Run with Docker

```bash
docker compose up --build
```

| URL | What |
|-----|------|
| http://localhost:8080 | Listings browser |
| http://localhost:8081 | Scraper status + manual triggers |
