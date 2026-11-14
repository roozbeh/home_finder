"""Read-only Mongo audit. It deliberately never calls write-capable collection methods."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pymongo import MongoClient

from .normalize import first_value, normalize_mongo_listing, parse_datetime

AUDIT_FIELDS = {
    "property_type": ("PROPERTY_TYPE", "PROPERTY_SUB_TYPE", "BUILDING_TYPE", "details.PROPERTY_TYPE"),
    "beds": ("BEDROOMS_TOTAL", "details.BEDROOMS_TOTAL"), "baths": ("BATHROOMS_FULL", "details.BATHROOMS_FULL"),
    "living_sqft": ("SQFT", "LIVING_AREA", "details.SQFT"), "lot_sqft": ("LOT_SQFT", "details.LOT_SQFT"),
    "year_built": ("YEAR_BUILT", "details.YEAR_BUILT"), "coordinates": ("LATITUDE", "details.LATITUDE"),
    "apn": ("APN", "PARCEL_NUMBER", "details.APN"), "days_on_market": ("DAYS_ON_MARKET", "details.DAYS_ON_MARKET"),
    "listing_url": ("LISTING_URL", "URL", "details.LISTING_URL"), "listed_at": ("LISTING_CONTRACT_DATE", "LIST_DATE", "details.LISTING_CONTRACT_DATE"),
    "price_history": ("_history", "PRICE_HISTORY", "details.PRICE_HISTORY"),
}

PROJECTION = {"LISTING_ID": 1, "DCID": 1, "MLS_STATUS": 1, "RAW_STATUS": 1, "LIST_PRICE": 1, "DISPLAY_PRICE": 1, "SRCHPRICE": 1, "CITY": 1, "STREET_ADDRESS": 1, "CLOSE_PRICE": 1, "CLOSE_DATE": 1, "PROPERTY_TYPE": 1, "PROPERTY_SUB_TYPE": 1, "BUILDING_TYPE": 1, "SQFT": 1, "BEDROOMS_TOTAL": 1, "BATHROOMS_FULL": 1, "LOT_SQFT": 1, "YEAR_BUILT": 1, "LATITUDE": 1, "LONGITUDE": 1, "APN": 1, "PARCEL_NUMBER": 1, "DAYS_ON_MARKET": 1, "LISTING_URL": 1, "LISTING_CONTRACT_DATE": 1, "LIST_DATE": 1, "_history": 1, "_updated_at": 1, "details_fetched": 1, "photos": 1, "details.status": 1, "details.PUBLIC_REMARKS": 1, "details.CLOSE_PRICE": 1, "details.CLOSE_DATE": 1, "details.PROPERTY_TYPE": 1, "details.BUILDING_TYPE": 1, "details.SQFT": 1, "details.BEDROOMS_TOTAL": 1, "details.BATHROOMS_FULL": 1, "details.LOT_SQFT": 1, "details.YEAR_BUILT": 1, "details.LATITUDE": 1, "details.APN": 1, "details.DAYS_ON_MARKET": 1, "details.LISTING_URL": 1, "details.LISTING_CONTRACT_DATE": 1, "details.PRICE_HISTORY": 1, "details.photocount": 1}


def _redacted_sample(record: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_mongo_listing(record)
    return {"listing_id": normalized.listing_id or None, "city": normalized.city, "status_raw": normalized.status_raw,
            "status_canonical": normalized.status_canonical, "list_price": normalized.list_price,
            "has_closed_sale_fields": normalized.sale_price is not None and normalized.sold_at is not None,
            "description_present": bool(normalized.description), "photo_count": normalized.photo_count,
            "fields": sorted(k for k in record if k not in {"_id", "details", "photos", "_history"})}


def audit_mongo(uri: str, database: str = "mls", collection: str = "mls_listings", scan_limit: int | None = None) -> dict[str, Any]:
    client = MongoClient(uri, serverSelectionTimeoutMS=12_000, appname="peninsula-flip-finder-audit")
    try:
        client.admin.command("ping")
        coll = client[database][collection]
        total = coll.estimated_document_count()
        cursor = coll.find({}, PROJECTION, no_cursor_timeout=False)
        if scan_limit:
            cursor = cursor.limit(scan_limit)
        docs = list(cursor)
        status = Counter(str(d.get("MLS_STATUS", "<missing>")) for d in docs)
        detail_status = Counter(str((d.get("details") or {}).get("status", "<missing>")) for d in docs)
        cities = Counter(str(d.get("CITY", "<missing>")).strip().upper() or "<missing>" for d in docs)
        prices = [normalize_mongo_listing(d).list_price for d in docs]
        listings = [normalize_mongo_listing(d) for d in docs]
        coverage = {name: round(100 * sum(first_value(d, *paths) not in (None, "") for d in docs) / len(docs), 2) if docs else 0.0 for name, paths in AUDIT_FIELDS.items()}
        close_price_count = sum(x.sale_price is not None for x in listings)
        close_date_count = sum(x.sold_at is not None for x in listings)
        usable_sales = [x for x in listings if x.status_canonical == "CLOSED" and x.sale_price is not None and x.sold_at is not None]
        descriptions = sum(bool(x.description and str(x.description).strip()) for x in listings)
        photo_docs = sum((x.photo_count or 0) > 0 for x in listings)
        ids = Counter(x.listing_id for x in listings if x.listing_id)
        duplicate_count = sum(count - 1 for count in ids.values() if count > 1)
        updates = [x.updated_at for x in listings if x.updated_at]
        sold_dates = [x.sold_at for x in usable_sales]
        numeric_prices = [p for p in prices if p is not None]
        report = {"generated_at": datetime.now(timezone.utc).isoformat(), "source": f"mongodb://{database}/{collection}",
                  "scan": {"estimated_total_documents": total, "documents_scanned": len(docs), "scan_limited": bool(scan_limit)},
                  "statuses": dict(status), "detail_statuses": dict(detail_status), "cities": dict(cities.most_common()),
                  "list_price": {"present": len(numeric_prices), "missing": len(docs)-len(numeric_prices), "min": min(numeric_prices) if numeric_prices else None, "max": max(numeric_prices) if numeric_prices else None},
                  "coverage_percent": coverage,
                  "content": {"description_present_percent": round(100 * descriptions / len(docs), 2) if docs else 0, "photo_metadata_present_percent": round(100 * photo_docs / len(docs), 2) if docs else 0},
                  "sales": {"close_price_present": close_price_count, "close_date_present": close_date_count, "usable_closed_comps": len(usable_sales), "oldest_usable_close": min(sold_dates).isoformat() if sold_dates else None, "newest_usable_close": max(sold_dates).isoformat() if sold_dates else None, "arv_supported": len(usable_sales) >= 3},
                  "freshness": {"oldest_record_update": min(updates).isoformat() if updates else None, "newest_record_update": max(updates).isoformat() if updates else None},
                  "duplicates_by_listing_id": duplicate_count, "redacted_samples": [_redacted_sample(d) for d in docs[:10]],
                  "raw_top_level_fields": sorted({key for d in docs for key in d if key not in {"_id"}})}
        return report
    finally:
        client.close()


def write_audit_documents(report: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "audit.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    sales = report["sales"]
    audit_md = output_dir / "data_audit.md"
    audit_md.write_text(f"# MLS Data Audit\n\nGenerated: {report['generated_at']}\n\n"
        f"- Documents scanned: {report['scan']['documents_scanned']} (estimated collection total: {report['scan']['estimated_total_documents']})\n"
        f"- Usable closed-sale records: {sales['usable_closed_comps']}\n"
        f"- Closed-sale date range: {sales['oldest_usable_close']} to {sales['newest_usable_close']}\n"
        f"- ARV support threshold (3 valid closed records): {'met only at collection level' if sales['arv_supported'] else 'not met'}; local/property-level comp validation is still required.\n"
        f"- Description coverage: {report['content']['description_present_percent']}%; photo metadata coverage: {report['content']['photo_metadata_present_percent']}%.\n\n"
        "## Status and geography\n\n```json\n" + json.dumps({"MLS_STATUS": report["statuses"], "cities": report["cities"]}, indent=2) + "\n```\n\n"
        "## Field coverage (%)\n\n```json\n" + json.dumps(report["coverage_percent"], indent=2) + "\n```\n\n"
        "## Method and limits\n\nThis audit used read-only MongoDB commands and redacted samples. It did not validate MLS licensing, status-code semantics, property condition, or the reliability of individual comparable sales. `UNKNOWN` statuses are never treated as active.\n")
    gaps_md = output_dir / "data_gaps.md"
    missing = [name for name, pct in report["coverage_percent"].items() if pct < 90]
    gaps_md.write_text("# Data Gaps and Required Authorized Sources\n\n"
        "## Findings\n\n"
        f"- Fields below 90% coverage: {', '.join(missing) if missing else 'none in scanned fields'}.\n"
        f"- Closed-comparable readiness: {'collection-level records exist, but each ARV must still require nearby, recent, like-for-like closed sales.' if sales['arv_supported'] else 'BLOCKED — insufficient valid closed sales; do not calculate ARV.'}\n"
        "- The existing ConnectMLS cookie/session implementation is not, by itself, evidence that retention or investment analytics is permitted. Obtain broker/MLS written confirmation before operational use.\n"
        "- Photo URLs/metadata are present only for private review if the MLS agreement permits; no AI image transfer or republishing is authorized by this project.\n\n"
        "## Authorized-source priorities\n\n"
        "1. Broker/MLS-approved export or RESO feed with active and at least 12 months of closed sales, permitted retention, status definitions, media rights, and update terms.\n"
        "2. Authorized historical sales export if MLS history is incomplete.\n"
        "3. County parcel/assessor records and permit histories using documented lawful access; join only with APN/address confidence.\n"
        "4. Official flood/fire/seismic/hazard sources with source date and geographic coverage.\n\n"
        "No endpoint has been assumed or invented. Until source permissions and closed-comp coverage are confirmed, outputs are screening leads—not valuations or bids.\n")
    return audit_md, gaps_md
