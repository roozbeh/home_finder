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
import re
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
PHOTO_DETAIL_URL = "https://bridge.connectmls.com/mls/search/results/photodetail.jsp"
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

    ConnectMLS stores the index in the filename, for example
    ``..._41150001_1.JPEG``.  We replace that trailing number with
    1..photocount rather than appending a new URL path segment.
    """
    primary    = details.get("PRIMARY_PHOTO_URI", "")
    photocount = details.get("photocount", 0)
    if not primary or not photocount:
        return []
    try:
        count = int(photocount)
    except (ValueError, TypeError):
        return []
    match = re.search(r"_(\d+)(\.[A-Za-z0-9]+)$", primary)
    if not match:
        return [primary]
    return [
        f"{primary[:match.start(1)]}{index}{primary[match.end(1):]}"
        for index in range(1, count + 1)
    ]


def fetch_gallery_photos(session: requests.Session, dcid: str) -> list[str]:
    """Read authorized full-size photo references from ConnectMLS's gallery.

    Detail JSON exposes only the primary-image filename. The same authorized
    MLS workflow has a photo-detail page containing the complete gallery. We
    store references only; image bytes are never downloaded into MongoDB.
    """
    response = session.get(
        PHOTO_DETAIL_URL,
        params={"dcid": dcid, "category": "CXP", "searchtype": "MLSNO"},
        timeout=30,
    )
    assert_authenticated_response(response)
    response.raise_for_status()
    candidates = re.findall(
        r'https://[^"\'\s<>]+?/HD_[^"\'\s<>]+?\.jpe?g', response.text, re.IGNORECASE
    )
    return list(dict.fromkeys(url.replace("\\/", "/") for url in candidates))


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
                            refresh_photo_galleries: bool = False,
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
    if refresh_photo_galleries:
        # Recover only records whose prior gallery URL derivation was not a
        # verified full-size MLS gallery. Do not delete records first: each
        # photos array is replaced only after the new gallery validates.
        query = {
            "DCID": {"$exists": True, "$ne": ""},
            "details.PRIMARY_PHOTO_URI": {"$exists": True, "$ne": ""},
            "details.photocount": {"$gte": 2},
            "photos": {"$not": re.compile(r"/HD_")},
        }
    if closed_only:
        query["MLS_STATUS"] = "SLD"
    elif active_only:
        # Daily screening needs complete data for newly discovered inventory,
        # but should not re-download every active record each day.
        query["MLS_STATUS"] = "ACTV"
    cursor = coll.find(
        query,
        {"_id": 1, "DCID": 1, "LISTING_ID": 1, "details": 1},
    )
    if limit is not None:
        cursor = cursor.limit(limit)
    pending = list(cursor)
    scope = " (closed sales only)" if closed_only else ""
    if refresh_active_details:
        scope += " (including all ACTV records)"
    if refresh_photo_galleries:
        scope += " (invalid photo galleries only)"
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

        try:
            if refresh_photo_galleries:
                detail = doc.get("details") or {}
                photos = fetch_gallery_photos(session, dcid)
                expected_count = int(detail.get("photocount") or 0)
                if not photos:
                    raise ValueError("Authorized MLS photo-detail page returned no full-size photos")
                if expected_count and len(photos) != expected_count:
                    raise ValueError(
                        f"Gallery count mismatch: MLS detail={expected_count}, gallery={len(photos)}"
                    )
            else:
                url = DETAILS_URL_TPL.format(dcid=dcid)
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
                detail = detail_list[0]
                photos = fetch_gallery_photos(session, dcid) or build_photos(detail)
            now_str = datetime.now(timezone.utc).strftime("%B %d %Y %I:%M%p")
            updates = {"photos": photos, "photo_gallery_fetched": now_str}
            if not refresh_photo_galleries:
                updates.update({"details": detail, "details_fetched": now_str})
            coll.update_one({"_id": doc["_id"]}, {"$set": updates})
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
        "--refresh-photo-galleries", action="store_true",
        help="backfill only listings with invalid/non-HD stored MLS gallery references",
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
    if args.refresh_photo_galleries and (args.closed_only or args.active_only or args.refresh_active_details):
        parser.error("--refresh-photo-galleries cannot be combined with detail-scope options")
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

    print("[2/2] Fetching authorized MLS gallery references..." if args.refresh_photo_galleries else "[2/2] Fetching details for new listings...")
    try:
        success, fail = fetch_and_store_details(
            session, refresh_active_details=args.refresh_active_details,
            refresh_photo_galleries=args.refresh_photo_galleries,
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
