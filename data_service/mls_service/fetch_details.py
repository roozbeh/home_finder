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
from datetime import datetime, timezone, timedelta
from html import unescape
from pymongo import MongoClient
from urllib.parse import urljoin
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
FLIPDB_DATABASE  = os.environ.get("FLIPDB_DATABASE", "flip_finder")
RATE_LIMIT_SLEEP = 1.0   # seconds between API calls to avoid rate-limiting
MLS_LISTINGS_GALLERY_URL = re.compile(
    r'(https://media\.mlslmedia\.com/property/MLSL/[0-9]{8}/[0-9a-fA-F]{32})/([0-9]{1,4})/([0-9]{1,3})'
)


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


def supplied_primary_photo(details: dict) -> list[str]:
    """Keep the supplied primary as fallback; never invent gallery siblings."""
    primary = details.get("PRIMARY_PHOTO_URI", "")
    return [primary] if isinstance(primary, str) and primary.startswith("https://") else []


def is_gallery_reference(url):
    return isinstance(url, str) and url.startswith('https://') and bool(re.search(
        r'/HD_[^?]+\.jpe?g|/bigphoto/[^?]+\.jpe?g|media\.mlslmedia\.com/property/|media\.crmls\.org/media[sz]/[0-9a-f-]{36}\.jpe?g', url, re.I))


def select_gallery(existing, recovered, detail):
    """A partial refresh must not replace a more complete stored gallery."""
    recovered = list(dict.fromkeys(url for url in recovered if is_gallery_reference(url)))
    old = list(dict.fromkeys(url for url in existing if is_gallery_reference(url)))
    try:
        expected = int(detail.get('photocount') or 0)
    except (TypeError, ValueError):
        expected = 0
    if recovered and (not expected or len(recovered) == expected):
        return recovered, 'complete'
    photos = old if len(old) > len(recovered) else recovered
    return photos or supplied_primary_photo(detail), 'partial'


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
    # ConnectMLS may emit either absolute URLs or root-relative image paths.
    # The original parser accepted only the former, which made galleries look
    # empty despite the authorized page containing every HD image reference.
    page = unescape(response.text).replace("\\/", "/")
    # ConnectMLS uses HD_ filenames, while the RAPMLS listings shown in the
    # same authorized photo-detail UI use UUID-based /bigphoto/... paths.
    # Both are page-provided full-size MLS references; neither is guessed.
    candidates = re.findall(
        r'(?:(?:https?:)?//|/)[^"\'\s<>]*?/(?:HD_[^"\'\s<>]+?|bigphoto/[^"\'\s<>]+?)\.jpe?g(?:\?[^"\'\s<>]*)?',
        page,
        re.IGNORECASE,
    )
    candidates += re.findall(r'https://media\.crmls\.org/media[sz]/[0-9a-f-]{36}\.jpe?g(?:\?[^"\'\s<>]*)?', page, re.IGNORECASE)
    candidates += re.findall(r'https://media\.mlslmedia\.com/property/[^"\'\s<>\\]+', page, re.IGNORECASE)
    normalized = [urljoin(PHOTO_DETAIL_URL, url) for url in candidates]
    # Deduplicate sizing variants by identity, retaining an actual supplied URL.
    # Do not manufacture a query-free image reference.
    photos = {}
    for url in normalized:
        if mlsl_photo := MLS_LISTINGS_GALLERY_URL.fullmatch(url):
            prefix, size, photo_number = mlsl_photo.groups()
            identity = f'{prefix}/{photo_number}'
            preferred_size = size == '2'
        else:
            identity = url.split('?')[0] if re.match(r'https://media\.crmls\.org/media[sz]/', url) else url
            preferred_size = False
        previous = photos.get(identity)
        if previous is None or (preferred_size and not previous[1]):
            photos[identity] = url, preferred_size
    return [url for url, _ in photos.values()]


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


def start_gallery_backfill_run(db, total_candidates: int, scope: str):
    """Create an observable, resumable photo-gallery repair run record."""
    now = datetime.now(timezone.utc)
    return db.mls_runs.insert_one({
        "type": "photo_gallery_backfill",
        "scope": scope,
        "status": "running",
        "started_at": now,
        "updated_at": now,
        "total_candidates": total_candidates,
        "processed": 0,
        "succeeded": 0,
        "failed": 0,
        "last_error": None,
    }).inserted_id


def update_gallery_backfill_run(db, run_id, *, processed: int, succeeded: int,
                                failed: int, status: str = "running",
                                last_error: str | None = None):
    """Persist progress without exposing MLS records or credentials."""
    updates = {
        "status": status,
        "updated_at": datetime.now(timezone.utc),
        "processed": processed,
        "succeeded": succeeded,
        "failed": failed,
    }
    if last_error:
        updates["last_error"] = last_error
    if status != "running":
        updates["finished_at"] = updates["updated_at"]
    db.mls_runs.update_one({"_id": run_id}, {"$set": updates})


# ── Main fetch loop ───────────────────────────────────────────────────────────

def fetch_and_store_details(session: requests.Session,
                            refresh_active_details: bool = False,
                            refresh_photo_galleries: bool = False,
                            flip_pairs_only: bool = False,
                            listing_ids: list[str] | None = None,
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
        {"photo_gallery_repair.status": {"$in": ["pending", "partial", "failed"]},
         "$or": [{"photo_gallery_repair.next_retry_at": {"$exists": False}},
                 {"photo_gallery_repair.next_retry_at": {"$lte": datetime.now(timezone.utc)}}]},
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
        if listing_ids:
            # Explicit, bounded repair for a reviewed set of listings.  This
            # intentionally bypasses the stale-gallery predicate: a listing
            # may have no legacy gallery at all and still need recovery.
            query = {
                "DCID": {"$exists": True, "$ne": ""},
                "LISTING_ID": {"$in": listing_ids},
            }
        if flip_pairs_only:
            # FlipDB is the product's curated universe: only purchase/resale
            # listings that participate in an observed flip pair belong in this
            # repair run.  This avoids spending MLS requests on the entire
            # historical archive.
            pair_mls_ids = set()
            pairs = client[FLIPDB_DATABASE].flipdb
            for field in ("buy_mls", "sell_mls"):
                pair_mls_ids.update(
                    value.strip() for value in pairs.distinct(field)
                    if isinstance(value, str) and value.strip()
                )
            permitted = set(listing_ids or pair_mls_ids)
            query["LISTING_ID"] = {"$in": sorted(permitted & pair_mls_ids)}
    if listing_ids and not refresh_photo_galleries:
        query = {"DCID": {"$exists": True, "$ne": ""}, "LISTING_ID": {"$in": listing_ids}}
    if closed_only:
        query["MLS_STATUS"] = "SLD"
    elif active_only:
        # Daily screening needs complete data for newly discovered inventory,
        # but should not re-download every active record each day.
        query["MLS_STATUS"] = "ACTV"
    cursor = coll.find(
        query,
        {"_id": 1, "DCID": 1, "LISTING_ID": 1, "details": 1, "photos": 1},
    )
    if limit is not None:
        cursor = cursor.limit(limit)
    pending = list(cursor)
    scope = " (closed sales only)" if closed_only else ""
    if refresh_active_details:
        scope += " (including all ACTV records)"
    if refresh_photo_galleries:
        scope += " (invalid photo galleries only)"
        if flip_pairs_only:
            scope += " (FlipDB purchase/resale listings only)"
    print(f"  {len(pending)} listings need details{scope}")

    backfill_run_id = (
        start_gallery_backfill_run(
            db, len(pending), "flip_pairs" if flip_pairs_only else "all_eligible_listings"
        )
        if refresh_photo_galleries else None
    )
    success = fail = 0
    processed = 0
    last_error = None
    stopped_for_storage = False
    for doc in pending:
        # Check before the first write and then every 100 records. Atlas's UI
        # can differ slightly from dbStats, so callers should leave headroom.
        if stop_at_storage_mb is not None and (success + fail) % 100 == 0:
            stats = db.command("dbStats")
            used_mb = (stats.get("storageSize", 0) + stats.get("indexSize", 0)) / 1_000_000
            if used_mb >= stop_at_storage_mb:
                print(f"  ⚠ Stopping before storage threshold: {used_mb:.1f} MB >= {stop_at_storage_mb:.1f} MB")
                stopped_for_storage = True
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
                photos = fetch_gallery_photos(session, dcid)
            photos, gallery_status = select_gallery(doc.get('photos') or [], photos, detail)
            now_str = datetime.now(timezone.utc).strftime("%B %d %Y %I:%M%p")
            updates = {"photos": photos, "photo_gallery_fetched": now_str}
            try:
                expected = int(detail.get("photocount") or 0)
            except (TypeError, ValueError):
                expected = 0
            updates["photo_gallery_repair"] = {
                "status": gallery_status,
                "checked_at": datetime.now(timezone.utc), "count": len(photos),
            }
            if gallery_status == 'partial':
                updates['photo_gallery_repair']['next_retry_at'] = datetime.now(timezone.utc) + timedelta(hours=1)
            if not refresh_photo_galleries:
                updates.update({"details": detail, "details_fetched": now_str})
            coll.update_one({"_id": doc["_id"]}, {"$set": updates})
            log_listing(db, listing_id, dcid, len(photos), success=True)
            print(f"  ✓ {listing_id} — {len(photos)} photos")
            success += 1

        except MLSAuthenticationError:
            if backfill_run_id:
                update_gallery_backfill_run(
                    db, backfill_run_id, processed=processed, succeeded=success,
                    failed=fail, status="blocked_auth", last_error="MLS session rejected",
                )
            raise
        except Exception as e:
            err = str(e)
            last_error = err
            print(f"  ✗ {listing_id} (DCID={dcid}): {err}")
            log_listing(db, listing_id, dcid, 0, success=False, error=err)
            fail += 1

        processed += 1
        if backfill_run_id and (processed == 1 or processed % 25 == 0):
            update_gallery_backfill_run(
                db, backfill_run_id, processed=processed, succeeded=success,
                failed=fail, last_error=last_error,
            )
        time.sleep(RATE_LIMIT_SLEEP)

    if backfill_run_id:
        final_status = "stopped_storage_threshold" if stopped_for_storage else "complete"
        update_gallery_backfill_run(
            db, backfill_run_id, processed=processed, succeeded=success,
            failed=fail, status=final_status, last_error=last_error,
        )
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
        "--flip-pairs-only", action="store_true",
        help="with --refresh-photo-galleries, limit repair to FlipDB purchase/resale MLS records",
    )
    parser.add_argument(
        "--listing-ids", default="",
        help="comma-separated MLS IDs; fetch details or repair galleries only for this bounded set",
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
    if args.flip_pairs_only and not args.refresh_photo_galleries:
        parser.error("--flip-pairs-only requires --refresh-photo-galleries")
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

    if args.refresh_photo_galleries:
        scope = " for FlipDB purchase/resale listings only" if args.flip_pairs_only else ""
        print(f"[2/2] Fetching authorized MLS gallery references{scope}...")
    else:
        print("[2/2] Fetching details for new listings...")
    try:
        success, fail = fetch_and_store_details(
            session, refresh_active_details=args.refresh_active_details,
            refresh_photo_galleries=args.refresh_photo_galleries,
            flip_pairs_only=args.flip_pairs_only,
            listing_ids=[value.strip() for value in args.listing_ids.split(",") if value.strip()] or None,
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
