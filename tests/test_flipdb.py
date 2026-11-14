from datetime import datetime, timezone

from flip_finder.flipdb import pair_key, pair_to_flipdb_record
from flip_finder.flips import identify_short_hold_resales
from flip_finder.normalize import normalize_mongo_listing


def _closed(identifier, price, date):
    return normalize_mongo_listing({
        "LISTING_ID": identifier,
        "MLS_STATUS": "SLD",
        "CLOSE_PRICE": price,
        "CLOSE_DATE": date,
        "CITY": "San Mateo",
        "STREET_ADDRESS": "1 Test Avenue",
        "POSTAL_CODE": "94401",
        "APN": "123-456-789",
    })


def test_flipdb_record_preserves_pair_provenance_without_claiming_a_verified_flip():
    pair = identify_short_hold_resales([
        _closed("buy-mls", 800_000, "01/01/2025"),
        _closed("sell-mls", 1_000_000, "07/01/2025"),
    ])[0]
    record = pair_to_flipdb_record(
        pair, "mls", "mls_listings", datetime(2026, 1, 1, tzinfo=timezone.utc), 365,
    )

    assert record["record_type"] == "observed_short_hold_resale"
    assert record["is_verified_flip"] is False
    assert record["buy_mls"] == "buy-mls"
    assert record["sell_mls"] == "sell-mls"
    assert record["buy_price"] == 800_000
    assert record["sell_price"] == 1_000_000
    assert record["gross_roi"] == 0.25
    assert record["source"]["database"] == "mls"
    assert record["pair_key"] == pair_key(pair)
    assert "unverified" in record["disclaimer"].lower()
