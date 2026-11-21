# Proposed Architecture — Peninsula Flip Finder

## Boundary and storage

`mls.mls_listings` remains read-only. The Phase 0 code reads it with a narrow projection and produces local audit artifacts only. A later approved pipeline should retain raw authorized snapshots separately (local encrypted storage or `flip_lab.raw_snapshots`), then write canonical records and review notes only to `flip_lab` or local DuckDB—not back to MLS data.

```text
Authorized MLS export / approved feed / read-only legacy Mongo
                         |
                    raw snapshot + provenance
                         |
        normalization / deduplication / status mapping
                         |
     active candidate rules       closed-sale comp selector
                         \         /
                    deterministic ARV gate
                         |
           rehab scope + scenario underwriting
                         |
      ranked private review queue + local dashboard/export
                         |
          human due diligence and manually recorded notes
```

## Components

- `sources.mongo_readonly`: legacy read-only adapter; no writes or authentication-token access.
- `sources.csv_import`: next adapter for broker/MLS-authorized exports, with a source manifest containing license/use class, source key, exported time, and checksum.
- `normalize`: canonical listing model, strict nullable number/date parsing, canonical status mapping. Unknown status is never active.
- `candidates`: deterministic price/geography/asset-type eligibility and text evidence flags. Listing text is only a signal, never a diagnosis.
- Future `comps` and `arv`: require recent, local, like-for-like CLOSED records with observed sale price and date; otherwise return `UNVERIFIED — DO NOT BID`.
- Future `underwrite`: versioned assumptions and cash/financed stress cases; outputs preliminary pretax screening estimates only.

## Data governance

Every stored derived value must retain source, source record ID, observed/inferred flag, fetched/exported time, and permitted-use classification. Keep MLS media private; do not republish or send photos to external AI without explicit license/privacy approval. Permit, parcel, and risk inputs must identify the responsible agency/dataset, geographic match confidence, and effective date.

## Operational controls

- Daily or slower refresh only after provider terms/rate limits are confirmed.
- Idempotent snapshot IDs and retained listing events/history; no mutation of source records.
- Local dashboard only unless display rights are documented.
- No calls, offers, alerts to third parties, or valuation/bid claims.
