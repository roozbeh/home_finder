# Peninsula Flip Finder

Private, explainable screening software for potential Bay Area single-family renovation projects. It is decision support only—not a valuation, offer system, contractor substitute, or financial advice.

## Phase 0 status

This repository now contains a read-only MongoDB audit plus initial normalization and candidate-screening rules. It intentionally does not extend the legacy ConnectMLS cookie scraper. Use of the existing MLS records for investment analytics, storage, photos, or downstream display needs broker/MLS confirmation.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
# Set MONGODB_URI to a read-only MongoDB account; do not commit .env.
```

The existing legacy service uses `data_service/env` and `MONGO_URI`. The audit accepts either `MONGODB_URI` or `MONGO_URI`, but does not read credentials from a file itself.

## Run the audit

```bash
set -a; source data_service/env; set +a
python -m flip_finder.cli audit --source mongo --output-dir audit_artifacts
```

This only issues MongoDB reads and writes reports under `audit_artifacts/`, which is gitignored. It emits `audit.json`, `data_audit.md`, and `data_gaps.md`; samples omit addresses, remarks, agent/contact data, photos, and credential fields.

An authorized CSV export can already be read (without uploading, copying, or modifying it) via `flip_finder.sources.csv_import.read_csv`. Its headers should use the MLS-like field names listed in the handoff, such as `LISTING_ID`, `MLS_STATUS`, `LIST_PRICE`, `CITY`, `STREET_ADDRESS`, `PROPERTY_TYPE`, `CLOSE_PRICE`, and `CLOSE_DATE`.

## Test

```bash
python -m pytest -q
```

## Observed short-hold resale history

This read-only command finds consecutive *observed closed sales* of the same
property that occurred 14–365 days apart and writes private CSV/Markdown
outputs. It calculates gross price spread and gross return only. It does not
infer renovation work, arm's-length status, or net/project profit.

```bash
set -a; source data_service/env; set +a
python -m flip_finder.cli flip-history
```

The default destination is `~/.local/state/home-finder/market_history`. Use `--output-dir` to select a different private directory.

`observed_short_hold_resales.csv` identifies whether each match used APN
(preferred) or normalized address (research-only). City and observed MLS
subdivision summaries are intentionally suppressed for geographies with fewer
than three pairs. The tool never fabricates neighborhood boundaries from an
address.

For credible Bay Area market comparison, ingest at least two years of
authorized closed-sale history; five years is preferable. The report is a
market-research signal alongside current comps, permits, condition review, and
full underwriting—not a bid model.

## Derived FlipDB research dataset

`flipdb` is a separate, derived collection in the `flip_finder` database. It
materializes the same observed 14–365-day resale pairs used by the private CSV,
but keeps them current for later visualization and model research. It never
changes `mls.mls_listings`.

```bash
set -a; source data_service/env; set +a
python -m flip_finder.cli flipdb-refresh --write
```

Each record has stable `pair_key`, `buy_mls`, `sell_mls`, buy/sell dates and
prices, hold period, gross spread/ROI, match confidence, caution text, and
source provenance. `is_verified_flip` is always `false`: short holding periods
and gross price movement do not prove renovation work or net profit. A daily
materialization job upserts current pairs and marks no-longer-observed pairs
inactive rather than deleting their audit trail.

## Current decisions and limits

- `SLD` is mapped to `CLOSED` because the existing ingestion source code describes that mapping; other legacy codes such as `BOMK`, `AC`, `NEW`, `PCH`, and `CS` remain `UNKNOWN` until official provider documentation confirms their semantics.
- A candidate must be verified `ACTIVE`, below the asking-price ceiling, in an initial target city, and verified detached. Text risk signals force manual review.
- No ARV, profitability, MAO, or candidate ranking is produced until the audit confirms sufficient recent closed-sale data and permissions are confirmed.

See [architecture.md](architecture.md), generated audit reports, and the supplied handoff plan for next milestones.
