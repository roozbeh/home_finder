from flip_finder.candidates import screen_listing
from flip_finder.normalize import normalize_mongo_listing


def test_candidate_requires_verified_active_status_and_detached_type():
    listing = normalize_mongo_listing({"LISTING_ID": "x", "MLS_STATUS": "ACTV", "LIST_PRICE": "999000", "CITY": "Daly City", "STREET_ADDRESS": "1 A St", "PROPERTY_TYPE": "Single Family Residence", "details": {"PUBLIC_REMARKS": "Original condition; needs updating."}})
    result = screen_listing(listing)
    assert result.eligible
    assert "original condition" in result.opportunity_evidence


def test_hard_stop_text_requires_manual_review():
    listing = normalize_mongo_listing({"LISTING_ID": "x", "MLS_STATUS": "ACTV", "LIST_PRICE": "999000", "CITY": "Daly City", "STREET_ADDRESS": "1 A St", "PROPERTY_TYPE": "Single Family", "details": {"PUBLIC_REMARKS": "Tenant occupied, foundation issue."}})
    result = screen_listing(listing)
    assert result.action == "MANUAL_REVIEW"
    assert not result.eligible
