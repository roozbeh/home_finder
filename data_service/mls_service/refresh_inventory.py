#!/usr/bin/env python3
"""Refresh active MLS inventory, then process a time-bounded detail backlog."""
import argparse
import re
import time
from datetime import datetime, timedelta, timezone
from collections import Counter

from pymongo import MongoClient

from fetch_details import DETAILS_URL_TPL, RATE_LIMIT_SLEEP, fetch_gallery_photos, log_listing, select_gallery
from mls_session import MLSAuthenticationError, assert_authenticated_response, load_active_session, retire_session
from search_and_store import (
    BAY_AREA_CITIES, MONGO_DB, MONGO_URI, build_session, fetch_target_active_listings, log_run, upsert_listings,
)

ACTIVE = ("ACTV", "NEW", "BOMK", "PCH")
STATUS_CHECK_LIMIT = 5
STALE_INVENTORY_DAYS = 15
DISPLAY_CHECK_DAYS = 10
LEGACY_CITY_ALIASES = {
    "REDWOODCIY": "REDWOOD CITY",
    "HILLSBOROU": "HILLSBOROUGH",
    "SOUTH SAN": "SOUTH SAN FRANCISCO",
}
STANDARD_DETAIL_LIMIT = 450
STANDARD_GALLERY_LIMIT = 100
DETAIL_FIELDS = {
    "DCID": 1, "LISTING_ID": 1, "RAW_STATUS": 1, "_updated_at": 1, "details_fetched": 1,
    "details.status": 1, "photo_gallery_repair": 1,
}


class DeadlineReached(RuntimeError):
    pass


def bound_requests(session, deadline):
    """Apply the cycle's wall-clock cutoff to each MLS request, including gallery requests."""
    request = session.request

    def request_before_cutoff(*args, **kwargs):
        remaining = deadline - time.time()
        if remaining < 3:
            raise DeadlineReached
        timeout = min(30, kwargs.get("timeout", 30), remaining - 1)
        connect = min(4, timeout / 2)
        kwargs["timeout"] = (connect, timeout - connect)
        return request(*args, **kwargs)

    session.request = request_before_cutoff


def utcnow():
    return datetime.now(timezone.utc)


def checked_at(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            for pattern in ("%B %d %Y %I:%M%p", "%m/%d/%Y"):
                try:
                    value = datetime.strptime(value, pattern)
                    break
                except ValueError:
                    continue
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    return datetime.min.replace(tzinfo=timezone.utc)


def record_misses(collection, complete_cities, seen_at):
    """A missing row only counts where the city search returned a complete result."""
    if not complete_cities:
        return
    cities = sorted(complete_cities)
    legacy_cities = sorted(complete_cities | {
        alias for alias, city in LEGACY_CITY_ALIASES.items() if city in complete_cities
    })
    missed = {
        # The inventory search requests ACTV only. An absence tells us
        # nothing about other source codes such as pending or back-on-market.
        "RAW_STATUS": "ACTV", "active_last_seen_at": {"$ne": seen_at},
        "$or": [
            {"active_search_city": {"$in": cities}},
            {"active_search_city": {"$exists": False}, "CITY": {"$in": legacy_cities}},
        ],
    }
    # Require two complete cycle observations separated in time. Fast reruns
    # cannot turn an isolated miss into a status-check candidate.
    collection.update_many(
        {**missed, "active_missing_since": {"$lte": seen_at - timedelta(minutes=10)},
         "active_status_next_check": {"$exists": False}},
        {"$set": {"active_status_next_check": seen_at}},
    )
    collection.update_many(
        {**missed, "active_missing_since": {"$exists": False}},
        {"$set": {"active_missing_since": seen_at}},
    )


def detail_work(collection, as_of, checks_due_days, limit):
    """Yield bounded work in status, current-active, then recent and older sold order."""
    cutoff = as_of - timedelta(days=checks_due_days)
    available = {"DCID": {"$exists": True, "$nin": ["", None]},
                 "$or": [{"refresh_retry_after": {"$exists": False}}, {"refresh_retry_after": {"$lte": as_of}}]}
    active = {"RAW_STATUS": {"$in": list(ACTIVE)}}
    stale_before = as_of - timedelta(days=STALE_INVENTORY_DAYS)
    stale_inventory = {"$or": [
        {"active_last_seen_at": {"$lt": stale_before}},
        {"active_last_seen_at": None, "_updated_at": {"$lt": stale_before}},
    ]}
    due_status = {**available, **active, "active_status_next_check": {"$lte": as_of}}
    status_limit = min(STATUS_CHECK_LIMIT, limit)
    overdue = list(collection.find({"$and": [due_status, stale_inventory]}, DETAIL_FIELDS)
                   .sort([("active_status_next_check", 1), ("_updated_at", 1), ("_id", 1)]).limit(status_limit))
    recent_misses = list(collection.find({**due_status, "$nor": [stale_inventory]}, DETAIL_FIELDS)
                         .sort([("active_status_next_check", 1), ("_id", 1)]).limit(status_limit))
    # Reserve both lanes and let either borrow unused slots from the other.
    stale_slots = (status_limit + 1) // 2
    recent_slots = status_limit - stale_slots
    queued_status = (overdue[:stale_slots] + recent_misses[:recent_slots]
                     + overdue[stale_slots:] + recent_misses[recent_slots:])[:status_limit]
    print(f"Status queue selected: {sum(row in overdue for row in queued_status)} stale inventory, "
          f"{sum(row in recent_misses for row in queued_status)} recent misses")
    for row in queued_status:
        yield "status", row

    current = collection.find({**available, **active, "active_last_seen_at": as_of}, DETAIL_FIELDS)
    new_active, expiring, galleries = [], [], []
    for row in current:
        stamp = checked_at(row.get("details_fetched"))
        repair = row.get("photo_gallery_repair") or {}
        if stamp.year == 1:
            new_active.append(row)
        elif stamp <= cutoff:
            expiring.append((stamp, row["_id"], row))
        elif (repair.get("status") in ("pending", "partial", "failed")
              and checked_at(repair.get("next_retry_at")) <= as_of):
            galleries.append(row)
    for _, _, row in sorted(expiring)[:limit - len(queued_status)]:
        yield "display check", row
    for row in sorted(new_active, key=lambda item: item["_id"])[:STANDARD_DETAIL_LIMIT]:
        yield "new active", row

    repair_due = {"photo_gallery_repair.status": {"$in": ["pending", "partial", "failed"]},
                  "$or": [{"photo_gallery_repair.next_retry_at": {"$exists": False}},
                          {"photo_gallery_repair.next_retry_at": {"$lte": as_of}}]}
    sold = {**available, "RAW_STATUS": "SLD", "$and": [{"$or": [
        {"details_fetched": {"$exists": False}}, {"details.status": {"$ne": "SLD"}}, repair_due,
    ]}]}
    recent = as_of - timedelta(days=14)
    sold_remaining = STANDARD_DETAIL_LIMIT
    for label, date_filter in (("recent sold", {"$gte": recent}), ("historical sold", {"$not": {"$gte": recent}})):
        if not sold_remaining:
            break
        for row in collection.find(
            {**sold, "sold_last_seen_at": date_filter}, DETAIL_FIELDS,
        ).sort("_id", 1).limit(sold_remaining):
            sold_remaining -= 1
            yield label, row
    galleries = sorted(galleries, key=lambda item: item["_id"])[:STANDARD_GALLERY_LIMIT]
    for row in galleries:
        yield "active gallery", row
    if len(galleries) < STANDARD_GALLERY_LIMIT:
        for row in collection.find({**available, "RAW_STATUS": {"$nin": ["SLD", *ACTIVE]},
                                    "$and": [repair_due]}, DETAIL_FIELDS).sort("_id", 1).limit(
            STANDARD_GALLERY_LIMIT - len(galleries),
        ):
            yield "historical gallery", row


def fetch_detail(session, dcid):
    response = session.get(DETAILS_URL_TPL.format(dcid=dcid), timeout=30)
    assert_authenticated_response(response)
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        raise ValueError("MLS returned no listing details")
    detail = rows[0]
    status = detail.get("status")
    if not isinstance(status, str) or not re.fullmatch(r"[A-Z]{2,8}", status):
        raise ValueError("MLS returned no usable status")
    return detail, status


def refresh_listing(collection, session, row, lane):
    detail, status = fetch_detail(session, row["DCID"])
    timestamp = utcnow()
    updates = {"details": detail, "details_fetched": timestamp,
               "RAW_STATUS": status, "MLS_STATUS": status}
    cleanup = {"refresh_retry_after": ""}
    if status == "SLD" and row["RAW_STATUS"] != "SLD":
        if checked_at(detail.get("CLOSE_DATE")) >= timestamp - timedelta(days=14):
            updates["sold_last_seen_at"] = timestamp
        if not row.get("photo_gallery_repair"):
            updates["photo_gallery_repair"] = {"status": "pending", "queued_at": timestamp}
    if lane == "status" and status in ACTIVE:
        updates["active_status_next_check"] = timestamp + timedelta(days=1)
    elif status not in ACTIVE:
        cleanup["active_status_next_check"] = ""

    # The status and access flags are useful even if a separate gallery request
    # fails or the cycle ends. A competing inventory update wins the compare-and-set.
    guard = {"_id": row["_id"], "RAW_STATUS": row["RAW_STATUS"], "_updated_at": row.get("_updated_at")}
    if not collection.update_one(guard, {"$set": updates, "$unset": cleanup}).matched_count:
        return 0
    if lane == "status" or status in ("WC", "CANC", "EXP", "WDN"):
        return 0

    time.sleep(RATE_LIMIT_SLEEP)
    recovered = fetch_gallery_photos(session, row["DCID"])
    existing = collection.find_one({"_id": row["_id"]}, {"photos": 1}) or {}
    photos, state = select_gallery(existing.get("photos") or [], recovered, detail)
    repair = {"status": state, "checked_at": timestamp, "count": len(photos)}
    if state == "partial":
        repair["next_retry_at"] = timestamp + timedelta(hours=1)
    collection.update_one({"_id": row["_id"], "details_fetched": timestamp}, {"$set": {
        "photos": photos, "photo_gallery_fetched": timestamp, "photo_gallery_repair": repair,
    }})
    return len(photos)


def drain_details(db, session, seen_at, deadline, limit, days, storage_mb):
    def storage_available():
        stats = db.command("dbStats")
        usage = (stats.get("storageSize", 0) + stats.get("indexSize", 0)) / 1_000_000
        if usage >= storage_mb:
            print(f"Detail work paused at storage threshold {usage:.1f}/{storage_mb:.1f} MB")
        return usage < storage_mb

    if time.time() >= deadline - 3 or not storage_available():
        print("Detail backlog: no work started")
        return
    collection = db.mls_listings
    succeeded = failed = 0
    attempted = Counter()
    processed = set()
    for lane, row in detail_work(collection, seen_at, days, limit):
        if time.time() >= deadline - 3:
            break
        if row["_id"] in processed:
            continue
        if processed and len(processed) % 100 == 0 and not storage_available():
            break
        processed.add(row["_id"])
        attempted[lane] += 1
        listing_id = row.get("LISTING_ID", row["DCID"])
        try:
            photos = refresh_listing(collection, session, row, lane)
            log_listing(db, listing_id, row["DCID"], photos, success=True)
            succeeded += 1
            print(f"Detail lane {lane}: {listing_id} updated")
        except DeadlineReached:
            print("Detail request cutoff reached")
            break
        except MLSAuthenticationError:
            raise
        except Exception as exc:
            collection.update_one({"_id": row["_id"]}, {"$set": {"refresh_retry_after": utcnow() + timedelta(hours=1)}})
            log_listing(db, listing_id, row["DCID"], 0, success=False, error=str(exc))
            failed += 1
            print(f"Detail lane {lane}: {listing_id} failed ({type(exc).__name__})")
        time.sleep(RATE_LIMIT_SLEEP)
    print(f"Detail backlog: {sum(attempted.values())} attempted, {succeeded} succeeded, {failed} failed, "
          f"by lane: {dict(attempted)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deadline", type=float, default=None, help="UTC Unix time when MLS work must stop")
    parser.add_argument("--limit", type=int, default=25, help="maximum extra status and display checks per cycle")
    parser.add_argument("--display-check-days", type=int, default=DISPLAY_CHECK_DAYS)
    parser.add_argument("--storage-mb", type=float, default=4500)
    parser.add_argument("--plan", action="store_true", help="Show queued work from the latest observation without writes")
    args = parser.parse_args()
    if args.limit < 1 or args.display_check_days < 1 or args.storage_mb <= 0:
        parser.error("limit, display-check-days and storage-mb must be positive")
    deadline = args.deadline if args.deadline is not None else time.time() + 13 * 60
    start = utcnow()
    # Match the existing local MLS tooling's eight-second server-selection budget.
    with MongoClient(MONGO_URI, serverSelectionTimeoutMS=8000, tz_aware=True) as mongo:
        db = mongo[MONGO_DB]
        if args.plan:
            last = db.mls_listings.find_one({"active_last_seen_at": {"$exists": True}},
                                            {"active_last_seen_at": 1}, sort=[("active_last_seen_at", -1)])
            as_of = last["active_last_seen_at"] if last else start
            print("Read-only detail plan:", dict(Counter(
                lane for lane, _ in detail_work(db.mls_listings, as_of, args.display_check_days, args.limit)
            )))
            return
        token = None
        observed, complete_cities = {}, set()
        inventory_saved = False
        try:
            token = load_active_session(MONGO_URI, MONGO_DB)
            with build_session(token["cookies"]) as session:
                bound_requests(session, deadline)
                rows = fetch_target_active_listings(session, BAY_AREA_CITIES, observed=observed,
                                                    complete_cities=complete_cities)
                counts = upsert_listings(rows, database=db, observed=observed, seen_at=start)
                record_misses(db.mls_listings, complete_cities, start)
                log_run(start, (utcnow() - start).total_seconds(), len(rows), *counts)
                inventory_saved = True
                print(f"Active snapshot: {len(rows)} listings, {len(complete_cities)}/{len(BAY_AREA_CITIES)} complete cities")
                drain_details(db, session, start, deadline, args.limit, args.display_check_days, args.storage_mb)
        except Exception as exc:
            if isinstance(exc, MLSAuthenticationError) and token is not None:
                retire_session(MONGO_URI, MONGO_DB, token["_id"], str(exc))
            if not inventory_saved:
                reason = "Active search cutoff reached before saving a snapshot" if isinstance(exc, DeadlineReached) else str(exc)
                log_run(start, (utcnow() - start).total_seconds(), 0, 0, 0, 0, error=reason)
            raise


if __name__ == "__main__":
    main()
