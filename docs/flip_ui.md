# Flip Finder review UI

The `flip_ui` service runs as `flips_admin` and serves the sign-in and private
labeling workspace at `flips.roozbeh.realtor/admin`. It reads `flip_finder.flipdb`,
joins the two MLS source records by `buy_mls` and
`sell_mls`, and writes reviewer labels only to `flip_finder.flip_labels`.

Set these values in the protected `data_service/env` file on each host (never
commit them):

```text
FLIP_UI_ADMIN_USERNAME=admin
FLIP_UI_ADMIN_PASSWORD=<a-long-unique-password>
FLIP_UI_SECRET_KEY=<a-long-random-session-secret>
FLIP_UI_COOKIE_SECURE=true
```

If `FLIP_UI_ADMIN_PASSWORD` is not set, the app temporarily uses the existing
`ADMIN_PASSWORD` value when present. A dedicated Flip UI password is preferred.

Run locally:

```sh
cd data_service
docker compose up -d --build flip_ui
# http://localhost:8090
```

The AWS HTTPS virtual host proxies `/admin` and its subpaths to `flips_admin`
at `127.0.0.1:8090`. It sends the homepage, `/homes`, `/static`, and the
agent partner route to `flips_public` at `127.0.0.1:8091`. The public service
uses `data_service/docker-compose.buyer-production.yml` and mounts the existing
SQLite catalog from the approved buyer publisher read-only. On AWS, use the
`flip-buyer` Compose project and
`--env-file /mnt/extra500/flip_finder/buyer-public/compose.env` to manage
`buyer-public`.
