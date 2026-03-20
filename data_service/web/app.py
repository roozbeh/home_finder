#!/usr/bin/env python3
"""Flask web app — conversational home-finder agent + MLS listings browser."""

import os
import uuid
import logging
from datetime import datetime, timezone
from flask import Flask, render_template, request, jsonify
from pymongo import MongoClient
import anthropic

from agentic.agent import run_agent

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

MONGO_URI     = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB      = os.environ.get("MONGO_DB", "mls")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CALENDLY_URL  = "https://calendly.com/ruzbeh-o0w7/new-meeting"
PAGE_SIZE     = 10

app    = Flask(__name__)
mongo  = MongoClient(MONGO_URI, connect=False)
db     = mongo[MONGO_DB]
ai     = anthropic.Anthropic(api_key=ANTHROPIC_KEY) if ANTHROPIC_KEY else None


# ── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def chat_ui():
    return render_template("index.html", calendly_url=CALENDLY_URL)


@app.route("/search")
def search():
    def _int(val, default=None):
        try:
            return int(val)
        except (TypeError, ValueError):
            return default

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

    total       = db.mls_listings.count_documents(mongo_filter)
    skip        = (page - 1) * PAGE_SIZE
    listings    = list(
        db.mls_listings
          .find(mongo_filter, {"_id": 0, "_history": 0})
          .sort("_updated_at", -1)
          .skip(skip)
          .limit(PAGE_SIZE)
    )
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    cities      = sorted(db.mls_listings.distinct("CITY"))

    return render_template(
        "search.html",
        listings=listings, total=total, page=page,
        total_pages=total_pages, cities=cities,
        q=q, city=city, status=status,
        min_price=min_price or "", max_price=max_price or "",
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


@app.route("/api/chat", methods=["POST"])
def api_chat():
    if not ai:
        return jsonify({"error": "ANTHROPIC_API_KEY not configured on server"}), 500

    data       = request.get_json(force=True)
    messages   = data.get("messages", [])
    session_id = data.get("session_id", str(uuid.uuid4()))

    if not messages:
        return jsonify({"error": "No messages provided"}), 400

    result = run_agent(messages, session_id, db, ai)

    if "error" in result:
        return jsonify({"error": result["error"], "session_id": session_id}), 500

    return jsonify({
        "message":    result["message"],
        "listings":   result["listings"],
        "session_id": session_id,
    })


@app.route("/api/feedback", methods=["POST"])
def api_feedback():
    data = request.get_json(force=True)
    db.listing_feedback.insert_one({
        "listing_id": data.get("listing_id"),
        "feedback":   data.get("feedback"),   # "good" or "bad"
        "user_id":    data.get("user_id", ""),
        "session_id": data.get("session_id", ""),
        "timestamp":  datetime.now(timezone.utc),
    })
    return jsonify({"ok": True})


@app.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    data  = request.get_json(force=True)
    name  = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    if not name or not email:
        return jsonify({"error": "name and email are required"}), 400

    user = db.users.find_one({"email": email})
    if user is None:
        user_id = str(uuid.uuid4())
        db.users.insert_one({
            "user_id":    user_id,
            "name":       name,
            "email":      email,
            "created_at": datetime.now(timezone.utc),
        })
    else:
        user_id = user["user_id"]
        db.users.update_one({"email": email}, {"$set": {"name": name}})

    return jsonify({"user_id": user_id, "name": name, "email": email})


@app.route("/api/auth/me", methods=["GET"])
def api_auth_me():
    user_id = request.args.get("user_id", "")
    if not user_id:
        return jsonify({"error": "user_id required"}), 400
    user = db.users.find_one({"user_id": user_id}, {"_id": 0, "created_at": 0})
    if not user:
        return jsonify({"error": "not found"}), 404
    return jsonify(user)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
