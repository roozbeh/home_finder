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
CYCLE_STARTED=$(date +%s)
NIGHTLY_SOLD=false
case "${1:-}" in
  --nightly-sold) NIGHTLY_SOLD=true; LOG_FILE="$LOG_DIR/sold_discovery.log" ;;
  "") ;;
  *) echo "Unknown MLS refresh mode: $1" >&2; exit 2 ;;
esac

mkdir -p "$LOG_DIR"
if [[ -f "$LOG_FILE" ]] && [[ $(wc -c < "$LOG_FILE") -ge $MAX_LOG_BYTES ]]; then
  mv -f "$LOG_FILE" "$LOG_FILE.1"
fi
exec >> "$LOG_FILE" 2>&1

echo "[$(date -Is)] live fetcher starting"
exec 9>"$LOCK_FILE"
if "$NIGHTLY_SOLD"; then
  if ! flock -w 780 9; then
    echo "[$(date -Is)] sold discovery skipped: MLS refresh lock unavailable"
    exit 1
  fi
elif ! flock -n 9; then
  echo "[$(date -Is)] skipped: another MLS refresh holds the lock"
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
if "$NIGHTLY_SOLD"; then
  docker exec "$WORKER_CONTAINER" python -u /tmp/search_and_store.py \
    --sold-only --market bay-area --sold-lookback-days 14
  echo "[$(date -Is)] sold discovery completed"
  exit 0
fi

docker cp mls_service/fetch_details.py "$WORKER_CONTAINER":/tmp/fetch_details.py
docker cp mls_service/refresh_inventory.py "$WORKER_CONTAINER":/tmp/refresh_inventory.py
docker exec "$WORKER_CONTAINER" python -u /tmp/refresh_inventory.py \
  --deadline "$((CYCLE_STARTED + ${FLIP_FINDER_CYCLE_SECONDS:-780}))" \
  --limit "${FLIP_FINDER_DETAIL_LIMIT:-25}" \
  --display-check-days "${FLIP_FINDER_DISPLAY_CHECK_DAYS:-75}" --storage-mb 4500
echo "[$(date -Is)] live fetcher completed"
