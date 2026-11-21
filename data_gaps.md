# Phase 0 Data Gaps

Generated: 2026-10-25

## Current readiness

The collection has 3,517 usable-at-collection-level `SLD` close-price/date records over roughly the latest year, so ARV development is no longer blocked by an absence of closed sales. A reliable ARV remains blocked for any individual property until the selector finds three to five recent, local, detached, size-compatible CLOSED comps with valid observed sale facts.

The configured Atlas path initially failed TLS handshake, then succeeded after the Atlas IP access update. The standard local audit command remains ready for repeatable reports; use a dedicated read-only Atlas account when provisioned.

## Data/access questions that still need an authorized answer

1. Does the broker/MLS agreement permit retaining the existing dataset and using it for private internal investment analytics? Confirm active, sold-history, and media rights separately.
2. Are `ACTV`, `BOMK`, `AC`, `NEW`, `PCH`, and `CS` valid current-for-sale statuses? Until written provider documentation is available, only `ACTV` is allowed through the initial active screen; the other codes remain `UNKNOWN`.
3. Does every screened market/property have three to five recent closed transactions with comparable location, detached type, size, condition, and observed close facts? Collection-wide availability does not establish property-level comp validity.
4. Bathrooms and listing URL are not currently available in the queried expected fields; discover the authoritative alternate field names. Lot size is only 84.56% complete. Do not silently fill those gaps.
5. Are raw snapshots point-in-time historical data, or only current records updated in place? Without point-in-time history, a leakage-safe backtest is blocked.

## Authorized source plan

- Request a broker/MLS-approved export or provider-supported feed with documented field dictionary, status definitions, history scope, retention/use permissions, rate limits, and media terms.
- If closed history is incomplete, request an authorized historical closed-sales export rather than guessing or scraping.
- Add parcel/assessor and permit records only from documented lawful agency/provider sources, preserving the record URL/key, retrieval date, and match confidence.
- Add flood, fire, seismic, and other risk layers only from official datasets with effective-date and geographic-coverage metadata.

Until these are resolved, the implemented normalization and candidate rules are a testable intake layer—not a valuation or decision engine.
