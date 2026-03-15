#!/usr/bin/env python3
"""Flask web app for browsing MLS listings stored in MongoDB."""

import os
from flask import Flask, render_template, request
from pymongo import MongoClient

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB  = os.environ.get("MONGO_DB", "mls")
PAGE_SIZE = 10

app    = Flask(__name__)
client = MongoClient(MONGO_URI)
db     = client[MONGO_DB]


def _int(val, default=None):
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


@app.route("/")
def index():
    q         = request.args.get("q", "").strip()
    city      = request.args.get("city", "").strip().upper()
    status    = request.args.get("status", "").strip()
    min_price = _int(request.args.get("min_price"))
    max_price = _int(request.args.get("max_price"))
    min_beds  = _int(request.args.get("min_beds"))
    page      = max(1, _int(request.args.get("page", "1"), 1))

    mongo_filter = {}

    if q:
        mongo_filter["$or"] = [
            {"STREET_ADDRESS": {"$regex": q, "$options": "i"}},
            {"LISTING_ID":     {"$regex": q, "$options": "i"}},
            {"CITY":           {"$regex": q, "$options": "i"}},
        ]
    if city:
        mongo_filter["CITY"] = city
    if status:
        mongo_filter["MLS_STATUS"] = status
    if min_price is not None:
        mongo_filter.setdefault("LIST_PRICE", {})["$gte"] = min_price
    if max_price is not None:
        mongo_filter.setdefault("LIST_PRICE", {})["$lte"] = max_price
    if min_beds is not None:
        mongo_filter["BEDROOMS_TOTAL"] = {"$gte": min_beds}

    total        = db.mls_listings.count_documents(mongo_filter)
    skip         = (page - 1) * PAGE_SIZE
    listings     = list(
        db.mls_listings
          .find(mongo_filter, {"_id": 0, "_history": 0})
          .sort("_updated_at", -1)
          .skip(skip)
          .limit(PAGE_SIZE)
    )
    total_pages  = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    # Distinct cities for the filter dropdown
    cities = sorted(db.mls_listings.distinct("CITY"))

    return render_template(
        "index.html",
        listings=listings,
        total=total,
        page=page,
        total_pages=total_pages,
        cities=cities,
        q=q,
        city=city,
        status=status,
        min_price=min_price or "",
        max_price=max_price or "",
        min_beds=min_beds or "",
    )


@app.route("/listing/<listing_id>")
def listing_detail(listing_id):
    doc = db.mls_listings.find_one({"LISTING_ID": listing_id}, {"_id": 0})
    if not doc:
        return "Listing not found", 404
    history = doc.pop("_history", [])
    history = sorted(history, key=lambda x: x.get("timestamp", ""), reverse=True)
    return render_template("detail.html", listing=doc, history=history)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
