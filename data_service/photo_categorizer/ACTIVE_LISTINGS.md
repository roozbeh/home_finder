# MLS photo classification and listing assessment

Build once after code updates:

```sh
docker-compose -f data_service/docker-compose.yml --profile photo-categorization build photo_categorizer
```

## Local Ollama

Run Ollama on the Docker host and use `--local` to switch both photo classification and any requested insight to the installed local model. The default is `gemma4:12b`. The worker checks Ollama for vision capability before submitting photos. Use `ollama list` to find the exact tag and `ollama show <MODEL_TAG>` to see its capabilities.

```sh
docker-compose -f data_service/docker-compose.yml --profile photo-categorization run --rm photo_categorizer \
  --pair-key <PAIR_KEY> --max-photos-per-side 64 --max-api-requests 129 --generate-insight --local
```

For an active listing, pass `--listing-id <LISTING_ID>` and allow up to 65 requests for 64 photos and one insight. `--max-api-requests` limits model requests for either provider. The Docker worker connects to `http://host.docker.internal:11434/api/chat` by default. Set `PHOTO_CATEGORIZER_OLLAMA_MODEL` in `data_service/env` to choose a persistent local default. `--model <MODEL_TAG>` overrides it for one run. Use `--ollama-url` if Ollama runs at a different address reachable from the container. Set `--ollama-timeout` if shared use of the local server needs a different request timeout. Local inference has no per-request API fee. Saved classifications and insights record the actual model. A repeated run with that model can reuse its stored labels. The historical-pair UI uses one current label per photo, so a successful run with a different model replaces that photo’s current machine label while retaining reviewer overrides.

## Active listing with the default provider

Verify the full gallery first (no AI calls or writes):

```sh
docker-compose -f data_service/docker-compose.yml --profile photo-categorization run --rm photo_categorizer \
  --listing-id ML82066483 --max-photos 64 --max-api-requests 65 --preflight
```

Classify and assess a single active MLS listing:

```sh
docker-compose -f data_service/docker-compose.yml --profile photo-categorization run --rm photo_categorizer \
  --listing-id ML82066483 --max-photos 64 --max-api-requests 65 --generate-insight
```

Uses the configured Anthropic key and Claude Haiku model. Images are resized to 384px. At most 64 photo requests plus one combined proposed-renovation and resale assessment request; SDK automatic retries are disabled for active jobs. Cached labels reduce cost. Resume with the same command after a failure; --refresh replaces selected model labels. The insight request reruns when --generate-insight is supplied.

Photos: flip_finder.active_photo_classifications. Insights: flip_finder.active_renovation_insights. No pair key is required and sold-pair insights are not modified. Active UI reads these collections automatically. Assessment never treats missing costs as zero. Failed photo or invalid insight responses stop the job; previously saved labels remain.

Sales evidence: same building type, same city, closed in last 365 days, within two miles, 70–140% of subject sqft, within one bedroom. Up to eight nearest distinct properties, with actual MLS IDs, sale prices, distances and public remarks. AI must use at least three supplied comps to supply a resale range. Condition and micro-location adjustments are AI estimates and require human review; there is no contractor inspection or appraisal. No resale value is copied from the preliminary candidate screen.

The UI solves ten-month break-even using the assessed resale range, proposed renovation range, monthly HOA, 10% annual simple interest, 1.1% annual purchase-price tax, 1% buy closing and 6.5% sale closing. Insurance is excluded. Unverified HOA or inadequate resale evidence leaves the recommendation pending. The monthly HOA is set to zero when MLS explicitly reports no HOA; other frequencies must be normalized. Purchase ceilings are estimates, with no target-profit cushion by default.

## Market-wide Moondream photo backfill

Run from the repository root with Ollama available on the Docker host. Start one worker per catalog. Use a separate Compose project so routine stops of the web stack leave this worker alone. The detached service keeps logs after exit and Docker restarts it after a process failure.

```sh
docker compose -p home-finder-photo -f data_service/docker-compose.yml \
  --profile photo-backfill up -d --build photo_backfill
```

Follow a live, read-only report in a separate terminal. This works with a worker that was already running before the image was rebuilt.

```sh
docker compose -f data_service/docker-compose.yml --profile photo-categorization run --rm \
  --entrypoint python photo_categorizer \
  /app/bulk_moondream.py --model moondream:latest --status --watch
```

The report refreshes every 15 seconds and lists the FlipDB, active, and sold queues. It shows photo totals from completed galleries and reads saved labels for the current gallery, including the latest save time. That gallery can include labels reused from an earlier pass. The report counts listings at startup, so changes to MLS inventory can affect the totals seen by a later monitor. Omit `--watch` for one snapshot. Ctrl+C exits the monitor.

`docker logs -f moondream-mls-backfill` shows phase counts, the current gallery and Ollama retries. Use `docker stop moondream-mls-backfill` to pause and `docker start moondream-mls-backfill` to resume the same image. Recreate the container through Compose to pick up a rebuilt image. Check `docker ps -a --filter name=moondream-mls-backfill` after a full Docker Desktop or host restart. Saved photos are reused when a listing resumes. Another pass retries previously unavailable photos and checks for new ones. The worker retries transient download and inference failures. Persistent inference failures exit the process so Docker can restart it without advancing the gallery checkpoint. Use `--dry-run` for a read-only inventory and model check.

The persistent service defaults to eight workers. Set `MOONDREAM_WORKERS` on the Compose invocation to override it. Ollama's `OLLAMA_NUM_PARALLEL` should match. On this Mac use `launchctl setenv OLLAMA_NUM_PARALLEL 8` and `brew services restart ollama` to set the runner slots. On a 24-photo sample with downloads completed before timing, 2, 4 and 8 workers measured 25.6, 63.9 and 69.9 photos per minute. Storage and network time can lower sustained throughput. The percentage in the status report measures listing galleries scanned.

A second test used the same 48 predownloaded photos for each pass and unloaded the model between passes to clear its image cache. With 8, 16 and 32 requests in flight on the same runner, throughput was 49.2, 51.2 and 52.1 photos per minute. Per-request p95 latency grew from 14.6 to 28.3 and 52.1 seconds. All three runs classified all 48 photos and returned matching categories. The regular worker stayed at eight. In the sample, the indexed MongoDB read median was 26 ms and download plus resize took 195 ms. The MongoDB measurement is a read round trip, not write latency.

The worker first processes both listing IDs from every FlipDB pair, deduplicated across pairs. It then resumes the existing active or sold pass. A new active pass prioritizes listings whose MLS details were fetched within the last 90 days, then scans the remaining active listings before sold listings. Use `--public-check-days` if the publication policy uses a different window. The publisher independently applies current inventory and public-display permissions. Both priority queues keep their saved position across restarts.

The worker downloads from trusted MLS photo hosts and counts unavailable and rejected references. It uses local Moondream captions and deterministic room rules. Unclear scenes are filed as `other`. Successful classifications are saved to `mls.mls_listings.gallery_photo_labels` with their caption, model, method, source, and timestamp. The write checks that the current gallery still matches and preserves higher-priority reviewed and targeted classifications. Job progress stays in `flip_finder.mls_photo_backfill_jobs`.
