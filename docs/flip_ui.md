# Flip Finder review UI

The separate `flip_ui` container serves the public mission page at
`flips.roozbeh.realtor` and the private labeling workspace at `/admin`.
It reads `flip_finder.flipdb`, joins the two MLS source records by `buy_mls` and
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

The AWS Apache virtual host proxies only to `127.0.0.1:8090`; the container is
not exposed directly to the internet. Obtain the TLS certificate only after the
`flips` DNS record resolves to the AWS host.
