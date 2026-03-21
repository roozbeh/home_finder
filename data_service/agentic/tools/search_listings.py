"""search_listings tool — queries mls_listings in MongoDB."""

import re
import logging
log = logging.getLogger(__name__)


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

    # Serialize datetimes; resolve thumbphoto from available fields
    listings = []
    for r in results:
        for k, v in list(r.items()):
            if hasattr(v, "strftime"):
                r[k] = v.strftime("%Y-%m-%d")

        # Resolve thumbnail URL — prefer photos[0], then TINYPROPPHOTO_ONELINE
        if not r.get("thumbphoto"):
            photos = r.get("photos", [])
            tiny   = r.get("TINYPROPPHOTO_ONELINE", "")
            if photos:
                r["thumbphoto"] = photos[0]
            elif tiny:
                # TINYPROPPHOTO_ONELINE may be a plain URL or an HTML <img> tag
                if tiny.strip().startswith("<"):
                    m = re.search(r'src=["\']([^"\']+)["\']', tiny)
                    r["thumbphoto"] = m.group(1) if m else ""
                else:
                    r["thumbphoto"] = tiny

        # Log the first listing so we can see what fields look like
        if not listings:
            log.info("[search_listings] sample thumb fields — "
                     "thumbphoto=%r  TINYPROPPHOTO_ONELINE=%r  photos_count=%d",
                     r.get("thumbphoto", "")[:80] if r.get("thumbphoto") else None,
                     r.get("TINYPROPPHOTO_ONELINE", "")[:80] if r.get("TINYPROPPHOTO_ONELINE") else None,
                     len(r.get("photos", [])))

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
