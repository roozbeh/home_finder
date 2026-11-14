#!/usr/bin/env python3
"""
fetch_details.py
-----------------
For listings in mls_listings that don't yet have a `details` field,
fetches detail data from the ConnectMLS API and stores it along with
a `details_fetched` timestamp and a `photos` URL array.

By default only processes listings that are missing `details`, plus sold
records whose details have not reached SLD. The opt-in active refresh mode
re-downloads detail payloads for every `ACTV` record after a fresh search,
which refreshes the fields required by the investment-screening pipeline.

Usage:
    export MONGO_URI='mongodb://user:pass@localhost:27017/mls?authSource=admin'
    python fetch_details.py
    python fetch_details.py --refresh-active-details
"""

import argparse
import os
import sys
import time
import requests
from datetime import datetime, timezone
from pymongo import MongoClient
from mls_session import (
    MLSAuthenticationError,
    assert_authenticated_response,
    load_active_session,
    retire_session,
)

DETAILS_URL_TPL  = (
    "https://bridge.connectmls.com/api/search/listing/details/data"
    "/LISTING/RE/{dcid}?search_id=undefined"
)
MONGO_URI        = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB         = os.environ.get("MONGO_DB", "mls")
RATE_LIMIT_SLEEP = 1.0   # seconds between API calls to avoid rate-limiting


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_active_session() -> dict:
    """Load the newest session that has not been retired after auth failure."""
    return load_active_session(MONGO_URI, MONGO_DB)


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

def fetch_and_store_details(session: requests.Session,
                            refresh_active_details: bool = False,
                            closed_only: bool = False,
                            active_only: bool = False,
                            limit: int | None = None,
                            stop_at_storage_mb: float | None = None) -> tuple[int, int]:
    client = MongoClient(MONGO_URI)
    db     = client[MONGO_DB]
    coll   = db.mls_listings

    # Listings needing details:
    #  1. no details yet, or
    #  2. listing is now SLD but its details blob predates the sale
    #     (stale details lack CLOSE_PRICE / buyer-agent info) — refetch those.
    refresh_conditions = [
        {"details": {"$exists": False}},
        {"MLS_STATUS": "SLD", "details.status": {"$ne": "SLD"}},
    ]
    if refresh_active_details:
        # ACTV is the only currently verified active code. This is deliberately
        # not broadened to legacy codes whose semantics are not yet documented.
        refresh_conditions.append({"MLS_STATUS": "ACTV"})

    query = {"DCID": {"$exists": True, "$ne": ""}, "$or": refresh_conditions}
    if closed_only:
        query["MLS_STATUS"] = "SLD"
    elif active_only:
        # Daily screening needs complete data for newly discovered inventory,
        # but should not re-download every active record each day.
        query["MLS_STATUS"] = "ACTV"
    cursor = coll.find(
        query,
        {"_id": 1, "DCID": 1, "LISTING_ID": 1},
    )
    if limit is not None:
        cursor = cursor.limit(limit)
    pending = list(cursor)
    scope = " (closed sales only)" if closed_only else ""
    if refresh_active_details:
        scope += " (including all ACTV records)"
    print(f"  {len(pending)} listings need details{scope}")

    success = fail = 0
    for doc in pending:
        # Check before the first write and then every 100 records. Atlas's UI
        # can differ slightly from dbStats, so callers should leave headroom.
        if stop_at_storage_mb is not None and (success + fail) % 100 == 0:
            stats = db.command("dbStats")
            used_mb = (stats.get("storageSize", 0) + stats.get("indexSize", 0)) / 1_000_000
            if used_mb >= stop_at_storage_mb:
                print(f"  ⚠ Stopping before storage threshold: {used_mb:.1f} MB >= {stop_at_storage_mb:.1f} MB")
                break
        dcid       = doc.get("DCID", "")
        listing_id = doc.get("LISTING_ID", dcid)
        if not dcid:
            continue

        url = DETAILS_URL_TPL.format(dcid=dcid)
        try:
            resp = session.get(url, timeout=30)
            assert_authenticated_response(resp)
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

        except MLSAuthenticationError:
            raise
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
    parser = argparse.ArgumentParser(description="Fetch authorized ConnectMLS listing details.")
    parser.add_argument(
        "--refresh-active-details", action="store_true",
        help="refresh detail payloads for all verified ACTV listings after a fresh search",
    )
    parser.add_argument(
        "--stop-at-storage-mb", type=float, default=None,
        help="stop cleanly when MongoDB storage plus indexes reaches this MB threshold",
    )
    parser.add_argument(
        "--closed-only", action="store_true",
        help="fetch details only for closed sales; suitable for historical-comparable enrichment",
    )
    parser.add_argument(
        "--active-only", action="store_true",
        help="fetch missing details only for verified active listings; suitable for daily runs",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="maximum listings to enrich in this batch; use bounded batches for storage monitoring",
    )
    args = parser.parse_args()
    if args.closed_only and args.active_only:
        parser.error("--closed-only and --active-only cannot be used together")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    if args.stop_at_storage_mb is not None and args.stop_at_storage_mb <= 0:
        parser.error("--stop-at-storage-mb must be positive")
    print("=== fetch_details.py: Fetch Listing Details ===")
    started_at = datetime.now(timezone.utc)
    t0         = time.time()

    print("[1/2] Loading cookies from MongoDB...")
    try:
        token = get_active_session()
    except RuntimeError as e:
        print(f"✗ {e}")
        # Log the failed run
        client = MongoClient(MONGO_URI)
        log_run(client[MONGO_DB], started_at, time.time() - t0, 0, 0, error=str(e))
        client.close()
        sys.exit(1)

    session = build_session(token["cookies"])

    print("[2/2] Fetching details for new listings...")
    try:
        success, fail = fetch_and_store_details(
            session, refresh_active_details=args.refresh_active_details,
            closed_only=args.closed_only, active_only=args.active_only, limit=args.limit,
            stop_at_storage_mb=args.stop_at_storage_mb,
        )
    except MLSAuthenticationError as e:
        retire_session(MONGO_URI, MONGO_DB, token["_id"], str(e))
        message = f"MLS session rejected and marked expired: {e}"
        print(f"✗ {message}")
        client = MongoClient(MONGO_URI)
        log_run(client[MONGO_DB], started_at, time.time() - t0, 0, 0, error=message)
        client.close()
        sys.exit(1)

    # Write summary run record
    client = MongoClient(MONGO_URI)
    log_run(client[MONGO_DB], started_at, time.time() - t0, success, fail)
    client.close()

    print("Done.")


if __name__ == "__main__":
    main()
