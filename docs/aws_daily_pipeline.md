# AWS daily flip pipeline

This deployment is intentionally a **scheduled screening aid**, not an
automated offer system.  It executes this sequence:

1. Refresh verified `ACTV` MLS inventory for the configured 101-city Bay Area
   search.
2. Enrich only newly discovered active listings with available details/photos.
3. Read closed records for conservative comparable analysis.
4. Store screening snapshots and alert-deduplication state in the separate
   `flip_finder` database.  It does not write screening data to `mls`.
5. Optionally email only new `VISIT` candidates that have preliminary ARV
   support.  They remain explicitly marked **PRELIMINARY — DO NOT BID**.

## Authorization prerequisite

The existing ConnectMLS login code includes browser automation and bot-detection
behavior.  This deployment does **not** run it.  Before enabling the timer,
obtain written confirmation from the broker/MLS that the intended data feed,
retention, investment screening, and (if applicable) a session shared from an
operator workstation to the server are allowed.  Prefer an official MLS API or
authorized data feed.

If that approval permits the interim shared-session workflow, an operator signs
in normally on an authorized workstation and writes an authorized session into
the existing protected `mls.auth_tokens` store.  The server may use it only for
the configured maximum age (default 12 hours).  It does not keep the session
alive and fails closed when it expires.

For a user-operated browser login helper (no credential filling or bot-check
automation), run on the authorized workstation:

```sh
cd "/Users/roozbeh/Google Drive/iPronto/home_finder/data_service"
set -a; source env; set +a
python3 mls_service/get_cookies_manual.py
```

Complete the normal login and MFA in the visible browser, then press Enter in
the terminal after reaching ConnectMLS.  The helper stores the session without
printing cookie values.

## Server prerequisites

The present AWS machine has a 500 GB volume at `/mnt/extra500`, with Docker
already stored there.  Keep logs and job locks there.  Do not store MLS photos
or secrets in the repository.  The root filesystem is small, so prune or resize
only after reviewing the target and taking a backup.

Deploy the repository so both `data_service/` and `flip_finder/` reside below
`/home/ubuntu/home_finder`.  Set restrictive permissions on `data_service/env`:

```sh
chmod 600 /home/ubuntu/home_finder/data_service/env
```

For alert email, add these *environment variables* to that protected env file
(never commit them):

```text
FLIP_ALERT_SMTP_HOST=...
FLIP_ALERT_SMTP_PORT=587
FLIP_ALERT_SMTP_USER=...
FLIP_ALERT_SMTP_PASSWORD=...
FLIP_ALERT_FROM=alerts@your-notification-subdomain.example
FLIP_ALERT_TO=your-reviewed-inbox@example.com
FLIP_ENABLE_EMAIL_ALERTS=true
```

Use a dedicated notification subdomain with SPF, DKIM, and DMARC, and monitor a
real reply address.  Start with `FLIP_ENABLE_EMAIL_ALERTS=false`; review the
stored results first.

## Install the timer

```sh
sudo install -m 755 /home/ubuntu/home_finder/ops/aws/daily_flip_pipeline.sh /home/ubuntu/home_finder/ops/aws/daily_flip_pipeline.sh
sudo install -m 644 /home/ubuntu/home_finder/ops/aws/flip-finder-daily.service /etc/systemd/system/flip-finder-daily.service
sudo install -m 644 /home/ubuntu/home_finder/ops/aws/flip-finder-daily.timer /etc/systemd/system/flip-finder-daily.timer
sudo systemctl daemon-reload
sudo systemctl enable --now flip-finder-daily.timer
systemctl list-timers flip-finder-daily.timer
```

First dry-run the sequence manually after a fresh authorized session is
available.  Inspect `/mnt/extra500/flip_finder/logs/` and the separate
`flip_finder.candidate_snapshots` collection before enabling email.

## Operational safeguards

- A filesystem lock prevents overlapping runs.
- A cookie older than 12 hours stops the refresh instead of attempting login.
- Alerts are fingerprinted; unchanged candidates are not repeatedly emailed.
- SMTP is opt-in through configuration; no email is sent from a default build.
- The classifier is deterministic and evidence-first.  It must not be marketed
  as a valuation or investment recommendation.
