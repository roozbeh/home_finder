"""Private Flip Finder review UI.

Public routes describe the product mission. Administrative routes require a
password and are the only routes allowed to read derived FlipDB/MLS data.
"""
from __future__ import annotations

import os
import secrets
import json
import re
from datetime import datetime, timezone
from functools import wraps
from html import unescape
from urllib.parse import urlencode, urljoin

from flask import Flask, abort, redirect, render_template, request, session, url_for
from pymongo import MongoClient


MONGO_URI = os.environ.get("MONGO_URI", "")
SOURCE_DATABASE = os.environ.get("MONGO_DB", "mls")
RESULT_DATABASE = os.environ.get("FLIPDB_DATABASE", "flip_finder")
ADMIN_USERNAME = os.environ.get("FLIP_UI_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("FLIP_UI_ADMIN_PASSWORD") or os.environ.get("ADMIN_PASSWORD", "")
DEMO_USERNAME = os.environ.get("FLIP_UI_DEMO_USERNAME", "")
DEMO_PASSWORD = os.environ.get("FLIP_UI_DEMO_PASSWORD", "")
DEMO_PAIR_KEYS = {
    key.strip() for key in os.environ.get(
        "FLIP_UI_DEMO_PAIR_KEYS",
        "3d81304b124ad90a83dc697c75d4fc43019fff6fa600a213075a336caea727fe,"
        "b2ecd3915d83ac3e9fb7e977381368232ef4a349edce0e013eb17e1f903b9163,"
        "7200836edc784f8d3a9334c51911394d6331a301c4a308bc31c06dfda7414cc1,"
        "c5a9190c7d85425fcd5b8e6328b0744c9333f58a04a43a5a9ad18945b917fac8,"
        "14605f41e5ab26b2170003e3f049274f7d4d747e697447f06395cd95d0dadf07,"
        "6dc5d6b65a06606399930991ee8f4e25a97d6d23f11e38c7d6aa48ef7f55cdf7,"
        "3eb2160185d52404366a16f4d266b864bdd145a2f4c7c130d5dbdee812bbb34c,"
        "ce1d82c1c75151c3b4a023f4cc70cfdb0a536e073d8b0702e78bc4239274de1a,"
        "3ce45ce0c25b5a85f3c5e1ffbbed23dd1aec6ff41586a6e8c75d58592a6ceb10,"
        "7eae2a23f45ea7af394aa62395d698386bc0b35b12ab17328933f767ad429405",
    ).split(",") if key.strip()
}

# Demo MLS comparisons use an allowlist so agent-only fields cannot leak
# through newly added MLS fields or the full-field comparison UI.
DEMO_PUBLIC_FIELDS = {
    "PUBLIC_REMARKS", "STREET_ADDRESS", "CITY", "POSTAL_CODE", "ZIP", "ZIP_CODE",
    "LIST_PRICE", "DISPLAY_PRICE", "ORIGINAL_PRICE", "CLOSE_PRICE", "CLOSE_DATE",
    "BEDROOMS_TOTAL", "BATHROOMS_FULL", "BATHROOMS_HALF", "SQFT", "YEAR_BUILT",
    "PROPERTY_TYPE", "PROPERTY_SUBTYPE", "BUILDING_TYPE", "LOT_SQFT", "ACRES",
    "HOA_FEE", "HOA_DISPLAY", "DOM", "CDOM", "DAYS_ON_MARKET", "DAYS_ON_MKT",
    "RAW_STATUS", "STATUS", "GAR_DISPLAY", "BATHS_DISPLAY", "ROOMS_TOTAL",
}

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


def signed_in_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("flip_ui_role") not in {"admin", "demo"}:
            return redirect(url_for("login", next=request.full_path))
        return view(*args, **kwargs)
    return wrapped


def demo_viewer() -> bool:
    return session.get("flip_ui_role") == "demo"


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
    if value in (None, ""):
        return "—"
    try:
        amount = float(str(value).replace("$", "").replace(",", ""))
    except (TypeError, ValueError):
        return str(value)
    return f"-${abs(amount):,.0f}" if amount < 0 else f"${amount:,.0f}"


def _bcard_value(value) -> tuple[str | None, str | None]:
    """Turn ConnectMLS javascript-link metadata into safe, readable text.

    Detail exports represent a person or office as a structured value whose
    ``href`` is a JavaScript ``openWindow`` call.  Showing that serialization
    makes the comparison table unreadable.  The name is still observed MLS
    data, and the optional link opens the same authenticated MLS card.
    """
    if not isinstance(value, dict) or not value.get("html"):
        return None, None
    name = re.sub(r"<[^>]+>", "", unescape(str(value["html"]))).strip()
    if not name:
        return None, None
    href = str(value.get("href") or "")
    match = re.search(r"openWindow\(['\"]([^'\"]+)", href)
    if not match:
        return name, None
    # The card requires the viewer's own authorized ConnectMLS session.
    return name, urljoin("https://bridge.connectmls.com/", match.group(1))


def _display_value(value) -> str | None:
    """Create compact display text without discarding the underlying record."""
    if value in (None, ""):
        return None
    name, _ = _bcard_value(value)
    if name:
        return name
    if isinstance(value, datetime):
        return value.strftime("%b %d, %Y, %-I:%M %p %Z")
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str, ensure_ascii=False)
    return str(value)


def _gallery_photo_urls(primary_photo: object, photo_count: object) -> list[str]:
    """Build ConnectMLS gallery URLs from its numbered primary image filename.

    ConnectMLS supplies e.g. ``..._41150001_1.JPEG``.  The image number is
    part of that filename (not a following URL segment), so replacing it is
    necessary to reach images 2..N.
    """
    if not isinstance(primary_photo, str) or not primary_photo.startswith("https://"):
        return []
    try:
        count = int(photo_count or 0)
    except (TypeError, ValueError):
        count = 0
    if count < 1:
        return [primary_photo]
    match = re.search(r"_(\d+)(\.[A-Za-z0-9]+)$", primary_photo)
    if not match:
        return [primary_photo]
    return [
        f"{primary_photo[:match.start(1)]}{index}{primary_photo[match.end(1):]}"
        for index in range(1, count + 1)
    ]


def _days_on_market(document: dict, details: dict) -> int | None:
    """Return the observed MLS DOM/CDOM value when the listing supplied one."""
    for key in ("DOM", "CDOM", "DAYS_ON_MARKET", "DAYS_ON_MKT"):
        value = document.get(key)
        if value in (None, ""):
            value = details.get(key)
        if value in (None, ""):
            continue
        match = re.search(r"-?\d+", str(value))
        if match:
            return int(match.group())
    return None


def categorized_photo_groups(result_db, pair_key: str) -> list[dict]:
    """Prepare the manually sampled vision tags for the private comparison UI."""
    labels = {
        "exterior": "Outside",
        "kitchen": "Kitchen",
        "bathroom": "Bathroom",
        "bedroom": "Bedroom",
        "living_room": "Living room",
        "dining_room": "Dining room",
        "laundry": "Laundry",
        "garage": "Garage",
        "other": "Other spaces",
    }
    grouped = {key: {"buy": [], "sell": []} for key in labels}
    rows = result_db.photo_classifications.find(
        {"pair_key": pair_key},
        {"_id": 0, "side": 1, "photo_url": 1, "photo_index": 1, "category": 1, "confidence": 1},
    ).sort([("category", 1), ("side", 1), ("photo_index", 1)])
    for row in rows:
        category = row.get("category")
        side = row.get("side")
        if category not in grouped or side not in {"buy", "sell"} or not row.get("photo_url"):
            continue
        grouped[category][side].append(row)
    return [
        {"key": key, "label": label, **grouped[key]}
        for key, label in labels.items()
        if grouped[key]["buy"] or grouped[key]["sell"]
    ]


def renovation_insight(result_db, pair_key: str) -> dict | None:
    """Return a previously reviewed, explicitly saved visual-cost assessment.

    This deliberately does not synthesize an assessment on page load.  An
    insight only becomes visible after a bounded, on-demand job has saved one.
    """
    return result_db.renovation_insights.find_one({"pair_key": pair_key}, {"_id": 0})


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


def listing_view(document: dict | None, *, demo: bool = False) -> dict:
    document = document or {}
    details = document.get("details") or {}
    primary_photo = details.get("PRIMARY_PHOTO_URI")
    stored_photos = document.get("photos") or []
    # Authorized gallery pages use ConnectMLS HD_ filenames, RAPMLS UUID-based
    # bigphoto paths, or the MLSListings media CDN. Older numbered-path guesses
    # match none of these formats.
    photos = [
        photo for photo in stored_photos
        if isinstance(photo, str) and (
            "/HD_" in photo or "/bigphoto/" in photo or
            "media.mlslmedia.com/property/" in photo or
            "bridgemls-assets.cdn-connectmls.com/pics/" in photo
        )
    ]
    if not photos:
        photos = _gallery_photo_urls(primary_photo, details.get("photocount"))
    photos = list(dict.fromkeys(
        photo for photo in photos if isinstance(photo, str) and photo.startswith("https://")
    ))
    field = lambda name: document.get(name) if document.get(name) not in (None, "") else details.get(name)
    description_fields = (("PUBLIC_REMARKS",) if demo else
                          ("PUBLIC_REMARKS", "DESCRIPTION", "REMARKS", "PUBLIC_DESCRIPTION"))
    description = next(
        (field(name) for name in description_fields if field(name) not in (None, "")), None
    )
    excluded_fields = {"_id", "_history", "details", "photos", "LPHOTOS", "TINYPROPPHOTO_ONELINE", "TOOLS"}

    def display_fields(payload: dict):
        rows = []
        for key, value in sorted(payload.items()):
            if key in excluded_fields:
                continue
            if demo and key not in DEMO_PUBLIC_FIELDS:
                continue
            text = _display_value(value)
            if text is not None:
                rows.append({"key": key, "label": key.replace("_", " ").title(), "value": text})
        return rows

    detail_fields = display_fields(details)
    top_level_fields = [row for row in display_fields(document) if row["key"] not in details]
    field_map = {row["key"]: row for row in top_level_fields}
    field_map.update({row["key"]: row for row in detail_fields})
    agent_card_fields = (
        ("Buyer agent", "BUYER_AGENT_NAME_BCARD"),
        ("Co-buyer agent", "CO_BUYER_AGENT_NAME_BCARD"),
        ("Listing agent", "LIST_AGENT_NAME_BCARD"),
        ("Co-listing agent", "COLISTERNAME_BCARD"),
        ("Buyer office", "BUYER_OFFICE_NAME_BCARD"),
        ("Listing office", "LIST_OFFICE_BCARD"),
    )
    agent_cards = []
    for label, key in (() if demo else agent_card_fields):
        name, href = _bcard_value(details.get(key) or document.get(key))
        if name:
            agent_cards.append({"label": label, "name": name, "href": href})
    return {
        "listing_id": document.get("LISTING_ID"),
        "address": field("STREET_ADDRESS") or "Address unavailable",
        "city": field("CITY"),
        "zip_code": field("POSTAL_CODE") or field("ZIP") or field("ZIP_CODE"),
        "list_price": field("LIST_PRICE") or field("DISPLAY_PRICE") or field("ORIGINAL_PRICE"),
        "sold_price": field("CLOSE_PRICE"),
        "close_date": field("CLOSE_DATE"),
        "days_on_market": _days_on_market(document, details),
        "beds": field("BEDROOMS_TOTAL"),
        "baths": field("BATHROOMS_FULL"),
        "sqft": field("SQFT"),
        "year_built": field("YEAR_BUILT"),
        "property_type": field("PROPERTY_TYPE") or field("BUILDING_TYPE"),
        "photos": photos,
        "description": description,
        "detail_fields": detail_fields,
        "top_level_fields": top_level_fields,
        "field_map": field_map,
        "agent_cards": agent_cards,
    }


def compare_listing_fields(buy: dict, sell: dict) -> tuple[list[dict], list[dict]]:
    """Separate observed field changes from values shared by both MLS records."""
    buy_fields = buy["field_map"]
    sell_fields = sell["field_map"]
    changed, unchanged = [], []
    for key in sorted(set(buy_fields) | set(sell_fields), key=lambda value: value.replace("_", " ")):
        purchase = buy_fields.get(key)
        resale = sell_fields.get(key)
        row = {
            "key": key,
            "label": (purchase or resale)["label"],
            "buy_value": purchase["value"] if purchase else "—",
            "sell_value": resale["value"] if resale else "—",
        }
        if row["buy_value"] == row["sell_value"]:
            row["value"] = row["buy_value"]
            unchanged.append(row)
        else:
            changed.append(row)
    return changed, unchanged


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
        if (DEMO_PASSWORD and DEMO_USERNAME
                and secrets.compare_digest(submitted_user, DEMO_USERNAME)
                and secrets.compare_digest(submitted_password, DEMO_PASSWORD)):
            session.clear()
            session["flip_ui_role"] = "demo"
            return redirect(next_url if next_url.startswith("/") and not next_url.startswith("//") else url_for("flip_list"))
        elif not ADMIN_PASSWORD:
            error = "Admin password is not configured on this server."
        elif (secrets.compare_digest(submitted_user, ADMIN_USERNAME)
              and secrets.compare_digest(submitted_password, ADMIN_PASSWORD)):
            session.clear()
            session["flip_ui_admin"] = True
            session["flip_ui_role"] = "admin"
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
@signed_in_required
def flip_list():
    _, result_db = _dbs()
    args = request.args
    query: dict = {"record_type": "observed_short_hold_resale", "active": True}
    if demo_viewer():
        query["pair_key"] = {"$in": sorted(DEMO_PAIR_KEYS)}
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

    requested_label = args.get("label", "all" if demo_viewer() else "unlabeled")
    labels = label_map(result_db, list(result_db.flipdb.distinct("pair_key", query)))
    label_keys = {key for key, value in labels.items() if value.get("label") == requested_label}
    if requested_label == "unlabeled":
        if demo_viewer():
            query["pair_key"] = {"$in": [key for key in DEMO_PAIR_KEYS if key not in labels]}
        else:
            query["pair_key"] = {"$nin": list(labels)}
    elif requested_label in {"success_flip", "failed_flip"}:
        query["pair_key"] = {"$in": [key for key in label_keys if key in DEMO_PAIR_KEYS] if demo_viewer() else list(label_keys)}

    sort_fields = {
        "buy_price": "buy_price",
        "sell_price": "sell_price",
        "gross_spread": "gross_spread",
        "hold_days": "hold_days",
        "label": "label",
    }
    sort_by = args.get("sort", "sell_date")
    if sort_by not in {*sort_fields, "sell_date"}:
        sort_by = "sell_date"
    sort_direction = 1 if args.get("direction") == "asc" else -1
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 50
    total = result_db.flipdb.count_documents(query)
    projection = {"_id": 0, "pair_key": 1, "city": 1, "subdivision": 1, "buy_price": 1,
                  "sell_price": 1, "buy_date": 1, "sell_date": 1, "hold_days": 1,
                  "gross_spread": 1, "gross_roi": 1, "confidence": 1, "buy_mls": 1,
                  "sell_mls": 1}
    if sort_by == "label":
        rows = list(result_db.flipdb.aggregate([
            {"$match": query},
            {"$lookup": {"from": "flip_labels", "localField": "pair_key",
                         "foreignField": "pair_key", "as": "_label"}},
            {"$set": {"_sort_label": {"$ifNull": [{"$arrayElemAt": ["$_label.label", 0]}, "unlabeled"]}}},
            {"$sort": {"_sort_label": sort_direction, "sell_date": -1}},
            {"$skip": (page - 1) * per_page}, {"$limit": per_page},
            {"$project": projection},
        ]))
    else:
        rows = list(result_db.flipdb.find(query, projection).sort(
            [(sort_by, sort_direction), ("sell_date", -1)]
        ).skip((page - 1) * per_page).limit(per_page))
    for row in rows:
        row["label"] = labels.get(row["pair_key"], {}).get("label", "unlabeled")
    city_query = {"active": True, **({"pair_key": {"$in": sorted(DEMO_PAIR_KEYS)}} if demo_viewer() else {})}
    cities = sorted(city for city in result_db.flipdb.distinct("city", city_query) if city)
    preserved = {key: value for key, value in args.items() if key != "page" and value}
    sort_links = {}
    for field in sort_fields:
        next_direction = "asc" if field != sort_by or sort_direction == -1 else "desc"
        sort_links[field] = urlencode({
            **{key: value for key, value in preserved.items() if key not in {"sort", "direction"}},
            "sort": field, "direction": next_direction,
        })
    return render_template(
        "list.html", rows=rows, cities=cities, filters=args, total=total,
        page=page, has_next=page * per_page < total, previous_query=urlencode(preserved),
        sort_by=sort_by, sort_direction=sort_direction, sort_links=sort_links,
    )


@app.route("/admin/flip/<pair_key>")
@signed_in_required
def flip_detail(pair_key: str):
    source_db, result_db = _dbs()
    flip = result_db.flipdb.find_one({"pair_key": pair_key, "active": True}, {"_id": 0})
    if not flip:
        abort(404)
    if demo_viewer() and pair_key not in DEMO_PAIR_KEYS:
        abort(404)
    labels = label_map(result_db, [pair_key])
    listing_ids = [flip["buy_mls"], flip["sell_mls"]]
    records = {
        row.get("LISTING_ID"): row
        for row in source_db.mls_listings.find({"LISTING_ID": {"$in": listing_ids}})
    }
    buy = listing_view(records.get(flip["buy_mls"]), demo=demo_viewer())
    sell = listing_view(records.get(flip["sell_mls"]), demo=demo_viewer())
    changed_fields, unchanged_fields = compare_listing_fields(buy, sell)
    photo_category_groups = categorized_photo_groups(result_db, pair_key)
    insight = renovation_insight(result_db, pair_key)
    return render_template(
        "detail.html", flip=flip, label=labels.get(pair_key),
        buy=buy, sell=sell, changed_fields=changed_fields, unchanged_fields=unchanged_fields,
        photo_category_groups=photo_category_groups, insight=insight,
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
