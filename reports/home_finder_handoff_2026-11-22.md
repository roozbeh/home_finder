# Home Finder handoff

State checked on November 22, 2026. Use this as the starting point for buyer UI, MLS ingestion, photo processing, or deployment work.

## Start here

- Read the [original request, Aida’s feedback, and Terra’s notes](public_fixer_upper_launch_plan_2026-11-21.md) and the [implementation checklist](terra_public_buyer_implementation_2026-11-21.md). The checklist describes the original target. Use the current state below when it differs from historical notes.
- Anonymous browsing uses real MLS listings cleared by the publisher. Keep fictional inventory out of buyer-facing review and production. Isolated automated tests can use fixtures, and earlier fixture screenshots are test evidence.
- Keep the same buyer application behavior in local review and deployment. The destination and approved configuration determine what is published. Preserve the private admin and demo role boundaries.
- The main working checkout has unrelated edits, untracked buyer work, and a branch history that diverges from GitHub `main`. Inspect the worktree before editing or staging. Keep release commits scoped and use a clean checkout based on GitHub `main` when packaging work for the remote. The refresh, dashboard, and buyer catalog publisher are on GitHub and on the remote. The new public web routes, buyer submission flows, and bulk photo worker remain local.

## Review links and deployment boundaries

| Surface | State |
|---|---|
| Local buyer [homepage](http://127.0.0.1:8091/) and [homes search](http://127.0.0.1:8091/homes) | Docker shows this web container as `flips_public`. Compose still calls its local service `buyer-preview`. The [desktop](buyer_live_review_2026-11-21/live-property-type-desktop.png) and [mobile](buyer_live_review_2026-11-21/live-property-type-mobile.png) captures show the grouped filter on live inventory. With the local publisher paused, this site reads the last local catalog. |
| Local [Richmond detail](http://127.0.0.1:8091/homes/10c2c035c90de5f1c7432cf72a9786e5?from_view=search) | HTTP 200 on the handoff check. On the earlier end-to-end check, all four image requests returned JPEGs and the page showed Parking and garage, Outdoor spaces, and Other. Availability follows the live catalog. |
| Local [agent application](http://127.0.0.1:8091/agents/partner), [leads](http://127.0.0.1:8091/admin/leads), and [partner queue](http://127.0.0.1:8091/admin/partners) | Form and queue code passed earlier fixture workflows. The current live-data preview returned HTTP 503 on the partner form. Its approved-mode form gate requires verified recipient and brokerage identity settings. Recheck the live-data click → receipt → private record paths after configuring the identity. |
| Local [legacy workspace](http://127.0.0.1:8090/admin/active) and [existing Flip Finder host](https://flips.roozbeh.realtor/) | The legacy private container is named `flips_admin` in Docker, with Compose service key `flip_ui`. External HTTPS launch of the new buyer UI is pending. |
| Remote buyer catalog publisher | The remote operator service writes a private catalog every 15 minutes under `/mnt/extra500/flip_finder/buyer-publisher/catalog`. It exposes no web port. The local preview still reads its own catalog volume. |
| Remote [Admin Data Status](https://ai.roozbeh.realtor/admin/data-status) | Live and admin-only. External signed-out access redirected to admin login. The deployed app rendered the metrics with a server-side admin test session. |

GitHub release commits include `790d2ec` and `84d4cfa` for MLS refresh, `f59f0d7` for the admin dashboard, and `32f6f8d`, `8bdd2a3`, `4455402` and `a23aeb9` for the buyer publisher and type normalization. The remote checkout is `/home/ubuntu/home_finder` on `ubuntu@ipronto.net`. For dashboard-only changes, rebuild and recreate `web` with Compose and `--no-deps` after a fast-forward pull. The cron script copies its Python entry points into the existing remote `mls_app` worker on each run. The local `mls_app` is paused. The [publisher runbook](../data_service/flip_ui/BUYER_PUBLISHER.md) documents the separate Compose service and its private configuration.

## Buyer UI and analysis

| Checklist area | Implementation and next verification |
|---|---|
| 1. Permissioned publication | `buyer_live_feed.py` reads the MLS summaries and details. `buyer_publish.py` projects an allowlisted SQLite catalog. `buyer_catalog.py` is the buyer web app’s reader. Check source and listing grants for fields, address, coordinates, media, valuation and comps. Keep private remarks, showing and access instructions, owner and buyer information, agent-only data, and restricted documents or history out of public output. Test withdrawal after warming listing, search, media and forms. |
| 2. Homepage and search | `templates/landing.html`, `templates/buyer_search.html`, `static/buyer-search-filters.js`, and `static/buyer-map.js`. Keep the landing artwork and **Browse homes** as the primary action. Public search uses live MLS records including eligible homes awaiting analysis. The list is paginated and the Leaflet map can cluster all matching locations permitted for display. Filters include sale/rent/sold, grouped min/max price, beds/baths, property type and additional fields. Recheck desktop, mobile, back navigation, filtering, clusters and map/server result parity. |
| 3–4. Detail, buyer contact, tours and partners | `public_buyer.py`, `buyer_submissions.py`, `buyer_notify.py`, and `templates/buyer_*.html`. Keep agent and tour actions beside the price on desktop and in a persistent mobile bar. Show Roozbeh as buyer contact and label listing attribution separately where allowed. Forms validate, use idempotency tokens, issue receipts and persist to private queues. A tour is pending confirmation. Live identity configuration and a fresh live-data walkthrough are open. |
| 5. Renovation | `renovation_costs.py`, `buyer_analysis.py`, and `buyer_analysis_review.py`. Structured sourced lines, exclusive alternatives, reconciled totals, compact expandable cost details and Fairfield arithmetic are covered by tests. Real cost evidence and permission to publish analysis are separate inputs. |
| 6. Coverage and processing | `buyer_worker.py` is the analysis processing and metering path, with resume, coverage, budget controls and a 100-listing pilot. Its deterministic fixture executor is test infrastructure. The local Mongo/Ollama photo classification backfill below is already real-data processing and saves room categories. The backfill does not calculate renovation budgets. Paid analysis and a production pilot require approved inputs and budget. |
| 7. Comps, historical projects and evaluation | Permissioned panels are in `buyer_analysis.py` and `templates/buyer_analysis_panels.html`. `buyer_evaluate.py` runs historical evaluation with protections against date and repeat-parcel leakage. Obtain real, licensed sale and paid-project evidence before claiming production coverage or model accuracy. |
| 8. Partner supply and rollout | `buyer_supply.py` provides private review and publication controls. Fixture tests cover the workflow. A real partner needs evidence of advertising, media and showing rights plus an agreed withdrawal process. Follow the [buyer rollout runbook](../data_service/flip_ui/BUYER_ROLLOUT.md) and [review workflow](../data_service/flip_ui/REVIEW_WORKFLOW.md). |

The `/homes/<id>` URL uses the first 32 hex characters of HMAC-SHA256 over the exact `SOURCE_MLS:LISTING_ID` pair. The private `BUYER_PUBLIC_ID_KEY` or publisher `public-id-key` file must match the running catalog. Mongo `_id` is used internally for photo checkpoints. A syntactically valid buyer URL returns 404 if the listing is outside the current published catalog. Keep credentials, key material, live policy inputs and submission stores outside commits and handoff artifacts.

### Verification entry points

From the repository root, rebuild only the local buyer services you need. The live-data publisher needs its private inputs and Mongo access.

```sh
docker compose -f data_service/docker-compose.buyer-preview.yml --profile live-feed \
  up --build -d buyer-preview buyer-live-publisher
docker compose -f data_service/docker-compose.buyer-preview.yml exec -T buyer-preview \
  python -B -m unittest buyer_public_test buyer_analysis_test \
  buyer_analysis_review_test buyer_worker_test tests.test_buyer_supply
```

The test command above passed 25 tests on the local preview image. Use the [live UI screenshots](buyer_live_review_2026-11-21/) for visual context. The [earlier fixture browser results](buyer_preview_2026-11-21/browser-results.json) cover desktop inquiry, mobile tour, partner application, duplicate retry, signed-in inbox checks, and denial for anonymous and demo accounts. The earlier artifacts include test records. Repeat the journey with an approved test recipient on the current live-data build. For production, require broker and source decisions, verified identity, private operator ingress, and a fresh external HTTPS walkthrough with approved inventory before declaring launch.

## MLS refresh and admin metrics

- The remote 15-minute job is `ops/aws/live_fetcher.sh` → `data_service/mls_service/refresh_inventory.py`. It records listings seen in active search, queues a status check after repeated absences from complete city searches, and updates status from MLS details. An absence alone does not prove a sale or withdrawal. For example, Atherton MLS `41121209` had a stored `ACTV` summary and a newer detail status of `WC`.
- Detail work orders status rechecks, old display checks for current inventory, new active enrichment, recent sold, historical sold, then gallery repair. The extra status/display allowance is 25 per cycle, with up to five status rechecks. The default 780-second request budget leaves about two minutes before the next 15-minute tick. The existing storage safeguard applies. Later remote log verification showed completed cycles with 257 and 260 successful follow-ups and zero failures.
- Nightly sold discovery uses the same MLS lock, searches a rolling 14-day close window, and queues the resulting records for the recurring job. It is installed for `03:07` in the server’s cron timezone. The first manually triggered run found 1,531 closed listings, including 1,291 newly inserted. Logs are `/mnt/extra500/flip_finder/logs/live_fetcher.log` and `/mnt/extra500/flip_finder/logs/sold_discovery.log`.
- [Admin Data Status](https://ai.roozbeh.realtor/admin/data-status) counts unique MLS listings with at least one saved category across `flip_finder.mls_photo_classifications`, `active_photo_classifications`, and `photo_classifications`. It also counts stored `ACTV` listings with stale inventory and stale details independently. Inventory age uses `active_last_seen_at` with `_updated_at` as a legacy fallback. Detail age uses `details_fetched`. The counts update on page load.
- The dashboard flags both ages at **15 days**. The latest cron default starts routine detail checks at **10 days** to make room for retries. When both status queues have work, the standard five slots reserve three for inventory older than 15 days and two for recent misses. The job recognizes verified aliases for Redwood City, Hillsborough, and South San Francisco. Portola in Plumas County is outside the active search. The buyer policy’s **90-day** display expiry is a separate rule.
- Local tests for the deployed refresh exercised MongoDB with mocked MLS responses, including two-snapshot status reconciliation, withdrawal, priority and deadline. The tests live in `data_service/mls_service/refresh_inventory_test.py` and use `MLS_REFRESH_TEST_URI` for an isolated database. Dashboard counts are tested in `data_service/web/admin_inventory_metrics_test.py`.

## Moondream photo backfill

Local Docker worker `data_service/photo_categorizer/bulk_moondream.py` calls host Ollama using `moondream:latest`. It prioritizes stored active listings with a fresh detail timestamp, then remaining active listings, then sold. This order uses the timestamp. The buyer publisher independently checks current inventory and permission fields. The publisher imports successful saved categories for currently published photos, with existing categorization taking precedence.

The earlier disposable container disappeared with its exit logs. The November 22 recovery resumed at 286 active galleries scanned. The new service retains exit logs, retries upstream failures, bounds connection timeouts and restarts after a failed process. A separate failure test verified Docker restarted the worker process once after an injected nonzero exit. `running` in Mongo is a saved state. Check the Docker container for live process status.

```sh
docker compose -p home-finder-photo -f data_service/docker-compose.yml \
  --profile photo-backfill up -d --build photo_backfill
docker logs -f moondream-mls-backfill
```

Start one backfill container for the checkpoint. This Mac runs Ollama as a Homebrew service. The recovery used eight workers and confirmed Ollama runner `-np 8`. On a 24-photo sample, two, four and eight workers measured 25.6, 63.9 and 69.9 photos per minute with downloads completed before timing. See the [backfill runbook](../data_service/photo_categorizer/ACTIVE_LISTINGS.md) for status, stop and resume commands. Results are in `flip_finder.mls_photo_classifications` and progress in `flip_finder.mls_photo_backfill_jobs`. Saved labels are reused across restarts. The [Haiku, Gemma and Moondream comparison](haiku_gemma_photo_comparison_2026-11-21.html) records the earlier five-photo benchmark.

## Suggested first steps for the next session

1. **UI work** starts with the signed-out live preview and the checklist section being changed. Verify scoped worktree changes before rebuilding. Fix the verified-identity configuration if the goal includes forms, then recheck all three receipts in the private queues.
2. **Pipeline work** starts with the remote cron logs and the dashboard. Determine whether the goal is 15-day operational freshness or protection against the 90-day publication expiry. Measure queue throughput and failed status checks before changing budgets or schedules.
3. **Photo work** starts with the saved checkpoint and Docker liveness check. Verify a published listing’s category groups and image URLs after the catalog refresh. Listings without current publication can have valid saved photo labels.
4. **Release work** starts from a clean checkout, packages the intended files only, and verifies the exact destination after deploy. Read the production rollout and privately obtain any outstanding approvals before routing public traffic to the new buyer UI.
