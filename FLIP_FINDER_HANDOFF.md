# Flip Finder — product and project handoff

## What we are building

Flip Finder is a Bay Area real-estate investment research product. Its purpose
is to give a small, disciplined investor a practical way to find, assess, and
learn from potential residential renovation/resale opportunities without
pretending that a preliminary data model is an appraisal, construction bid, or
investment recommendation.

The initial user is the founder, working with his father, who has 40+ years of
civil-engineering and residential-construction experience. The father can help
evaluate shortlisted homes in person, but is not a California-licensed
professional engineer or contractor. The product should therefore automate
initial screening and organize evidence, while keeping the final decision and
on-site assessment human.

The product mission is to make potential flip opportunities more explainable
and accessible: turn MLS observations and sale history into a focused review
queue, so investors spend their time on properties worth investigating.

## Investment use case

The target is primarily single-family homes in the San Francisco Bay Area,
often under $1M for early screening, that may be acquired, renovated with a
manageable scope, and resold at a meaningful profit.

For each opportunity, the desired system should eventually answer:

- Why might the property be undervalued or worth a closer look?
- What renovation work may be required, and how uncertain is that conclusion?
- What comparable-sales evidence supports the possible after-repair value
  (ARV)?
- What might acquisition, renovation, finance, holding, and resale costs be?
- What maximum offer is worth considering under conservative assumptions?
- What risks could erase profit: structural work, permits, occupancy, title,
  market movement, poor comparables, or incomplete data?
- Is it worth visiting with an experienced construction reviewer?

All financial figures are preliminary decision-support estimates, never
investment-ready valuations or guarantees.

## Key product insight: learn from observed short-hold resales

Conventional ARV analysis compares a current listing with nearby sold
comparables. That is necessary but insufficient for deciding *where* to look.

Flip Finder adds a second lens: identify properties that appear to have sold,
then sold again within roughly a year. The gross resale spread is:

`resale sold price - purchase sold price`

and the observed gross ROI is:

`gross resale spread / purchase sold price`

This does **not** prove a flip occurred. It does not establish rehab cost,
financing cost, seller concessions, transaction costs, related-party status, or
net profit. It is an observed short-hold resale signal that can help reveal
markets, neighborhoods, price bands, and patterns deserving research.

The system uses explicit language such as “observed gross spread” and “gross
ROI — not net profit.” A pair can be labeled manually as a likely success flip,
a failed flip, or left unlabeled after human review.

## Current data assets

### MLS data

There is an existing MongoDB Atlas MLS collection containing Bay Area listing
records. Records include, when available:

- Active and sold listing status
- Listing and closed prices, dates, price history, DOM/CDOM
- Address/city/ZIP, home attributes, beds, baths, square footage, year built
- Listing descriptions and property attributes
- Listing-agent and office fields
- MLS detail payloads
- Authorized photo references (not image bytes)

The original MLS collection is treated as the source of truth. Derived product
data is stored separately.

### Details and photos

The detail fetcher enriches listings through an existing authorized MLS
workflow. We do not bypass access controls or invent endpoints.

Important implementation detail: the listing-details response supplies only a
primary photo reference, while the authenticated MLS photo-detail page exposes
the complete gallery. The updated pipeline fetches the photo-detail page with
the existing authorized MLS session and stores only full-size image URLs. It
does not download or store image files in MongoDB.

### Derived FlipDB

A derived database/collection materializes observed short-hold resale pairs.
Each pair contains at least:

- `pair_key`
- Purchase MLS ID and resale MLS ID
- Purchase price/date and resale price/date
- Hold duration in days
- Gross resale spread and gross ROI
- City, subdivision/neighborhood information where available
- Match confidence and a caution/explanation field
- Active/inactive state and refresh provenance

The derived data is separate from raw MLS source records so it can be rebuilt,
audited, and changed without mutating source history.

## Current pipeline architecture

```text
Authorized MLS session
        |
        v
MLS listing refresh (active + sold observations)
        |
        +--> detail enrichment
        |       +--> descriptions, attributes, agents, photo references
        |
        v
MongoDB source collection: mls.mls_listings
        |
        v
Daily Flip Detector materialization
        |
        v
Derived FlipDB: observed short-hold resale pairs + review labels
        |
        v
Private Flip Finder review UI
```

### Operational schedule

- A periodic live refresh job checks fresh MLS inventory and detail backlog.
- A daily Flip Detector job reviews source history and updates observed resale
  pairs.
- Session reuse is allowed while it remains valid. If MLS access actually
  returns an unauthorized/expired response, the saved session is retired and
  human reauthorization is required. The system does not automate credential
  login.

## Current private review UI

There is a public landing page and a password-protected private review
workspace. The review workspace is intentionally not marketed or linked from
the public landing page.

The queue supports filtering by:

- City
- Purchase-price range
- Gross-spread range
- Purchase-date range
- Label: unlabeled, success flip, failed flip, or all labels

Queue columns can be sorted ascending/descending by purchase price, resale
price, observed spread, hold duration, and manual label. Active sorting keeps
the chosen filters.

Each pair detail page shows:

- Purchase and resale MLS IDs
- Side-by-side purchase and resale photos, with gallery thumbnails
- Listing price, sold price, close date, beds, baths, size, build year, type
- Listing description and available MLS fields
- Observed gross spread and gross ROI beneath the resale price
- A side-by-side “What changed” comparison of observed MLS fields
- A collapsed section for fields that stayed the same
- Parsed agent/office names instead of raw MLS JavaScript-link metadata
- Manual success/failed/unlabeled controls with persisted label history

The UI uses the phrase “observed gross profit” only with care. The standard
display is a price plus a short line such as `$150,000 · 55.6% return`; nearby
product language must continue to make clear that this is gross, observed, and
not net profit.

## Example of manual research value

A human reviewer can visually compare a property's purchase listing to its
resale listing:

- Did the exterior, kitchen, layout, finishes, landscaping, or condition
  visibly change?
- Did bed/bath count, square footage, or property type change?
- Was the resale professionally renovated, merely staged, or apparently not
  renovated?
- Were there price, hold-time, agent/office, or MLS-description signals worth
  investigating further?

That manual judgment becomes labeled training data later. It should not be
treated as a fully objective ground truth without a documented labeling guide.

## Future product roadmap

### 1. Data quality and provenance

- Continue improving completeness of historical sold records and details.
- Add source provenance and refresh timestamps to every derived finding.
- Add permitted public property records, permit history, hazard/risk datasets,
  and authorized historical-sale exports where available.
- Keep observed source facts distinct from calculated estimates and human
  labels.

### 2. Neighborhood and market analysis

- Aggregate observed short-hold resales by city, neighborhood/subdivision,
  price band, hold duration, and confidence.
- Surface sample size and uncertainty prominently; small samples should never
  be presented as a neighborhood investment conclusion.
- Compare short-hold outcomes with broader local sold-market behavior.

### 3. Current-listing opportunity detection

- Refresh active listings daily or more frequently only where authorized.
- Identify potential cosmetic distress, price reductions, stale days-on-market,
  inherited/outdated presentation, and other explainable signals.
- Separate possible light/cosmetic projects from structural, permit, occupancy,
  or major-systems risks.

### 4. Comparable-sales and underwriting

- Build conservative ARV calculations using recent nearby closed sales with
  explicit filters for location, size, age, property type, and condition.
- Require enough relevant closed comparable sales before showing an ARV.
- Use conservative/base/optimistic renovation, cost, financing, hold, sale,
  contingency, profit, ROI, and maximum-allowable-offer scenarios.
- If available data cannot support a reliable ARV, show the blocker rather
  than manufacturing a number.

### 5. Learning model, later

The long-term idea is a classifier that scores new listings for likelihood of
being a worthwhile *research candidate*, plus an underwriting model that
suggests a conservative offer range. It should be trained only after enough
high-quality human-reviewed labels exist.

The first model should be simple and explainable: engineered data-quality,
market, property, text, and visual-condition features; calibration; human
review; and strong “insufficient evidence” behavior. It should not be sold as
an autonomous investing or valuation engine.

## Product and business hypotheses discussed

- There are commercial products with adjacent capabilities (property data,
  investor lists, lead generation, comp analysis), but an explainable,
  localized, investor-review workflow can still differentiate.
- A logical validation path is a small Bay Area pilot with a handful of
  investors or investor-friendly agents before attempting a nationwide SaaS.
- Agents may be useful distribution partners because they know local buyers
  and transactions, but MLS contact and buyer data must not be repurposed for
  unsolicited marketing unless use is authorized and compliant.
- Another possible model is free decision-support software paired with
  transaction representation. That involves licensing, brokerage, conflict,
  disclosure, and regulatory questions that require qualified professional
  advice before implementation.

## Compliance and safety constraints

- Never scrape in violation of provider terms or bypass access restrictions.
- Use only authorized MLS sessions/feeds and permitted public data sources.
- Do not expose MLS cookies, passwords, database credentials, or private
  admin URLs in shared material.
- Do not automatically contact listing agents, buyers, or sellers.
- Do not use MLS data for marketing or solicitation unless the MLS agreement,
  broker policy, privacy rules, and applicable law allow it.
- Do not call preliminary values appraisals, construction bids, guarantees, or
  investment advice.
- Do not store image files in the database; store authorized references only.

## Questions an advisor/ChatGPT can help with

1. What is the most defensible narrow initial customer segment: local flippers,
   agent-investors, small funds, or construction-savvy homeowners?
2. What labeling rubric should distinguish strong flip signals, weak signals,
   likely cosmetic work, and major-risk properties?
3. What user research questions would validate willingness to pay before a
   large product build?
4. What are ethical and compliant ways to recruit pilot users without using
   MLS contact data for solicitation?
5. What product positioning clearly differentiates this from generic property
   data platforms while avoiding overpromising financial outcomes?
6. What additional public/authorized datasets would most improve risk and
   renovation assessment in California?

## Current state summary

The foundational ingestion, detail enrichment, observed-resale materialization,
and private manual-labeling UI exist. The current focus is improving the
review experience and data quality so that labeled, explainable observations
can later support conservative opportunity screening and underwriting.

This project is not yet a public investment product, an automated offer engine,
or a substitute for a licensed real-estate, legal, tax, appraisal, engineering,
or construction professional.
