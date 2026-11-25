# HomeFinder agent handoff

Use this file to resume work after a context reset. Check the working tree before editing. This checkout has in-progress changes across the Flip UI, MLS cleanup, photo classification, and renovation work. Preserve existing edits and keep unrelated work out of the current task.

## Where to pick up

- [cluster_cleanup.md](cluster_cleanup.md) is the Atlas and SQLite cleanup plan. Read **Implementation progress** for completed stages, verification, and remaining decisions. Update that section when you verify or complete another migration step.
- [ACTIVE_LISTINGS.md](data_service/photo_categorizer/ACTIVE_LISTINGS.md) has Moondream commands, checkpoints, and operational details.
- [BUYER_PUBLISHER.md](data_service/flip_ui/BUYER_PUBLISHER.md) and [BUYER_ROLLOUT.md](data_service/flip_ui/BUYER_ROLLOUT.md) record the publisher retirement and the Atlas MLS display policy. Read the cleanup plan for the newer Atlas preview.
- [RENOVATION_MODELS.md](data_service/photo_categorizer/RENOVATION_MODELS.md) covers the separate renovation model and cost work. [PLAN_FOR_FUTURE.md](PLAN_FOR_FUTURE.md) covers other repository work.

## Local services

| URL | Service | Property data |
| --- | --- | --- |
| [8092 public Atlas preview](http://127.0.0.1:8092/homes) | Compose project `homefinder-atlas-staging` | Direct reads from `mls.mls_listings` |
| [8091 local public service](http://127.0.0.1:8091/homes) | `flips_public` | Direct reads from `mls.mls_listings` |
| [8090 admin](http://127.0.0.1:8090/) | `flips_admin` | Atlas listings and private FlipDB data |

These services were running when checked on November 24, 2026 UTC. Use `docker ps` or a local HTTP check for current status. The public Atlas preview is bound to loopback. Its dedicated Atlas account can read only `mls.mls_listings` on the `mlsdata` cluster. The ignored `data_service/buyer-atlas-staging.env` supplies `BUYER_ATLAS_URI` and `FLIP_UI_SECRET_KEY`. Use the existing publication directory for the MLS policy and opaque URL key. Keep credentials and key contents out of logs, commits, and handoff files.

## Atlas listings and preview

- Use `mls.mls_listings` as the canonical inventory. Listing details are embedded in `details`, gallery references in `photos` and `details.PRIMARY_PHOTO_URI`, and labels in `gallery_photo_labels`. The photo bytes come from approved MLS photo servers.
- The preview implementation is in [buyer_atlas.py](data_service/flip_ui/buyer_atlas.py) and [public_buyer.py](data_service/flip_ui/public_buyer.py). Preserve the existing opaque URLs, explicit public field projection, photo allowlist, and MLS display grants. Stale active listings show the recorded status and last inventory check. Display permission expires independently. Recheck MLS eligibility for detail, map previews, and media.
- Use [1149 Berkeley](http://127.0.0.1:8092/homes/30f60b51f4db6b9f63f8771c5dbdf7ed) as the first preview smoke check. The Atlas filter is `{ SOURCE_MLS: "MLSLISTINGS", LISTING_ID: "ML82065431" }`. The pilot had 10 labels across five categories. [buyer_atlas_test.py](data_service/flip_ui/buyer_atlas_test.py) covers stale inventory, price and photo changes, opt-outs, and category rendering.

## Moondream worker and migration

- The live worker is `moondream-mls-backfill`, service `photo_backfill` in Compose project `home-finder-photo`. It uses local Ollama with `moondream:latest` and defaults to eight photo requests in flight. Run Docker Compose commands from the repository root. Rebuild and recreate the service to pick up code changes. A plain container restart reuses the existing image.
- The worker first reads both sides of every `flip_finder.flipdb` pair and deduplicates the listing IDs. The November cutover found 1,101 listings in this queue. After the FlipDB pass, it resumes the active and sold queues. All three phases save new labels on the corresponding `mls.mls_listings` document. Job progress lives in `flip_finder.mls_photo_backfill_jobs`.
- Follow progress with `docker logs --tail 20 -f moondream-mls-backfill`. **Current gallery** counts photos checked in the current listing. The listing count advances after the gallery finishes. Stopping the log tail leaves the worker running.
- [bulk_moondream.py](data_service/photo_categorizer/bulk_moondream.py) is the worker. [gallery_photo_labels.py](data_service/photo_categorizer/gallery_photo_labels.py) centralizes photo identity and guarded merges. Match the current gallery by stable photo identity and guard writes against concurrent gallery or label changes. Preserve manual reviews and targeted classifications ahead of bulk Moondream labels. Historical pair data remains available for pair-specific review.
- The [legacy importer](data_service/photo_categorizer/import_gallery_photo_labels.py) defaults to a dry run. `--apply` writes matched labels, and `--legacy-since` selects the old Moondream records at or after an inclusive UTC cutoff. The baseline import of 102,526 labels and the final catch-up of 198 labels both completed. The replacement was verified writing new labels directly to Atlas. The catch-up was specific to that cutover.

## Approval boundaries

- Bring canonical schema changes, Atlas indexes or access changes, retention, production cutover, and deletions to Roozbeh before acting. Keep the production deployment and legacy rollback resources available while the remaining decisions are open.
- The cleanup target uses canonical Atlas inventory with zero MongoDB views and zero replacement inventory copies. Retire SQLite workflows and their stores without importing SQLite data to Atlas.
- Keep the existing Atlas collections and local data until their removal is approved. Saved estimates in the public preview and historical pair-specific photo review parity are still open. See [cluster_cleanup.md](cluster_cleanup.md) for the Gemma pilot, collection retirement, and SQLite rollout steps.
