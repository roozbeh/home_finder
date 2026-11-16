#!/usr/bin/env python3
"""Save one reviewed visual-cost assessment for a FlipDB pair.

This intentionally makes no model/API calls.  It is a transparent bridge for
reviewed demo insights while the bounded automated assessor is designed.
"""
from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone

from pymongo import MongoClient


def number(value: object, default: float = 0) -> float:
    try:
        return float(str(value).replace("$", "").replace(",", ""))
    except (TypeError, ValueError):
        return default


def money(value: float) -> str:
    return f"${value:,.0f}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Save a reviewed Flip Finder insight.")
    parser.add_argument("--pair-key", required=True)
    parser.add_argument("--renovation-low", type=float, required=True)
    parser.add_argument("--renovation-high", type=float, required=True)
    parser.add_argument("--interest-rate", type=float, default=0.10)
    parser.add_argument("--property-tax-rate", type=float, default=0.011)
    parser.add_argument("--purchase-closing-rate", type=float, default=0.01)
    parser.add_argument("--sale-closing-rate", type=float, default=0.065)
    args = parser.parse_args()
    if args.renovation_low < 0 or args.renovation_high < args.renovation_low:
        parser.error("renovation values must be non-negative and ordered low to high")

    client = MongoClient(os.environ["MONGO_URI"])
    source_db = client[os.environ.get("MONGO_DB", "mls")]
    result_db = client[os.environ.get("FLIPDB_DATABASE", "flip_finder")]
    flip = result_db.flipdb.find_one({"pair_key": args.pair_key, "active": True}, {"_id": 0})
    if not flip:
        parser.error("active FlipDB pair was not found")
    purchase = source_db.mls_listings.find_one({"LISTING_ID": flip["buy_mls"]}, {"_id": 0, "details.HOA_FEE": 1}) or {}
    hoa_monthly = number((purchase.get("details") or {}).get("HOA_FEE"))
    purchase_price = number(flip.get("buy_price"))
    sale_price = number(flip.get("sell_price"))
    hold_days = number(flip.get("hold_days"))
    renovation_midpoint = (args.renovation_low + args.renovation_high) / 2
    interest = purchase_price * args.interest_rate * hold_days / 365
    property_tax = purchase_price * args.property_tax_rate * hold_days / 365
    hoa = hoa_monthly * 12 * hold_days / 365
    purchase_closing = purchase_price * args.purchase_closing_rate
    sale_closing = sale_price * args.sale_closing_rate
    fixed_costs = purchase_price + purchase_closing + interest + property_tax + hoa + sale_closing

    def result(renovation: float) -> float:
        return sale_price - fixed_costs - renovation

    insight = {
        "pair_key": args.pair_key,
        "schema_version": 1,
        "method": "reviewed_demo_from_cached_photo_labels_and_mls",
        "created_at": datetime.now(timezone.utc),
        "confidence": "LOW",
        "summary": (
            "The stored purchase/resale MLS photos and remarks support a moderate interior "
            "turnover estimate, centered on the kitchen, flooring, and paint. This is a "
            "reviewed visual estimate, not a reconstruction of the seller's actual invoices."
        ),
        "scope": "Moderate interior turnover / refit",
        "scope_note": "No photo evidence confirms structural work, layout changes, or major systems upgrades.",
        "observations": [
            {"area": "Kitchen", "detail": "Visible finish, appliance, backsplash, counter, flooring, and lighting changes."},
            {"area": "Living areas", "detail": "Purchase sample shows tile and dated furnishings; resale remarks identify fresh paint and laminate flooring."},
            {"area": "Bedrooms", "detail": "Photos support neutral repainting and refreshed finishes, not a full-room remodel."},
            {"area": "Bathroom", "detail": "Resale bath appears refreshed, but no comparable purchase image confirms the timing or scope."},
            {"area": "Exterior", "detail": "No material exterior overhaul is evident in the sampled photos."},
        ],
        "financials": {
            "lines": [
                {"label": "Property purchased", "amount": -purchase_price},
                {"label": "Purchase closing costs", "amount": -purchase_closing, "note": f"{args.purchase_closing_rate:.1%} of purchase price"},
                {"label": "Cost of renovations", "amount": -renovation_midpoint, "range": f"{money(args.renovation_low)}–{money(args.renovation_high)}"},
                {"label": "Cost of money", "amount": -interest, "note": f"{args.interest_rate:.1%} annual rate × {hold_days:.0f} days"},
                {"label": "Property tax", "amount": -property_tax, "note": f"{args.property_tax_rate:.1%} annual estimate × {hold_days:.0f} days"},
                {"label": "HOA carrying cost", "amount": -hoa, "note": f"{money(hoa_monthly)}/month × {hold_days:.0f} days"},
                {"label": "Sale closing costs", "amount": -sale_closing, "note": f"{args.sale_closing_rate:.1%} of sale price"},
                {"label": "Sold price", "amount": sale_price},
            ],
            "result_midpoint": result(renovation_midpoint),
            "result_range": f"Range with renovation estimate: {money(result(args.renovation_high))} to {money(result(args.renovation_low))}",
        },
        "assumptions": [
            f"{hold_days:.0f}-day hold from the stored FlipDB record.",
            f"Purchase price finances at {args.interest_rate:.1%} simple annual interest for the full hold.",
            f"Property tax uses {args.property_tax_rate:.1%} of purchase price, prorated for the hold; parcel tax, bonds, and supplemental bills may differ.",
            f"HOA uses the purchase MLS fee of {money(hoa_monthly)} per month, prorated for the hold.",
            f"Closing estimates use {args.purchase_closing_rate:.1%} on purchase and {args.sale_closing_rate:.1%} on resale; actual escrow, title, commissions, and concessions may differ.",
            "Renovation range estimates visible work only and does not prove actual spend.",
        ],
        "disclaimer": (
            "Estimated all-in result only. Excludes insurance, utilities, staging, marketing, special assessments, "
            "income taxes, and unobserved work; it is not actual spend, a settlement statement, or net profit."
        ),
    }
    result_db.renovation_insights.update_one({"pair_key": args.pair_key}, {"$set": insight}, upsert=True)
    client.close()
    print(f"Saved reviewed insight for {args.pair_key}: midpoint {money(result(renovation_midpoint))}.")


if __name__ == "__main__":
    main()
