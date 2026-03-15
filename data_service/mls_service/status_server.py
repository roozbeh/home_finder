#!/usr/bin/env python3
"""
status_server.py
-----------------
Flask dashboard for the mls_app container.
- Shows recent login / search run history from MongoDB.
- Lets you kick off login or search jobs manually via buttons.
- Streams live subprocess output so you can watch progress in real time.
"""

import os
import subprocess
import threading
from datetime import datetime, timezone
from flask import Flask, render_template_string, redirect, url_for, Response, jsonify
from pymongo import MongoClient

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB  = os.environ.get("MONGO_DB", "mls")

app    = Flask(__name__)
client = MongoClient(MONGO_URI)
db     = client[MONGO_DB]

# ── In-memory job state ───────────────────────────────────────────────────────
# Tracks whether a job is currently running and buffers its last output lines.

_lock = threading.Lock()
_jobs = {
    "login":  {"running": False, "started_at": None, "lines": []},
    "search": {"running": False, "started_at": None, "lines": []},
}


def _run_job(job_type: str, cmd: list[str]):
    """Run a subprocess, capture output line-by-line, update _jobs state."""
    with _lock:
        _jobs[job_type]["running"]    = True
        _jobs[job_type]["started_at"] = datetime.now(timezone.utc)
        _jobs[job_type]["lines"]      = []

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=os.environ,
            cwd="/app",
        )
        for line in proc.stdout:
            line = line.rstrip()
            with _lock:
                _jobs[job_type]["lines"].append(line)
                # Keep last 200 lines
                if len(_jobs[job_type]["lines"]) > 200:
                    _jobs[job_type]["lines"].pop(0)
        proc.wait()
    finally:
        with _lock:
            _jobs[job_type]["running"] = False


# ── HTML template ─────────────────────────────────────────────────────────────

TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MLS Scraper Status</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    body        { background:#f0f2f5; }
    .navbar     { background-color:#092a56 !important; }
    thead th    { background-color:#092a56; color:#fff; }
    .ok         { color:#198754; font-weight:600; }
    .err        { color:#dc3545; font-weight:600; }
    .age-old    { color:#dc3545; }
    .age-ok     { color:#198754; }
    .log-box    {
      background:#1e1e1e; color:#d4d4d4; font-family:monospace;
      font-size:.78rem; height:220px; overflow-y:auto;
      padding:.6rem .8rem; border-radius:.25rem;
      white-space:pre-wrap; word-break:break-all;
    }
    .spinner-border { width:1rem; height:1rem; border-width:.15em; }
  </style>
</head>
<body>

<nav class="navbar navbar-dark mb-4">
  <div class="container-fluid px-4">
    <span class="navbar-brand fw-bold">🔧 MLS Scraper Status</span>
    <span class="text-white-50 small" id="clock">{{ now }}</span>
  </div>
</nav>

<div class="container-fluid px-4">
  <div class="row g-4">

    <!-- ── Run controls ──────────────────────────────────────────────────── -->
    <div class="col-12 col-lg-6">
      <div class="card h-100">
        <div class="card-header fw-semibold">Login (get_cookies.py)</div>
        <div class="card-body d-flex flex-column gap-2">
          <div class="d-flex align-items-center gap-3">
            <form method="post" action="/trigger/login">
              <button class="btn btn-primary btn-sm" id="btn-login"
                      {% if jobs.login.running %}disabled{% endif %}>
                {% if jobs.login.running %}
                  <span class="spinner-border me-1"></span> Running…
                {% else %}
                  ▶ Run Login Now
                {% endif %}
              </button>
            </form>
            {% if jobs.login.started_at %}
            <span class="text-muted small">
              Started {{ jobs.login.started_at }}
            </span>
            {% endif %}
          </div>
          <div class="log-box flex-grow-1" id="log-login">{{ jobs.login.output }}</div>
        </div>
      </div>
    </div>

    <div class="col-12 col-lg-6">
      <div class="card h-100">
        <div class="card-header fw-semibold">Search (search_and_store.py)</div>
        <div class="card-body d-flex flex-column gap-2">
          <div class="d-flex align-items-center gap-3">
            <form method="post" action="/trigger/search">
              <button class="btn btn-success btn-sm" id="btn-search"
                      {% if jobs.search.running %}disabled{% endif %}>
                {% if jobs.search.running %}
                  <span class="spinner-border me-1"></span> Running…
                {% else %}
                  ▶ Run Search Now
                {% endif %}
              </button>
            </form>
            {% if jobs.search.started_at %}
            <span class="text-muted small">
              Started {{ jobs.search.started_at }}
            </span>
            {% endif %}
          </div>
          <div class="log-box flex-grow-1" id="log-search">{{ jobs.search.output }}</div>
        </div>
      </div>
    </div>

    <!-- ── Recent logins ─────────────────────────────────────────────────── -->
    <div class="col-12 col-xl-4">
      <div class="card">
        <div class="card-header fw-semibold">
          Recent Logins
          <span class="badge bg-secondary float-end">last 5</span>
        </div>
        <div class="table-responsive">
          <table class="table table-sm table-hover mb-0 small">
            <thead>
              <tr>
                <th>Timestamp (UTC)</th>
                <th class="text-center">Cookies</th>
                <th>Age</th>
                <th>Result</th>
              </tr>
            </thead>
            <tbody>
              {% for r in logins %}
              <tr>
                <td class="text-nowrap">{{ r.ts }}</td>
                <td class="text-center">{{ r.cookie_count }}</td>
                <td class="text-nowrap {% if r.age_h > 6 %}age-old{% else %}age-ok{% endif %}">
                  {{ r.age }}
                </td>
                <td>
                  {% if r.error %}
                    <span class="err" title="{{ r.error }}">✗ failed</span>
                  {% else %}
                    <span class="ok">✓ ok</span>
                  {% endif %}
                </td>
              </tr>
              {% else %}
              <tr><td colspan="4" class="text-muted text-center py-3">No logins yet.</td></tr>
              {% endfor %}
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- ── Recent searches ───────────────────────────────────────────────── -->
    <div class="col-12 col-xl-8">
      <div class="card">
        <div class="card-header fw-semibold">
          Recent Search Runs
          <span class="badge bg-secondary float-end">last 5</span>
        </div>
        <div class="table-responsive">
          <table class="table table-sm table-hover mb-0 small">
            <thead>
              <tr>
                <th>Timestamp (UTC)</th>
                <th class="text-center">Duration</th>
                <th class="text-center">Fetched</th>
                <th class="text-center">New</th>
                <th class="text-center">Updated</th>
                <th class="text-center">Unchanged</th>
                <th>Result</th>
              </tr>
            </thead>
            <tbody>
              {% for r in searches %}
              <tr>
                <td class="text-nowrap">{{ r.ts }}</td>
                <td class="text-center">{{ r.duration_s }}s</td>
                <td class="text-center">{{ r.fetched }}</td>
                <td class="text-center text-success fw-semibold">{{ r.inserted }}</td>
                <td class="text-center text-primary">{{ r.updated }}</td>
                <td class="text-center text-muted">{{ r.unchanged }}</td>
                <td>
                  {% if r.error %}
                    <span class="err" title="{{ r.error }}">✗ {{ r.error[:60] }}</span>
                  {% else %}
                    <span class="ok">✓ ok</span>
                  {% endif %}
                </td>
              </tr>
              {% else %}
              <tr><td colspan="7" class="text-muted text-center py-3">No search runs yet.</td></tr>
              {% endfor %}
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- ── DB summary ────────────────────────────────────────────────────── -->
    <div class="col-12">
      <div class="card">
        <div class="card-header fw-semibold">Database Summary</div>
        <div class="card-body">
          <div class="row text-center g-3">
            {% for stat in db_stats %}
            <div class="col-6 col-sm-3">
              <div class="fs-3 fw-bold text-primary">{{ stat.count }}</div>
              <div class="text-muted small">{{ stat.label }}</div>
            </div>
            {% endfor %}
          </div>
        </div>
      </div>
    </div>

  </div>
</div>

<script>
// Poll /api/jobs every 2s to update log boxes and button states
// without a full page reload.
function pad(n){ return String(n).padStart(2,'0'); }
function tick(){
  const d = new Date();
  document.getElementById('clock').textContent =
    d.getUTCFullYear()+'-'+pad(d.getUTCMonth()+1)+'-'+pad(d.getUTCDate())+' '+
    pad(d.getUTCHours())+':'+pad(d.getUTCMinutes())+':'+pad(d.getUTCSeconds())+' UTC';
}
setInterval(tick, 1000);

function scrollToBottom(el){ el.scrollTop = el.scrollHeight; }

function poll(){
  fetch('/api/jobs')
    .then(r => r.json())
    .then(data => {
      ['login','search'].forEach(type => {
        const job = data[type];
        const logEl = document.getElementById('log-' + type);
        const btnEl = document.getElementById('btn-' + type);

        logEl.textContent = job.lines.join('\\n');
        scrollToBottom(logEl);

        if (job.running) {
          btnEl.disabled = true;
          btnEl.innerHTML = '<span class="spinner-border me-1"></span> Running…';
        } else {
          btnEl.disabled = false;
          btnEl.innerHTML = type === 'login' ? '▶ Run Login Now' : '▶ Run Search Now';
        }
      });
    });
}

// Initial scroll
document.addEventListener('DOMContentLoaded', () => {
  scrollToBottom(document.getElementById('log-login'));
  scrollToBottom(document.getElementById('log-search'));
});

setInterval(poll, 2000);
</script>
</body>
</html>
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_job_context(job_type: str) -> dict:
    with _lock:
        j = _jobs[job_type]
        return {
            "running":    j["running"],
            "started_at": j["started_at"].strftime("%Y-%m-%d %H:%M:%S UTC")
                          if j["started_at"] else None,
            "output":     "\n".join(j["lines"]),
        }


def _ts(raw):
    if raw and raw.tzinfo is None:
        raw = raw.replace(tzinfo=timezone.utc)
    return raw


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    now = datetime.now(timezone.utc)

    raw_logins = list(db.mls_runs.find({"type": "login"}).sort("timestamp", -1).limit(5))
    logins = []
    for r in raw_logins:
        ts    = _ts(r.get("timestamp"))
        age_h = (now - ts).total_seconds() / 3600 if ts else 0
        logins.append({
            "ts":           ts.strftime("%Y-%m-%d %H:%M:%S") if ts else "—",
            "cookie_count": r.get("cookie_count", 0),
            "age_h":        age_h,
            "age":          f"{age_h:.1f}h ago",
            "error":        r.get("error"),
        })

    raw_searches = list(db.mls_runs.find({"type": "search"}).sort("timestamp", -1).limit(5))
    searches = []
    for r in raw_searches:
        ts = _ts(r.get("timestamp"))
        searches.append({
            "ts":         ts.strftime("%Y-%m-%d %H:%M:%S") if ts else "—",
            "duration_s": r.get("duration_s", "—"),
            "fetched":    r.get("fetched", 0),
            "inserted":   r.get("inserted", 0),
            "updated":    r.get("updated", 0),
            "unchanged":  r.get("unchanged", 0),
            "error":      r.get("error"),
        })

    db_stats = [
        {"label": "Total Listings", "count": db.mls_listings.count_documents({})},
        {"label": "Auth Tokens",    "count": db.auth_tokens.count_documents({})},
        {"label": "Login Runs",     "count": db.mls_runs.count_documents({"type": "login"})},
        {"label": "Search Runs",    "count": db.mls_runs.count_documents({"type": "search"})},
    ]

    return render_template_string(
        TEMPLATE,
        jobs={t: _make_job_context(t) for t in ("login", "search")},
        logins=logins,
        searches=searches,
        db_stats=db_stats,
        now=now.strftime("%Y-%m-%d %H:%M:%S UTC"),
    )


@app.route("/trigger/<job_type>", methods=["POST"])
def trigger(job_type):
    if job_type not in ("login", "search"):
        return "Not found", 404

    with _lock:
        if _jobs[job_type]["running"]:
            return redirect(url_for("index"))

    cmd = {
        "login":  ["python", "/app/get_cookies.py"],
        "search": ["python", "/app/search_and_store.py"],
    }[job_type]

    thread = threading.Thread(target=_run_job, args=(job_type, cmd), daemon=True)
    thread.start()
    return redirect(url_for("index"))


@app.route("/api/jobs")
def api_jobs():
    """JSON endpoint polled by the page JS every 2 seconds."""
    with _lock:
        payload = {}
        for job_type, j in _jobs.items():
            payload[job_type] = {
                "running":    j["running"],
                "started_at": j["started_at"].isoformat() if j["started_at"] else None,
                "lines":      list(j["lines"]),
            }
    return jsonify(payload)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8081, debug=False)
