"""Deterministic preliminary flip underwriting; inputs remain explicit assumptions."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UnderwritingAssumptions:
    buyer_closing_pct: float = 0.02
    selling_pct: float = 0.06
    contingency_pct: float = 0.20
    holding_monthly: float = 2_500
    permits_and_misc: float = 10_000
    annual_interest_rate: float = 0.0
    loan_to_cost: float = 0.0
    lender_points_pct: float = 0.0
    minimum_profit: float = 100_000


@dataclass(frozen=True)
class UnderwritingResult:
    purchase_price: float
    resale_price: float
    rehab: float
    months: int
    buyer_closing: float
    contingency: float
    financing: float
    holding: float
    selling: float
    profit_pre_tax: float
    roi_on_cost: float
    mao: float


def underwrite(resale_price: float, purchase_price: float, rehab: float, months: int,
               assumptions: UnderwritingAssumptions = UnderwritingAssumptions()) -> UnderwritingResult:
    if min(resale_price, purchase_price, rehab) < 0 or months <= 0:
        raise ValueError("prices and rehab must be non-negative and months must be positive")
    buyer_closing = purchase_price * assumptions.buyer_closing_pct
    contingency = rehab * assumptions.contingency_pct
    loan = (purchase_price + rehab) * assumptions.loan_to_cost
    financing = loan * (assumptions.annual_interest_rate * months / 12 + assumptions.lender_points_pct)
    holding = assumptions.holding_monthly * months
    selling = resale_price * assumptions.selling_pct
    profit = resale_price - purchase_price - buyer_closing - rehab - contingency - financing - holding - selling - assumptions.permits_and_misc
    cost = purchase_price + buyer_closing + rehab + contingency + financing + holding + assumptions.permits_and_misc
    mao = (resale_price * (1 - assumptions.selling_pct) - rehab * (1 + assumptions.contingency_pct)
           - assumptions.holding_monthly * months - assumptions.permits_and_misc
           - assumptions.minimum_profit) / (1 + assumptions.buyer_closing_pct)
    return UnderwritingResult(purchase_price, resale_price, rehab, months, buyer_closing, contingency,
                              financing, holding, selling, profit, profit / cost if cost else 0.0, max(0.0, mao))
