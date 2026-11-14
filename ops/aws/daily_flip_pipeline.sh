#!/usr/bin/env bash
# Called by systemd.  Keep job state/logs on the attached data volume.
set -Eeuo pipefail

APP_ROOT="${FLIP_FINDER_APP_ROOT:-/home/ubuntu/home_finder}"
DATA_SERVICE_DIR="${APP_ROOT}/data_service"
LOG_DIR="${FLIP_FINDER_LOG_DIR:-/mnt/extra500/flip_finder/logs}"
LOCK_FILE="${FLIP_FINDER_LOCK_FILE:-/mnt/extra500/flip_finder/daily.lock}"
mkdir -p "$LOG_DIR" "$(dirname "$LOCK_FILE")"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "$(date -Is) another daily pipeline run is already active; exiting"
  exit 0
fi

log_file="$LOG_DIR/daily-$(date +%F).log"
exec >>"$log_file" 2>&1
echo "$(date -Is) starting authorized daily flip pipeline"

cd "$DATA_SERVICE_DIR"
compose=(docker-compose -f docker-compose.yml -f docker-compose.pipeline.yml)
"${compose[@]}" run --rm mls_refresh_active
"${compose[@]}" run --rm mls_fetch_new_active_details

# Set FLIP_ENABLE_EMAIL_ALERTS=true only after SMTP/DKIM/SPF configuration.
screen_args=(run --rm flip_screen)
if [[ "${FLIP_ENABLE_EMAIL_ALERTS:-false}" == "true" ]]; then
  # Shellcheck intentionally avoids expanding any credentials; compose reads env.
  screen_args+=(--send-alerts)
fi
"${compose[@]}" "${screen_args[@]}"
echo "$(date -Is) daily flip pipeline completed"
