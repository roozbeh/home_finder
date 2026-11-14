from flip_finder.normalize import canonical_city, canonical_status, normalize_mongo_listing, parse_number


def test_numeric_parser_does_not_impute_missing_or_invalid_values():
    assert parse_number("$1,234,500") == 1234500.0
    assert parse_number(None) is None
    assert parse_number("unknown") is None


def test_normalizes_sale_without_mixing_list_and_sale_price():
    listing = normalize_mongo_listing({"LISTING_ID": "1", "MLS_STATUS": "SLD", "LIST_PRICE": "$900,000", "CLOSE_PRICE": "950000", "CLOSE_DATE": "10/01/2026", "CITY": "Daly City", "STREET_ADDRESS": "1 Test Way"})
    assert listing.list_price == 900000
    assert listing.sale_price == 950000
    assert listing.status_canonical == "CLOSED"


def test_unknown_status_is_not_active():
    assert canonical_status("BOMK") == "UNKNOWN"


def test_known_source_city_abbreviations_are_normalized():
    assert canonical_city("REDWOODCIY") == "REDWOOD CITY"
    assert canonical_city("EASTPAALTO") == "EAST PALO ALTO"
    assert canonical_city("PORTO") == "PORTOLA VALLEY"
    assert canonical_city("SOUTH SAN") == "SOUTH SAN FRANCISCO"
    assert canonical_city("AMERICNYON") == "AMERICAN CANYON"
