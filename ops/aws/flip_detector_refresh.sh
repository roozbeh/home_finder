#!/usr/bin/env bash
# Materialize the derived flip_finder.flipdb research collection once daily.
# It reads mls.mls_listings and writes only separate flip-finder collections.
set -Eeuo pipefail

APP_ROOT="${FLIP_FINDER_APP_ROOT:-/home/ubuntu/home_finder}"
DATA_SERVICE_DIR="${APP_ROOT}/data_service"
LOG_DIR="${FLIP_FINDER_LOG_DIR:-/mnt/extra500/flip_finder/logs}"
LOG_FILE="${LOG_DIR}/flip_detector_refresh.log"
LOCK_FILE="${LOG_DIR}/flip_detector_refresh.lock"
MAX_LOG_BYTES=52428800

mkdir -p "$LOG_DIR"
if [[ -f "$LOG_FILE" ]] && [[ $(wc -c < "$LOG_FILE") -ge $MAX_LOG_BYTES ]]; then
  mv -f "$LOG_FILE" "$LOG_FILE.1"
fi
exec >>"$LOG_FILE" 2>&1

echo "[$(date -Is)] flip detector refresh starting"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "[$(date -Is)] skipped: another flip detector refresh is already active"
  exit 0
fi

cd "$DATA_SERVICE_DIR"
docker-compose -f docker-compose.yml -f docker-compose.pipeline.yml run --rm flip_detector_refresh
echo "[$(date -Is)] flip detector refresh completed"
