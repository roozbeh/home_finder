"""Daily materialization of observed short-hold resale pairs.

``flipdb`` is a derived, provenance-preserving research collection. A record
means that the licensed MLS source contains two consecutive closed-sale events
for one parcel/address within the configured hold window. It is *not* evidence
that renovations occurred, that either transaction was arm's length, or that a
net flip profit was earned.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING, MongoClient, UpdateOne

from .flips import ResalePair, identify_short_hold_resales
from .market_history import load_closed_listings


FLIPDB_DATABASE = "flip_finder"
FLIPDB_COLLECTION = "flipdb"
FLIPDB_SCHEMA_VERSION = 1


def pair_key(pair: ResalePair) -> str:
    """Create a stable, non-address identifier for an observed resale pair."""
    values = (
        str(FLIPDB_SCHEMA_VERSION), pair.match_method, pair.property_key,
        pair.purchase_listing_id, pair.resale_listing_id,
    )
    return hashlib.sha256("|".join(values).encode("utf-8")).hexdigest()


def pair_to_flipdb_record(pair: ResalePair, source_database: str,
                          source_collection: str, observed_at: datetime,
                          max_hold_days: int = 365) -> dict[str, Any]:
    """Map a resale pair into a display- and model-ready provenance record."""
    return {
        "schema_version": FLIPDB_SCHEMA_VERSION,
        "pair_key": pair_key(pair),
        "record_type": "observed_short_hold_resale",
        "is_verified_flip": False,
        "source": {
            "database": source_database,
            "collection": source_collection,
            "analysis": "consecutive_closed_sales",
            "max_hold_days": max_hold_days,
            "provenance": "MLS listing data",
        },
        "property_key": pair.property_key,
        "match_method": pair.match_method,
        "city": pair.city,
        "subdivision": pair.subdivision,
        "buy_mls": pair.purchase_listing_id,
        "buy_date": pair.purchase_date,
        "buy_price": pair.purchase_price,
        "sell_mls": pair.resale_listing_id,
        "sell_date": pair.resale_date,
        "sell_price": pair.resale_price,
        "hold_days": pair.hold_days,
        "gross_spread": pair.gross_spread,
        "gross_roi": pair.gross_roi,
        "annualized_gross_return": pair.annualized_gross_return,
        "confidence": pair.confidence,
        "caution": pair.caution,
        "active": True,
        "last_seen_at": observed_at,
        "disclaimer": (
            "Observed gross price change only. Renovation scope, transaction type, "
            "costs, financing, taxes, concessions, and net profit are unverified."
        ),
    }


def materialize_flipdb(uri: str, source_database: str = "mls",
                       source_collection: str = "mls_listings",
                       result_database: str = FLIPDB_DATABASE,
                       result_collection: str = FLIPDB_COLLECTION,
                       max_hold_days: int = 365, min_hold_days: int = 14,
                       write: bool = False) -> dict[str, int]:
    """Review all closed source records and idempotently upsert observed pairs.

    Records that disappear from a later recomputation are retained for audit
    history but marked inactive; source MLS listings are never modified.
    """
    listings = load_closed_listings(uri, source_database, source_collection)
    pairs = identify_short_hold_resales(
        listings, max_hold_days=max_hold_days, min_hold_days=min_hold_days,
    )
    stats = {"closed_listings": len(listings), "pairs": len(pairs), "upserted": 0, "deactivated": 0}
    if not write:
        return stats

    now = datetime.now(timezone.utc)
    run_id = str(uuid.uuid4())
    client = MongoClient(uri)
    try:
        database = client[result_database]
        collection = database[result_collection]
        collection.create_index([("pair_key", ASCENDING)], unique=True, name="pair_key_unique")
        collection.create_index([("active", ASCENDING), ("sell_date", -1)], name="active_recent_sales")
        collection.create_index([("city", ASCENDING), ("sell_date", -1)], name="city_recent_sales")

        operations = []
        for pair in pairs:
            record = pair_to_flipdb_record(
                pair, source_database, source_collection, now, max_hold_days,
            )
            record["last_seen_run_id"] = run_id
            operations.append(UpdateOne(
                {"pair_key": record["pair_key"]},
                {"$set": record, "$setOnInsert": {"first_seen_at": now}},
                upsert=True,
            ))
        if operations:
            result = collection.bulk_write(operations, ordered=False)
            stats["upserted"] = result.upserted_count + result.matched_count

        stale_query = {
            "record_type": "observed_short_hold_resale",
            "source.database": source_database,
            "source.collection": source_collection,
            "active": True,
            "last_seen_run_id": {"$ne": run_id},
        }
        stale = collection.update_many(
            stale_query,
            {"$set": {"active": False, "no_longer_detected_at": now}},
        )
        stats["deactivated"] = stale.modified_count
        database.flipdb_runs.insert_one({
            "run_id": run_id,
            "type": "flipdb_refresh",
            "timestamp": now,
            "source_database": source_database,
            "source_collection": source_collection,
            "result_collection": result_collection,
            "max_hold_days": max_hold_days,
            "min_hold_days": min_hold_days,
            **stats,
        })
        return stats
    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Materialize observed MLS short-hold resale pairs into flipdb."
    )
    parser.add_argument("--source-database", default=os.getenv("MONGO_DB", "mls"))
    parser.add_argument("--source-collection", default=os.getenv("MONGO_COLLECTION", "mls_listings"))
    parser.add_argument("--result-database", default=os.getenv("FLIPDB_DATABASE", FLIPDB_DATABASE))
    parser.add_argument("--result-collection", default=os.getenv("FLIPDB_COLLECTION", FLIPDB_COLLECTION))
    parser.add_argument("--max-hold-days", type=int, default=365)
    parser.add_argument("--min-hold-days", type=int, default=14)
    parser.add_argument("--write", action="store_true", help="upsert the derived flipdb records")
    args = parser.parse_args()
    if args.max_hold_days < args.min_hold_days or args.min_hold_days < 1:
        parser.error("hold-day bounds must be positive and ordered")
    uri = os.getenv("MONGODB_URI") or os.getenv("MONGO_URI")
    if not uri:
        parser.error("Set MONGODB_URI or MONGO_URI in the environment")
    stats = materialize_flipdb(
        uri, args.source_database, args.source_collection,
        args.result_database, args.result_collection,
        args.max_hold_days, args.min_hold_days, args.write,
    )
    prefix = "Stored" if args.write else "Dry run"
    print(
        f"{prefix}: {stats['pairs']} observed pairs from {stats['closed_listings']} closed listings; "
        f"{stats['upserted']} upserted; {stats['deactivated']} deactivated."
    )


if __name__ == "__main__":
    main()
