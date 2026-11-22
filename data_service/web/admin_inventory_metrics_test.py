import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from admin_inventory_metrics import active_freshness, photo_coverage


class AdminInventoryMetricsTest(unittest.TestCase):
    def test_active_age_uses_inventory_seen_and_detail_timestamps_separately(self):
        now = datetime(2026, 11, 22, tzinfo=timezone.utc)
        old = now - timedelta(days=16)
        listings = Mock()
        listings.find.return_value = [
            {"active_last_seen_at": now, "_updated_at": old, "details_fetched": "November 01 2026 01:15PM"},
            {"_updated_at": old, "details_fetched": now.isoformat()},
            {"_updated_at": now - timedelta(days=15), "details_fetched": old.replace(tzinfo=None)},
            {"active_last_seen_at": "invalid"},
        ]
        self.assertEqual(active_freshness(listings, now), {
            "inventory_stale": 1, "inventory_unknown": 1, "detail_stale": 2, "detail_unknown": 1,
        })
        self.assertEqual(listings.find.call_args.args[0], {"MLS_STATUS": "ACTV"})

    def test_deduplicates_listings_across_photo_jobs_and_filters_unsuccessful_bulk_results(self):
        db = Mock()
        db.mls_photo_classifications.distinct.return_value = ["one", "Two"]
        db.active_photo_classifications.distinct.return_value = ["ONE", "three", " "]
        db.photo_classifications.distinct.return_value = ["three", "four"]
        self.assertEqual(photo_coverage(db), {"total": 4, "bulk": 2})
        field, criteria = db.mls_photo_classifications.distinct.call_args.args
        self.assertEqual(field, "listing_id")
        self.assertEqual(criteria["result"], "classified")
        self.assertIn("unclassified", criteria["category"]["$nin"])
