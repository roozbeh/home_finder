"""Conservative, configurable preliminary rehab templates—not contractor quotes."""
from __future__ import annotations

from dataclasses import dataclass
from .normalize import Listing
from .signals import classify_description

@dataclass(frozen=True)
class RehabEstimate:
    scope: str
    low: float
    base: float
    high: float
    confidence: str
    reason: str

def estimate_rehab(listing: Listing) -> RehabEstimate:
    sqft = listing.living_sqft
    if not sqft or sqft <= 0:
        return RehabEstimate("UNKNOWN", 0, 0, 0, "LOW", "missing living area; no budget produced")
    signals = classify_description(listing.description)
    risks = [s for s in signals if s.kind == "risk"]
    opportunities = [s for s in signals if s.kind == "opportunity"]
    if any(s.hard_stop for s in risks):
        return RehabEstimate("MANUAL_ONLY", 0, 0, 0, "LOW", "hard-stop listing text signal")
    if opportunities:
        return RehabEstimate("COSMETIC", sqft * 25, sqft * 45, sqft * 70, "LOW", "listing text indicates cosmetic/outdated work")
    return RehabEstimate("UNKNOWN_CONDITION", sqft * 35, sqft * 65, sqft * 100, "LOW", "condition not verified; widened screening range")
