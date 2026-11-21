# Public Bay Area fixer-upper search and agent lead plan

November 21, 2026 · Working draft for Terra

**I would launch a free, agent-backed Bay Area fixer-upper search for buyers.** The product should answer three questions that a conventional listing page leaves open: *What work would I actually do here? What did similar finished homes sell for? Who can help me verify this before I make an offer?* That gives a buyer a reason to contact you even when the analysis concludes that a property is a poor flip.

Three findings change the launch plan:

- **Public data rights need confirmation before launch.** CRMLS has an explicit AI rule. Its ordinary exception covers a defined client transaction or project under specified conditions. A market-wide public analysis product needs a different authorization for affected data. BAREIS also restricts third-party access. Hiding private remarks addresses only one part of the problem. [CRMLS rule 12.11.1](https://go.crmls.org/wp-content/uploads/2026/10/CRMLS-Rules-Policies-7.10.26_v2.pdf) · [BAREIS rules 12–14](https://www.bareis.com/volunteer-committees-members-home-menu/370-rules-and-regulations.html)
- **The first AI pass is affordable.** On November 21, MongoDB held **10,061 active-status residential listings and 390,040 photo references** across the nine counties. My base estimate for one full pass with the current Haiku design is **$448 at standard API rates or $224 using eligible batch processing**, before retries. At the recent intake rate, the base ongoing AI estimate is **about $174/month standard or $87/month batch**, before refreshes. Data licensing, local cost verification, and your time deserve more budget attention.
- **Aida found a real product defect.** On 2988 Orchid, the saved assessment says **$8,000–$16,000 total for two cosmetic bathroom refreshes**. The UI extracts the first range in the paragraph and can present **$2,000–$4,000**, which is only the vanity allowance, as the bathroom budget. Fixing the display is necessary. Making the estimate credible also requires a defined scope and local cost evidence.

## 1. Public launch and MLS permissions

Build a new public `/homes` and `/homes/<id>` experience. Reuse the map, listing cards, gallery, and financial calculations. Keep `/admin` as the authenticated review workspace. The public app should read a **separate, publishable listing record** that contains only the data and analysis approved for that listing.

The existing demo allowlist is a useful starting point. It does not establish that the current ConnectMLS import can be republished, that sold photos can be shown, or that listing content can be sent to an AI provider. There is also an important reversal from the demo design: the demo hides agent cards, while a public BAREIS detail page must identify the listing brokerage and listing agent. **Credit the listing side visibly, and identify you separately as the buyer’s contact.** [BAREIS rule 14.4](https://www.bareis.com/volunteer-committees-members-home-menu/370-rules-and-regulations.html)

| Permission to establish | What the broker should request in writing |
|---|---|
| Public search and maps | Which originating MLSs, statuses, fields, photos, addresses, map coordinates, and sold records the agreement covers. Ask which consumer uses are allowed for a site marketed to buyers evaluating renovations or flips. |
| AI processing | Permission to send photos and public remarks to the named provider, retain categories and observations, and publish derived renovation ranges, after-repair values, and financial scenarios. Include the proposed processor, retention terms, and batch workflow. |
| Historical analysis | Separate answers for internal comparable selection, public sold-property cards, before/after photographs, historical evaluation, and model training. Each has a different use and retention requirement. |
| Leads and monetization | Describe the actual flow: an inquiry goes to your brokerage, and later some buyers may consent to an introduction to a partner brokerage. Ask whether either arrangement affects the license or vendor classification. |
| Suppression and corrections | Obtain the actual feed fields and deadlines for Internet display, address suppression, valuation/comment suppression, media removal, expiration, and corrections. Include what must happen to cached pages and derived estimates. |

Two useful starting points are **BAREIS**, which asks for a member-sponsored vendor request describing the intended service and quotes a **$50 application fee and 3–6 weeks** for processing, and **MLSListings**, which directs brokers seeking a raw feed to `data@mlslistings.com` with their intended use. BAREIS discloses additional license fees during the application. Bay East has its own IDX agreements. Treat shared listings according to the originating source and the signed agreement covering them. [BAREIS application process](https://bareis.com/root-documents/forms/forms-1/idx-forms/3704-vendor-application-process-faq-article/file.html) · [MLSListings IDX FAQ](https://mlsl.my.site.com/community/s/article/IDX-FAQ) · [Bay East IDX services](https://bayeast.org/idxservices/)

**Implementation requirements for Terra**

1. Give each source explicit permissions for public display, photos, geolocation, sold comps, external AI processing, derived estimates, and training. Apply them to the **subject and every comp**. A subject approved for analysis can still have a restricted comp. Unknown permissions keep the corresponding feature off.
2. Build the public record server-side. Exclude private remarks, showing and access instructions, occupant and owner contacts, internal notes, reviewer identities, and raw MLS payloads. Apply the same projection to HTML, APIs, map popups, image metadata, social previews, sitemaps, and cached responses. Give the public service database access only to the published records.
3. Preserve MLS attribution, listing-agent and brokerage credit, timestamps, required notices, and photo marks. Label your brokerage’s analysis separately. An address-suppressed listing must also suppress precise map pins and identifiable URLs. When valuations are suppressed, remove the ARV, profit badge, purchase ceiling, and any equivalent figure that reveals the estimate. [BAREIS rule 14.4](https://www.bareis.com/volunteer-committees-members-home-menu/370-rules-and-regulations.html) · [NAR model IDX rules](https://www.nar.realtor/sites/default/files/policies/2014/Section-18-Internet-Data-Exchange-IDX-Rules.pdf)
4. Process permission changes and withdrawals ahead of AI jobs. Test a listing that becomes restricted while its analysis is queued and another that is already in the CDN. BAREIS specifies a 12-hour minimum refresh frequency for downloads and displays. Your 15-minute updater is useful only if removal propagates through every public surface. [BAREIS display and refresh rules](https://www.bareis.com/volunteer-committees-members-home-menu/370-rules-and-regulations.html)
5. Include the domain, attribution, contact module, and first-contact advertising disclosures in the preview Roozbeh will show the responsible broker. California DRE guidance covers the licensee’s identity, license number, and responsible broker’s licensed name. [California DRE advertising disclosure guide](https://www.dre.ca.gov/files/pdf/forms/re559.pdf)

**Fallback if broad AI rights are unavailable:** launch approved public IDX search with a tour/contact flow, and demonstrate enriched analysis on inventory for which you have explicit rights to the photos, property data, and proposed use. That lets you test the buyer proposition without making the business depend on approval from every Bay Area feed.

## 2. Fairfield review — 2988 Orchid, BA326091746

I inspected **all 28 original listing photos**, the stored AI assessment, its eight candidate comparables, and the code that renders its room budgets. The listing was stored at **$460,000**, with **1,426 sq ft**. My reading is a livable house with a seller-disclosed roof need and several discretionary finish upgrades. The photographs support pricing a **selective refresh** and a **full-remodel alternative** separately. [Fairfield review page](https://flips.roozbeh.realtor/admin/active/BA326091746)

| Area | What the evidence supports | What I would change |
|---|---|---|
| Roof | The public listing remarks specifically identify a new roof as needed. The gallery does not establish roof area, layers, underlayment, or decking condition. | Keep the roof prominent. Mark **seller-reported, roofer quote needed**. The AI’s $8,500–$12,000 is unsupported by a takeoff or bid. Sacramento’s published asphalt replacement benchmark is $31,775 for its standardized project. That discrepancy warrants a quote, not an automatic substitution. [JLC Sacramento project costs](https://www.jlconline.com/cost-vs-value/2025/pacific/sacramento-ca/) |
| Bathrooms | Photo **15** shows a brown vanity and framed shower enclosure. Photo **21** shows a different vanity and curtained bathing area. Both look dated. Neither photo establishes failed waterproofing or a need to rebuild the shower. | Show two alternatives: **cosmetic refresh** and **full remodel**, each priced per bath with quantity two. Aida’s $25k–$40k **per full bath** is plausible. JLC’s 2025 midrange project is **$26,049 in Sacramento and $32,160 in San Francisco**. Those are regional benchmarks. A Fairfield contractor should price this house. [Sacramento benchmark](https://www.jlconline.com/cost-vs-value/2025/pacific/sacramento-ca/) · [San Francisco benchmark](https://www.jlconline.com/cost-vs-value/2025/pacific/san-francisco-ca/) |
| Kitchen | Photos **5–8** show several angles of the same kitchen, with existing cabinetry, counters, appliances, and an overhead light panel. The layout appears usable. Appliance operation and cabinet-box condition are unknown. | Price a retain-layout refresh first. Add separate alternatives for refacing and replacement. The UI can show **$8k–$15k** for the kitchen even though the model meant cabinetry alone and separately priced counters and lighting. The photos also do not justify a skylight allowance. |
| Floors and paint | Photos **9–19** show substantial visible wood flooring. Several floor coverings appear to be area rugs. The bright walls and floors do not establish a whole-house replacement need. | Measure refinishable area. Price touch-up, refinishing, and replacement as alternatives. The saved suggestion to carpet two or three bedrooms needs better evidence. Assign kitchen, bath, and whole-house painting once each. |
| Mechanical and exterior | HVAC condition and remaining service life cannot be determined from this gallery. The yard, courtyard, garage, and outbuilding are visible. | Put HVAC, water heater, and concealed work under **inspection items**. Keep basic landscape cleanup optional. Give a possible future ADU no value in the base case without entitlement, cost, and market evidence. |

**The financial conclusion is clearer than the renovation estimate.** Two unusually relevant sales in the saved MLS evidence are **2731 Orchid**, 1,426 sq ft, sold for **$530,000**, about **0.33 miles** away, and **2761 Orchid**, also 1,426 sq ft, marketed as completely remodeled, sold for **$560,000**, about **0.27 miles** away. They give a buyer a tangible comparison for the model’s $545,000 midpoint. The saved explanation also invokes a 1,926-square-foot, two-parcel sale outside its five selected comp IDs. The public report should cite only the sales actually used and explain any premium over the best comparable.

| Fairfield scenario | Estimated result at $460,000 asking | Break-even purchase ceiling |
|---|---:|---:|
| Saved midpoint: $545k resale and $50k work | **−$47,575** | **$416,848** |
| Favorable ends of the saved ranges: $568k resale and $35k work | **−$11,070** | **$449,959** |
| Sensitivity: $545k resale and $103k work | **−$100,575** | **$368,776** |

The last row changes one assumption. It replaces the saved **$12k midpoint for two cosmetic baths** within the $50k total with **$65k for two full baths**, the midpoint of Aida’s range. It illustrates a different scope, not a new contractor estimate. All three rows use the app’s ten-month hold, 10% simple interest on the purchase, 1.1% annual property tax, 1% purchase closing costs, 6.5% sale costs, and zero HOA. Insurance and several carrying costs are still excluded. **Even the favorable combination shows a loss at asking.** That is a more useful public insight than arguing that either bathroom price is universally correct.

**The engineering repair:** replace prose extraction in [active.py](../data_service/flip_ui/active.py) with structured cost items. Each item needs a room, task, quantity, units, unit-price range, source, source date, geography, evidence photo IDs, inclusion status, and pricing confidence. Let code compute subtotals and totals. Mark alternatives as mutually exclusive. Older records whose prose cannot be reconciled should say **room subtotal unavailable** until reviewed. Fairfield should be the first acceptance test.

For the price source, I would commission **two or three Solano County contractors to price the same written scopes**, starting with these two baths, the kitchen refresh, and the roof. Ask separately for labor, materials, permits, disposal, contingency, and exclusions. A dated local bid is stronger evidence than a regional benchmark. A paid invoice tied to the completed scope is stronger still. Verify usage rights before incorporating a commercial cost database. Fairfield’s permit portal can help verify permits and inspections for a particular job. [Fairfield BUILD](https://www.fairfield.ca.gov/government/city-departments/community-development/fairfield-build)

## 3. Market-wide coverage, cost, and hardware

### What the first run would process

These are MongoDB MCP counts from November 21, 2026, using the app’s active statuses across the nine Bay Area counties, before the ten-property demo restriction.

| Inventory measure | Count |
|---|---:|
| Stored active-status residential listings | 10,061 |
| Listings inside the map’s current coordinate bounds | 10,055 |
| Stored gallery photo references across all nine counties | 390,040 |
| Average photo references per listing | 38.8 |
| Listings with an empty stored gallery | 57 |
| Listings with more than 64 photos | 1,045 |
| Active assessments already saved | 10 |

These are stored listings and references. The approved public feed will determine the real launch inventory, and a stored URL still needs a successful image fetch. The present cap of 64 would skip **16,627 photos**. If the product promise is “all available photos analyzed,” use resumable chunks until each eligible gallery is complete. Show explicit states for missing galleries, pending analysis, insufficient comparables, and completed analysis. That lets search show eligible homes while keeping unsupported numbers off their cards.

### Initial and recurring AI spend

I priced the system you have: one Haiku 4.5 call per photo, resized to a maximum of 384 px, followed by one text assessment per listing. Anthropic lists **$1 per million input tokens and $5 per million output tokens**, with a **50% batch discount**. The current worker uses synchronous calls. Batch pricing requires implementing the batch workflow. The following token amounts are planning assumptions, not measured billing records. [Anthropic Haiku 4.5 pricing](https://platform.claude.com/docs/en/models/haiku-4-5/overview) · [Anthropic vision pricing](https://platform.claude.com/docs/en/build-with-claude/vision)

| Usage assumption | Tokens per photo, input/output | Tokens per assessment, input/output | Initial full pass, standard | Initial full pass, batch |
|---|---:|---:|---:|---:|
| Lean | 300 / 45 | 3,000 / 700 | $270 | $135 |
| **Base** | **500 / 65** | **5,000 / 1,500** | **$448** | **$224** |
| Heavy | 1,000 / 120 | 8,000 / 3,000 | $855 | $428 |

The base case is **about 4.45¢ per listing**: roughly $322 to classify the market’s photos and $126 to assess the listings. This excludes retries, paid source access, human review, and any future high-resolution second pass. I would reserve **$1,000 for initial inference**, with the first **100 rights-cleared listings** acting as a spending checkpoint. Record actual image and text tokens, output tokens, retries, cache hits, and dollars per successful listing before releasing the remaining queue.

For ongoing intake, **910 listings still active on November 21 were first inserted November 14–20**, averaging **130 per day**. This is a directional proxy: it misses listings that already left active status and can include imports of older listings. I excluded the November 13 bulk load. At that pace, **3,900 listing-equivalents per 30 days** would cost **$105–$332 standard**, with **$174** as the base case. Batch equivalents are approximately **$52–$166**, with **$87** as the base case. Track actual new listings, changed images, and comp-only refreshes for a month to replace this proxy.

**The most important cost control is deciding what deserves another call.** A price change should recalculate the economics without reclassifying photos. A new photo should trigger one image call and, if it materially changes the evidence, one assessment. A changed comparable should refresh valuation. A revised contractor price book should reprice applicable scopes in code. The 15-minute updater should enqueue those events with versioned inputs, deduplication, retries, and a spend cap. A public page view should never trigger paid inference.

### What I would budget for the business

| Budget item | My planning allowance |
|---|---:|
| Initial market inference, including room for retries | Up to $1,000 |
| Initial local scope pricing, broker/legal review, and setup | **$2,000–$5,000 reserve** pending quotes |
| Ongoing AI, including refresh allowance | $100–$400/month |
| App, worker, database increment, backups, maps, monitoring, transactional email | $75–$250/month at pilot traffic |
| Ongoing contractor sampling and review | $250–$750/month, or equivalent founder/partner time |
| MLS/IDX/derived-data licenses | **Obtain written quotes** by source and use |
| Development labor and paid customer acquisition | Separate decisions |

**My working budget would be $3,000–$6,000 upfront and roughly $425–$1,400/month, plus quoted MLS fees, development labor, and advertising.** The contractor and professional-review amounts are reserves I would set, not market quotes. If the data license is expensive, test one authorized market first. A product with 200 credible local analyses and ten real buyer conversations will tell you more about this business than 10,000 unverified price badges.

### Traffic and hardware

I would plan the first 90 days around **1,000–5,000 visits/month** from your network, interviews, referrals, and local search. That is a product-planning assumption. There is no established public traffic baseline here. Load-test for **25,000 visits/month**, then expand when actual demand warrants it.

| Scenario | Estimated load at five pages per visit | Deployment I would choose |
|---|---:|---|
| Pilot, 1k–5k visits/month | 5k–25k page views | One **2-vCPU, 4-GB** web instance, existing MongoDB if it has headroom, CDN for allowed public assets, separate background worker |
| Growth, 25k visits/month | 125k page views | Load-test at **20 requests/second** for bursts. Cache listing summaries, query map bounds, cluster pins, lazy-load thumbnails, and measure database latency |
| Expansion, 100k visits/month | 500k page views | Add web replicas if measured latency or availability needs justify them. Review photo bandwidth, map sessions, and database reads before adding CPU |

A comparable AWS Lightsail Linux bundle with public IPv4 is **$24/month for 4 GB and two vCPUs** or **$44/month for 8 GB and two vCPUs**. These are cost anchors, not estimates of your existing AWS invoice. No GPU is needed for hosted inference. Map and image traffic can dominate: at an illustrative 2 MB per page, 125,000 page views represents roughly **250 GB**. Use licensed photo delivery and a map provider appropriate for public traffic. The standard OpenStreetMap tile service has a usage policy and no guaranteed capacity. [AWS Lightsail pricing](https://aws.amazon.com/lightsail/pricing/) · [OpenStreetMap tile policy](https://operations.osmfoundation.org/policies/tiles/)

## 4. Buyer leads, agent recruitment, and off-market supply

### Buyer-facing flow

Put **Request a tour** and **Ask Roozbeh about this home** beside the analysis. I prefer the second label to “Contact agent”: it tells the buyer who receives the request and avoids confusion with the listing agent. For Fairfield, a buyer could open the form with a relevant question already selected, such as *“Can you help me verify the roof budget?”* That ties the inquiry to the reason the buyer used the product.

| Route | What Terra should build | Acceptance condition |
|---|---|---|
| `/homes/<id>/request-tour` | Name, preferred contact channel, contact detail, preferred time windows, optional note, and whether the buyer is already represented | Receipt says **requested**. A showing becomes confirmed only after the agent verifies access. No invented availability. |
| `/homes/<id>/contact` | Property-specific inquiry, named recipient and brokerage, preferred reply channel, optional question about scope, price, or comparables | Buyer can request one response without enrolling in marketing. Store property, consent text/version, recipient, and timestamp. |
| `/agents/partner` | Short recruitment page and application: name, CA DRE number, responsible brokerage, markets served, fixer-upper experience, availability, and contact details | Verify license and broker affiliation before routing leads. Application alone gives no access to buyer information. |
| Private lead inbox | Deduplication, assignment, response deadline, handoff, outcome, opt-out, and retention controls | One accountable agent per inquiry. A duplicate submission does not distribute the buyer to multiple agents. |

Start by handling leads yourself. Measure **qualified conversations, tours held, representation agreements, offers, and eventual closings**. A useful pilot target would be **10 qualified conversations and three held tours in 60 days**. Define qualified as a reachable buyer who wants help with a specific property or buying decision. Track the source and cost per qualified conversation. Traffic alone does not validate this model.

**For paid agents, decide the commercial structure before building billing.** A brokerage-to-brokerage referral arrangement, a flat advertising purchase, and selling personal lead data are different transactions. federal Regulation X recognizes certain cooperative brokerage/referral arrangements and restricts payments for settlement-service referrals. California also places compensation for licensed acts through the responsible broker. BAREIS separately publishes a third-party public-site category that restricts vendor finder’s fees. Have the broker and counsel confirm which structure and feed agreement cover this business before charging. Keep title, escrow, mortgage, and other settlement-service referrals outside the initial revenue model. [Regulation X §1024.14](https://www.consumerfinance.gov/rules-policy/regulations/1024/14/) · [California DRE professional responsibility guidance](https://www.dre.ca.gov/files/pdf/professionalresponsibilitycoursebooklet.pdf) · [BAREIS public-site guidance](https://www.bareis.com/root-documents/forms/forms-1/idx-forms/122-public-internet-sites.html)

Give buyers a privacy notice at collection, identify who will receive their details, and get separate permission for ongoing outreach or a partner handoff. Have counsel assess California privacy obligations before selling or sharing leads. Put the applicable buyer-representation steps in the agent’s tour workflow. California’s regulations address agreement timing before a showing, while the web form itself can remain a simple request. [California privacy guidance](https://oag.ca.gov/privacy/ccpa) · [California DRE regulation 2906.2](https://www.dre.ca.gov/files/pdf/relaw/regs.pdf) · [FTC telemarketing guidance](https://www.ftc.gov/business-guidance/resources/complying-telemarketing-sales-rule)

### What Aida’s competitor research suggests

- **BiggerPockets** already helps investors assemble teams of agents, lenders, and tax professionals. **InvestorLift** advertises free buyer access, off-market deals, ARV, and underwriting. These examples support Aida’s skepticism about selling another general investor subscription. Your differentiator can be a local agent who shows the evidence behind an estimate and helps a buyer verify it in person. [BiggerPockets team marketplace](https://www.biggerpockets.com/build-team) · [InvestorLift](https://investorlift.com/)
- **7 Minutes House Deals** advertises free signup, gated inventory, and booking a spot to view a property. Treat it as a potential supply partner. For **391 Ponderosa**, I could verify the platform’s gated model and a Redfin property page showing off-market status. I could not independently confirm the supplied deal page’s live availability or Zillow’s current status. A public-record property page and a privately circulated opportunity can coexist. [7 Minutes House Deals](https://7minuteshousedeals.com/) · [391 Ponderosa on Redfin](https://www.redfin.com/CA/South-San-Francisco/391-Ponderosa-Rd-94080/home/624441)
- **The first off-market integration should be one consenting wholesaler.** Ask for explicit advertising and photo rights, the party authorized to offer access, assignment terms where relevant, asking price, showing windows, and a withdrawal/update mechanism. The partner can provide a small feed or enter deals manually. Send availability changes to the assigned agent. Display a source label and last-confirmed time. Keep private access instructions inside the brokerage workflow.
- **The cheaper acquisition experiment is already in your MLS data.** Once licensed, offer filters for 45+ and 60+ days on market, price reductions, and explicitly sourced bank-owned or auction status. Test whether those filters lead to qualified buyer conversations. Public records, a trust ownership designation, or time on market alone do not establish seller distress or permission to advertise.

## 5. How historical flips should improve future analysis

**The answer to Aida today:** “Our active estimates use the subject’s photo observations, public listing remarks, and nearby closed sales. Our historical flip labels are stored separately. They do not currently train the active estimator. We’re building a way to use comparable past projects and verified costs, then test whether the forecasts improve.”

I checked both the code and MongoDB. There are **553 historical pair records**, **11 saved historical renovation insights**, **five success labels and six failed labels**, and **10 active assessments with 237 active photo classifications**. The dedicated photo, room, and cost correction collections returned **zero records**. The active assessment selects up to eight nearby closed sales and feeds the model their facts and public remarks alongside short observations from the subject’s photos. It does not query the historical flip labels, compare original comp photos, or learn actual renovation spend. [Active assessment implementation](../data_service/photo_categorizer/active_listing.py) · [Reviewer workflow](../data_service/flip_ui/REVIEW_WORKFLOW.md)

I would use historical records for **three distinct questions**:

| Buyer question | Historical evidence to use | Product output |
|---|---|---|
| What could this home sell for after a specified renovation? | Similar **completed sales**, matched by property type, size, location, date, and finish. Include relevant ordinary sales as well as flips. | An after-repair value range, three to five supporting sales, and an explanation of adjustments or weaknesses. |
| What work would get it to that finish, and what might it cost? | Before/after evidence for scope, then dated contractor bids or invoices for price. Keep evidence of actual spend distinct from reviewer opinion. | Room-level work items, quantity, local price source, confidence, and finish alternatives. |
| How did comparable projects perform? | Verified purchase and resale prices, hold period, financing and transaction costs where available, and actual renovation spend where documented. | Similar past projects with **observed gross spread**, **modeled result**, or **documented net result**, according to the evidence available. |

**Fairfield shows why proximity alone fails.** I found a historical pair just **0.10 miles** from Orchid, but it is a **662-square-foot, one-bedroom unit**. It should not inform the economics of a 1,426-square-foot house. A more relevant candidate is **428 Crofters**, about **0.36 miles** away, 1,474 sq ft, bought for **$415,000** and sold for **$565,000** after **77 days**. Its **$150,000 gross spread** is observable. The database marks the flip unverified, and its actual rehab spend is unknown. It is a good candidate for a reviewer to investigate. It would be misleading to predict that the buyer of Orchid could earn $150,000. The same-size finished sales on Orchid are stronger evidence for the resale target. This fits Fannie Mae’s guidance to consider comparable properties’ competitive characteristics, location, and market-supported adjustments. [Fannie Mae comparable-sales guidance](https://guide-selling.fanniemae.com/sel/b4-1.3-08/comparable-sales)

**Development sequence**

1. **Build comparable retrieval first.** Start with transparent same-type, similar-size, nearby sales; rank by recency, finish, and market similarity. Show the reason each comp qualifies. Add a separate “similar historical projects” panel. Broaden the search only with an explanation. With inadequate evidence, publish the listing and leave its valuation pending.
2. **Collect better labels.** Keep the existing success/failure judgment, then capture *why*: purchase discount, renovation execution, holding time, market movement, or weak evidence. For costs, distinguish AI estimate, agent estimate, contractor bid, and paid invoice. Record labor, materials, geography, date, finish level, and scope. Obtain permission to use documents and redact personal details.
3. **Make the economics reproducible.** Compute the result from the selected scope, purchase price, expected resale, and a versioned ledger. Add Aida’s marketing, photography, staging, notary, attorney, title/escrow, and transfer costs when applicable. Show financing, tax, HOA, insurance, utilities, contingency, and renovation financing as explicit assumptions. Record when an item is already included in a percentage or brokerage package so it is counted once. Keep **break-even ceiling**, **offer for a selected profit target**, and **owner-occupant renovation budget** as separate answers.
4. **Test history before trusting it.** Reconstruct older predictions using only information available on the forecast date. Hold out later sales and keep repeat listings of the same parcel together. Compare the current method, a simple adjusted-comp baseline, and the history-enhanced method on the same homes. Report resale error, interval coverage, renovation error on invoice-backed projects, and how often a claimed profitable deal becomes a loss. Include losses, incomplete projects, and homes that never resold where observable. A dataset made only of completed flips favors survivors.
5. **Consider model training after evaluation identifies a specific need.** Photo-category corrections can improve categorization. Verified room costs can calibrate unit prices. Agent success labels can test ranking usefulness. These are different targets. Eleven subjective outcomes cannot validate a market-wide profit forecast. The first release can benefit from historical evidence without fine-tuning any model.

## Handoff order for Sol or Terra

Start with the Fairfield estimate repair and build the pilot in a private preview using fixtures or data already cleared for the intended use.

| Order | Owner and deliverable | Done when |
|---|---|---|
| **1. Repair the estimate** | Terra replaces prose parsing with structured scope and price items. You obtain local cost checks. | Fairfield’s vanity cannot appear as a whole-bath price, alternatives do not double-count, and every published total reconciles to line items with sources. |
| **2. Build the public pilot** | Terra adds isolated `/homes`, request-tour/contact, partner application, attribution, and suppression handling. | A buyer can browse and contact you. Private information stays out of every public surface. A withdrawal and a valuation opt-out both pass end-to-end tests. |
| **3. Meter 100 listings** | Run a rights-cleared sample across condition, geography, gallery size, and property type. Review a stratified sample with an agent and contractor. | You have actual dollars per property, coverage, failure rates, comp quality, and the rate of materially misleading estimates. |
| **4. Launch one market and measure leads** | Handle the first inquiries yourself and record objections, tours, and outcomes. | You can identify which analyses generated a qualified conversation and what prevented the others from doing so. |
| **5. Expand coverage and historical evidence** | Add the resumable all-photo queue, versioned reanalysis, nearby historical projects, and time-based evaluation. | Incremental changes are cheap, every eligible listing has an accountable processing state, and any claimed forecasting improvement is demonstrated on held-out data. |
| **6. Monetize partner routing** | Broker-approved agreements, verified agents, buyer-authorized handoffs, outcome reporting, then billing. | The economics work per qualified lead or completed referral, and the signed MLS and brokerage arrangements permit the structure. |

## Roozbeh’s action item before public launch

- [ ] **When the website is ready for review**, walk my broker and the relevant MLS providers through the private preview. Use the permissions table in section 1 to record the approved uses, processor terms, restrictions, and fees for each source. Confirm the disclosures and lead-routing terms with my broker, then give Terra the written decisions so the team can enable the approved sources and features for launch.
