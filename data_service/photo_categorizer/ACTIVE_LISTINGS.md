# Active listing assessment with Claude Haiku

Build once after code updates:

```sh
docker-compose -f data_service/docker-compose.yml --profile photo-categorization build photo_categorizer
```

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
