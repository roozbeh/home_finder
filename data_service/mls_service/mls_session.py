"""Lifecycle management for broker-authorized MLS browser sessions.

The pipeline never creates or refreshes a login session. It reuses the newest
session that has not been retired and retires it only after the MLS explicitly
rejects it or redirects an API request to its login flow.
"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlparse

from pymongo import MongoClient


class MLSAuthenticationError(RuntimeError):
    """The MLS rejected the stored, operator-authorized session."""


def load_active_session(mongo_uri: str, mongo_db: str) -> dict:
    """Return the newest session not permanently retired by this pipeline."""
    client = MongoClient(mongo_uri)
    try:
        token = client[mongo_db].auth_tokens.find_one(
            {"$or": [{"expired_at": {"$exists": False}}, {"expired_at": None}]},
            sort=[("timestamp", -1)],
        )
    finally:
        client.close()
    if not token:
        raise RuntimeError(
            "No usable MLS session exists. Complete an approved manual login to save a new session."
        )
    if not token.get("cookies"):
        raise RuntimeError("The newest usable MLS session has no cookies and cannot be used.")
    return token


def retire_session(mongo_uri: str, mongo_db: str, token_id, reason: str) -> bool:
    """Permanently exclude one token after an explicit authentication failure."""
    client = MongoClient(mongo_uri)
    try:
        result = client[mongo_db].auth_tokens.update_one(
            {
                "_id": token_id,
                "$or": [{"expired_at": {"$exists": False}}, {"expired_at": None}],
            },
            {"$set": {
                "expired_at": datetime.now(timezone.utc),
                "status": "expired",
                "expired_reason": reason[:500],
            }},
        )
        return result.modified_count == 1
    finally:
        client.close()


def assert_authenticated_response(response) -> None:
    """Raise only when an API response clearly represents a rejected login.

    Network/server errors are deliberately not treated as token expiry. This
    prevents a transient outage or a rate-limit response from retiring a usable
    authorized session.
    """
    if response.status_code in (401, 403):
        raise MLSAuthenticationError(f"MLS returned HTTP {response.status_code}.")

    final_host = (urlparse(response.url).hostname or "").lower()
    if final_host and final_host != "bridge.connectmls.com":
        raise MLSAuthenticationError(f"MLS redirected the API request to {final_host}.")

    if response.is_redirect:
        location = (response.headers.get("Location") or "").lower()
        if "login" in location or "clareity" in location:
            raise MLSAuthenticationError("MLS redirected the API request to its login page.")

    content_type = (response.headers.get("Content-Type") or "").lower()
    if "text/html" in content_type:
        body = response.text[:10_000].lower()
        login_markers = ("id=\"username\"", "name=\"username\"", "session expired", "sign in")
        if any(marker in body for marker in login_markers):
            raise MLSAuthenticationError("MLS returned a login page instead of API data.")
