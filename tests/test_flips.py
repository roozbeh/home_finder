from datetime import datetime, timezone

from flip_finder.flips import identify_short_hold_resales, summarize_flip_markets
from flip_finder.normalize import normalize_mongo_listing


def _sold(identifier, price, date, apn="123-456-789", city="Daly City", subdivision="Westlake"):
    return normalize_mongo_listing({
        "LISTING_ID": identifier, "MLS_STATUS": "SLD", "CLOSE_PRICE": price,
        "CLOSE_DATE": date, "CITY": city, "STREET_ADDRESS": "1 Test Avenue",
        "POSTAL_CODE": "94015", "APN": apn, "SUBDIVISION_NAME": subdivision,
        "PROPERTY_TYPE": "Single Family", "SQFT": 1000,
    })


def test_short_hold_pair_is_observed_gross_spread_not_flip_profit():
    pairs = identify_short_hold_resales([
        _sold("purchase", 800000, "01/01/2025"),
        _sold("resale", 1000000, "07/01/2025"),
    ])
    assert len(pairs) == 1
    pair = pairs[0]
    assert pair.match_method == "apn"
    assert pair.hold_days == 181
    assert pair.gross_spread == 200000
    assert pair.gross_roi == 0.25
    assert "costs" in pair.caution


def test_address_fallback_is_low_confidence_and_short_holds_are_flagged():
    pairs = identify_short_hold_resales([
        _sold("purchase", 800000, "01/01/2025", apn=""),
        _sold("resale", 900000, "01/20/2025", apn=""),
    ])
    assert len(pairs) == 1
    assert pairs[0].match_method == "address"
    assert pairs[0].confidence == "LOW"
    assert "deed/transaction review" in pairs[0].caution


def test_detail_parcel_number_is_used_as_the_preferred_property_key():
    listing = normalize_mongo_listing({
        "LISTING_ID": "parcel", "MLS_STATUS": "SLD", "CLOSE_PRICE": 900000,
        "CLOSE_DATE": "01/20/2025", "CITY": "Daly City", "STREET_ADDRESS": "1 Test Avenue",
        "details": {"PARCEL_NUMBER": "123-456-789"},
    })
    assert listing.apn == "123-456-789"


def test_market_summary_requires_minimum_sample_and_never_invents_neighborhood():
    pairs = tuple(
        identify_short_hold_resales([
            _sold(f"p{i}", 800000, "01/01/2025", apn=f"A{i}"),
            _sold(f"r{i}", 1000000, "07/01/2025", apn=f"A{i}"),
        ])[0]
        for i in range(3)
    )
    rows = summarize_flip_markets(pairs, by="city")
    assert rows[0].geography == "DALY CITY"
    assert rows[0].pair_count == 3
    assert rows[0].median_gross_roi == 0.25
