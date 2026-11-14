from datetime import datetime, timezone
from flip_finder.rehab import estimate_rehab
from flip_finder.rank import rank
from flip_finder.comps import ArvResult
from flip_finder.normalize import normalize_mongo_listing

def test_hard_stop_rehab_is_manual_only_and_ranking_is_blocked():
    listing = normalize_mongo_listing({"LISTING_ID":"x","MLS_STATUS":"ACTV","LIST_PRICE":900000,"CITY":"Daly City","STREET_ADDRESS":"1 A","PROPERTY_TYPE":"Single Family","SQFT":1000,"details":{"PUBLIC_REMARKS":"foundation issue"}})
    rehab = estimate_rehab(listing)
    assert rehab.scope == "MANUAL_ONLY"
    assert rank(ArvResult("UNVERIFIED — DO NOT BID", "no comps", (), None, None, None, "LOW"), rehab, None, None).score is None
