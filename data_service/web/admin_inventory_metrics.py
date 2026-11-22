"""Read-only refresh and photo classification counts for the admin dashboard."""
from datetime import datetime, timedelta, timezone

STALE_DAYS = 15
QUERY_MS = 8000


def _timestamp(value):
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
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return None


def active_freshness(listings, now):
    cutoff = now - timedelta(days=STALE_DAYS)
    counts = dict(inventory_stale=0, inventory_unknown=0, detail_stale=0, detail_unknown=0)
    for listing in listings.find(
        {"MLS_STATUS": "ACTV"},
        {"_id": 0, "active_last_seen_at": 1, "_updated_at": 1, "details_fetched": 1},
        max_time_ms=QUERY_MS,
    ):
        last_seen = _timestamp(listing.get("active_last_seen_at")) or _timestamp(listing.get("_updated_at"))
        details = _timestamp(listing.get("details_fetched"))
        for key, stamp in (("inventory", last_seen), ("detail", details)):
            if stamp is None:
                counts[f"{key}_unknown"] += 1
            elif stamp < cutoff:
                counts[f"{key}_stale"] += 1
    return counts


def photo_coverage(classifications):
    saved_label = {"listing_id": {"$type": "string"}, "category": {"$type": "string", "$nin": ["", "unclassified"]}}

    def listing_ids(collection, criteria):
        return {value.strip().upper() for value in collection.distinct("listing_id", criteria, maxTimeMS=QUERY_MS)
                if value.strip()}

    bulk = listing_ids(classifications.mls_photo_classifications, {**saved_label, "result": "classified"})
    active = listing_ids(classifications.active_photo_classifications, saved_label)
    historical = listing_ids(classifications.photo_classifications, saved_label)
    return {"total": len(bulk | active | historical), "bulk": len(bulk)}
