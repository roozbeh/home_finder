#!/bin/sh
# AWS cron invokes the request-only scripts inside this lightweight container.
# Authentication is intentionally performed only by the local auth_token tool.
set -eu

echo "mls_app is ready: using MongoDB-authorized MLS sessions; browser login disabled."
exec tail -f /dev/null
