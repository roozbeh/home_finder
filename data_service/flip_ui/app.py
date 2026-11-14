"""Private Flip Finder review UI.

Public routes describe the product mission. Administrative routes require a
password and are the only routes allowed to read derived FlipDB/MLS data.
"""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone
from functools import wraps
from urllib.parse import urlencode

from flask import Flask, abort, redirect, render_template, request, session, url_for
from pymongo import MongoClient


MONGO_URI = os.environ.get("MONGO_URI", "")
SOURCE_DATABASE = os.environ.get("MONGO_DB", "mls")
RESULT_DATABASE = os.environ.get("FLIPDB_DATABASE", "flip_finder")
ADMIN_USERNAME = os.environ.get("FLIP_UI_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("FLIP_UI_ADMIN_PASSWORD") or os.environ.get("ADMIN_PASSWORD", "")

app = Flask(__name__)
app.secret_key = os.environ.get("FLIP_UI_SECRET_KEY", "") or os.environ.get("SECRET_KEY", "")
if not app.secret_key:
    # Development-only fallback. Production deployments must set FLIP_UI_SECRET_KEY.
    app.secret_key = "replace-this-development-session-key"
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("FLIP_UI_COOKIE_SECURE", "true").lower() == "true",
)

mongo = MongoClient(MONGO_URI, connect=False) if MONGO_URI else None


def _dbs():
    if mongo is None:
        raise RuntimeError("MONGO_URI is not configured")
    return mongo[SOURCE_DATABASE], mongo[RESULT_DATABASE]


def csrf_token() -> str:
    if "flip_ui_csrf" not in session:
        session["flip_ui_csrf"] = secrets.token_urlsafe(32)
    return session["flip_ui_csrf"]


def validate_csrf() -> None:
    submitted = request.form.get("csrf_token", "")
    if not submitted or not secrets.compare_digest(submitted, session.get("flip_ui_csrf", "")):
        abort(400)


@app.context_processor
def inject_template_values():
    return {"csrf_token": csrf_token}


@app.after_request
def protect_admin_responses(response):
    """Keep the private review workspace out of browser/proxy caches and search."""
    if request.path.startswith("/admin"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return response


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("flip_ui_admin"):
            return redirect(url_for("login", next=request.full_path))
        return view(*args, **kwargs)
    return wrapped


def parse_float(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value.replace(",", ""))
    except (AttributeError, ValueError):
        return None


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def money(value) -> str:
    return f"${float(value):,.0f}" if value is not None else "—"


def label_map(result_db, pair_keys: list[str]) -> dict[str, dict]:
    if not pair_keys:
        return {}
    return {
        row["pair_key"]: row
        for row in result_db.flip_labels.find(
            {"pair_key": {"$in": pair_keys}},
            {"_id": 0, "pair_key": 1, "label": 1, "updated_at": 1, "history": 1},
        )
    }


def listing_view(document: dict | None) -> dict:
    document = document or {}
    details = document.get("details") or {}
    photos = document.get("photos") or []
    if not photos and details.get("PRIMARY_PHOTO_URI"):
        photos = [details["PRIMARY_PHOTO_URI"]]
    photos = [photo for photo in photos if isinstance(photo, str) and photo.startswith("https://")][:8]
    field = lambda name: document.get(name) if document.get(name) not in (None, "") else details.get(name)
    return {
        "listing_id": document.get("LISTING_ID"),
        "address": field("STREET_ADDRESS") or "Address unavailable",
        "city": field("CITY"),
        "zip_code": field("POSTAL_CODE") or field("ZIP") or field("ZIP_CODE"),
        "price": field("CLOSE_PRICE") or field("LIST_PRICE"),
        "close_date": field("CLOSE_DATE"),
        "beds": field("BEDROOMS_TOTAL"),
        "baths": field("BATHROOMS_FULL"),
        "sqft": field("SQFT"),
        "year_built": field("YEAR_BUILT"),
        "property_type": field("PROPERTY_TYPE") or field("BUILDING_TYPE"),
        "photos": photos,
    }


@app.template_filter("money")
def money_filter(value):
    return money(value)


@app.template_filter("percent")
def percent_filter(value):
    return f"{float(value) * 100:.1f}%" if value is not None else "—"


@app.template_filter("dateonly")
def dateonly_filter(value):
    if isinstance(value, datetime):
        return value.strftime("%b %d, %Y")
    return str(value or "—")


@app.route("/")
def landing():
    return render_template("landing.html")


@app.route("/admin/login", methods=["GET", "POST"])
def login():
    error = None
    next_url = request.values.get("next") or url_for("flip_list")
    if request.method == "POST":
        validate_csrf()
        submitted_user = request.form.get("username", "")
        submitted_password = request.form.get("password", "")
        if not ADMIN_PASSWORD:
            error = "Admin password is not configured on this server."
        elif (secrets.compare_digest(submitted_user, ADMIN_USERNAME)
              and secrets.compare_digest(submitted_password, ADMIN_PASSWORD)):
            session.clear()
            session["flip_ui_admin"] = True
            return redirect(next_url if next_url.startswith("/") and not next_url.startswith("//") else url_for("flip_list"))
        else:
            error = "Incorrect username or password."
    return render_template("login.html", error=error, next_url=next_url)


@app.post("/admin/logout")
def logout():
    validate_csrf()
    session.clear()
    return redirect(url_for("landing"))


@app.route("/admin")
@admin_required
def flip_list():
    _, result_db = _dbs()
    args = request.args
    query: dict = {"record_type": "observed_short_hold_resale", "active": True}
    city = args.get("city", "").strip().upper()
    if city:
        query["city"] = city

    numeric_filters = {
        "buy_price": ("buy_price", "$gte"),
        "buy_price_max": ("buy_price", "$lte"),
        "profit_min": ("gross_spread", "$gte"),
        "profit_max": ("gross_spread", "$lte"),
    }
    for key, (field, operator) in numeric_filters.items():
        value = parse_float(args.get(key))
        if value is not None:
            query.setdefault(field, {})[operator] = value
    for key, operator in (("buy_date_from", "$gte"), ("buy_date_to", "$lte")):
        value = parse_date(args.get(key))
        if value is not None:
            query.setdefault("buy_date", {})[operator] = value

    requested_label = args.get("label", "unlabeled")
    labels = label_map(result_db, list(result_db.flipdb.distinct("pair_key", query)))
    label_keys = {key for key, value in labels.items() if value.get("label") == requested_label}
    if requested_label == "unlabeled":
        query["pair_key"] = {"$nin": list(labels)}
    elif requested_label in {"success_flip", "failed_flip"}:
        query["pair_key"] = {"$in": list(label_keys)}

    page = max(1, request.args.get("page", 1, type=int))
    per_page = 50
    total = result_db.flipdb.count_documents(query)
    rows = list(result_db.flipdb.find(
        query,
        {"_id": 0, "pair_key": 1, "city": 1, "subdivision": 1, "buy_price": 1,
         "sell_price": 1, "buy_date": 1, "sell_date": 1, "hold_days": 1,
         "gross_spread": 1, "gross_roi": 1, "confidence": 1, "buy_mls": 1,
         "sell_mls": 1},
    ).sort("sell_date", -1).skip((page - 1) * per_page).limit(per_page))
    for row in rows:
        row["label"] = labels.get(row["pair_key"], {}).get("label", "unlabeled")
    cities = sorted(city for city in result_db.flipdb.distinct("city", {"active": True}) if city)
    preserved = {key: value for key, value in args.items() if key != "page" and value}
    return render_template(
        "list.html", rows=rows, cities=cities, filters=args, total=total,
        page=page, has_next=page * per_page < total, previous_query=urlencode(preserved),
    )


@app.route("/admin/flip/<pair_key>")
@admin_required
def flip_detail(pair_key: str):
    source_db, result_db = _dbs()
    flip = result_db.flipdb.find_one({"pair_key": pair_key, "active": True}, {"_id": 0})
    if not flip:
        abort(404)
    labels = label_map(result_db, [pair_key])
    listing_ids = [flip["buy_mls"], flip["sell_mls"]]
    projection = {
        "_id": 0, "LISTING_ID": 1, "STREET_ADDRESS": 1, "CITY": 1,
        "POSTAL_CODE": 1, "ZIP": 1, "ZIP_CODE": 1, "CLOSE_PRICE": 1,
        "CLOSE_DATE": 1, "LIST_PRICE": 1, "BEDROOMS_TOTAL": 1,
        "BATHROOMS_FULL": 1, "SQFT": 1, "YEAR_BUILT": 1,
        "PROPERTY_TYPE": 1, "BUILDING_TYPE": 1, "photos": 1,
        "details.PRIMARY_PHOTO_URI": 1, "details.CLOSE_PRICE": 1,
        "details.CLOSE_DATE": 1, "details.BEDROOMS_TOTAL": 1,
        "details.BATHROOMS_FULL": 1, "details.SQFT": 1,
        "details.YEAR_BUILT": 1, "details.PROPERTY_TYPE": 1,
    }
    records = {
        row.get("LISTING_ID"): row
        for row in source_db.mls_listings.find({"LISTING_ID": {"$in": listing_ids}}, projection)
    }
    return render_template(
        "detail.html", flip=flip, label=labels.get(pair_key),
        buy=listing_view(records.get(flip["buy_mls"])),
        sell=listing_view(records.get(flip["sell_mls"])),
    )


@app.post("/admin/flip/<pair_key>/label")
@admin_required
def save_label(pair_key: str):
    validate_csrf()
    label = request.form.get("label")
    if label not in {"success_flip", "failed_flip", "unlabeled"}:
        abort(400)
    _, result_db = _dbs()
    if not result_db.flipdb.find_one({"pair_key": pair_key}, {"_id": 1}):
        abort(404)
    now = datetime.now(timezone.utc)
    prior = result_db.flip_labels.find_one({"pair_key": pair_key}, {"label": 1})
    if label == "unlabeled":
        result_db.flip_labels.delete_one({"pair_key": pair_key})
    else:
        event = {"from": (prior or {}).get("label"), "to": label, "at": now,
                 "by": ADMIN_USERNAME}
        result_db.flip_labels.update_one(
            {"pair_key": pair_key},
            {"$set": {"label": label, "updated_at": now, "updated_by": ADMIN_USERNAME},
             "$setOnInsert": {"pair_key": pair_key, "created_at": now},
             "$push": {"history": event}},
            upsert=True,
        )
    return redirect(url_for("flip_detail", pair_key=pair_key))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
