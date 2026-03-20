"""Shared pytest fixtures."""

import sys
import os
import pytest
from unittest.mock import MagicMock, patch

# Ensure the data_service root is on sys.path so `import agentic` works
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── MongoDB mock ──────────────────────────────────────────────────────────────

def _make_cursor(docs):
    """Return a mock PyMongo cursor that yields docs."""
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.limit.return_value = iter(docs)
    return cursor


@pytest.fixture()
def mock_db():
    db = MagicMock()
    return db


@pytest.fixture()
def empty_db(mock_db):
    """DB whose mls_listings collection always returns no results."""
    mock_db.mls_listings.find.return_value = _make_cursor([])
    return mock_db


@pytest.fixture()
def listings_db(mock_db):
    """DB pre-loaded with two fake listings."""
    docs = [
        {
            "LISTING_ID":     "123",
            "STREET_ADDRESS": "100 Main St",
            "CITY":           "BELMONT",
            "LIST_PRICE":     1_500_000,
            "BEDROOMS_TOTAL": 3,
            "BATHROOMS_FULL": 2,
            "BATHS_DISPLAY":  "2",
            "SQFT":           1800,
            "MLS_STATUS":     "ACTV",
        },
        {
            "LISTING_ID":     "456",
            "STREET_ADDRESS": "200 Oak Ave",
            "CITY":           "SAN CARLOS",
            "LIST_PRICE":     2_200_000,
            "BEDROOMS_TOTAL": 4,
            "BATHROOMS_FULL": 3,
            "BATHS_DISPLAY":  "3",
            "SQFT":           2400,
            "MLS_STATUS":     "NEW",
        },
    ]
    mock_db.mls_listings.find.return_value = _make_cursor(docs)
    return mock_db


# ── Anthropic mock ────────────────────────────────────────────────────────────

def _text_response(text: str):
    """Build a fake Anthropic response that ends the turn with text."""
    block = MagicMock()
    block.type = "text"
    block.text = text

    resp = MagicMock()
    resp.stop_reason = "end_turn"
    resp.content = [block]
    return resp


def _tool_then_text_response(tool_name: str, tool_input: dict, tool_id: str, final_text: str):
    """
    Returns a factory for two responses:
    1st call → tool_use with given tool_name/input
    2nd call → end_turn with final_text
    """
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.id   = tool_id
    tool_block.name = tool_name
    tool_block.input = tool_input

    tool_resp = MagicMock()
    tool_resp.stop_reason = "tool_use"
    tool_resp.content = [tool_block]

    text_resp = _text_response(final_text)
    return [tool_resp, text_resp]


@pytest.fixture()
def ai_end_turn():
    """Anthropic client that immediately returns a text response."""
    client = MagicMock()
    client.messages.create.return_value = _text_response("Hello! How can I help you?")
    return client


@pytest.fixture()
def ai_tool_then_end(listings_db):
    """
    Anthropic client that calls search_listings once, then gives a text reply.
    Returns (client, db).
    """
    responses = _tool_then_text_response(
        tool_name="search_listings",
        tool_input={"cities": ["BELMONT"], "min_beds": 3},
        tool_id="tool_abc",
        final_text="I found some great options in Belmont!",
    )
    client = MagicMock()
    client.messages.create.side_effect = responses
    return client, listings_db
