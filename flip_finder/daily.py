"""Daily, provenance-preserving flip screening.

This module deliberately uses deterministic rules and explicit underwriting
assumptions.  It is a screening aid, not a valuation, offer, or automated
trading system.  It reads MLS records and writes its own results only to the
separate ``flip_finder`` database.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import smtplib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any

from pymongo import MongoClient

from .candidates import CandidateScreen, screen_listing
from .comps import ArvResult, estimate_arv
from .normalize import Listing, canonical_city, normalize_mongo_listing
from .rank import RankResult, rank
from .rehab import RehabEstimate, estimate_rehab
from .underwrite import UnderwritingAssumptions, UnderwritingResult, underwrite


RESULT_DATABASE = "flip_finder"
ALERT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ScenarioSet:
    conservative: UnderwritingResult | None
    base: UnderwritingResult | None
    optimistic: UnderwritingResult | None


def _scenario_dict(value: UnderwritingResult | None) -> dict[str, Any] | None:
    return asdict(value) if value else None


def build_scenarios(arv: ArvResult, rehab: RehabEstimate, asking_price: float | None) -> ScenarioSet:
    """Create disclosed scenarios.  No scenario is created without an ARV."""
    if asking_price is None or arv.arv_low is None or arv.arv_base is None or arv.arv_high is None:
        return ScenarioSet(None, None, None)
    # Assumptions are intentionally conservative for an initial Bay Area screen;
    # operators must replace them with project-specific lender/broker quotes.
    financing = dict(annual_interest_rate=0.10, loan_to_cost=0.70, lender_points_pct=0.01)
    conservative = underwrite(
        arv.arv_low, asking_price, rehab.high, 6,
        UnderwritingAssumptions(holding_monthly=5_000, permits_and_misc=15_000,
                               contingency_pct=0.25, minimum_profit=125_000, **financing),
    )
    base = underwrite(
        arv.arv_base, asking_price, rehab.base, 4,
        UnderwritingAssumptions(holding_monthly=4_000, permits_and_misc=12_000,
                               contingency_pct=0.20, minimum_profit=100_000, **financing),
    )
    optimistic = underwrite(
        arv.arv_high, asking_price, rehab.low, 3,
        UnderwritingAssumptions(holding_monthly=3_000, permits_and_misc=8_000,
                               contingency_pct=0.15, minimum_profit=75_000, **financing),
    )
    return ScenarioSet(conservative, base, optimistic)


def evaluate_listing(subject: Listing, sales: list[Listing], allowed_cities: set[str]) -> dict[str, Any]:
    """Pure evaluation used by both the runner and tests."""
    screen: CandidateScreen = screen_listing(subject, cities=allowed_cities)
    rehab = estimate_rehab(subject)
    arv = estimate_arv(subject, sales)
    scenarios = build_scenarios(arv, rehab, subject.list_price) if screen.eligible else ScenarioSet(None, None, None)
    result_rank: RankResult = rank(arv, rehab, scenarios.base, scenarios.conservative)
    offer = scenarios.conservative.mao if scenarios.conservative else None
    reasons = list(screen.reasons) + list(result_rank.reasons)
    return {
        "schema_version": ALERT_SCHEMA_VERSION,
        "listing_id": subject.listing_id,
        "source": subject.source,
        "source_id": subject.source_id,
        "observed_at": datetime.now(timezone.utc),
        "listing": subject.to_dict(),
        "screen": {
            "eligible": screen.eligible, "action": screen.action,
            "reasons": list(screen.reasons),
            "opportunity_evidence": list(screen.opportunity_evidence),
            "risk_evidence": list(screen.risk_evidence),
            "signals": [asdict(signal) for signal in screen.signals],
        },
        "arv": {
            "status": arv.status, "reason": arv.reason,
            "arv_low": arv.arv_low, "arv_base": arv.arv_base, "arv_high": arv.arv_high,
            "confidence": arv.confidence, "comps": [asdict(comp) for comp in arv.comps],
        },
        "rehab": asdict(rehab),
        "scenarios": {"conservative": _scenario_dict(scenarios.conservative),
                      "base": _scenario_dict(scenarios.base), "optimistic": _scenario_dict(scenarios.optimistic)},
        "recommended_max_offer": offer,
        "offer_status": "PRELIMINARY — DO NOT BID" if offer is not None else "NOT_CALCULATED",
        "rank": {"action": result_rank.action, "score": result_rank.score,
                 "reasons": reasons},
        "disclaimer": "Screening estimate only; verify condition, permits, title, hazards, financing, and closed comps before any offer.",
    }


def _alert_fingerprint(result: dict[str, Any]) -> str:
    rank_data = result["rank"]
    arv = result["arv"]
    payload = "|".join(str(value) for value in (
        result["listing_id"], rank_data["action"], rank_data["score"],
        result["recommended_max_offer"], arv["arv_base"], arv["confidence"],
    ))
    return hashlib.sha256(payload.encode()).hexdigest()


def should_alert(result: dict[str, Any]) -> bool:
    """Only notification-grade candidates can create an alert."""
    return result["rank"]["action"] == "VISIT" and result["arv"]["status"] == "PRELIMINARY"


def build_alert_email(results: list[dict[str, Any]], recipient: str, sender: str) -> EmailMessage:
    msg = EmailMessage()
    msg["To"] = recipient
    msg["From"] = sender
    msg["Subject"] = f"Flip Finder: {len(results)} preliminary visit candidate(s)"
    rows = []
    for item in results:
        listing = item["listing"]
        rows.append(
            f"{listing.get('full_address') or item['listing_id']} | ask ${listing.get('list_price', 0):,.0f} | "
            f"ARV ${item['arv']['arv_base']:,.0f} | max offer ${item['recommended_max_offer']:,.0f} | "
            f"{item['arv']['confidence']} confidence"
        )
    msg.set_content(
        "New preliminary screening candidates:\n\n" + "\n".join(rows) +
        "\n\nThese are screening estimates, not appraisals or recommendations to bid. "
        "Review condition, permits, title, hazards, financing, and all comps before acting."
    )
    return msg


def send_email_if_configured(results: list[dict[str, Any]]) -> None:
    """Send through a configured SMTP relay; credentials are environment-only."""
    host, recipient, sender = (os.getenv("FLIP_ALERT_SMTP_HOST"), os.getenv("FLIP_ALERT_TO"), os.getenv("FLIP_ALERT_FROM"))
    if not all((host, recipient, sender)):
        raise RuntimeError("email not configured: set FLIP_ALERT_SMTP_HOST, FLIP_ALERT_TO, and FLIP_ALERT_FROM")
    port = int(os.getenv("FLIP_ALERT_SMTP_PORT", "587"))
    user, password = os.getenv("FLIP_ALERT_SMTP_USER"), os.getenv("FLIP_ALERT_SMTP_PASSWORD")
    msg = build_alert_email(results, recipient, sender)
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.starttls()
        if user and password:
            smtp.login(user, password)
        smtp.send_message(msg)


def run(uri: str, mls_database: str, collection: str, result_database: str,
        write: bool, send_alerts: bool) -> tuple[int, int]:
    client = MongoClient(uri)
    try:
        source = client[mls_database][collection]
        raw_sales = list(source.find({"MLS_STATUS": "SLD"}))
        sales = [normalize_mongo_listing(row) for row in raw_sales]
        raw_active = list(source.find({"MLS_STATUS": "ACTV"}))
        subjects = [normalize_mongo_listing(row) for row in raw_active]
        allowed_cities = {canonical_city(row.get("CITY")) for row in raw_active}
        allowed_cities.discard(None)
        evaluations = [evaluate_listing(subject, sales, allowed_cities) for subject in subjects]
        alerts = [item for item in evaluations if should_alert(item)]
        if not write:
            print(f"Dry run: evaluated {len(evaluations)} active records; {len(alerts)} notification-grade candidates.")
            return len(evaluations), len(alerts)

        db = client[result_database]
        db.candidate_snapshots.create_index([("listing_id", 1)], unique=True)
        db.alerts.create_index([("fingerprint", 1)], unique=True)
        now = datetime.now(timezone.utc)
        run_doc = {"started_at": now, "source_database": mls_database, "source_collection": collection,
                   "active_evaluated": len(evaluations), "alerts_considered": len(alerts), "send_alerts": send_alerts}
        db.runs.insert_one(run_doc)
        for item in evaluations:
            db.candidate_snapshots.update_one({"listing_id": item["listing_id"]}, {"$set": item}, upsert=True)
        new_alerts: list[dict[str, Any]] = []
        for item in alerts:
            fingerprint = _alert_fingerprint(item)
            try:
                db.alerts.insert_one({"fingerprint": fingerprint, "created_at": now,
                                      "listing_id": item["listing_id"], "status": "queued", "candidate": item})
                new_alerts.append(item)
            except Exception as exc:
                # Duplicate-key is expected deduplication; do not hide other DB errors.
                if "E11000" not in str(exc):
                    raise
        if send_alerts and new_alerts:
            send_email_if_configured(new_alerts)
            db.alerts.update_many({"fingerprint": {"$in": [_alert_fingerprint(x) for x in new_alerts]}},
                                  {"$set": {"status": "sent", "sent_at": datetime.now(timezone.utc)}})
        print(f"Stored {len(evaluations)} evaluations; {len(new_alerts)} new alert(s).")
        return len(evaluations), len(new_alerts)
    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the conservative daily flip screen.")
    parser.add_argument("--database", default=os.getenv("MONGO_DB", "mls"))
    parser.add_argument("--collection", default=os.getenv("MONGO_COLLECTION", "mls_listings"))
    parser.add_argument("--result-database", default=os.getenv("FLIP_FINDER_DB", RESULT_DATABASE))
    parser.add_argument("--write", action="store_true", help="store results in the separate result database")
    parser.add_argument("--send-alerts", action="store_true", help="send only new, notification-grade candidates through configured SMTP")
    args = parser.parse_args()
    if args.send_alerts and not args.write:
        parser.error("--send-alerts requires --write so alerts can be deduplicated")
    uri = os.getenv("MONGODB_URI") or os.getenv("MONGO_URI")
    if not uri:
        parser.error("Set MONGODB_URI or MONGO_URI in the environment")
    run(uri, args.database, args.collection, args.result_database, args.write, args.send_alerts)


if __name__ == "__main__":
    main()
