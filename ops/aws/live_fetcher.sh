#!/usr/bin/env bash
# Refresh current active MLS inventory and enrich newly discovered listings.
# Invoked by cron every 15 minutes; it never attempts MLS login.
set -Eeuo pipefail

APP_ROOT="${FLIP_FINDER_APP_ROOT:-/home/ubuntu/home_finder}"
DATA_SERVICE_DIR="$APP_ROOT/data_service"
LOG_DIR="${FLIP_FINDER_LOG_DIR:-/mnt/extra500/flip_finder/logs}"
LOG_FILE="$LOG_DIR/live_fetcher.log"
LOCK_FILE="${FLIP_FINDER_LOCK_FILE:-$LOG_DIR/live_fetcher.lock}"
MAX_LOG_BYTES=52428800

mkdir -p "$LOG_DIR"
if [[ -f "$LOG_FILE" ]] && [[ $(wc -c < "$LOG_FILE") -ge $MAX_LOG_BYTES ]]; then
  mv -f "$LOG_FILE" "$LOG_FILE.1"
fi
exec >> "$LOG_FILE" 2>&1

echo "[$(date -Is)] live fetcher starting"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "[$(date -Is)] skipped: previous live-fetcher run still holds lock"
  exit 0
fi

# The initial details backfill can take hours.  Avoid concurrent API calls and
# writes until it is complete; the next cron tick will catch up.
if ps -ef | grep -q '[f]etch_details.py --active-only'; then
  echo "[$(date -Is)] skipped: active-details backfill is still running"
  exit 0
fi

cd "$DATA_SERVICE_DIR"
WORKER_CONTAINER="${MLS_WORKER_CONTAINER:-mls_app}"
if ! docker inspect -f '{{.State.Running}}' "$WORKER_CONTAINER" 2>/dev/null | grep -qx true; then
  # During deployment, preserve the established cron behavior until mls_app is
  # online rather than silently skipping a refresh.
  WORKER_CONTAINER="mls_web"
  echo "[$(date -Is)] mls_app is unavailable; temporarily using mls_web"
fi

docker cp mls_service/mls_session.py "$WORKER_CONTAINER":/tmp/mls_session.py
docker cp mls_service/search_and_store.py "$WORKER_CONTAINER":/tmp/search_and_store.py
docker exec "$WORKER_CONTAINER" python /tmp/search_and_store.py --active-only --market bay-area

# At one request/second this cap leaves room inside a 15-minute cadence while
# still allowing a temporary backlog to drain.  Normal runs fetch only new
# active records because already enriched records are excluded.
docker cp mls_service/fetch_details.py "$WORKER_CONTAINER":/tmp/fetch_details.py
docker exec "$WORKER_CONTAINER" python /tmp/fetch_details.py --active-only --limit 450 --stop-at-storage-mb 4500

# Historical closed-sale details are not on the urgent active-inventory path,
# but are essential for resale-pair research and later comparable analysis.
# Fetch a bounded backlog each run; per-listing failures remain in detail_logs
# for diagnosis rather than silently dropping records.
docker exec "$WORKER_CONTAINER" python /tmp/fetch_details.py --closed-only --limit 450 --stop-at-storage-mb 4500
echo "[$(date -Is)] live fetcher completed"
