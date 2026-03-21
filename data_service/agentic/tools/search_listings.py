"""search_listings tool — queries mls_listings in MongoDB."""


def search_listings(args: dict, db):
    """
    Returns (summary_text, listings_list).
    listings_list is a list of dicts suitable for JSON serialization.
    """
    filt = {"MLS_STATUS": {"$in": ["ACTV", "NEW", "AC", "PCH", "CS", "BOMK"]}}

    cities = [c.upper() for c in args.get("cities", []) if c]
    if cities:
        filt["CITY"] = {"$in": cities}

    price = {}
    if args.get("min_price"):
        price["$gte"] = float(args["min_price"])
    if args.get("max_price"):
        price["$lte"] = float(args["max_price"])
    if price:
        filt["LIST_PRICE"] = price

    beds = {}
    if args.get("min_beds"):
        beds["$gte"] = int(args["min_beds"])
    if args.get("max_beds"):
        beds["$lte"] = int(args["max_beds"])
    if beds:
        filt["BEDROOMS_TOTAL"] = beds

    sqft = {}
    if args.get("min_sqft"):
        sqft["$gte"] = float(args["min_sqft"])
    if args.get("max_sqft"):
        sqft["$lte"] = float(args["max_sqft"])
    if sqft:
        filt["SQFT"] = sqft

    limit = min(int(args.get("limit", 5)), 8)
    skip_fields = {
        "_id": 0, "_history": 0,
        "SOURCE_MLS_CIRCLE": 0,
        "TOOLS": 0, "LPHOTOS": 0,
        "details": 0,            # large nested blob — not needed in chat cards
        "details_fetched": 0,
    }

    results = list(
        db.mls_listings
          .find(filt, skip_fields)
          .sort("_updated_at", -1)
          .limit(limit)
    )

    if not results:
        return (
            "No listings found matching those criteria. "
            "Try expanding the search (wider price range or more cities).",
            [],
        )

    # Serialize datetimes; expose TINYPROPPHOTO_ONELINE as thumbphoto
    listings = []
    for r in results:
        for k, v in list(r.items()):
            if hasattr(v, "strftime"):
                r[k] = v.strftime("%Y-%m-%d")
        if not r.get("thumbphoto") and r.get("TINYPROPPHOTO_ONELINE"):
            r["thumbphoto"] = r["TINYPROPPHOTO_ONELINE"]
        r.pop("TINYPROPPHOTO_ONELINE", None)
        listings.append(r)

    summary = f"Found {len(listings)} listing(s):\n"
    for listing in listings:
        price_str = f"${listing.get('LIST_PRICE', 0):,.0f}" if listing.get("LIST_PRICE") else "N/A"
        beds_val  = listing.get("BEDROOMS_TOTAL", "?")
        baths_val = listing.get("BATHS_DISPLAY") or listing.get("BATHROOMS_FULL", "?")
        sqft_val  = f"{listing.get('SQFT', 0):,.0f} sqft" if listing.get("SQFT") else ""
        addr      = listing.get("STREET_ADDRESS", "Unknown")
        city      = listing.get("CITY", "").title()
        summary += (
            f"- {addr}, {city} | {price_str} | "
            f"{beds_val}bd/{baths_val}ba {sqft_val} | "
            f"{listing.get('MLS_STATUS', '?')} | ID:{listing.get('LISTING_ID', '?')}\n"
        )

    return summary, listings
