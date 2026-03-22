#!/usr/bin/env python3
"""
fetch_details.py
-----------------
For listings in mls_listings that don't yet have a `details` field,
fetches detail data from the ConnectMLS API and stores it along with
a `details_fetched` timestamp and a `photos` URL array.

Only processes listings that are missing `details`, so repeated runs
are safe and won't burn unnecessary API quota.

Usage:
    export MONGO_URI='mongodb://user:pass@localhost:27017/mls?authSource=admin'
    python fetch_details.py
"""

import os
import sys
import time
import requests
from datetime import datetime, timezone
from pymongo import MongoClient

DETAILS_URL_TPL  = (
    "https://bridge.connectmls.com/api/search/listing/details/data"
    "/LISTING/RE/{dcid}?search_id=undefined"
)
MONGO_URI        = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB         = os.environ.get("MONGO_DB", "mls")
RATE_LIMIT_SLEEP = 1.0   # seconds between API calls to avoid rate-limiting


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_latest_cookies() -> list[dict]:
    client = MongoClient(MONGO_URI)
    db     = client[MONGO_DB]
    doc    = db.auth_tokens.find_one(sort=[("timestamp", -1)])
    client.close()
    if not doc:
        raise RuntimeError("No cookies in auth_tokens. Run get_cookies.py first.")
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


def build_photos(details: dict) -> list[str]:
    """
    Derive all full-size photo URLs from PRIMARY_PHOTO_URI and photocount.

    URL structure: .../MLSL/{listing_num}/{hash}/{quality}/{index}
    We keep the quality segment from PRIMARY_PHOTO_URI and replace just
    the index (last path segment) with 1..photocount.
    """
    primary    = details.get("PRIMARY_PHOTO_URI", "")
    photocount = details.get("photocount", 0)
    if not primary or not photocount:
        return []
    base = primary.rsplit("/", 1)[0]   # strip the trailing photo index
    try:
        count = int(photocount)
    except (ValueError, TypeError):
        return []
    return [f"{base}/{i}" for i in range(1, count + 1)]


# ── Logging ───────────────────────────────────────────────────────────────────

def log_listing(db, listing_id: str, dcid: str, photo_count: int,
                success: bool, error: str = None):
    """Write one log entry per fetched listing to detail_logs collection."""
    db.detail_logs.insert_one({
        "listing_id":  listing_id,
        "dcid":        dcid,
        "photo_count": photo_count,
        "success":     success,
        "error":       error,
        "timestamp":   datetime.now(timezone.utc),
    })


def log_run(db, started_at: datetime, duration_s: float,
            success: int, failed: int, error: str = None):
    """Write a summary record to mls_runs (type=details)."""
    db.mls_runs.insert_one({
        "type":       "details",
        "timestamp":  started_at,
        "duration_s": round(duration_s, 1),
        "success":    success,
        "failed":     failed,
        "error":      error,
    })


# ── Main fetch loop ───────────────────────────────────────────────────────────

def fetch_and_store_details(session: requests.Session) -> tuple[int, int]:
    client = MongoClient(MONGO_URI)
    db     = client[MONGO_DB]
    coll   = db.mls_listings

    # Only listings that have a DCID but no details yet
    pending = list(coll.find(
        {"details": {"$exists": False}, "DCID": {"$exists": True, "$ne": ""}},
        {"_id": 1, "DCID": 1, "LISTING_ID": 1},
    ))
    print(f"  {len(pending)} listings need details")

    success = fail = 0
    for doc in pending:
        dcid       = doc.get("DCID", "")
        listing_id = doc.get("LISTING_ID", dcid)
        if not dcid:
            continue

        url = DETAILS_URL_TPL.format(dcid=dcid)
        try:
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
            data        = resp.json()
            detail_list = data.get("data", [])
            if not detail_list:
                msg = f"Empty data for DCID={dcid}"
                print(f"  ⚠  {msg}")
                log_listing(db, listing_id, dcid, 0, success=False, error=msg)
                fail += 1
                continue

            detail  = detail_list[0]
            photos  = build_photos(detail)
            now_str = datetime.now(timezone.utc).strftime("%B %d %Y %I:%M%p")

            coll.update_one(
                {"_id": doc["_id"]},
                {"$set": {
                    "details":         detail,
                    "details_fetched": now_str,
                    "photos":          photos,
                }},
            )
            log_listing(db, listing_id, dcid, len(photos), success=True)
            print(f"  ✓ {listing_id} — {len(photos)} photos")
            success += 1

        except Exception as e:
            err = str(e)
            print(f"  ✗ {listing_id} (DCID={dcid}): {err}")
            log_listing(db, listing_id, dcid, 0, success=False, error=err)
            fail += 1

        time.sleep(RATE_LIMIT_SLEEP)

    client.close()
    print(f"  Details stored: {success}  Failed: {fail}")
    return success, fail


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    print("=== fetch_details.py: Fetch Listing Details ===")
    started_at = datetime.now(timezone.utc)
    t0         = time.time()

    print("[1/2] Loading cookies from MongoDB...")
    try:
        cookies = get_latest_cookies()
    except RuntimeError as e:
        print(f"✗ {e}")
        # Log the failed run
        client = MongoClient(MONGO_URI)
        log_run(client[MONGO_DB], started_at, time.time() - t0, 0, 0, error=str(e))
        client.close()
        sys.exit(1)

    session = build_session(cookies)

    print("[2/2] Fetching details for new listings...")
    success, fail = fetch_and_store_details(session)

    # Write summary run record
    client = MongoClient(MONGO_URI)
    log_run(client[MONGO_DB], started_at, time.time() - t0, success, fail)
    client.close()

    print("Done.")


if __name__ == "__main__":
    main()
