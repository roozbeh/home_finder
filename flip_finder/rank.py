"""Transparent ranking; blocked records never receive a financial rank."""
from __future__ import annotations
from dataclasses import dataclass
from .comps import ArvResult
from .rehab import RehabEstimate
from .underwrite import UnderwritingResult

@dataclass(frozen=True)
class RankResult:
    action: str
    score: float | None
    reasons: tuple[str, ...]

def rank(arv: ArvResult, rehab: RehabEstimate, base: UnderwritingResult | None,
         stress: UnderwritingResult | None) -> RankResult:
    if arv.status != "PRELIMINARY" or base is None or stress is None or rehab.scope == "MANUAL_ONLY":
        return RankResult("RESEARCH", None, ("financial ranking blocked: insufficient comps or unresolved risk",))
    score = base.profit_pre_tax / 1000 + max(0, stress.profit_pre_tax) / 2000 + len(arv.comps) * 2
    reasons = (f"{len(arv.comps)} observed closed comps", f"base preliminary profit ${base.profit_pre_tax:,.0f}", f"stress preliminary profit ${stress.profit_pre_tax:,.0f}")
    return RankResult("VISIT" if stress.profit_pre_tax >= 0 and base.profit_pre_tax >= 100000 else "RESEARCH", score, reasons)
