from datetime import datetime, timezone

from flip_finder.daily import evaluate_listing, should_alert
from flip_finder.normalize import normalize_mongo_listing


def _record(identifier, status, price, date=None, description="fixer needs updating", sqft=1000):
    return {"LISTING_ID": identifier, "MLS_STATUS": status, "LIST_PRICE": price,
            "CLOSE_PRICE": price if status == "SLD" else None, "CLOSE_DATE": date,
            "CITY": "Daly City", "STREET_ADDRESS": f"{identifier} Test", "PROPERTY_TYPE": "Single Family",
            "SQFT": sqft, "details": {"PUBLIC_REMARKS": description}}


def test_daily_screen_produces_disclosed_offer_only_with_three_comps():
    subject = normalize_mongo_listing(_record("subject", "ACTV", 700000))
    sales = [normalize_mongo_listing(_record("a", "SLD", 1400000, "2026-11-01")),
             normalize_mongo_listing(_record("b", "SLD", 1500000, "2026-10-01", sqft=1050)),
             normalize_mongo_listing(_record("c", "SLD", 1300000, "2026-09-01", sqft=950))]
    result = evaluate_listing(subject, sales, {"DALY CITY"})
    assert result["arv"]["status"] == "PRELIMINARY"
    assert result["recommended_max_offer"] is not None
    assert result["offer_status"] == "PRELIMINARY — DO NOT BID"
    assert result["scenarios"]["conservative"]["months"] == 6


def test_daily_screen_never_alerts_when_hard_stop_present():
    subject = normalize_mongo_listing(_record("subject", "ACTV", 700000, description="foundation damage fixer"))
    sales = [normalize_mongo_listing(_record("a", "SLD", 1400000, "2026-11-01")),
             normalize_mongo_listing(_record("b", "SLD", 1500000, "2026-10-01")),
             normalize_mongo_listing(_record("c", "SLD", 1300000, "2026-09-01"))]
    result = evaluate_listing(subject, sales, {"DALY CITY"})
    assert result["rank"]["action"] == "RESEARCH"
    assert not should_alert(result)
