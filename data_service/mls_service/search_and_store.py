#!/usr/bin/env python3
"""
search_and_store.py
--------------------
Reads the latest session cookies from MongoDB (auth_tokens), runs the MLS
property search, and upserts results into mls_listings.

Changed field values are recorded in a _history array on each document:
  { timestamp, field, old_value, new_value }

Usage:
    export MONGO_URI='mongodb://mlsuser:mlspassword@localhost:27017/mls?authSource=admin'
    python search_and_store.py
"""

import os
import sys
import time
import requests
from datetime import datetime, timezone

from pymongo import MongoClient

# ── Config ────────────────────────────────────────────────────────────────────

SEARCH_URL = "https://bridge.connectmls.com/api/search/listing/list"
MONGO_URI  = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB   = os.environ.get("MONGO_DB", "mls")

# Fields to skip when comparing for changes (HTML blobs, arrays, metadata)
SKIP_FIELDS = {"SOURCE_MLS_CIRCLE", "TINYPROPPHOTO_ONELINE", "TOOLS", "LPHOTOS"}

# Fields to coerce from string to number for correct range queries
NUMERIC_FIELDS = {
    "LIST_PRICE", "DISPLAY_PRICE", "REPORT_DISPLAY_PRICE", "SRCHPRICE",
    "BEDROOMS_TOTAL", "BATHROOMS_FULL", "SQFT", "LOT_SQFT",
    "DAYS_ON_MARKET", "YEAR_BUILT", "ACRES", "LONGITUDE", "LATITUDE",
}

SEARCH_PAYLOAD = {
    "searchclass": "RE",
    "searchtype":  "LISTING",
    "boundaries":  None,
    "layers":      [],
    "report":      "agent-rd-table",
    "fields": [
        {"ordinal": None, "id": "MLS_STATUS",
         "value": "ACTV,BOMK,AC,NEW,PCH,CS",
         "option": None, "min": None, "max": None, "none": None, "all": None},
        {"ordinal": None, "id": "CITY", "value": "BURLINGAME",
         "option": "", "min": None, "max": None, "none": None, "all": None},
        {"ordinal": None, "id": "COUNTY_OR_PARISH", "value": "",
         "option": None, "min": None, "max": None, "none": None, "all": None},
        {"ordinal": None, "id": "SUBDIVISION_NAME", "value": "",
         "option": "", "min": None, "max": None, "none": None, "all": None},
        {"ordinal": None, "id": "SRCHPRICE", "value": None, "option": None,
         "min": None, "max": None, "none": None, "all": None},
        {"ordinal": None, "id": "BUILDING_TYPE", "value": "",
         "option": "", "min": None, "max": None, "none": "", "all": ""},
        {"ordinal": None, "id": "BEDROOMS_TOTAL", "value": None, "option": None,
         "min": None, "max": None, "none": None, "all": None},
        {"ordinal": None, "id": "BATHROOMS_FULL", "value": None, "option": None,
         "min": None, "max": None, "none": None, "all": None},
        {"ordinal": None, "id": "SQFT", "value": None, "option": None,
         "min": None, "max": None, "none": None, "all": None},
        {"ordinal": None, "id": "DEFAULT_ADDRESS_SEARCH", "value": "",
         "option": None, "min": None, "max": None, "none": None, "all": None},
        {"ordinal": None, "id": "LISTING_CONTRACT_DATE", "value": None,
         "option": None, "min": None, "max": None, "none": None, "all": None},
        {"ordinal": None, "id": "OFF_MARKET_DATE", "value": None,
         "option": None, "min": None, "max": None, "none": None, "all": None},
        {"ordinal": None, "id": "CLOSE_DATE", "value": None,
         "option": None, "min": None, "max": None, "none": None, "all": None},
        {"ordinal": None, "id": "BOARDID", "value": "",
         "option": "", "min": None, "max": None, "none": None, "all": None},
        {"ordinal": None, "id": "LISTING_AGREEMENT", "value": "",
         "option": "", "min": None, "max": None, "none": None, "all": None},
        {"ordinal": None, "id": "SPECIAL_INFO", "value": "",
         "option": None, "min": None, "max": None, "none": None, "all": None},
        {"ordinal": None, "id": "LISTING_ID", "value": None,
         "option": "", "min": None, "max": None, "none": None, "all": None},
        {"ordinal": None, "id": "FEATURES_SEARCH", "value": None,
         "option": "", "min": None, "max": None, "none": "", "all": ""},
        {"ordinal": None, "id": "SOURCE_MLS",
         "value": "BR,CC,BE,ML,SF,BA,ME,CR,CL,CD",
         "option": "", "min": None, "max": None, "none": None, "all": None},
    ],
    "record":       True,
    "context_data": {},
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _coerce_types(listing: dict) -> dict:
    """Convert known numeric fields from string to int/float."""
    result = {}
    for k, v in listing.items():
        if k in NUMERIC_FIELDS and v not in (None, ""):
            try:
                result[k] = float(v) if "." in str(v) else int(v)
            except (ValueError, TypeError):
                result[k] = v
        else:
            result[k] = v
    return result


# ── Data fetching ─────────────────────────────────────────────────────────────

def get_latest_cookies() -> list[dict]:
    client = MongoClient(MONGO_URI)
    db     = client[MONGO_DB]
    doc    = db.auth_tokens.find_one(sort=[("timestamp", -1)])
    client.close()

    if not doc:
        raise RuntimeError("No cookies in auth_tokens. Run get_cookies.py first.")

    ts    = doc["timestamp"]
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
    print(f"  Using cookies from {ts.isoformat()} ({age_h:.1f}h ago)")
    return doc["cookies"]


def build_session(cookies: list[dict]) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
        ),
        "Accept":          "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin":          "https://bridge.connectmls.com",
        "Referer":         "https://bridge.connectmls.com/",
    })
    for c in cookies:
        session.cookies.set(c["name"], c["value"], domain=c.get("domain", ""))
    return session


def fetch_listings(session: requests.Session) -> list[dict]:
    print(f"  POSTing to {SEARCH_URL} ...")
    resp = session.post(
        SEARCH_URL,
        json=SEARCH_PAYLOAD,
        headers={"Content-Type": "application/json;charset=UTF-8"},
        timeout=60,
    )
    resp.raise_for_status()
    data     = resp.json()
    listings = data.get("data", data) if isinstance(data, dict) else data
    print(f"  Fetched {len(listings)} listings")
    return listings


# ── Upsert with history ───────────────────────────────────────────────────────

def upsert_listings(listings: list[dict]):
    client   = MongoClient(MONGO_URI)
    db       = client[MONGO_DB]
    coll     = db.mls_listings
    now      = datetime.now(timezone.utc)

    inserted = updated = unchanged = 0

    for raw in listings:
        listing = _coerce_types(raw)
        lid     = listing.get("LISTING_ID") or listing.get("DCID")
        if not lid:
            continue

        existing = coll.find_one({"LISTING_ID": listing.get("LISTING_ID", lid)})

        if existing is None:
            doc = dict(listing)
            doc["_inserted_at"] = now
            doc["_updated_at"]  = now
            doc["_history"]     = []
            coll.insert_one(doc)
            inserted += 1

        else:
            # Detect field-level changes
            changes = []
            for field, new_val in listing.items():
                if field in SKIP_FIELDS:
                    continue
                old_val = existing.get(field)
                if old_val != new_val:
                    changes.append({
                        "timestamp": now,
                        "field":     field,
                        "old_value": old_val,
                        "new_value": new_val,
                    })

            update_doc = {"$set": {**listing, "_updated_at": now}}
            if changes:
                update_doc["$push"] = {"_history": {"$each": changes}}
                updated += 1
            else:
                unchanged += 1

            coll.update_one(
                {"LISTING_ID": listing.get("LISTING_ID", lid)},
                update_doc,
            )

    client.close()
    print(f"  ✓ Inserted: {inserted}  Updated: {updated}  Unchanged: {unchanged}")
    return inserted, updated, unchanged


# ── Run logging ───────────────────────────────────────────────────────────────

def log_run(started_at: datetime, duration_s: float, fetched: int,
            inserted: int, updated: int, unchanged: int, error: str = None):
    client = MongoClient(MONGO_URI)
    db     = client[MONGO_DB]
    db.mls_runs.insert_one({
        "type":       "search",
        "timestamp":  started_at,
        "duration_s": round(duration_s, 1),
        "fetched":    fetched,
        "inserted":   inserted,
        "updated":    updated,
        "unchanged":  unchanged,
        "error":      error,
    })
    client.close()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=== search_and_store.py: MLS Fetch & Store ===")
    started_at = datetime.now(timezone.utc)
    t0         = time.time()

    print("[1/3] Loading cookies from MongoDB...")
    try:
        cookies = get_latest_cookies()
    except RuntimeError as e:
        print(f"✗ {e}")
        log_run(started_at, time.time() - t0, 0, 0, 0, 0, error=str(e))
        sys.exit(1)

    print("[2/3] Fetching listings from MLS API...")
    session = build_session(cookies)
    try:
        listings = fetch_listings(session)
    except Exception as e:
        print(f"✗ Fetch failed: {e}")
        log_run(started_at, time.time() - t0, 0, 0, 0, 0, error=str(e))
        sys.exit(1)

    print("[3/3] Upserting listings to MongoDB...")
    inserted, updated, unchanged = upsert_listings(listings)
    log_run(started_at, time.time() - t0, len(listings), inserted, updated, unchanged)
    print("Done.")


if __name__ == "__main__":
    main()
