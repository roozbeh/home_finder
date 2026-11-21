# Phase 0 MLS Data Audit

Generated: 2026-10-25

## What was verified from the repository

- The legacy feed targets `mls.mls_listings` through ConnectMLS session-cookie requests. `search_and_store.py` performs upserts into that collection; this project does not call or modify it.
- The legacy script explicitly requests active-like codes `ACTV,BOMK,AC,NEW,PCH,CS`, and separately requests `SLD` records for a rolling 365-day interval in selected Peninsula cities. Its source code says `SLD` is closed/sold, but only official MLS/provider status documentation can validate every code and allowed downstream use.
- `fetch_details.py` retrieves structured detail records and derives photo URLs for private MLS use. Presence of this code does not grant retention, analytics, image-analysis, or display rights.

## Live read-only audit result

After Atlas Network Access was updated, an audit from the existing `mls_app` container succeeded. It used only `ping`, `estimatedDocumentCount`, and a narrow-projection `find`; no MongoDB write was attempted.

- Documents scanned: **5,306** (estimated total: **5,306**).
- Raw `MLS_STATUS`: `SLD` 3,517; `ACTV` 1,507; `CS` 225; `NEW` 48; `PCH` 6; `AC` 2; `BOMK` 1.
- `SLD` records with both observed close price and close date: **3,517**. Close-date values span approximately **2025-09-29 to 2026-09-25** (stored as epoch milliseconds).
- Descriptions present: **96.85%**. Photo metadata present: **100%**. Duplicate non-empty `LISTING_ID` values: **0**.
- Full coverage: property type, beds, living area, year built, coordinates, days on market, and listing date. Lot size: **84.56%**. APN: **99.87%**. Listing URL: **0%**. The queried `BATHROOMS_FULL` fields have **0%** coverage and need field-name discovery before bathroom matching.

This supports building a conservative closed-comp selector. It does **not** establish that every active candidate has three to five recent, nearby, comparable closed sales, nor does it establish provider permission for analytics or photo use.

## Geographic data-quality finding

The collection uses source city variants, including `REDWOODCIY`, `EASTPAALTO`, `HALFMO BAY`, `HILLSBOROU`, and `FOSTERCITY`. Initial normalization now maps these to canonical city names; source values are retained as observed provenance. The raw data includes 167 Daly City, 119 Pacifica, 71 East Palo Alto, and 726 Redwood City-coded records. San Bruno and South San Francisco were not present in this snapshot.

## Safe audit behavior

The audit uses `ping`, `estimatedDocumentCount`, and a read-only `find` with a narrow projection. It redacts addresses, descriptions, photos, agent/contact details, and credentials from samples. It never reads `auth_tokens`, never calls update/insert/delete methods, and never treats a missing/unknown value as zero.
