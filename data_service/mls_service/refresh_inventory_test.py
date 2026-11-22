"""Test the scheduled lanes with an isolated MongoDB and mocked MLS responses."""
import os
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch
from uuid import uuid4

from pymongo import MongoClient

import refresh_inventory as refresh
import search_and_store as search


class SearchTest(unittest.TestCase):
    def test_capped_or_malformed_city_does_not_count_as_complete(self):
        observed, complete = {}, set()
        with patch.object(search, "ROW_CAP_THRESHOLD", 2), patch.object(search, "fetch_listings", side_effect=[
            [{"LISTING_ID": "one"}], [{"LISTING_ID": "two"}, {"LISTING_ID": "three"}], [{}],
        ]):
            result = search.fetch_target_active_listings(None, ["OK", "CAP", "BAD"],
                                                        observed=observed, complete_cities=complete)
        self.assertEqual(complete, {"OK"})
        self.assertEqual(observed, {"one": "OK", "two": "CAP", "three": "CAP"})
        self.assertEqual(len(result), 3)

    def test_request_cutoff_and_timeouts(self):
        session = Mock()
        call = session.request
        with patch.object(refresh.time, "time", side_effect=[90, 99]):
            refresh.bound_requests(session, 100)
            session.request("GET", "https://example.invalid", timeout=30)
            with self.assertRaises(refresh.DeadlineReached):
                session.request("GET", "https://example.invalid")
        call.assert_called_once_with("GET", "https://example.invalid", timeout=(4, 5))

    def test_legacy_timestamp_and_unusable_detail(self):
        self.assertEqual(refresh.checked_at("March 19 2026 11:03PM"),
                         datetime(2026, 3, 19, 23, 3, tzinfo=timezone.utc))
        session = Mock()
        session.get.return_value.json.return_value = {"data": [{"status": "", "CLOSE_PRICE": 100}]}
        with patch.object(refresh, "assert_authenticated_response"), self.assertRaises(ValueError):
            refresh.fetch_detail(session, "bad")


@unittest.skipUnless(os.getenv("MLS_REFRESH_TEST_URI"), "set MLS_REFRESH_TEST_URI to an isolated MongoDB")
class RefreshIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.mongo = MongoClient(os.environ["MLS_REFRESH_TEST_URI"], tz_aware=True, serverSelectionTimeoutMS=5000)
        self.db = self.mongo[f"refresh_test_{uuid4().hex}"]
        self.addCleanup(self.mongo.close)
        self.addCleanup(self.mongo.drop_database, self.db.name)
        self.col = self.db.mls_listings
        self.now = datetime(2026, 11, 22, tzinfo=timezone.utc)
        self.old = self.now - timedelta(days=85)

    def seed(self, listing, *, city="ATHERTON", status="ACTV", **extra):
        self.col.insert_one({"LISTING_ID": listing, "DCID": listing, "CITY": city,
                             "RAW_STATUS": status, "MLS_STATUS": status, "_updated_at": self.old, **extra})

    def observe(self, listing, when):
        search.upsert_listings([{"LISTING_ID": listing, "DCID": listing, "CITY": "ATHERTON",
                                "RAW_STATUS": "ACTV", "MLS_STATUS": "ACTV"}],
                               database=self.db, observed={listing: "ATHERTON"}, seen_at=when)

    def test_repeated_misses_status_correction_and_return(self):
        self.seed("missing", details_fetched=self.old)
        self.seed("returns", details_fetched=self.old)
        self.seed("incomplete-city", city="BELMONT")
        self.seed("pending", status="PCH")
        self.observe("present", self.now)
        refresh.record_misses(self.col, {"ATHERTON"}, self.now)
        self.assertFalse(self.col.find_one({"LISTING_ID": "missing"}).get("active_status_next_check"))
        next_cycle = self.now + timedelta(minutes=15)
        self.observe("returns", next_cycle)
        refresh.record_misses(self.col, {"ATHERTON"}, next_cycle)
        missing = self.col.find_one({"LISTING_ID": "missing"})
        self.assertEqual(missing["active_status_next_check"], next_cycle)
        self.assertEqual(missing["RAW_STATUS"], "ACTV")
        for listing in ("incomplete-city", "returns", "pending"):
            self.assertNotIn("active_missing_since", self.col.find_one({"LISTING_ID": listing}))

        with patch.object(refresh, "fetch_detail", return_value=({"status": "WC"}, "WC")), \
             patch.object(refresh, "fetch_gallery_photos") as gallery:
            refresh.refresh_listing(self.col, None, missing, "status")
        gallery.assert_not_called()
        confirmed = self.col.find_one({"LISTING_ID": "missing"})
        self.assertEqual(confirmed["RAW_STATUS"], "WC")
        self.assertEqual(confirmed["details"]["status"], "WC")
        self.assertNotIn("active_status_next_check", confirmed)

    def test_refreshes_oldest_current_before_recent_and_historical_sales(self):
        self.seed("current-old", active_last_seen_at=self.now, details_fetched="August 20 2026 12:00AM")
        self.seed("current-newer", active_last_seen_at=self.now, details_fetched=self.old)
        self.seed("current-fresh", active_last_seen_at=self.now, details_fetched=self.now)
        self.seed("archive", status="SLD")
        self.seed("recent", status="SLD", sold_last_seen_at=self.now)
        work = list(refresh.detail_work(self.col, self.now, 75, 25))
        self.assertEqual([(lane, row["LISTING_ID"]) for lane, row in work],
                         [("display check", "current-old"), ("display check", "current-newer"),
                          ("recent sold", "recent"), ("historical sold", "archive")])

    def test_nightly_sales_become_recent_work_and_preserve_existing_details(self):
        self.seed("sold", details={"status": "ACTV"}, photos=["existing"])
        search.upsert_listings([{"LISTING_ID": "sold", "RAW_STATUS": "SLD", "MLS_STATUS": "SLD",
                                "details": {"status": "fake"}, "photos": []}],
                               database=self.db, sold_seen_at=self.now)
        stored = self.col.find_one({"LISTING_ID": "sold"})
        self.assertEqual(stored["details"]["status"], "ACTV")
        self.assertEqual(stored["photos"], ["existing"])
        self.assertEqual(stored["sold_last_seen_at"], self.now)
        self.assertEqual(next(refresh.detail_work(self.col, self.now, 75, 25))[0], "recent sold")

    def test_budget_preserves_saved_details_when_gallery_cannot_start(self):
        self.seed("active", active_last_seen_at=self.now, details_fetched=self.old)
        row = self.col.find_one({"LISTING_ID": "active"})
        with patch.object(refresh, "fetch_detail", return_value=({"status": "ACTV", "INTERNET_DISPLAY": "No Address"}, "ACTV")), \
             patch.object(refresh, "fetch_gallery_photos", side_effect=refresh.DeadlineReached), \
             patch.object(refresh.time, "sleep"), self.assertRaises(refresh.DeadlineReached):
            refresh.refresh_listing(self.col, None, row, "active")
        self.assertEqual(self.col.find_one({"LISTING_ID": "active"})["details"]["INTERNET_DISPLAY"], "No Address")

    def test_caps_extra_work_and_stops_before_starting_at_deadline(self):
        for n in range(30):
            self.seed(f"recheck-{n}", active_last_seen_at=self.now, details_fetched=self.old)
        self.seed("new-core", active_last_seen_at=self.now)
        self.seed("sold-core", status="SLD")
        with patch.object(refresh, "refresh_listing", return_value=0) as listing, \
             patch.object(refresh, "log_listing"), patch.object(refresh.time, "sleep"):
            refresh.drain_details(self.db, None, self.now, time.time() + 60, 25, 75, 4500)
            self.assertEqual(listing.call_count, 27)
            self.assertEqual([call.args[-1] for call in listing.call_args_list][-2:],
                             ["new active", "historical sold"])
            listing.reset_mock()
            refresh.drain_details(self.db, None, self.now, time.time() + 1, 25, 75, 4500)
            listing.assert_not_called()


if __name__ == "__main__":
    unittest.main()
