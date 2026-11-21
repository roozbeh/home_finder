# Flip Finder handoff — MongoDB MCP setup

Updated: November 15, 2026 (America/Los_Angeles)

## Product context

This repository contains a private Bay Area property-flip research product.

- MLS listings are collected into MongoDB Atlas database `mls`, primarily in `mls_listings`.
- Consecutive short-hold sales are derived into Atlas database `flip_finder`, collection `flipdb`.
- The private review interface is the local Flip UI at `http://localhost:8090/admin`.
- A FlipDB pair compares the purchase MLS listing with the resale MLS listing. It shows gross price spread and gross ROI only; it must not claim net profit because renovation, financing, tax, and closing costs are not known.
- The public landing page describes the research workflow: historical sold-property collection, identifying observed resale patterns, and surfacing new research candidates. It is intentionally not investment advice.

## Why MongoDB MCP is needed

Application containers currently have Atlas connectivity, but direct diagnostic queries are awkward and the local data lookup for one gallery issue was inconsistent. A direct, read-only MongoDB Atlas MCP connection would let the coding assistant inspect exact records, schemas, backfill state, and gallery metadata without modifying data.

Use MongoDB's official Atlas Managed MCP Server, not an unverified community MCP package:

- Official guide: <https://www.mongodb.com/docs/mcp-server/get-started/>
- MongoDB MCP tools: <https://www.mongodb.com/docs/mcp-server/tools/>

### Required setup / security boundary

1. In MongoDB Atlas, enable AI-client connections for the applicable organization/project if Atlas asks for it.
2. Add the official MongoDB Atlas MCP integration in the AI client that is running Codex.
3. Authenticate through the Atlas OAuth/managed connection flow.
4. Restrict the associated database principal to **read-only** access:
   - `read` on database `mls`
   - `read` on database `flip_finder`
5. Do **not** grant `readWrite`, `dbAdmin`, cluster admin, database-user management, or Atlas project-owner access.
6. Do not paste a MongoDB URI, password, API key, or cookie into chat or into this file.

After it is connected, announce that the MongoDB MCP server is available and resume the gallery investigation below.

## Current local UI work

These local-only changes are in the current dirty worktree and have been rebuilt into the local `flip_ui` Docker container:

- Review-page galleries have independent Previous/Next controls for purchase and resale MLS images.
- Resale display uses a compact line under the resale price: `gross spread · gross ROI return`.
- Detail views show `Closed … (N days on market)` when an MLS DOM/CDOM field is available.
- A new experimental room-by-room comparison appears below the normal purchase/resale galleries.
  - It presents a full-size, independently navigable purchase gallery and resale gallery per room category.
  - It is currently a sampled experiment, not a full-gallery classification.

### Classified experiment

The one classified FlipDB pair is:

- Review URL: `http://localhost:8090/admin/flip/566d0260feac6186046f72b860f295392eaa84e65d325def4259311dd8edb80b`
- Address displayed: `2313 Peppertree Unit 1, Antioch, CA 94509`
- Purchase MLS: `41098272` (29 gallery images)
- Resale MLS: `41120373` (16 gallery images)
- Experiment scope: 8 representative photos from each side, 16 total.

The current categories looked plausible: outside, kitchen, bathroom, bedroom, living room, and other.

### Classification implementation

Files added/changed:

- `data_service/photo_categorizer/` — manual-only categorization worker.
- `data_service/docker-compose.yml` — profile `photo-categorization`, capped at 512 MB RAM / 0.5 CPU, never starts with the normal stack.
- `data_service/flip_ui/app.py`
- `data_service/flip_ui/templates/detail.html`
- `data_service/flip_ui/static/photo-categories.css`

The first experiment used Anthropic Claude Haiku 4.5 through an **already-existing** `ANTHROPIC_API_KEY` injected by `data_service/env`; do not expose or edit that key in chat. It stores derived metadata only in `flip_finder.photo_classifications`: pair key, side, photo URL, index, category, confidence, visible features, model, and timestamp. It does not store images.

No broad classifier run has been authorized. Before any batch run, add a strict maximum-photo limit and budget guard.

## Preferred future vision path

For bulk room categories, test a local zero-shot vision model (CLIP/SigLIP) against the above 16 labeled images first. It has no per-photo API charge, but needs more memory than the present AWS host can safely spare.

- Current AWS host: 2 GB RAM; not suitable for a resident PyTorch worker beside existing services.
- Minimum safer CPU worker: 4 GB RAM, 2 vCPU, ~10 GB free disk.
- Comfortable worker: 8 GB RAM, 4 vCPU.
- GPU is optional for inference; it mainly accelerates batch time.
- Local vision is appropriate for room labels, but not reliable enough alone to estimate renovation scope or renovation cost.

## Gallery backfill context

The MLS gallery fetcher was updated to parse authorized ConnectMLS full-size `HD_` image references rather than relying on an unreliable numbered-URL guess. Gallery URLs are references only; images are not copied into MongoDB.

The FlipDB-scoped photo-gallery repair is intended to process only listings used by active flip pairs. Backfill progress is reported through the admin/status UI and is resume-safe because records with valid `HD_` references are skipped.

### Open issue to diagnose after MCP is connected

User reported that purchase MLS `BA326014263` has 20 photos in ConnectMLS but displays only one in Flip UI:

- Review URL: `http://localhost:8090/admin/flip/5cd25442b25fa78ef4830bf508505b998de84d58bb54cdfa5e24966e5289ff45`
- Pair key: `5cd25442b25fa78ef4830bf508505b998de84d58bb54cdfa5e24966e5289ff45`
- Purchase MLS: `BA326014263`
- Resale MLS: `41141888`

Direct diagnostic questions to run through MongoDB MCP:

1. Find the exact source documents for both listing IDs in `mls.mls_listings` (and any related detail collection if applicable).
2. Inspect `photos`, `details.PRIMARY_PHOTO_URI`, `details.photocount`, and any gallery-completeness/backfill fields.
3. Confirm whether `BA326014263` was selected by the FlipDB-only gallery backfill.
4. If the authorized source page has 20 images but the stored record has one, capture the page/parser shape before changing the parser.
5. Determine whether this is a record-specific failure or a systematic gap (especially for `BA...` MLS IDs).

Do not delete listings or run a broad rewrite until this evidence is collected.

## Useful local commands

Start/rebuild just the local private UI:

```sh
cd "/Users/roozbeh/Google Drive/iPronto/home_finder"
docker-compose -f data_service/docker-compose.yml build flip_ui
docker-compose -f data_service/docker-compose.yml up -d --no-deps flip_ui
```

Run the manual photo categorizer for one pair (requires the already-configured local environment key):

```sh
docker-compose -f data_service/docker-compose.yml --profile photo-categorization run --rm photo_categorizer \
  --pair-key <PAIR_KEY> --max-photos-per-side 8
```

Run the FlipDB-only full-gallery repair locally (detached):

```sh
docker-compose -f data_service/docker-compose.yml exec -d app sh -lc \
  'PYTHONUNBUFFERED=1 python -u /app/fetch_details.py --refresh-photo-galleries --flip-pairs-only > /app/data/photo_gallery_backfill.log 2>&1'
```

Tail its log:

```sh
docker-compose -f data_service/docker-compose.yml exec app tail -f /app/data/photo_gallery_backfill.log
```

## Git / deployment notes

- The worktree contains unrelated user changes. Preserve them; do not reset, clean, or blanket-stage.
- The room-classification UI and categorizer changes are not yet committed or deployed.
- Existing local commits include:
  - `0a3641c Add navigation controls to listing galleries`
  - `a9ffda0 Animate Flip Finder landing story`
- The public landing-page update was separately pushed through a clean temporary worktree as `eef56eb` and verified at `https://flips.roozbeh.realtor/`.
- The AWS host is fragile: old Docker/AUFS behavior and 2 GB RAM. Avoid container-wide rebuilds or service restarts without a clear need.
