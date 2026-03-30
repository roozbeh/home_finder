#!/usr/bin/env python3
"""Flask web app — conversational home-finder agent + MLS listings browser."""

import os
import uuid
import time
import logging
from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import Flask, render_template, request, jsonify, Response, stream_with_context, session, redirect
from pymongo import MongoClient
import anthropic

from agentic.agent import run_agent, run_agent_streaming

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

MONGO_URI      = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB       = os.environ.get("MONGO_DB", "mls")
ANTHROPIC_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
CALENDLY_URL   = "https://calendly.com/ruzbeh-o0w7/new-meeting"
PAGE_SIZE      = 10
SECRET_KEY     = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-production")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme")

app            = Flask(__name__)
app.secret_key = SECRET_KEY
mongo          = MongoClient(MONGO_URI, connect=False)
db             = mongo[MONGO_DB]
ai             = anthropic.Anthropic(api_key=ANTHROPIC_KEY) if ANTHROPIC_KEY else None


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect("/admin/login")
        return f(*args, **kwargs)
    return decorated


# ── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def chat_ui():
    return render_template("index.html", calendly_url=CALENDLY_URL)


@app.route("/about")
def about():
    return render_template("about.html")


@app.route("/support")
def support():
    return render_template("support.html")


@app.route("/privacy_policy")
def privacy_policy():
    return render_template("privacy_policy.html")


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

    t0         = time.time()
    data       = request.get_json(force=True)
    messages   = data.get("messages", [])
    session_id = data.get("session_id", str(uuid.uuid4()))
    user_id    = data.get("user_id", "")

    logging.info("[api/chat] received  session=%s  messages=%d  user_id=%s",
                 session_id, len(messages), user_id or "anon")

    if not messages:
        return jsonify({"error": "No messages provided"}), 400

    result = run_agent(messages, session_id, db, ai)
    logging.info("[api/chat] completed  session=%s  elapsed=%.2fs  error=%s",
                 session_id, time.time() - t0, "error" in result)

    if "error" in result:
        return jsonify({"error": result["error"], "session_id": session_id}), 500

    # Persist conversation to chat_sessions
    assistant_entry = {"role": "assistant", "content": result["message"], "listings": result["listings"]}
    full_messages   = list(messages) + [assistant_entry]
    first_user_text = next((m.get("content", "") for m in messages if m.get("role") == "user"), "")
    title           = (first_user_text[:60] + "…") if len(first_user_text) > 60 else first_user_text
    now             = datetime.now(timezone.utc)
    db.chat_sessions.update_one(
        {"session_id": session_id},
        {
            "$set":         {"messages": full_messages, "user_id": user_id, "updated_at": now},
            "$setOnInsert": {"title": title, "created_at": now},
        },
        upsert=True,
    )

    return jsonify({
        "message":    result["message"],
        "listings":   result["listings"],
        "session_id": session_id,
    })


@app.route("/api/chat/stream", methods=["POST"])
def api_chat_stream():
    if not ai:
        return jsonify({"error": "ANTHROPIC_API_KEY not configured on server"}), 500

    data       = request.get_json(force=True)
    messages   = data.get("messages", [])
    session_id = data.get("session_id", str(uuid.uuid4()))
    user_id    = data.get("user_id", "")

    if not messages:
        return jsonify({"error": "No messages provided"}), 400

    logging.info("[api/chat/stream] session=%s  messages=%d", session_id, len(messages))

    def generate():
        full_text = ""
        listings  = []
        import json as _json

        for chunk in run_agent_streaming(messages, session_id, db, ai):
            yield chunk
            # Parse to track final state for session persistence
            try:
                obj = _json.loads(chunk[len("data: "):].strip())
                if obj.get("type") == "done":
                    full_text = obj.get("full_text", "")
                    listings  = obj.get("listings", [])
            except Exception:
                pass

        # Persist the conversation after streaming completes
        if full_text:
            assistant_entry = {"role": "assistant", "content": full_text, "listings": listings}
            full_messages   = list(messages) + [assistant_entry]
            first_user_text = next((m.get("content", "") for m in messages if m.get("role") == "user"), "")
            title           = (first_user_text[:60] + "…") if len(first_user_text) > 60 else first_user_text
            now             = datetime.now(timezone.utc)
            db.chat_sessions.update_one(
                {"session_id": session_id},
                {
                    "$set":         {"messages": full_messages, "user_id": user_id, "updated_at": now},
                    "$setOnInsert": {"title": title, "created_at": now},
                },
                upsert=True,
            )

    resp = Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
    )
    resp.headers["Cache-Control"]    = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


@app.route("/api/sessions", methods=["GET"])
def api_sessions():
    user_id = request.args.get("user_id", "")
    if not user_id:
        return jsonify({"error": "user_id required"}), 400
    sessions = list(
        db.chat_sessions
          .find({"user_id": user_id}, {"_id": 0, "messages": 0})
          .sort("updated_at", -1)
          .limit(50)
    )
    for s in sessions:
        for k in ("created_at", "updated_at"):
            if k in s and hasattr(s[k], "isoformat"):
                s[k] = s[k].isoformat()
    return jsonify(sessions)


@app.route("/api/sessions/<session_id>", methods=["GET"])
def api_session_get(session_id):
    session = db.chat_sessions.find_one({"session_id": session_id}, {"_id": 0})
    if not session:
        return jsonify({"error": "not found"}), 404
    for k in ("created_at", "updated_at"):
        if k in session and hasattr(session[k], "isoformat"):
            session[k] = session[k].isoformat()
    return jsonify(session)


@app.route("/api/sessions/<session_id>", methods=["DELETE"])
def api_session_delete(session_id):
    db.chat_sessions.delete_one({"session_id": session_id})
    return jsonify({"ok": True})


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
    data          = request.get_json(force=True)
    name          = (data.get("name") or "").strip()
    email         = (data.get("email") or "").strip().lower()
    apple_user_id = (data.get("apple_user_id") or "").strip()

    # Look up by apple_user_id first (stable across reinstalls),
    # then fall back to email lookup.
    user = None
    if apple_user_id:
        user = db.users.find_one({"apple_user_id": apple_user_id})
    if user is None and email:
        user = db.users.find_one({"email": email})

    # Require at least apple_user_id or email to identify/create the user
    if user is None and not email and not apple_user_id:
        return jsonify({"error": "name and email are required"}), 400

    if user is None:
        user_id = str(uuid.uuid4())
        db.users.insert_one({
            "user_id":       user_id,
            "name":          name,
            "email":         email,
            "apple_user_id": apple_user_id,
            "created_at":    datetime.now(timezone.utc),
        })
    else:
        user_id = user["user_id"]
        # Backfill any missing fields and update name
        update = {"name": name or user.get("name", "")}
        if apple_user_id and not user.get("apple_user_id"):
            update["apple_user_id"] = apple_user_id
        if email and not user.get("email"):
            update["email"] = email
        db.users.update_one({"user_id": user_id}, {"$set": update})
        # Use stored values for anything the client didn't send
        if not email:
            email = user.get("email", "")
        if not name:
            name = user.get("name", "")

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


# ── SEO / Growth ──────────────────────────────────────────────────────────────

@app.route("/robots.txt")
def robots_txt():
    content = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /admin\n"
        "Disallow: /api/\n"
        f"Sitemap: https://ai.roozbeh.realtor/sitemap.xml\n"
    )
    return Response(content, mimetype="text/plain")


@app.route("/sitemap.xml")
def sitemap_xml():
    urls = [
        ("https://ai.roozbeh.realtor/",             "1.0",  "daily"),
        ("https://ai.roozbeh.realtor/about",         "0.9",  "monthly"),
        ("https://ai.roozbeh.realtor/search",        "0.8",  "daily"),
        ("https://ai.roozbeh.realtor/get-the-app",   "0.8",  "monthly"),
        ("https://ai.roozbeh.realtor/support",       "0.5",  "monthly"),
        ("https://ai.roozbeh.realtor/privacy_policy","0.3",  "monthly"),
    ]
    # Neighborhood pages
    cities = db.mls_listings.distinct("CITY")
    for city in sorted(cities):
        slug = city.lower().replace(" ", "-")
        urls.append((f"https://ai.roozbeh.realtor/homes-for-sale/{slug}", "0.9", "daily"))
    # Active listing pages (cap at 1000 to keep sitemap manageable)
    active = list(
        db.mls_listings
          .find({"MLS_STATUS": {"$in": ["ACTV", "NEW", "AC"]}}, {"LISTING_ID": 1, "_updated_at": 1})
          .sort("_updated_at", -1)
          .limit(1000)
    )
    for doc in active:
        lid  = doc.get("LISTING_ID", "")
        date = doc.get("_updated_at")
        lastmod = date.strftime("%Y-%m-%d") if hasattr(date, "strftime") else ""
        urls.append((f"https://ai.roozbeh.realtor/listing/{lid}", "0.7", "weekly", lastmod))

    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for entry in urls:
        loc, priority, changefreq = entry[0], entry[1], entry[2]
        lastmod = entry[3] if len(entry) > 3 else ""
        lines.append("  <url>")
        lines.append(f"    <loc>{loc}</loc>")
        lines.append(f"    <changefreq>{changefreq}</changefreq>")
        lines.append(f"    <priority>{priority}</priority>")
        if lastmod:
            lines.append(f"    <lastmod>{lastmod}</lastmod>")
        lines.append("  </url>")
    lines.append("</urlset>")
    return Response("\n".join(lines), mimetype="application/xml")


@app.route("/get-the-app")
def get_the_app():
    return render_template("get_the_app.html")


@app.route("/homes-for-sale/<city_slug>")
def homes_for_sale(city_slug):
    city_query = city_slug.upper().replace("-", " ")
    listings = list(
        db.mls_listings
          .find(
              {"CITY": city_query, "MLS_STATUS": {"$in": ["ACTV", "NEW", "AC"]}},
              {"_id": 0, "_history": 0, "SOURCE_MLS_CIRCLE": 0, "TOOLS": 0, "LPHOTOS": 0},
          )
          .sort("LIST_PRICE", 1)
          .limit(24)
    )
    if not listings and city_query not in db.mls_listings.distinct("CITY"):
        return "City not found", 404
    city_title = city_slug.replace("-", " ").title()
    count = db.mls_listings.count_documents(
        {"CITY": city_query, "MLS_STATUS": {"$in": ["ACTV", "NEW", "AC"]}}
    )
    min_price = min((l["LIST_PRICE"] for l in listings if l.get("LIST_PRICE")), default=None)
    max_price = max((l["LIST_PRICE"] for l in listings if l.get("LIST_PRICE")), default=None)
    return render_template(
        "city_listings.html",
        city_title=city_title,
        city_slug=city_slug,
        listings=listings,
        count=count,
        min_price=min_price,
        max_price=max_price,
    )


# ── Admin ─────────────────────────────────────────────────────────────────────

@app.route("/admin")
def admin_index():
    if session.get("admin_logged_in"):
        return redirect("/admin/dashboard")
    return redirect("/admin/login")


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session["admin_logged_in"] = True
            return redirect("/admin/dashboard")
        error = "Invalid username or password."
    return render_template("admin_login.html", error=error)


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_logged_in", None)
    return redirect("/admin/login")


@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    now         = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start  = now - timedelta(days=7)
    month_start = now - timedelta(days=30)

    # ── Core metrics ──────────────────────────────────────────────────────────
    sessions_today = db.chat_sessions.count_documents({"created_at": {"$gte": today_start}})
    sessions_week  = db.chat_sessions.count_documents({"created_at": {"$gte": week_start}})
    sessions_month = db.chat_sessions.count_documents({"created_at": {"$gte": month_start}})
    sessions_total = db.chat_sessions.count_documents({})

    users_today = db.users.count_documents({"created_at": {"$gte": today_start}})
    users_week  = db.users.count_documents({"created_at": {"$gte": week_start}})
    users_month = db.users.count_documents({"created_at": {"$gte": month_start}})
    users_total = db.users.count_documents({})

    leads_today = db.contacts.count_documents({"created_at": {"$gte": today_start}})
    leads_week  = db.contacts.count_documents({"created_at": {"$gte": week_start}})
    leads_month = db.contacts.count_documents({"created_at": {"$gte": month_start}})
    leads_total = db.contacts.count_documents({})

    # ── Engagement stats ──────────────────────────────────────────────────────
    thumbs_up   = db.listing_feedback.count_documents({"feedback": "good"})
    thumbs_down = db.listing_feedback.count_documents({"feedback": "bad"})

    # Average messages per session (sample up to 500 recent sessions)
    sample_sessions = list(db.chat_sessions.find({}, {"messages": 1}).sort("updated_at", -1).limit(500))
    if sample_sessions:
        avg_messages = sum(len(s.get("messages", [])) for s in sample_sessions) / len(sample_sessions)
    else:
        avg_messages = 0.0

    # Sessions with a lead captured (Maya called save_contact)
    sessions_with_lead = db.contacts.distinct("session_id")
    conversion_rate = (len(sessions_with_lead) / sessions_total * 100) if sessions_total else 0

    # ── Chart data: sessions per day for last 14 days ─────────────────────────
    fourteen_days_ago = now - timedelta(days=13)
    pipeline = [
        {"$match": {"created_at": {"$gte": fourteen_days_ago}}},
        {"$group": {
            "_id":   {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}},
            "count": {"$sum": 1},
        }},
        {"$sort": {"_id": 1}},
    ]
    daily_map = {item["_id"]: item["count"] for item in db.chat_sessions.aggregate(pipeline)}
    chart_labels = []
    chart_data   = []
    for i in range(13, -1, -1):
        d = now - timedelta(days=i)
        chart_labels.append(d.strftime("%b %d"))
        chart_data.append(daily_map.get(d.strftime("%Y-%m-%d"), 0))

    # ── Recent leads ──────────────────────────────────────────────────────────
    recent_leads = list(db.contacts.find({}, {"_id": 0}).sort("created_at", -1).limit(25))
    for lead in recent_leads:
        if hasattr(lead.get("created_at"), "isoformat"):
            lead["created_at"] = lead["created_at"].isoformat()

    # ── Recent sessions ───────────────────────────────────────────────────────
    recent_sessions = list(
        db.chat_sessions
          .find({}, {"_id": 0, "messages": 0})
          .sort("updated_at", -1)
          .limit(25)
    )
    for s in recent_sessions:
        s["msg_count"] = len(
            db.chat_sessions.find_one({"session_id": s["session_id"]}, {"messages": 1}).get("messages", [])
        )
        for k in ("created_at", "updated_at"):
            if k in s and hasattr(s[k], "isoformat"):
                s[k] = s[k].isoformat()

    # ── Top liked listings ────────────────────────────────────────────────────
    top_liked = list(db.listing_feedback.aggregate([
        {"$match": {"feedback": "good"}},
        {"$group": {"_id": "$listing_id", "likes": {"$sum": 1}}},
        {"$sort": {"likes": -1}},
        {"$limit": 5},
    ]))
    for item in top_liked:
        doc = db.mls_listings.find_one(
            {"LISTING_ID": item["_id"]},
            {"STREET_ADDRESS": 1, "CITY": 1, "LIST_PRICE": 1},
        )
        if doc:
            item["address"] = doc.get("STREET_ADDRESS", "")
            item["city"]    = doc.get("CITY", "").title()
            item["price"]   = doc.get("LIST_PRICE", 0)
        else:
            item["address"] = item["_id"]
            item["city"]    = ""
            item["price"]   = 0

    # ── Top cities from listing feedback ─────────────────────────────────────
    city_pipeline = [
        {"$match": {"feedback": "good"}},
        {"$lookup": {
            "from":         "mls_listings",
            "localField":   "listing_id",
            "foreignField": "LISTING_ID",
            "as":           "listing",
        }},
        {"$unwind": "$listing"},
        {"$group": {"_id": "$listing.CITY", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 8},
    ]
    top_cities = [
        {"city": item["_id"].title(), "count": item["count"]}
        for item in db.listing_feedback.aggregate(city_pipeline)
        if item.get("_id")
    ]

    return render_template(
        "admin_dashboard.html",
        sessions_today=sessions_today,
        sessions_week=sessions_week,
        sessions_month=sessions_month,
        sessions_total=sessions_total,
        users_today=users_today,
        users_week=users_week,
        users_month=users_month,
        users_total=users_total,
        leads_today=leads_today,
        leads_week=leads_week,
        leads_month=leads_month,
        leads_total=leads_total,
        thumbs_up=thumbs_up,
        thumbs_down=thumbs_down,
        avg_messages=round(avg_messages, 1),
        conversion_rate=round(conversion_rate, 1),
        chart_labels=chart_labels,
        chart_data=chart_data,
        recent_leads=recent_leads,
        recent_sessions=recent_sessions,
        top_liked=top_liked,
        top_cities=top_cities,
    )


@app.route("/admin/conversation/<conv_session_id>")
@admin_required
def admin_conversation(conv_session_id):
    conv = db.chat_sessions.find_one({"session_id": conv_session_id}, {"_id": 0})
    if not conv:
        return "Session not found", 404

    user = None
    if conv.get("user_id"):
        user = db.users.find_one({"user_id": conv["user_id"]}, {"_id": 0})
        if user and hasattr(user.get("created_at"), "isoformat"):
            user["created_at"] = user["created_at"].isoformat()

    # Contacts saved during this session
    saved_contacts = list(db.contacts.find({"session_id": conv_session_id}, {"_id": 0}))
    for c in saved_contacts:
        if hasattr(c.get("created_at"), "isoformat"):
            c["created_at"] = c["created_at"].isoformat()

    # Feedback given during this session
    feedback_items = list(db.listing_feedback.find({"session_id": conv_session_id}, {"_id": 0}))
    for f in feedback_items:
        doc = db.mls_listings.find_one({"LISTING_ID": f.get("listing_id")}, {"STREET_ADDRESS": 1, "CITY": 1})
        f["address"] = doc.get("STREET_ADDRESS", f.get("listing_id", "")) if doc else f.get("listing_id", "")
        if hasattr(f.get("timestamp"), "isoformat"):
            f["timestamp"] = f["timestamp"].isoformat()

    # Count user messages for quick stats
    messages = conv.get("messages", [])
    user_msg_count      = sum(1 for m in messages if m.get("role") == "user")
    assistant_msg_count = sum(1 for m in messages if m.get("role") == "assistant")
    listings_shown      = sum(len(m.get("listings", [])) for m in messages if m.get("role") == "assistant")

    for k in ("created_at", "updated_at"):
        if k in conv and hasattr(conv[k], "isoformat"):
            conv[k] = conv[k].isoformat()

    return render_template(
        "admin_conversation.html",
        conv=conv,
        user=user,
        saved_contacts=saved_contacts,
        feedback_items=feedback_items,
        user_msg_count=user_msg_count,
        assistant_msg_count=assistant_msg_count,
        listings_shown=listings_shown,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
