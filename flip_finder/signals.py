"""Explainable listing-text signals. A signal is evidence, never a diagnosis."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TextSignal:
    kind: str  # opportunity or risk
    category: str
    phrase: str
    snippet: str
    hard_stop: bool = False


OPPORTUNITY_RULES = {
    "cosmetic_or_outdated": ("fixer", "tlc", "needs updating", "original condition", "cosmetic", "deferred maintenance", "bring your contractor"),
    "seller_motivation": ("estate sale", "trust sale", "as-is", "price reduction", "back on market"),
}
RISK_RULES = {
    "structural_or_water": ("foundation", "settlement", "water intrusion", "retaining wall", "slope", "mold", "fire damage", "major pest", "landslide"),
    "legal_or_permit": ("unpermitted", "nonconforming", "court confirmation", "probate"),
    "occupancy_or_financing": ("tenant occupied", "cash only"),
    "hazard_or_insurance": ("creek", "seismic", "insurance", "flood zone"),
    "major_scope": ("roof replacement", "contractor special"),
}
HARD_STOP_PHRASES = {"foundation", "settlement", "fire damage", "landslide", "tenant occupied", "court confirmation"}


def _snippet(text: str, start: int, end: int, radius: int = 72) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    prefix = "…" if left else ""
    suffix = "…" if right < len(text) else ""
    return prefix + " ".join(text[left:right].split()) + suffix


def classify_description(description: str | None) -> tuple[TextSignal, ...]:
    """Return each configured phrase with its original-text context, once."""
    if not description or not description.strip():
        return ()
    lower = description.lower()
    signals: list[TextSignal] = []
    for kind, rules in (("opportunity", OPPORTUNITY_RULES), ("risk", RISK_RULES)):
        for category, phrases in rules.items():
            for phrase in phrases:
                index = lower.find(phrase)
                if index >= 0:
                    signals.append(TextSignal(
                        kind=kind, category=category, phrase=phrase,
                        snippet=_snippet(description, index, index + len(phrase)),
                        hard_stop=kind == "risk" and phrase in HARD_STOP_PHRASES,
                    ))
    return tuple(signals)
