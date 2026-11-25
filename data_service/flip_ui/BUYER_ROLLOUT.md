# Flip Finder buyer site rollout

Local staging and production read `mls.mls_listings` directly through the dedicated read-only Atlas account. The app evaluates MLS display permissions for search, detail, map previews, and media. Apache proxies public pages to `flips_public` and `/admin` to the separate private `flips_admin` app.

## Local Atlas staging

From the repository root:

```sh
docker compose -p homefinder-atlas-staging -f data_service/docker-compose.buyer-atlas-staging.yml up -d --build
```

Open [search on port 8092](http://127.0.0.1:8092/homes). The ignored `data_service/buyer-atlas-staging.env` supplies `BUYER_ATLAS_URI` and `FLIP_UI_SECRET_KEY`. The production Compose file also runs locally on port 8091 with those settings.

## MLS display policy

In the read-only MLS policy directory, provide `live-policy.json` with a `sources` object keyed by the exact feed `SOURCE_MLS` value. Each authorized source needs:

- `production_approved`, `basis`, `effective`, `refresh_by`, and a source-level `allow` object.
- `internet_display_values`, mapping each reviewed `details.INTERNET_DISPLAY` value to its permitted field names. An unknown or omitted value excludes the listing. `internet_sites_values` must also permit the listing’s site-display setting. The default recognized value is `Yes - All Sites`. The reader excludes listings with `No - No Sites`. Select the address and geolocation fields only where the value permits both. Confirm the official meanings for each source.
- `listing_refresh_hours`, the maximum age of the active summary. The optional `permission_refresh_hours` sets the maximum age of the permission-bearing detail and defaults to the same interval. Refresh the details with the existing MLS worker. A recently seen summary alone cannot renew an old permission.
- `credit_name` for the approved source label where needed. Use optional `reviewed_listings` entries keyed by `SOURCE_MLS:LISTING_ID` to attach rights-cleared, re-encoded images and reviewed analysis. Enable `publish_source_photos` where the agreement allows photos to be served from the source CDN. The buyer server fetches only from its allowlisted media hosts, removes metadata and rechecks publication before returning bytes. These features also require permission in both source and listing policy.

Approve each source and field mapping with the responsible broker. Record scope, attribution, permission effective times, refresh deadlines, and seller opt-outs. Review authorization for derived analysis and access to any additional data separately. Keep policy files, approved local assets, and the existing opaque ID key outside Git.

## Production

On AWS, run from `data_service`:

```sh
docker-compose --env-file /mnt/extra500/flip_finder/buyer-public/compose.env \
  -p flip-buyer -f docker-compose.buyer-production.yml up -d --build buyer-public
```

`BUYER_PRODUCTION_ENV_FILE` points to the protected buyer-only env file. `BUYER_PUBLISH_INPUT_DIR` points to the existing policy and opaque ID key. Verify the app on loopback, then verify search, listings, media, and authenticated admin access through HTTPS. Keep the Apache configuration backup and any previously verified Atlas application image available for rollback.
