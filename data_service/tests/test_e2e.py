"""
End-to-end tests — real Anthropic API, mocked MongoDB.

Run with:
    pytest tests/ -m e2e -v

Skipped automatically in normal runs (pytest tests/ -m "not e2e").
Requires E2E_ANTHROPIC_API_KEY in data_service/env file.
"""

import os
import pytest
from unittest.mock import MagicMock
from dotenv import load_dotenv

# Load E2E_ANTHROPIC_API_KEY from data_service/env
_ENV_FILE = os.path.join(os.path.dirname(__file__), "..", "env")
load_dotenv(_ENV_FILE)

import anthropic
from agentic.agent import run_agent


def _make_cursor(docs):
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.limit.return_value = iter(docs)
    return cursor


@pytest.fixture()
def e2e_client():
    key = os.environ.get("E2E_ANTHROPIC_API_KEY")
    if not key:
        pytest.skip("E2E_ANTHROPIC_API_KEY not set in env file")
    return anthropic.Anthropic(api_key=key)


@pytest.fixture()
def mock_db_with_listings():
    """Mocked MongoDB with realistic listing data for the agent to find."""
    docs = [
        {
            "LISTING_ID":     "E2E-001",
            "STREET_ADDRESS": "42 Maple Lane",
            "CITY":           "BELMONT",
            "LIST_PRICE":     1_750_000,
            "BEDROOMS_TOTAL": 3,
            "BATHROOMS_FULL": 2,
            "BATHS_DISPLAY":  "2",
            "SQFT":           1950,
            "LOT_SQFT":       6000,
            "YEAR_BUILT":     1985,
            "DAYS_ON_MARKET": 5,
            "MLS_STATUS":     "ACTV",
        },
        {
            "LISTING_ID":     "E2E-002",
            "STREET_ADDRESS": "88 Oak Street",
            "CITY":           "BELMONT",
            "LIST_PRICE":     1_950_000,
            "BEDROOMS_TOTAL": 4,
            "BATHROOMS_FULL": 3,
            "BATHS_DISPLAY":  "3",
            "SQFT":           2300,
            "LOT_SQFT":       7500,
            "YEAR_BUILT":     2001,
            "DAYS_ON_MARKET": 12,
            "MLS_STATUS":     "NEW",
        },
    ]
    db = MagicMock()
    db.mls_listings.find.return_value = _make_cursor(docs)
    return db


@pytest.mark.e2e
class TestE2E:
    def test_agent_searches_and_replies(self, e2e_client, mock_db_with_listings):
        """
        Full round-trip: user asks for homes → agent calls search_listings
        (against mock DB) → agent replies with results.
        """
        msgs = [{"role": "user", "content": "I'm looking for a 3-bedroom home in Belmont under $2 million"}]
        result = run_agent(msgs, "e2e-session-1", mock_db_with_listings, e2e_client)

        assert "error" not in result, f"Agent returned error: {result.get('error')}"
        assert result["message"], "Agent returned empty message"

        # The agent should have called search_listings and returned listings
        assert len(result["listings"]) > 0, "Agent did not return any listings"

        # MongoDB find should have been called (agent used the tool)
        mock_db_with_listings.mls_listings.find.assert_called()

        # The filter should target BELMONT
        call_filt = mock_db_with_listings.mls_listings.find.call_args[0][0]
        assert "BELMONT" in call_filt.get("CITY", {}).get("$in", [])

    def test_agent_answers_school_question(self, e2e_client, mock_db_with_listings):
        """
        Agent should answer a school question using get_school_info
        without hitting MongoDB.
        """
        msgs = [{"role": "user", "content": "What are the schools like in Burlingame?"}]
        result = run_agent(msgs, "e2e-session-2", mock_db_with_listings, e2e_client)

        assert "error" not in result
        assert result["message"]
        # School info is hardcoded — no DB needed
        reply = result["message"].lower()
        assert any(word in reply for word in ["school", "district", "burlingame"])

    def test_agent_strips_leading_greeting_and_still_works(self, e2e_client, mock_db_with_listings):
        """
        Simulates the real frontend flow where history starts with Maya's
        greeting (assistant role) before the first user message.
        """
        msgs = [
            {"role": "assistant", "content": "Hi! I'm Maya, your Bay Area home finder. What are you looking for?"},
            {"role": "user",      "content": "Show me homes in Belmont with at least 3 bedrooms"},
        ]
        result = run_agent(msgs, "e2e-session-3", mock_db_with_listings, e2e_client)

        assert "error" not in result, f"Agent returned error: {result.get('error')}"
        assert result["message"]
