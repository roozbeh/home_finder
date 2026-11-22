# Home Finder future work

The buyer UI and private workflows are implemented. The local signed-out buyer preview uses eligible real inventory. The existing external Flip Finder host serves the legacy private workspace. The new buyer site still needs its approved production configuration and external HTTPS acceptance.

## Buyer release

- [ ] Obtain written brokerage approval and source-specific MLS decisions for public listing display, attribution, suppression deadlines, media, sold comparables, valuations and AI use. Record source and listing grants, effective dates, required notices, and refresh deadlines privately. Audit actual feed mappings and source permissions before enabling them. Confirm approved content, processor and historical training rights separately.
- [ ] Collect verified buyer contact identity, responsible brokerage, license, advertising language, approved intake and retention wording, and notification destination. Configure those values privately. Decide who owns replies, how tours and representation are verified, and how buyer consent is recorded for any later partner introduction.
- [ ] Repeat the current approved-inventory journey from a fresh signed-out desktop and phone. Verify filters, list/map parity, back navigation, details with and without analysis, private-field suppression, withdrawn media and direct URLs, and the inquiry, tour and partner receipts. Confirm the matching records and status changes in the private queues and denial for signed-out and demo roles.
- [ ] Approve the production hostname, TLS and renewal, operator-only queue ingress, release window, backups and rollback owner. Stage the buyer app separately from ingestion and private review. Enable approved inventory source by source and require a fresh external HTTPS smoke test before announcing release. [Buyer rollout](data_service/flip_ui/BUYER_ROLLOUT.md) · [Publisher runbook](data_service/flip_ui/BUYER_PUBLISHER.md)

## Renovation and evidence

- [ ] Obtain comparable local bids or licensed price evidence for the Fairfield bathroom alternatives, kitchen, and seller-reported roof. Review quantities, evidence photos, provenance, inspection gaps, exclusions, confidence and ownership of any invoice attachments. Keep component amounts under the appropriate scope and retain the Fairfield regression for the two-bath alternatives.
- [ ] Confirm financing, holding and transaction assumptions with the responsible brokerage before publishing scenarios. Record unknown fees and identify any already included in bundled resale fees. Reconcile monthly carry, contingency, tax, insurance, staging, marketing, photography, legal, escrow and transfer costs without double-counting.
- [ ] Verify the actual selected sales and relevant historical purchase/resale pairs after rights clearance. Treat observed gross spread separately from documented costs and net results. Keep the Fairfield one-bedroom exclusion and Crofters evidence cases in the test set. Add verified scope, cost and outcome evidence.
- [ ] Run the leakage-safe historical evaluation on a licensed, later-sales holdout with repeat parcels grouped and only evidence available at forecast time. Compare the current, adjusted-comp and history-enhanced methods. Record resale error, interval coverage, false profitable calls and invoice-backed renovation error before claiming an accuracy gain. [Review workflow](data_service/flip_ui/REVIEW_WORKFLOW.md)

## Processing and capacity

- [ ] Benchmark Gemma 4 12B on reviewed scopes and use dated cost inputs to price validated work. Compare it with 31B only where the paired benchmark shows enough improvement. [Local renovation model benchmark](data_service/photo_categorizer/RENOVATION_MODELS.md)
- [ ] Check the current Moondream checkpoint, worker liveness, failed galleries and throughput. Confirm saved categories appear for permitted photos after the catalog refresh. Keep processing eligibility separate from publication eligibility. Evaluate optional Gemma/Ollama categorization on a representative rights-cleared set if the existing backfill's accuracy or capacity warrants it.
- [ ] Use the rights-cleared 100-listing pilot to validate all-photo accounting, resumability, changed photos and prices, comp/price-book updates, spend caps, retry rate and cost per successful listing. Obtain provider terms, a production budget and local cost evidence before enabling paid processing or expanding coverage. Separate fixture metering, actual spend and projected cost.
- [ ] Run the signed-out 20-request/second test against the target deployment and a representative catalog. Record request latency, errors, database work and image traffic before changing instance size. Monitor the 15-minute and nightly jobs, stale listing counts, MLS failures and storage independently. [Photo operations](data_service/photo_categorizer/ACTIVE_LISTINGS.md)

## Supply and buyer follow-up

- [ ] Pilot owner-handled inquiries and track qualified conversations, held tours, representation, offers and closings. The earlier planning target was 10 qualified conversations and three held tours in 60 days.
- [ ] Select one consenting wholesaler. Verify authority to advertise, media rights, access, assignment terms and its withdrawal/update process. Validate a real opportunity and tour from source through private access history to withdrawal.
- [ ] Verify partner licenses and brokerage affiliation and obtain brokerage-approved terms and explicit buyer permission before assigning a lead. Decide any paid model with the broker. Title leads, trust-owner targeting, gated inventory and automated paid distribution remain separate business decisions.

Historical research reports, generated screenshots and market-history exports belong in private artifact storage. Use [the repository guide](CLAUDE.md) for project structure, commands and active runbooks.
