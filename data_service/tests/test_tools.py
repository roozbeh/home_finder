"""Tests for individual tool implementations."""

import pytest
from unittest.mock import MagicMock, call
from agentic.tools.search_listings import search_listings
from agentic.tools.get_school_info import get_school_info
from agentic.tools.save_contact import save_contact
from agentic.tools.executor import exec_tool


# ── search_listings ───────────────────────────────────────────────────────────

class TestSearchListings:
    def test_no_results_returns_fallback_message(self, empty_db):
        text, listings = search_listings({}, empty_db)
        assert "No listings found" in text
        assert listings == []

    def test_results_returned_as_list(self, listings_db):
        text, listings = search_listings({}, listings_db)
        assert len(listings) == 2
        assert "Found 2 listing(s)" in text

    def test_summary_contains_address_and_price(self, listings_db):
        text, listings = search_listings({}, listings_db)
        assert "100 Main St" in text
        assert "$1,500,000" in text

    def test_city_filter_uppercased(self, listings_db):
        search_listings({"cities": ["belmont", "San Carlos"]}, listings_db)
        call_args = listings_db.mls_listings.find.call_args
        filt = call_args[0][0]
        assert filt["CITY"] == {"$in": ["BELMONT", "SAN CARLOS"]}

    def test_price_filter_applied(self, listings_db):
        search_listings({"min_price": 1_000_000, "max_price": 2_000_000}, listings_db)
        filt = listings_db.mls_listings.find.call_args[0][0]
        assert filt["LIST_PRICE"] == {"$gte": 1_000_000.0, "$lte": 2_000_000.0}

    def test_beds_filter_applied(self, listings_db):
        search_listings({"min_beds": 3, "max_beds": 4}, listings_db)
        filt = listings_db.mls_listings.find.call_args[0][0]
        assert filt["BEDROOMS_TOTAL"] == {"$gte": 3, "$lte": 4}

    def test_sqft_filter_applied(self, listings_db):
        search_listings({"min_sqft": 1500, "max_sqft": 3000}, listings_db)
        filt = listings_db.mls_listings.find.call_args[0][0]
        assert filt["SQFT"] == {"$gte": 1500.0, "$lte": 3000.0}

    def test_limit_capped_at_8(self, listings_db):
        search_listings({"limit": 100}, listings_db)
        cursor = listings_db.mls_listings.find.return_value
        cursor.sort.return_value.limit.assert_called_with(8)

    def test_status_filter_always_present(self, listings_db):
        search_listings({}, listings_db)
        filt = listings_db.mls_listings.find.call_args[0][0]
        assert "MLS_STATUS" in filt
        assert "$in" in filt["MLS_STATUS"]

    def test_datetime_fields_serialized(self, mock_db):
        """datetime objects in listing docs must be converted to strings."""
        from datetime import datetime
        doc = {
            "LISTING_ID": "999",
            "STREET_ADDRESS": "1 Test Rd",
            "CITY": "BELMONT",
            "LIST_PRICE": 1_000_000,
            "MLS_STATUS": "ACTV",
            "_updated_at": datetime(2024, 6, 1, 12, 0),
        }
        cursor = MagicMock()
        cursor.sort.return_value = cursor
        cursor.limit.return_value = iter([doc])
        mock_db.mls_listings.find.return_value = cursor

        _, listings = search_listings({}, mock_db)
        assert listings[0]["_updated_at"] == "2024-06-01"


# ── get_school_info ───────────────────────────────────────────────────────────

class TestGetSchoolInfo:
    def test_known_city_returns_district_and_rating(self):
        text, extra = get_school_info({"city": "Burlingame"})
        assert extra is None
        assert "Burlingame" in text
        assert "9/10" in text
        assert "District" in text

    def test_city_lookup_case_insensitive(self):
        text, _ = get_school_info({"city": "PALO ALTO"})
        assert "10/10" in text

    def test_city_lowercase_normalized(self):
        text, _ = get_school_info({"city": "saratoga"})
        assert "10/10" in text

    def test_unknown_city_returns_fallback(self):
        text, extra = get_school_info({"city": "Narnia"})
        assert extra is None
        assert "GreatSchools.org" in text

    def test_partial_match_works(self):
        # "FOSTER CITY" contains "FOSTERCITY" key partially
        text, _ = get_school_info({"city": "Foster City"})
        assert "Hillsdale" in text

    def test_does_not_require_db(self):
        # db is unused — should work with None
        text, _ = get_school_info({"city": "San Mateo"}, db=None)
        assert "San Mateo" in text


# ── save_contact ──────────────────────────────────────────────────────────────

class TestSaveContact:
    def test_inserts_contact_document(self, mock_db):
        save_contact({"name": "Alice", "email": "alice@example.com"}, mock_db, "sess-1")
        mock_db.contacts.insert_one.assert_called_once()
        doc = mock_db.contacts.insert_one.call_args[0][0]
        assert doc["name"] == "Alice"
        assert doc["email"] == "alice@example.com"
        assert doc["session_id"] == "sess-1"

    def test_returns_confirmation_text(self, mock_db):
        text, extra = save_contact({"name": "Bob", "email": "bob@example.com"}, mock_db)
        assert extra is None
        assert "Bob" in text
        assert "bob@example.com" in text

    def test_optional_phone_stored(self, mock_db):
        save_contact({"name": "Carol", "email": "c@example.com", "phone": "555-1234"}, mock_db)
        doc = mock_db.contacts.insert_one.call_args[0][0]
        assert doc["phone"] == "555-1234"

    def test_preferences_stored(self, mock_db):
        prefs = {"city": "Belmont", "beds": 3}
        save_contact({"name": "Dave", "email": "d@example.com", "preferences": prefs}, mock_db)
        doc = mock_db.contacts.insert_one.call_args[0][0]
        assert doc["preferences"] == prefs


# ── executor ──────────────────────────────────────────────────────────────────

class TestExecutor:
    def test_dispatches_search_listings(self, listings_db):
        text, listings = exec_tool("search_listings", {}, listings_db)
        assert isinstance(listings, list)

    def test_dispatches_get_school_info(self, mock_db):
        text, extra = exec_tool("get_school_info", {"city": "Belmont"}, mock_db)
        assert "8/10" in text
        assert extra is None

    def test_dispatches_save_contact(self, mock_db):
        text, extra = exec_tool(
            "save_contact",
            {"name": "Eve", "email": "e@example.com"},
            mock_db,
            session_id="sess-xyz",
        )
        assert "Eve" in text
        assert extra is None

    def test_unknown_tool_returns_error_string(self, mock_db):
        text, extra = exec_tool("nonexistent_tool", {}, mock_db)
        assert "Unknown tool" in text
        assert extra is None
