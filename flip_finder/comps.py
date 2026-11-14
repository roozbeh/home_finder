"""Deterministic, auditable comparable-sale selection and preliminary ARV bands."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import median

from .normalize import Listing


@dataclass(frozen=True)
class Comparable:
    listing_id: str
    city: str | None
    sold_at: datetime
    sale_price: float
    living_sqft: float
    price_per_sqft: float
    size_delta_ratio: float


@dataclass(frozen=True)
class ArvResult:
    status: str
    reason: str | None
    comps: tuple[Comparable, ...]
    arv_low: float | None
    arv_base: float | None
    arv_high: float | None
    confidence: str


def select_closed_comps(subject: Listing, sales: list[Listing], as_of: datetime | None = None,
                        lookback_days: int = 180, max_size_delta: float = 0.25,
                        limit: int = 5) -> tuple[Comparable, ...]:
    """Select same-city, recent, similar-size, observed CLOSED sales.

    This is intentionally conservative: it does not approximate distance or
    condition when those fields have not been verified.
    """
    if not subject.city or not subject.living_sqft or subject.living_sqft <= 0:
        return ()
    cutoff = (as_of or datetime.now(timezone.utc)) - timedelta(days=lookback_days)
    candidates: list[Comparable] = []
    for sale in sales:
        if sale.status_canonical != "CLOSED" or sale.sale_price is None or sale.sale_price <= 0 or sale.sold_at is None:
            continue
        if sale.city != subject.city or not sale.living_sqft or sale.living_sqft <= 0 or sale.sold_at < cutoff:
            continue
        if sale.is_detached is not True:
            continue
        delta = abs(sale.living_sqft - subject.living_sqft) / subject.living_sqft
        if delta > max_size_delta:
            continue
        candidates.append(Comparable(sale.listing_id, sale.city, sale.sold_at, sale.sale_price,
                                     sale.living_sqft, sale.sale_price / sale.living_sqft, delta))
    candidates.sort(key=lambda c: (c.size_delta_ratio, -c.sold_at.timestamp()))
    return tuple(candidates[:limit])


def estimate_arv(subject: Listing, sales: list[Listing], as_of: datetime | None = None) -> ArvResult:
    comps = select_closed_comps(subject, sales, as_of=as_of)
    if len(comps) < 3:
        return ArvResult("UNVERIFIED — DO NOT BID", "fewer than three recent same-city, similar-size closed comps", comps, None, None, None, "LOW")
    ppsf = sorted(c.price_per_sqft for c in comps)
    # Indexed quantiles avoid false precision on the small required comp set.
    low = ppsf[max(0, int((len(ppsf) - 1) * 0.25))]
    high = ppsf[min(len(ppsf) - 1, int((len(ppsf) - 1) * 0.75 + 0.999))]
    base = median(ppsf)
    confidence = "MEDIUM" if len(comps) >= 5 else "LOW"
    return ArvResult("PRELIMINARY", None, comps, low * subject.living_sqft,
                     base * subject.living_sqft, high * subject.living_sqft, confidence)
