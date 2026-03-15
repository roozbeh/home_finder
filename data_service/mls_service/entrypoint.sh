#!/bin/bash
# entrypoint.sh — starts the status web server, cron, and an initial fetch.
set -e

# 1. Persist relevant env vars to a file that cron jobs can source.
#    Cron does not inherit environment variables from the parent process.
printenv | grep -E '^(MLS_|MONGO_|HEADLESS|COOKIES|WDM_|CHROME)' \
  | sed 's/^\(.*=.*\)$/export \1/' > /etc/profile.d/mls-env.sh
chmod 644 /etc/profile.d/mls-env.sh

# 2. Install crontab — run get_cookies then search_and_store every day at 02:00.
(crontab -l 2>/dev/null; echo \
  "0 2 * * * . /etc/profile.d/mls-env.sh && cd /app && python get_cookies.py && python search_and_store.py >> /var/log/mls_cron.log 2>&1") \
  | crontab -

touch /var/log/mls_cron.log

# 3. Start cron daemon in background.
cron
echo "Cron started. Daily fetch scheduled at 02:00."

# 4. Start the status web server in the background.
echo ""
echo "=== Starting status server on :8081 ==="
python /app/status_server.py >> /var/log/mls_status.log 2>&1 &
echo "Status server started (pid $!)."

# 5. Run an initial fetch so the database is populated immediately on startup.
echo ""
echo "=== Running initial fetch ==="
python /app/get_cookies.py && python /app/search_and_store.py \
  || echo "⚠  Initial fetch failed — will retry at 02:00"

# 6. Keep container alive by tailing the cron log.
echo ""
echo "=== Container ready. Tailing cron log (status UI → http://localhost:8081) ==="
tail -f /var/log/mls_cron.log
