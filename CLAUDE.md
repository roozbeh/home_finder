# Home Finder repository guide

Start here when working on the MLS pipeline, the buyer site, or the private Flip Finder workspace. [Future work](PLAN_FOR_FUTURE.md) tracks outstanding release, sourcing, analysis and operations tasks. The service runbooks below contain the current commands and verification steps.

## Services and Docker names

| Docker container | Compose service | Purpose |
|---|---|---|
| `mls_web` | `web` | Existing Home Finder chat and MLS browsing at [ai.roozbeh.realtor](https://ai.roozbeh.realtor/). The private [data-status page](https://ai.roozbeh.realtor/admin/data-status) reports ingestion and photo coverage. Code is in `data_service/web/`. |
| `mls_app` | `app` | Remote MLS ingestion worker. The server’s 15-minute cron and nightly sold discovery invoke it. The local buyer preview can run with this container stopped. Code is in `data_service/mls_service/`. |
| `flips_admin` | `flip_ui` | Legacy private Flip Finder workspace with raw MLS and analysis access. It binds loopback port `8090`, and [the private Flip Finder route](https://flips.roozbeh.realtor/admin) proxies to it. Code is in `data_service/flip_ui/`. |
| `flips_public` | `buyer-public` | Buyer search and listing pages backed by read-only Atlas inventory. Local review binds [127.0.0.1:8091](http://127.0.0.1:8091/homes). |
| `moondream-mls-backfill` | `photo_backfill` | Local long-running photo classifier using host Ollama. Writes labels and progress to MongoDB. Its separate Compose project is `home-finder-photo`. |

`docker ps` shows image tags and running container names in separate columns. Use the service key with `docker compose`, and the container name with `docker logs` or `docker inspect`. Docker Desktop also shows containers from other projects, such as Storytelling’s `server-backend-1`.

## Where to work

- **Buyer routes and search** are in `data_service/flip_ui/public_buyer.py`, `buyer_atlas.py`, `buyer_property_types.py`, and `templates/buyer_*.html`. Read the [buyer rollout guide](data_service/flip_ui/BUYER_ROLLOUT.md) for permissions and Atlas settings.
- **Public listing projection** is in `buyer_live_feed.py` and `buyer_publish.py`. Source and listing permissions determine which Atlas fields and photos appear. Buyer links use an opaque ID derived from the source and MLS ID with the existing private key.
- **MLS refresh** starts at `ops/aws/live_fetcher.sh` and `ops/aws/live_fetcher.cron`. The worker uses `data_service/mls_service/refresh_inventory.py`, `search_and_store.py`, and `fetch_details.py`. Remote logs live under `/mnt/extra500/flip_finder/logs/`. Current detail rechecks start at 10 days, the dashboard flags ages at 15 days, and the buyer policy’s 90-day display expiry is independent.
- **Photo work** is in `data_service/photo_categorizer/`. Follow the [photo runbook](data_service/photo_categorizer/ACTIVE_LISTINGS.md) for Ollama, checkpoints, and service commands. The backfill’s running state depends on Docker and Ollama on the Mac. Remote ingestion has its own schedule.
- **Mobile and app setup** are in [the iOS guide](data_service/iOS/CLAUDE.md). [The service guide](data_service/CLAUDE.md) contains subsystem background. Prefer active Compose definitions and cron scripts for current service names and schedules.

## Common local commands

Run from the repository root. All of these select specific services.

```sh
docker compose -p homefinder-atlas-staging -f data_service/docker-compose.buyer-atlas-staging.yml up -d --build
docker compose -f data_service/docker-compose.yml up -d --build flip_ui
docker compose -p home-finder-photo -f data_service/docker-compose.yml \
  --profile photo-backfill up -d --build photo_backfill
docker logs -f moondream-mls-backfill
```

Start or rebuild services individually so the local preview can be reviewed without starting ingestion.

## Testing and release

- Use real eligible inventory for buyer-facing review and isolated fixtures for regression tests. Verify desktop and mobile search, list/map parity, media, and withdrawal behavior.
- Run buyer regressions with `python -m unittest discover -p '*test.py'` from `data_service/flip_ui`. The refresh and photo recovery tests live next to their workers.
- The remote checkout is `/home/ubuntu/home_finder`. Deploy a reviewed GitHub commit with a fast-forward pull, then rebuild only the intended service. Verify the public HTTPS site and the authenticated admin workspace after a release.
- Keep credentials, raw data, generated reports, market exports, screenshots, and private inbox evidence in private storage outside the checkout. The repository contains example settings and fixture-based tests. The root `reports/`, `output/` and `outputs/` names are ignored.
