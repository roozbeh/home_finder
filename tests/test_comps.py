from datetime import datetime, timezone

from flip_finder.comps import estimate_arv
from flip_finder.normalize import normalize_mongo_listing


def listing(identifier, status, price, date, sqft=1000, city="Daly City"):
    return normalize_mongo_listing({"LISTING_ID": identifier, "MLS_STATUS": status, "LIST_PRICE": price,
        "CLOSE_PRICE": price if status == "SLD" else None, "CLOSE_DATE": date if status == "SLD" else None,
        "CITY": city, "STREET_ADDRESS": "1 Test", "PROPERTY_TYPE": "Single Family", "SQFT": sqft})


def test_arv_requires_at_least_three_closed_comps():
    subject = listing("subject", "ACTV", 900000, None)
    result = estimate_arv(subject, [listing("one", "SLD", 1000000, "2026-10-01"), listing("two", "SLD", 1100000, "2026-09-01")], datetime(2026, 10, 25, tzinfo=timezone.utc))
    assert result.status.startswith("UNVERIFIED")
    assert result.arv_base is None


def test_arv_uses_only_recent_same_city_similar_size_closed_sales():
    subject = listing("subject", "ACTV", 900000, None)
    sales = [listing("a", "SLD", 1000000, "2026-10-01"), listing("b", "SLD", 1100000, "2026-09-01", 1100), listing("c", "SLD", 900000, "2026-08-01", 900), listing("other", "SLD", 2000000, "2026-10-01", 1000, "Pacifica")]
    result = estimate_arv(subject, sales, datetime(2026, 10, 25, tzinfo=timezone.utc))
    assert result.status == "PRELIMINARY"
    assert len(result.comps) == 3
