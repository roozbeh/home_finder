"""Observed short-hold resale analysis.

This module measures *recorded gross resale spreads*, not renovation cost,
net profit, or verified flip activity.  It deliberately makes no claim that a
short-hold transaction involved repairs.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from statistics import median
import re

from .normalize import Listing


def _clean_address(value: str | None) -> str | None:
    if not value:
        return None
    # Conservative canonicalization: adequate for a fallback key, never an
    # assertion that two records are the same parcel.
    text = value.upper()
    text = re.sub(r"\b(STREET|ST|AVENUE|AVE|ROAD|RD|DRIVE|DR|COURT|CT|LANE|LN|PLACE|PL|BOULEVARD|BLVD)\b", "", text)
    text = re.sub(r"[^A-Z0-9]", "", text)
    return text or None


def property_key(listing: Listing) -> tuple[str, str] | None:
    """Return an APN key when available, otherwise a clearly-marked address key."""
    apn = re.sub(r"[^A-Z0-9]", "", (listing.apn or "").upper())
    if apn:
        return ("apn", apn)
    address = _clean_address(listing.full_address)
    if address and listing.city:
        return ("address", f"{address}|{listing.city}|{listing.zip_code or ''}")
    return None


@dataclass(frozen=True)
class ResalePair:
    property_key: str
    match_method: str
    city: str | None
    subdivision: str | None
    purchase_listing_id: str
    purchase_date: datetime
    purchase_price: float
    resale_listing_id: str
    resale_date: datetime
    resale_price: float
    hold_days: int
    gross_spread: float
    gross_roi: float
    annualized_gross_return: float | None
    confidence: str
    caution: str


@dataclass(frozen=True)
class MarketFlipSummary:
    geography_type: str
    geography: str
    pair_count: int
    median_hold_days: float
    median_gross_spread: float
    median_gross_roi: float
    positive_spread_rate: float
    confidence: str


def _closed_events(listings: list[Listing]) -> dict[tuple[str, str], list[Listing]]:
    grouped: dict[tuple[str, str], list[Listing]] = defaultdict(list)
    for item in listings:
        key = property_key(item)
        if (key is None or item.status_canonical != "CLOSED" or
                item.sale_price is None or item.sale_price <= 0 or item.sold_at is None):
            continue
        grouped[key].append(item)
    return grouped


def identify_short_hold_resales(listings: list[Listing], max_hold_days: int = 365,
                                min_hold_days: int = 14) -> tuple[ResalePair, ...]:
    """Link sequential observed closed sales of the same property.

    APN joins are medium confidence; normalized-address joins are low
    confidence and are intentionally retained only as research leads.
    """
    if min_hold_days < 1 or max_hold_days < min_hold_days:
        raise ValueError("hold-day bounds must be positive and ordered")
    pairs: list[ResalePair] = []
    for key, events in _closed_events(listings).items():
        events.sort(key=lambda item: (item.sold_at, item.listing_id))
        for purchase, resale in zip(events, events[1:]):
            hold_days = (resale.sold_at.date() - purchase.sold_at.date()).days
            if not min_hold_days <= hold_days <= max_hold_days:
                continue
            gross_spread = resale.sale_price - purchase.sale_price
            gross_roi = gross_spread / purchase.sale_price
            # Extremely short same-property resales often denote corrections,
            # transfers, or recording artifacts—not an ordinary renovation.
            confidence = "MEDIUM" if key[0] == "apn" else "LOW"
            caution = (
                "observed short-hold resale; renovation, costs, financing, and arm's-length status are unverified"
                if hold_days >= 30 else
                "very short hold; likely requires deed/transaction review before use"
            )
            annualized = None
            if resale.sale_price > 0 and purchase.sale_price > 0:
                annualized = (resale.sale_price / purchase.sale_price) ** (365 / hold_days) - 1
            pairs.append(ResalePair(
                property_key=key[1], match_method=key[0], city=resale.city or purchase.city,
                subdivision=resale.subdivision or purchase.subdivision,
                purchase_listing_id=purchase.listing_id, purchase_date=purchase.sold_at,
                purchase_price=purchase.sale_price, resale_listing_id=resale.listing_id,
                resale_date=resale.sold_at, resale_price=resale.sale_price, hold_days=hold_days,
                gross_spread=gross_spread, gross_roi=gross_roi,
                annualized_gross_return=annualized, confidence=confidence, caution=caution,
            ))
    return tuple(sorted(pairs, key=lambda pair: (pair.resale_date, pair.property_key), reverse=True))


def summarize_flip_markets(pairs: tuple[ResalePair, ...], by: str = "city",
                           min_pairs: int = 3) -> tuple[MarketFlipSummary, ...]:
    """Summarize observed pairs without treating small samples as rankings."""
    if by not in {"city", "subdivision"}:
        raise ValueError("by must be city or subdivision")
    grouped: dict[str, list[ResalePair]] = defaultdict(list)
    for pair in pairs:
        geography = pair.city if by == "city" else pair.subdivision
        if geography:
            grouped[geography].append(pair)
    summaries = []
    for geography, group in grouped.items():
        if len(group) < min_pairs:
            continue
        summaries.append(MarketFlipSummary(
            geography_type=by, geography=geography, pair_count=len(group),
            median_hold_days=median(p.hold_days for p in group),
            median_gross_spread=median(p.gross_spread for p in group),
            median_gross_roi=median(p.gross_roi for p in group),
            positive_spread_rate=sum(p.gross_spread > 0 for p in group) / len(group),
            confidence="MEDIUM" if len(group) >= 10 and all(p.match_method == "apn" for p in group) else "LOW",
        ))
    return tuple(sorted(summaries, key=lambda row: (row.median_gross_roi, row.pair_count), reverse=True))
