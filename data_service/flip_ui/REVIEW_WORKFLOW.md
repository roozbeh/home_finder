# Reviewer workflow

Sign in as admin and open a resale pair. Room observations appear below the room's purchase/resale photos. Expand the observation review form to replace the text or leave it empty to suppress an unsupported claim. Click the large room photo (or focus it and press Enter) to change its category and add a private note. Hallway is available as a reviewed category. Saving reloads and regroups the photos.

The financial table remains above the listing galleries. Renovation scope appears directly beneath the renovation amount. Expand Edit renovation estimate, enter a low and high range (the same amount in both gives a fixed estimate), update scope and explanation, and save. All-in results recalculate from the new midpoint and range. Reviewer notes are visible only to admins; reviewed observation text and financial estimates are visible to demo viewers.

MongoDB collections in flip_finder:

- photo_reviews: latest photo correction, keyed by pair_key, side and photo_url.
- room_insight_reviews: latest room observation correction, keyed by pair_key and room.
- insight_reviews: latest renovation range/scope correction, keyed by pair_key.
- review_events: appended before/after review events with reviewer, timestamp and AI source snapshot.

The source photo_classifications and renovation_insights records stay intact. Reviewer overrides survive AI regeneration. These records are feedback for future supervised training or evaluation; saving them does not automatically train a model. Treat cost edits as reviewer estimates unless supported by actual invoices. Export with separate AI, reviewer and actual-outcome fields to avoid training on estimates as if they were actual spend.

Legacy AI observation area names are mapped to rooms by their area text. Unmatched observations appear under Other spaces for review. This association is provisional; human reviewed room text is authoritative. The current design records latest edits and separate events; concurrent reviewers can overwrite latest state and should coordinate until revision conflict handling is added.

## Historical evaluation

Run `python buyer_evaluate.py` from this directory for the fixture-based evaluation. For an approved dated export, pass a JSON dataset as the positional argument. The evaluator freezes comparison evidence before the holdout date and groups forecasts by parcel.
