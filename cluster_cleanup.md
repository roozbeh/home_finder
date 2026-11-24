# HomeFinder database inventory and cleanup plan

**Inventory and Atlas verification date:** November 22, 2026, Pacific time

Remove SQLite and retire its buyer workflows across production, preview, local development, and tests. SQLite records will be removed with their stores. Use the existing Atlas deployment for HomeFinder and FlipFinder. Public and admin property search and listing views use `mls.mls_listings`, including its embedded `details` and gallery fields. Public reads use dedicated read-only credentials and an explicit field allowlist. The target is one public search experience, with zero MongoDB views and zero inventory copies.

The public FlipFinder service currently runs without MongoDB credentials. A separate publisher reads Atlas, applies display and freshness checks, and writes the SQLite `homes` and `media` tables. The site reads that file for search, counts, maps, listing details, and photos. This creates a duplicate inventory and gives search its own refresh schedule. The running local buyer service has a 105 MiB copy with 7,676 listing rows and 295,204 media rows. [Buyer deployment](data_service/docker-compose.buyer-production.yml) · [Live publisher](data_service/flip_ui/buyer_live_feed.py) · [Current search backend](data_service/flip_ui/buyer_catalog.py)

## Deployment inventory

| Deployment | Databases and scope | Finding |
| --- | --- | --- |
| Atlas `HomeFinder / mlsdata` | AWS Oregon, Flex, MongoDB 8.0.39. `mls` and `flip_finder` contain 22 application collections. | The connected Atlas organization exposes one project and one hosted cluster. The authenticated connection also showed `admin` and the internal `local.oplog.rs`. No application views were visible. |
| Docker `data_service_mongo_data` | Older stopped local HomeFinder MongoDB volume, 345 MiB. | Four application collections. The original `mls_mongo` container is stopped. |
| Docker `escrape_mongo_data` | Older local MongoDB volume associated with the `escrape` Compose project, 304 MiB. | Four application collections. The current Docker container list shows no attached `escrape` MongoDB service. |
| Local buyer Docker volumes | Six SQLite files across the inspected live and preview mounts. | Two are rebuildable inventory copies. The other files store submissions, workflow state, or preview supply data. |
| AWS HomeFinder deployment | SQLite inventory at `/mnt/extra500/flip_finder/buyer-publisher/catalog/catalog.sqlite`. | The Docker publisher was running and writing this copy. |

Both old MongoDB volumes were examined from isolated copies. The machine also has stopped PostgreSQL and MySQL containers for other Compose projects. Their application schemas are outside this HomeFinder inventory.

## Atlas collections

Counts are point-in-time document counts from collection metadata. Running workers can change them. The latest read counted 42,335 listing documents and 42,335 embedded `details` objects. Atlas collection metadata, an exact document count, and a first-document lookup each confirmed the separate `mls.mls_details` collection is empty. The four active status codes totaled 9,728 records across all sources and geographies. This total describes the unfiltered feed.

| Database and collection | Documents | Current use | Planned action |
| --- | ---: | --- | --- |
| `mls.mls_listings` | 42,335 | Canonical listings with status, embedded details, galleries, and inventory observations. | **Keep.** Use for public and admin property search and listing views. |
| `mls.mls_details` | 0 | Legacy namespace initialized with three indexes. | **Drop after preflight.** Remove the initializer and obsolete documentation, verify zero again, then remove the collection. |
| `mls.auth_tokens` | 14 | MLS ingestion authentication and token history. | **Keep private.** Review expired-token retention separately. |
| `mls.mls_runs` | 2,691 | Run summaries and operational health. | **Keep.** Define retention based on operational use. |
| `mls.detail_logs` | 74,200 | Per-listing detail-fetch events. | **Keep.** Review readers and necessary lookback before setting retention. |
| `mls.gallery_repair_locks` | 0 | Gallery repair inserts a lock while it runs and deletes it when done. | **Keep while gallery repair is in service.** Check retirement with that job. |
| `mls.agent_bcard_emails` | 1,068 | Agent email enrichment cache keyed by DRE. | **Consolidation candidate.** Check value and provenance parity with `agent_metrics`. |
| `mls.agent_metrics` | 2,703 | Agent statistics and outreach data. | **Keep.** Evaluate adding existing email provenance as part of an approved consolidation. |
| `mls.users` | 10 | HomeFinder account records. | **Keep.** |
| `mls.chat_sessions` | 66 | Conversation records linked to accounts. | **Keep** with the account deletion workflow. |
| `mls.listing_feedback` | 2 | User feedback on listings. | **Keep** with the account deletion workflow. |
| `mls.contacts` | 3 | Contacts from the original HomeFinder flow. | **Keep** for the current workflow. Retire the separate SQLite buyer inbox with its own data. |
| `flip_finder.flipdb` | 553 | Detected historical buy and sell pairs. | **Keep** as the derived historical dataset. |
| `flip_finder.flipdb_runs` | 1 | Flip detector run records. | **Keep** while there is a consumer. Review retention and logging consolidation. |
| `flip_finder.flip_labels` | 11 | Saved reviewer labels on historical pairs. | **Keep** reviewer decisions. |
| `flip_finder.mls_photo_classifications` | 45,434 | Bulk Moondream classifications and photo results. | **Proposed retirement after migration.** Move current gallery labels into the listing documents and verify Moondream resumes from the new layout. |
| `flip_finder.active_photo_classifications` | 299 | Targeted active-listing gallery labels, including the data path for manual labels. | **Proposed retirement after migration.** Embed current labels and preserve manual review precedence and model provenance. |
| `flip_finder.photo_classifications` | 695 | Historical flip-pair labels with before and after context. | **Proposed retirement after migration.** Embed matching gallery labels and verify pair and side behavior in the historical review. |
| `flip_finder.mls_photo_backfill_jobs` | 1 | Live Moondream worker checkpoint. | **Keep** worker ownership and resume state. |
| `flip_finder.active_renovation_insights` | 10 | Older active-listing assessments and purchase-analysis inputs. | **Keep.** Review consolidation only after versioned readers can preserve existing recommendations. |
| `flip_finder.active_renovation_cost_drafts` | 2 | One failed attempt and one partial result from the same Gemma pilot property. | **Retire.** Remove the pilot writer and reader, verify the legacy estimate still works, and drop the collection after approval. |
| `flip_finder.renovation_insights` | 11 | Assessments for historical buy and sell pairs. | **Keep** pair relationships and reviewed scopes. |

[Legacy MLS initializer](data_service/db/init.js) · [Current MLS detail worker](data_service/mls_service/fetch_details.py) · [Agent enrichment](data_service/mls_service/enrich_agent_emails.py) · [Photo worker](data_service/photo_categorizer/bulk_moondream.py) · [Renovation worker](data_service/photo_categorizer/cost_backfill.py)

## Measured collection overlap

- **Photo classifications.** The targeted active-label collection and bulk collection share **10 exact listing-ID and photo-URL pairs**. The historical flip-pair collection shares zero exact pairs with the other two. Photo URL aliases and model or review revisions can affect a deeper comparison. All **299** targeted rows and **695** historical rows matched a current gallery in `mls.mls_listings` after normalizing the supported MLS image URL variants. The largest listing document measured about **34 KiB** and the largest gallery had **202** URLs. These figures support embedding compact current labels with the listing and measuring document sizes after backfill.
- **Active renovation assessments.** The two Gemma documents are a failed attempt and a partial result for the same listing. That listing also has a legacy assessment. Remove the Gemma pilot collection after its writer and reader are retired. Preserve the separately stored legacy assessment.
- **Agent outreach.** All **1,068** DRE keys in the email cache also occur in `agent_metrics`. Confirm whether the cached value is already reflected in the current primary email and preserve its source and fetch date. This is the clearest collection consolidation candidate after the empty MLS namespace.

## Proposed photo classification layout

- Store one current classification per current gallery image in its existing `mls.mls_listings` document. Match it using a stable photo identifier that recognizes the provider's URL variants. Store the category, caption, model or human provenance, and classification time. Keep it outside the provider-owned `details` payload so an MLS refresh preserves it. Use field-level updates and guard against a gallery changing while inference runs. Keep a manual label ahead of automated labels.
- Preserve the historical flip presentation by reading the buy and sell listing identities from the existing pair. Verify that historical image selection, pair-side labels, and manual review behavior match. Resolve any label conflict or pair-specific override before removing its legacy storage.
- Switch Moondream and the single-listing and historical classification tools to the approved layout. Keep run checkpoints in the existing `mls_photo_backfill_jobs` collection. Decide the retention of failed attempts and old model outputs before cutover. Update the public and admin photo group readers and inventory metrics. Confirm that retries resume, a gallery refresh cannot attach an old label to a new photo, and the writers do not recreate retired collections.
- After Roozbeh approves the embedded schema and the staging results, retire `active_photo_classifications` and `photo_classifications`. Retire `mls_photo_classifications` when its bulk history disposition is approved and all readers and writers use the embedded labels. Keep current gallery label counts and sampled historical reviews identical across the cutover.

[Photo provider URL handling](data_service/flip_ui/buyer_media.py) · [Historical pair photo grouping](data_service/flip_ui/app.py#L228) · [MongoDB embedded data](https://www.mongodb.com/docs/manual/data-modeling/embedding/)

## Retirement of the Gemma pilot draft collection

1. Confirm the collection still contains only the two pilot records and record the draft IDs and states. Review the existing one-property HTML report for any pilot output Roozbeh wants to retain.
2. Disable and remove the Gemma pilot job and its publication dependency. Remove the `cost-estimation-backfill` entry point and current draft reader wiring, or disable the writer explicitly while any approved code is retained. Verify the separate Haiku and reviewed assessment paths still render and photo classification still runs.
3. After approval, drop `flip_finder.active_renovation_cost_drafts` and verify a normal app startup and publisher or Atlas search run leave the namespace absent. The cleanup creates no replacement collection.

[Gemma pilot worker](data_service/photo_categorizer/cost_backfill.py) · [Current draft consumer](data_service/flip_ui/buyer_live_feed.py)

## Older local MongoDB volumes

| Application collection | `data_service_mongo_data` | `escrape_mongo_data` |
| --- | ---: | ---: |
| `mls.mls_listings` | 573 | 576 |
| `mls.mls_details` | 0 | 0 |
| `mls.auth_tokens` | 4 | 6 |
| `mls.mls_runs` | 8 | 11 |

Each volume also contains MongoDB-managed `admin.system.users`, `admin.system.version`, `config.system.sessions`, and `local.startup_log`.

The two volumes share 544 listing keys. All **573 of 573** HomeFinder keys appear in Atlas. For `escrape`, **564 of 576** keys appear in Atlas. The remaining **12** are absent from both Atlas and the other local volume. These matches use the exact pair of source MLS and listing ID. Review field history before the HomeFinder volume is removed. Evaluate the 12 `escrape` records separately before any decision on that volume. Keep obsolete credentials out of any listing import.

## SQLite files and tables

| Environment and file | Tables and row counts | Planned disposition |
| --- | --- | --- |
| AWS `buyer-publisher/catalog/catalog.sqlite` | `homes` 19,382 (5,502 sale, 13,880 sold), `media` 120,124 | Stop the AWS publisher and retire the inventory after the Atlas cutover and rollback window. |
| `data_service_buyer-live-catalog/catalog.sqlite` | `homes` 7,676, `media` 295,204 | Retire after live Atlas reads pass staging and the rollback window closes. |
| `data_service_buyer-live-private/submissions.sqlite` | `submissions` 0, `history` 0, `outbox` 0, `events` 22 | Retire buyer forms, the inbox, and delivery jobs. Remove the file after the cutover. |
| `data_service_buyer-preview-catalog/catalog.sqlite` | `homes` 4, `media` 2 | Remove the fixture copy. Test the new reader with fixtures and a staging integration check. |
| `data_service_buyer-preview-private/submissions.sqlite` | `submissions` 9, `history` 0, `outbox` 9, `events` 11 | Retire preview forms and delivery. Verify the records are preview data before removal. |
| `data_service_buyer-preview-private/analysis.sqlite` | `control` 1, `worker_listings` 4, `worker_images` 76, `worker_gallery` 76, `worker_attempts` 77, `worker_results` 3, `worker_cache` 0 | Retire the preview worker and its saved results. Existing Atlas-based photo and cost workers continue separately. |
| `data_service_buyer-preview-private/supply.sqlite` | `opportunities` 0, `updates` 0 | Retire direct supply and its rebuild job after confirming the workflow is off in all environments. |

The code can also create `review.sqlite` via `BUYER_REVIEW_DB`, with `cost_reviews` and `historical_costs` tables and attached evidence. It can create a direct-supply SQLite file under `/tmp` or a configured path. Check those paths in each deployment before removal. The inventory table records the six inspected local volume files and the AWS publisher copy. Counts include all physically stored rows.

[Submissions and notification queue](data_service/flip_ui/buyer_submissions.py) · [Cost reviews and evidence](data_service/flip_ui/buyer_analysis_review.py) · [Preview analysis worker](data_service/flip_ui/buyer_worker.py) · [Direct supply](data_service/flip_ui/buyer_supply.py)

## Implementation sequence

1. **Switch property search and listing views to Atlas.** Replace the SQLite reader for search, counts, maps, detail, and photos with direct queries to `mls.mls_listings`. Read enriched details and gallery references from the same document. Keep public links stable, preserve the server-side field allowlist and source display rules, and verify performance in staging before switching traffic. Review any proposal to read additional Atlas collections from public property routes with Roozbeh.
2. **Show the status and freshness stored in MLS.** Active listings with old inventory checks remain searchable and display the recorded status and last verified date. Price, status, and gallery updates appear on the next request. Withdrawals and display opt-outs apply to all public routes. MLS display permission keeps its own expiration. Test 1149 Berkeley, a price update, a withdrawal, and an opt-out.
3. **Retire SQLite-backed workflows.** Remove buyer submissions, tour and partner request forms, the notification queue, SQLite activity tracking, the cost review store, the preview analysis worker, and direct-supply publication. Confirm which routes and jobs are enabled, handle any pending notifications or real records with Roozbeh, and remove their data with the SQLite stores. This cleanup includes no import into Atlas. Existing Atlas ingestion and historical assessment workflows remain available. Retire the Gemma pilot drafts through the separate step below.
4. **Remove the SQLite deployment and code.** Switch staging and production to Atlas after approval. Stop both local and AWS catalog publishers and their schedules. Remove SQLite configuration, mounts, setup instructions, code paths, and test fixtures. Update the inventory audit to check live MLS eligibility. After verifying all configured paths and completing the rollback window, remove the SQLite files and sidecars from active storage.
5. **Drop the empty legacy Atlas collection.** Remove the `mls_details` initializer in `data_service/db/init.js` and correct the old documentation. Confirm whether any deployed job references it. Capture its index definitions in the change record and run a fresh exact count immediately before the approved drop. After dropping it, verify the name is absent, a normal ingestion run leaves it absent, and public and admin listing details still load from `mls_listings.details`. This changes the surveyed count from 22 to 21 application collections. Keep `gallery_repair_locks` while its running job depends on it.
6. **Retire the stopped HomeFinder Docker MongoDB instance.** Verify local development, preview, ingestion, and backfills point to Atlas. Check all mounts of `data_service_mongo_data` and confirm no service requires the stopped `mls_mongo` container. Review any unique field history in the 573 matched listings. Remove the old Compose service and volume definitions and the local-Mongo URI example. After approval, remove the stopped container and its named data volume explicitly, then verify that HomeFinder starts and reads Atlas. Treat the separately owned `escrape_mongo_data` volume and its 12 Atlas-missing records as a separate cleanup decision.

7. **Retire the Gemma pilot drafts and consolidate photo classifications.** Apply the two sequences above after approval. The surveyed count would go from **22 to 20** after dropping the empty MLS details collection and the pilot draft collection, and to **17** if all three classification collections are retired. This assumes the listed changes are the only changes to Atlas namespaces.

## Decisions for Roozbeh before implementation

- Approve the public and admin query scope and dedicated read-only Atlas credential. Review any proposed change to the canonical listing schema, new namespace, or additional reader privileges before implementation.
- Confirm the disposition of any real submissions, queued messages, review attachments, or unique local field history found during preflight. The target removes SQLite workflows and their records.
- Approve the staging results, production cutover, rollback period, and final drop and volume-removal operations. Approve the embedded photo label schema, the disposition of historical classifier outputs, and each classification collection drop separately.

## Handoff for implementation

- Start from the current HomeFinder checkout. Review the working tree and preserve existing changes. Treat this file as the scope for the migration.
- Start with the direct Atlas search reader and stale-listing behavior in staging. Use `mls.mls_listings` and its embedded details for listing data. Preserve the same public URLs and MLS display rules. The scope excludes MongoDB views and replacement inventory copies.
- Once staging search works, follow the remaining sequence. Retire SQLite workflows and data without importing them to Atlas. Keep the Gemma pilot and photo classification retirement steps separate so each can be verified before its collection is dropped.
- Bring changes to the canonical listing schema, Atlas indexes or access, data retention, production cutover, and any destructive actions to Roozbeh for approval. The dedicated search credential should come through the approved deployment environment.
- Keep a short implementation note in this file with completed stages, verification results, and pending decisions so the migration can resume in a new session.

## Acceptance checks

- Public and admin search, counts, listing views, maps, and public media derive property data from Atlas `mls.mls_listings`. The public account has read-only access. Existing URLs work and private fields stay outside public responses.
- A staging status, price, or gallery update appears on the next request. Stale records show the last check date. Withdrawals and opt-outs take effect on every public route.
- HomeFinder starts locally and in preview and production on Atlas. No SQLite runtime stores, SQLite-backed buyer workflows, snapshot publishers, or local HomeFinder MongoDB service or named volume remain.
- Atlas no longer lists `mls.mls_details` after a normal ingestion cycle. Listing pages still show the details embedded in `mls_listings`. The gallery repair lock continues to serve the running repair job.
- The Gemma draft collection stays absent after a normal app run, and existing legacy and reviewed estimates still render. Photo classifications remain correct after updating a listing gallery.

## Implementation progress

**November 23, 2026.** Added the isolated public Atlas staging service at `127.0.0.1:8092`. It reads property search, counts, map results, detail pages, and media from `mls.mls_listings`, with an explicit field projection and the existing source and listing display rules. It uses the existing HMAC key and URL paths. Cold opaque URLs resolve through a projected scan of eligible identities. A bounded in-process cache stores only successful identity lookups. Each detail, map preview, and media request re-reads the listing by its indexed MLS ID and checks the current display rights. Normal search reads full galleries only for the displayed page unless the filters require galleries. Status and inventory-check dates appear on stale cards, detail pages, and map previews. The independent permission and sold windows still apply.

The staging service has no SQLite mounts or publisher. It disables inquiry, tour, partner, inbox, tracking, cost review, preview worker, and direct-supply workflows. The existing admin property routes already query `mls.mls_listings` and render embedded details. The dedicated public staging service deliberately has no admin Atlas connection. Use the existing separate admin deployment for authenticated parity checks. The production and existing preview deployment files and stored records remain in service for the staged rollout.

**Verification.** The 42-test Flip UI suite passed in a network-disabled container. The new route tests cover the stale 1149 Berkeley case, private field omission, an immediate price and gallery change, withdrawn inventory and embedded status, opt-outs, withheld addresses, expired permission, and disabled forms. Compose configuration, JavaScript syntax, and isolated staging startup and route registration checks passed. Read-only Atlas queries confirmed that `ML82065431` remains `ACTV` at $998,000 with a 2026-10-25 inventory check and full-address display permission. Atlas examined one key and document for the exact indexed listing lookup. A full active-candidate explain used `LIST_PRICE_1`, examined 42,383 keys and documents, and took 356 ms server-side before response transfer and application work. Measure end-to-end search, map, and cold and warm detail requests through the staging service before approving traffic. The broad scan is the first index question to evaluate after measuring those requests.

**Live Atlas staging verified.** Roozbeh created `homefinder_public_preview` in Atlas. The Atlas account listing confirms a single `read` grant on `mls.mls_listings` scoped to the `mlsdata` cluster. The account read 1149 Berkeley and Atlas denied its reads from `mls.mls_details` and `flip_finder.flipdb`. The Docker preview is running at `http://127.0.0.1:8092/homes` under Compose project `homefinder-atlas-staging`. The staging env file is ignored by Git and has owner-only permissions. Authenticated tests against the existing Atlas-backed admin app returned HTTP 200 for Menlo Park search and the 1149 Berkeley listing.

Live HTTP checks passed for unfiltered and filtered search, counts, map pins and preview, the existing opaque listing URL, and JPEG delivery. The detail and map preview showed 1149 Berkeley as active at $998,000 with its October 25 inventory check. One local run measured 1.86 s for unfiltered sale search, 1.25 s for filtered search, 1.33 s for the first detail request, and 34 ms for a repeat detail request. These are individual observations. Use a representative load test before deciding whether an index is needed for production.

The staging env file supplies `BUYER_ATLAS_URI` and `FLIP_UI_SECRET_KEY`. The default is `data_service/buyer-atlas-staging.env`, and `BUYER_ATLAS_STAGING_ENV_FILE` can point to a private alternate path. The existing publication input directory supplies `live-policy.json` and `public-id-key`. Restart the isolated service with `docker compose -p homefinder-atlas-staging -f data_service/docker-compose.buyer-atlas-staging.yml up --build -d`.

**One-listing photo pilot verified.** Roozbeh approved the `gallery_photo_labels` field for 1149 Berkeley (`ML82065431`) only. Imported one label for each of its 10 current photos with a gallery-change guard. Each entry stores the stable photo identifier, category, classification time, and source and model provenance. The targeted labels took precedence over bulk labels, which disagreed on six photos. The Atlas preview reads only the embedded identifiers and categories and keeps the existing photo URL paths. The live listing showed Exterior (3), Kitchen (2), Living areas (2), Bathrooms (2), and Bedrooms (1). The category filter and JPEG route passed live checks. The 43-test suite also confirmed that the reader recognizes provider size aliases, drops labels for replaced photos, and suppresses classified floor plans. Atlas confirmed that exactly one listing has the pilot field.

**Public parity decision.** The old publisher also uses separate classification and renovation collections. Public Atlas staging uses embedded labels for gallery groups and classifier-based floor-plan filtering. It still needs a decision on how to display saved estimates and preserve historical pair-specific reviews before production cutover. Any new index, credential changes, retention periods, production cutover, and removal of SQLite files, volumes, or Atlas collections remain approval items. No data has been copied from SQLite to Atlas.

**Expanded photo migration verified.** The approved import applied the Berkeley layout to current galleries across MLS inventory. The snapshot through `2026-11-24T04:39:15.487477+00:00` yielded 102,526 labels on 2,637 listings, including 101,532 Moondream, 299 targeted, and 695 historical labels. Higher-priority labels resolved 91 photo category disagreements. The import skipped 1,349 unavailable results and two photos no longer in their listings' galleries. It kept Berkeley's 10 labels and category counts and filled in their available captions. A repeated audit of the same snapshot found zero pending updates. The largest measured listing with embedded labels was about 103 KiB and the largest embedded gallery had 172 labels. The local public preview returned 1,937 categorized sale listings and rendered groups on Berkeley and a second listing.

**Moondream replacement ready.** The new image saves successful classifications in `mls.mls_listings.gallery_photo_labels`, checks the gallery and existing labels on each write, and preserves the checkpoint collection. It queues both sides of every FlipDB pair before resuming the existing active and sold queues. A live read-only model and inventory check resolved 1,101 distinct flip listings. Ten worker and importer tests and 43 Flip UI tests passed. The existing container kept running as requested. When replacing it, stop the old writer, import its records since the inclusive snapshot cutoff, and recreate `photo_backfill` from the built image. This imports the overlap before the new worker resumes. The separate legacy collections remain available for the pending retention and removal decisions. The saved-estimate and production cutover decisions remain open.

**Moondream replacement running.** Roozbeh stopped the old writer, imported its final 198 labels across seven listings with zero concurrent changes, and recreated the container at 04:44 UTC on November 24. Docker confirmed the replacement image and zero restarts. The FlipDB priority job was running, and a live Atlas read found 40 newly embedded worker labels on `ML82038481` while the first gallery was still in progress.
