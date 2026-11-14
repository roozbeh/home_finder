"""Transparent Phase 1 eligibility and listing-text evidence rules."""
from __future__ import annotations

from dataclasses import dataclass
from .normalize import Listing
from .signals import TextSignal, classify_description

TARGET_CITIES = {
    "ATHERTON", "BELMONT", "BRISBANE", "BURLINGAME", "COLMA", "DALY CITY",
    "EAST PALO ALTO", "FOSTER CITY", "HALF MOON BAY", "HILLSBOROUGH", "MENLO PARK",
    "MILLBRAE", "PACIFICA", "PORTOLA VALLEY", "REDWOOD CITY", "SAN BRUNO",
    "SAN CARLOS", "SAN MATEO", "SOUTH SAN FRANCISCO", "WOODSIDE",
}
OPPORTUNITY_TERMS = ("fixer", "tlc", "needs updating", "original condition", "estate sale", "trust sale", "as-is", "bring your contractor", "deferred maintenance", "cosmetic", "price reduction", "back on market")
RISK_TERMS = ("foundation", "settlement", "water intrusion", "creek", "retaining wall", "slope", "mold", "fire damage", "unpermitted", "contractor special", "cash only", "tenant occupied", "court confirmation", "probate", "seismic", "major pest", "roof replacement", "nonconforming", "insurance", "landslide", "flood zone")
HARD_STOP_TERMS = ("tenant occupied", "court confirmation", "foundation", "settlement", "fire damage", "landslide")


@dataclass(frozen=True)
class CandidateScreen:
    eligible: bool
    action: str
    reasons: tuple[str, ...]
    opportunity_evidence: tuple[str, ...]
    risk_evidence: tuple[str, ...]
    signals: tuple[TextSignal, ...] = ()


def _matches(description: str | None, terms: tuple[str, ...]) -> tuple[str, ...]:
    text = (description or "").lower()
    return tuple(term for term in terms if term in text)


def screen_listing(listing: Listing, max_list_price: float = 1_000_000, cities: set[str] = TARGET_CITIES) -> CandidateScreen:
    reasons: list[str] = []
    if listing.status_canonical != "ACTIVE": reasons.append("status_not_verified_active")
    if listing.list_price is None: reasons.append("missing_list_price")
    elif listing.list_price > max_list_price: reasons.append("above_asking_price_ceiling")
    if not listing.city or listing.city.upper() not in cities: reasons.append("outside_initial_market")
    if listing.is_detached is not True: reasons.append("not_verified_detached_single_family")
    if not listing.full_address: reasons.append("missing_address")
    signals = classify_description(listing.description)
    opportunity = tuple(signal.phrase for signal in signals if signal.kind == "opportunity")
    risks = tuple(signal.phrase for signal in signals if signal.kind == "risk")
    hard_stop = tuple(signal for signal in signals if signal.hard_stop)
    if hard_stop:
        return CandidateScreen(False, "MANUAL_REVIEW", tuple(reasons + ["hard_stop_text_signal"]), opportunity, risks, signals)
    if reasons:
        return CandidateScreen(False, "REJECT", tuple(reasons), opportunity, risks, signals)
    return CandidateScreen(True, "RESEARCH" if risks else "WATCHLIST", tuple(), opportunity, risks, signals)
