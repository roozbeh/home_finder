"""Canonicalization for source records. No missing critical value becomes zero."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any


def first_value(record: dict[str, Any], *paths: str) -> Any:
    for path in paths:
        value: Any = record
        for part in path.split("."):
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(part)
        if value not in (None, ""):
            return value
    return None


def parse_number(value: Any) -> float | None:
    """Parse source numeric variants; invalid and absent values remain None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    if not isinstance(value, str):
        return None
    cleaned = value.strip().replace("$", "").replace(",", "")
    cleaned = re.sub(r"\s+(?:sq\.?\s*ft\.?|sf|acres?)$", "", cleaned, flags=re.I)
    if not cleaned or cleaned.lower() in {"n/a", "na", "--", "unknown"}:
        return None
    try:
        return float(Decimal(cleaned))
    except InvalidOperation:
        return None


def parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # ConnectMLS stores some dates as Unix milliseconds. Values in seconds
        # are also accepted; nonsensical epochs remain unavailable.
        try:
            seconds = float(value) / 1000 if abs(float(value)) > 10_000_000_000 else float(value)
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%Y %I:%M%p", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


STATUS_MAP = {
    "SLD": "CLOSED", "SOLD": "CLOSED", "CLOSED": "CLOSED",
    "ACTV": "ACTIVE", "ACTIVE": "ACTIVE",
    "PEND": "PENDING", "PENDING": "PENDING",
    "CONT": "CONTINGENT", "CONTINGENT": "CONTINGENT",
    "WDN": "WITHDRAWN", "WITHDRAWN": "WITHDRAWN",
    "EXP": "EXPIRED", "EXPIRED": "EXPIRED",
}

CITY_ALIASES = {
    "REDWOODCIY": "REDWOOD CITY",
    "EASTPAALTO": "EAST PALO ALTO",
    "HALFMO BAY": "HALF MOON BAY",
    "HILLSBOROU": "HILLSBOROUGH",
    "FOSTERCITY": "FOSTER CITY",
    "PORTO": "PORTOLA VALLEY",
    "SOUTH SAN": "SOUTH SAN FRANCISCO",
    "AMERICNYON": "AMERICAN CANYON",
    "CORTEMAERA": "CORTE MADERA",
}


def canonical_city(value: Any) -> str | None:
    if value is None or not str(value).strip():
        return None
    city = " ".join(str(value).upper().split())
    return CITY_ALIASES.get(city, city)


def canonical_status(raw: Any) -> str:
    return STATUS_MAP.get(str(raw or "").strip().upper(), "UNKNOWN")


def infer_detached(property_type: Any, building_type: Any) -> bool | None:
    text = " ".join(str(x or "") for x in (property_type, building_type)).lower()
    if not text:
        return None
    if any(token in text for token in ("condo", "co-op", "coop", "mobile", "manufactured", "land", "fractional")):
        return False
    if any(token in text for token in ("single family", "single-family", "detached", "sfr", "residential")):
        return True
    return None


@dataclass(frozen=True)
class Listing:
    source: str
    source_id: str
    listing_id: str
    full_address: str | None
    city: str | None
    zip_code: str | None
    apn: str | None
    subdivision: str | None
    status_raw: str | None
    status_canonical: str
    list_price: float | None
    property_type: str | None
    is_detached: bool | None
    living_sqft: float | None
    latitude: float | None
    longitude: float | None
    beds: float | None
    baths: float | None
    description: str | None
    sale_price: float | None
    sold_at: datetime | None
    listed_at: datetime | None
    updated_at: datetime | None
    photo_count: int | None
    listing_url: str | None
    data_quality_flags: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_mongo_listing(record: dict[str, Any], source: str = "mls.mls_listings") -> Listing:
    details = record.get("details") if isinstance(record.get("details"), dict) else {}
    raw_status = first_value(record, "MLS_STATUS", "RAW_STATUS", "details.status")
    listing_id = str(first_value(record, "LISTING_ID", "DCID", "_id") or "")
    street = first_value(record, "STREET_ADDRESS", "ADDRESS", "details.STREET_ADDRESS")
    city = first_value(record, "CITY", "details.CITY")
    address = " ".join(str(x).strip() for x in (street, city) if x not in (None, "")) or None
    photos = record.get("photos")
    photo_count = len(photos) if isinstance(photos, list) else parse_number(first_value(record, "details.photocount", "photocount"))
    flags: list[str] = []
    if not listing_id:
        flags.append("missing_listing_id")
    if not address:
        flags.append("missing_address")
    list_price = parse_number(first_value(record, "LIST_PRICE", "DISPLAY_PRICE", "SRCHPRICE"))
    if list_price is None:
        flags.append("missing_list_price")
    status = canonical_status(raw_status)
    if status == "UNKNOWN":
        flags.append("unknown_status")
    return Listing(
        source=source, source_id=listing_id, listing_id=listing_id,
        full_address=address, city=canonical_city(city),
        zip_code=str(first_value(record, "POSTAL_CODE", "ZIP", "ZIP_CODE", "details.POSTAL_CODE") or "").strip() or None,
        apn=str(first_value(record, "APN", "PARCEL_NUMBER", "details.APN", "details.PARCEL_NUMBER") or "").strip() or None,
        subdivision=str(first_value(record, "SUBDIVISION_NAME", "details.SUBDIVISION_NAME") or "").strip() or None,
        status_raw=str(raw_status) if raw_status is not None else None,
        status_canonical=status, list_price=list_price,
        property_type=first_value(record, "PROPERTY_TYPE", "PROPERTY_SUB_TYPE", "BUILDING_TYPE", "details.PROPERTY_TYPE"),
        is_detached=infer_detached(first_value(record, "PROPERTY_TYPE", "PROPERTY_SUB_TYPE", "details.PROPERTY_TYPE"), first_value(record, "BUILDING_TYPE", "details.BUILDING_TYPE")),
        living_sqft=parse_number(first_value(record, "SQFT", "LIVING_AREA", "details.SQFT")),
        latitude=parse_number(first_value(record, "LATITUDE", "details.LATITUDE")),
        longitude=parse_number(first_value(record, "LONGITUDE", "details.LONGITUDE")),
        beds=parse_number(first_value(record, "BEDROOMS_TOTAL", "details.BEDROOMS_TOTAL")),
        baths=parse_number(first_value(record, "BATHROOMS_FULL", "details.BATHROOMS_FULL")),
        description=first_value(record, "details.PUBLIC_REMARKS", "PUBLIC_REMARKS", "REMARKS"),
        sale_price=parse_number(first_value(record, "CLOSE_PRICE", "details.CLOSE_PRICE")),
        sold_at=parse_datetime(first_value(record, "CLOSE_DATE", "details.CLOSE_DATE")),
        listed_at=parse_datetime(first_value(record, "LISTING_CONTRACT_DATE", "LIST_DATE", "details.LISTING_CONTRACT_DATE")),
        updated_at=parse_datetime(first_value(record, "_updated_at", "details_fetched")),
        photo_count=int(photo_count) if photo_count is not None else None,
        listing_url=first_value(record, "LISTING_URL", "URL", "details.LISTING_URL"),
        data_quality_flags=tuple(flags),
    )
