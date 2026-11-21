#!/usr/bin/env python3
"""
search_and_store.py
--------------------
Reads the latest session cookies from MongoDB (auth_tokens), runs the MLS
property search, and upserts results into mls_listings.

Changed field values are recorded in a _history array on each document:
  { timestamp, field, old_value, new_value }

Usage:
    export MONGO_URI='mongodb://mlsuser:mlspassword@localhost:27017/mls?authSource=admin'
    python search_and_store.py
"""

import argparse
import os
import sys
import time
from collections.abc import Callable
import requests
from datetime import datetime, timezone, timedelta

from pymongo import MongoClient
from mls_session import (
    MLSAuthenticationError,
    assert_authenticated_response,
    load_active_session,
    retire_session,
)

# ── Config ────────────────────────────────────────────────────────────────────

SEARCH_URL = "https://bridge.connectmls.com/api/search/listing/list"
MONGO_URI  = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB   = os.environ.get("MONGO_DB", "mls")

# Fields to skip when comparing for changes (HTML blobs, arrays, metadata)
SKIP_FIELDS = {"SOURCE_MLS_CIRCLE", "TINYPROPPHOTO_ONELINE", "TOOLS", "LPHOTOS"}
ENRICHED_FIELDS = {"photos", "details", "details_fetched", "photo_gallery_fetched",
                   "photo_gallery_repair", "_id", "_history", "_inserted_at", "_updated_at"}

# Fields to coerce from string to number for correct range queries
NUMERIC_FIELDS = {
    "LIST_PRICE", "DISPLAY_PRICE", "REPORT_DISPLAY_PRICE", "SRCHPRICE",
    "BEDROOMS_TOTAL", "BATHROOMS_FULL", "SQFT", "LOT_SQFT",
    "DAYS_ON_MARKET", "YEAR_BUILT", "ACRES", "LONGITUDE", "LATITUDE",
}

# ── Search configuration ──────────────────────────────────────────────────────

# Statuses fetched by the original active-market search (unchanged).
ACTIVE_STATUSES = "ACTV,BOMK,AC,NEW,PCH,CS"
ACTIVE_CITIES   = ("BELMONT,BRISBANE,COLMA,DALY CITY,EASTPAALTO,FOSTERCITY,"
                   "HALFMO BAY,LOS ALTOS,LOS GATOS,PACIFICA,PORTOLA,SAN CARLOS,"
                   "SARATOGA,ATHERTON,WOODSIDE")

# Sold/closed status code used by this MLS (verified against the search API:
# MLS_STATUS=SLD returns rows whose MLS_STATUS/RAW_STATUS are both "SLD";
# "SOLD" is accepted as an alias by the API and normalizes to SLD).
SOLD_STATUS = "SLD"

# All 20 incorporated San Mateo County cities/towns. The sold search runs once
# per city to stay below the API's ~5000-row response cap. These are source-city
# query values; normalization occurs downstream. Unincorporated communities are
# intentionally not included until their accepted MLS locality values are
# verified separately.
# Source: https://www.smcgov.org/lafco/cities-san-mateo-county
TARGET_CITIES = [
    "ATHERTON", "BELMONT", "BRISBANE", "BURLINGAME", "COLMA", "DALY CITY",
    "EASTPAALTO", "FOSTERCITY", "HALFMO BAY", "HILLSBOROUGH", "MENLO PARK",
    "MILLBRAE", "PACIFICA", "PORTOLA", "REDWOOD CITY", "SAN BRUNO",
    "SAN CARLOS", "SAN MATEO", "SOUTH SAN FRANCISCO", "WOODSIDE",
]

# The 101 incorporated municipalities in the nine-county San Francisco Bay
# Area.  These are source-query locality values, not a claim that every
# locality is necessarily available through the authenticated MLS account.
# Failed/unsupported localities are logged by fetch_sold_listings and must not
# be interpreted downstream as zero closed sales.
# Scope reference: https://en.wikipedia.org/wiki/List_of_cities_and_towns_in_the_San_Francisco_Bay_Area
BAY_AREA_CITIES = [
    "ALAMEDA", "ALBANY", "AMERICAN CANYON", "ANTIOCH", "ATHERTON",
    "BELMONT", "BELVEDERE", "BENICIA", "BERKELEY", "BRENTWOOD",
    "BRISBANE", "BURLINGAME", "CALISTOGA", "CAMPBELL", "CLAYTON",
    "CLOVERDALE", "COLMA", "CONCORD", "CORTE MADERA", "COTATI",
    "CUPERTINO", "DALY CITY", "DANVILLE", "DIXON", "DUBLIN",
    "EASTPAALTO", "EL CERRITO", "EMERYVILLE", "FAIRFAX", "FAIRFIELD",
    "FOSTERCITY", "FREMONT", "GILROY", "HALFMO BAY", "HAYWARD",
    "HEALDSBURG", "HERCULES", "HILLSBOROUGH", "LAFAYETTE", "LARKSPUR",
    "LIVERMORE", "LOS ALTOS", "LOS ALTOS HILLS", "LOS GATOS", "MARTINEZ",
    "MENLO PARK", "MILL VALLEY", "MILLBRAE", "MILPITAS", "MONTE SERENO",
    "MORAGA", "MORGAN HILL", "MOUNTAIN VIEW", "NAPA", "NEWARK", "NOVATO",
    "OAKLAND", "OAKLEY", "ORINDA", "PACIFICA", "PALO ALTO", "PETALUMA",
    "PIEDMONT", "PINOLE", "PITTSBURG", "PLEASANT HILL", "PLEASANTON",
    "PORTOLA", "REDWOOD CITY", "RICHMOND", "RIO VISTA", "ROHNERT PARK",
    "ROSS", "ST HELENA", "SAN ANSELMO", "SAN BRUNO", "SAN CARLOS",
    "SAN FRANCISCO", "SAN JOSE", "SAN LEANDRO", "SAN MATEO", "SAN PABLO",
    "SAN RAFAEL", "SAN RAMON", "SANTA CLARA", "SANTA ROSA", "SARATOGA",
    "SAUSALITO", "SEBASTOPOL", "SONOMA", "SOUTH SAN FRANCISCO", "SUISUN CITY",
    "SUNNYVALE", "TIBURON", "UNION CITY", "VACAVILLE", "VALLEJO",
    "WALNUT CREEK", "WINDSOR", "WOODSIDE", "YOUNTVILLE",
]

# How far back the sold/closed search looks.
SOLD_LOOKBACK_DAYS = 365

# If a single query returns this many rows the API response is likely
# truncated (server cap ~5000) — re-run the city in monthly chunks instead.
ROW_CAP_THRESHOLD = 4900


def build_search_payload(status_value: str, city_value: str,
                         close_min: str = None, close_max: str = None) -> dict:
    """Build a search payload with the same field list as the original search."""
    return {
        "searchclass": "RE",
        "searchtype":  "LISTING",
        "boundaries":  None,
        "layers":      [],
        "report":      "agent-rd-table",
        "fields": [
            {"ordinal": None, "id": "MLS_STATUS",
             "value": status_value,
             "option": None, "min": None, "max": None, "none": None, "all": None},
            {"ordinal": None, "id": "CITY", "value": city_value,
             "option": "", "min": None, "max": None, "none": None, "all": None},
            {"ordinal": None, "id": "COUNTY_OR_PARISH", "value": "",
             "option": None, "min": None, "max": None, "none": None, "all": None},
            {"ordinal": None, "id": "SUBDIVISION_NAME", "value": "",
             "option": "", "min": None, "max": None, "none": None, "all": None},
            {"ordinal": None, "id": "SRCHPRICE", "value": None, "option": None,
             "min": None, "max": None, "none": None, "all": None},
            {"ordinal": None, "id": "BUILDING_TYPE", "value": "",
             "option": "", "min": None, "max": None, "none": "", "all": ""},
            {"ordinal": None, "id": "BEDROOMS_TOTAL", "value": None, "option": None,
             "min": None, "max": None, "none": None, "all": None},
            {"ordinal": None, "id": "BATHROOMS_FULL", "value": None, "option": None,
             "min": None, "max": None, "none": None, "all": None},
            {"ordinal": None, "id": "SQFT", "value": None, "option": None,
             "min": None, "max": None, "none": None, "all": None},
            {"ordinal": None, "id": "DEFAULT_ADDRESS_SEARCH", "value": "",
             "option": None, "min": None, "max": None, "none": None, "all": None},
            {"ordinal": None, "id": "LISTING_CONTRACT_DATE", "value": None,
             "option": None, "min": None, "max": None, "none": None, "all": None},
            {"ordinal": None, "id": "OFF_MARKET_DATE", "value": None,
             "option": None, "min": None, "max": None, "none": None, "all": None},
            {"ordinal": None, "id": "CLOSE_DATE", "value": None,
             "option": None, "min": close_min, "max": close_max, "none": None, "all": None},
            {"ordinal": None, "id": "BOARDID", "value": "",
             "option": "", "min": None, "max": None, "none": None, "all": None},
            {"ordinal": None, "id": "LISTING_AGREEMENT", "value": "",
             "option": "", "min": None, "max": None, "none": None, "all": None},
            {"ordinal": None, "id": "SPECIAL_INFO", "value": "",
             "option": None, "min": None, "max": None, "none": None, "all": None},
            {"ordinal": None, "id": "LISTING_ID", "value": None,
             "option": "", "min": None, "max": None, "none": None, "all": None},
            {"ordinal": None, "id": "FEATURES_SEARCH", "value": None,
             "option": "", "min": None, "max": None, "none": "", "all": ""},
            {"ordinal": None, "id": "SOURCE_MLS",
             "value": "BR,CC,BE,ML,SF,BA,ME,CR,CL,CD",
             "option": "", "min": None, "max": None, "none": None, "all": None},
        ],
        "record":       True,
        "context_data": {},
    }


# Original active-market search payload (identical to previous behavior).
SEARCH_PAYLOAD = build_search_payload(ACTIVE_STATUSES, ACTIVE_CITIES)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _coerce_types(listing: dict) -> dict:
    """Convert known numeric fields from string to int/float."""
    result = {}
    for k, v in listing.items():
        if k in NUMERIC_FIELDS and v not in (None, ""):
            try:
                result[k] = float(v) if "." in str(v) else int(v)
            except (ValueError, TypeError):
                result[k] = v
        else:
            result[k] = v
    return result


# ── Data fetching ─────────────────────────────────────────────────────────────

def get_active_session() -> dict:
    """Load a non-retired session without imposing an arbitrary age limit."""
    doc = load_active_session(MONGO_URI, MONGO_DB)
    ts = doc.get("timestamp")
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
        print(f"  Using unexpired cookies from {ts.isoformat()} ({age_h:.1f}h ago)")
    return doc


def build_session(cookies: list[dict]) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
        ),
        "Accept":          "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin":          "https://bridge.connectmls.com",
        "Referer":         "https://bridge.connectmls.com/",
    })
    for c in cookies:
        session.cookies.set(c["name"], c["value"], domain=c.get("domain", ""))
    return session


def fetch_listings(session: requests.Session, payload: dict = None,
                   label: str = "") -> list[dict]:
    payload = payload if payload is not None else SEARCH_PAYLOAD
    print(f"  POSTing to {SEARCH_URL} {label}...")
    resp = session.post(
        SEARCH_URL,
        json=payload,
        headers={"Content-Type": "application/json;charset=UTF-8"},
        timeout=120,
    )
    assert_authenticated_response(resp)
    resp.raise_for_status()
    data     = resp.json()
    listings = data.get("data", data) if isinstance(data, dict) else data
    print(f"  Fetched {len(listings)} listings {label}")
    return listings


def _month_windows(start: datetime, end: datetime) -> list[tuple[str, str]]:
    """Split [start, end] into monthly (MM/DD/YYYY) windows for cap-safe queries."""
    windows = []
    cur = start
    while cur < end:
        nxt = min(end, datetime(cur.year + (1 if cur.month == 12 else 0),
                                (cur.month % 12) + 1, cur.day,
                                tzinfo=cur.tzinfo))
        windows.append((cur.strftime("%m/%d/%Y"), nxt.strftime("%m/%d/%Y")))
        cur = nxt
    return windows


def fetch_sold_listings(session: requests.Session,
                        lookback_days: int = SOLD_LOOKBACK_DAYS,
                        cities: list[str] = TARGET_CITIES,
                        progress: Callable[[int, int, str, int], None] | None = None) -> tuple[list[dict], list[str]]:
    """Fetch SLD (closed) listings for TARGET_CITIES within a bounded history.

    Runs one query per city; if a city hits the row cap it is re-fetched in
    monthly windows. Results are deduplicated by LISTING_ID.
    """
    if lookback_days < 1:
        raise ValueError("lookback_days must be at least one day")
    end   = datetime.now(timezone.utc)
    start = end - timedelta(days=lookback_days)
    close_max = end.strftime("%m/%d/%Y")
    close_min = start.strftime("%m/%d/%Y")

    by_id: dict[str, dict] = {}
    errors: list[str] = []
    for completed, city in enumerate(cities, start=1):
        city_rows = 0
        try:
            payload = build_search_payload(SOLD_STATUS, city, close_min, close_max)
            rows = fetch_listings(session, payload, label=f"[sold {city}]")
            if len(rows) >= ROW_CAP_THRESHOLD:
                print(f"  ⚠ {city} hit row cap — re-fetching in monthly windows")
                for w_min, w_max in _month_windows(start, end):
                    p = build_search_payload(SOLD_STATUS, city, w_min, w_max)
                    for row in fetch_listings(session, p, label=f"[sold {city} {w_min}-{w_max}]"):
                        by_id[row.get("LISTING_ID") or row.get("DCID")] = row
            else:
                for row in rows:
                    by_id[row.get("LISTING_ID") or row.get("DCID")] = row
            city_rows = len(rows)
        except MLSAuthenticationError:
            raise
        except Exception as exc:
            message = f"{city}: {exc}"
            errors.append(message)
            print(f"  ✗ Sold fetch failed for {message}; continuing")
        if progress:
            progress(completed, len(cities), city, city_rows)
    by_id.pop(None, None)
    print(f"  Sold/closed total (deduped): {len(by_id)}; city failures: {len(errors)}")
    return list(by_id.values()), errors


def fetch_target_active_listings(session: requests.Session,
                                 cities: list[str] = TARGET_CITIES,
                                 statuses: str = "ACTV") -> list[dict]:
    """Fetch the verified active inventory for each requested city.

    ``ACTV`` is the only active status whose meaning has been verified for
    screening.  In particular, do not silently treat backup, contingent, or
    pending records as available investment opportunities.
    """
    by_id: dict[str, dict] = {}
    for city in cities:
        payload = build_search_payload(statuses, city)
        for row in fetch_listings(session, payload, label=f"[active {city}]"):
            by_id[row.get("LISTING_ID") or row.get("DCID")] = row
    by_id.pop(None, None)
    print(f"  Target-city active total (deduped): {len(by_id)}")
    return list(by_id.values())


# ── Upsert with history ───────────────────────────────────────────────────────

def upsert_listings(listings: list[dict]):
    client   = MongoClient(MONGO_URI)
    db       = client[MONGO_DB]
    coll     = db.mls_listings
    now      = datetime.now(timezone.utc)

    inserted = updated = unchanged = 0

    for raw in listings:
        # Search summaries must not overwrite gallery/detail enrichment.
        listing = {key: value for key, value in _coerce_types(raw).items()
                   if key not in ENRICHED_FIELDS}
        lid     = listing.get("LISTING_ID") or listing.get("DCID")
        if not lid:
            continue

        existing = coll.find_one({"LISTING_ID": listing.get("LISTING_ID", lid)})

        if existing is None:
            doc = dict(listing)
            doc["_inserted_at"] = now
            doc["_updated_at"]  = now
            doc["_history"]     = []
            doc["photo_gallery_repair"] = {"status": "pending", "queued_at": now}
            coll.insert_one(doc)
            inserted += 1

        else:
            # Detect field-level changes
            changes = []
            for field, new_val in listing.items():
                if field in SKIP_FIELDS:
                    continue
                old_val = existing.get(field)
                if old_val != new_val:
                    changes.append({
                        "timestamp": now,
                        "field":     field,
                        "old_value": old_val,
                        "new_value": new_val,
                    })

            update_doc = {"$set": {**listing, "_updated_at": now}}
            if changes:
                update_doc["$push"] = {"_history": {"$each": changes}}
                updated += 1
            else:
                unchanged += 1

            coll.update_one(
                {"LISTING_ID": listing.get("LISTING_ID", lid)},
                update_doc,
            )

    client.close()
    print(f"  ✓ Inserted: {inserted}  Updated: {updated}  Unchanged: {unchanged}")
    return inserted, updated, unchanged


# ── Run logging ───────────────────────────────────────────────────────────────

def log_run(started_at: datetime, duration_s: float, fetched: int,
            inserted: int, updated: int, unchanged: int, error: str = None,
            run_type: str = "search"):
    client = MongoClient(MONGO_URI)
    db     = client[MONGO_DB]
    db.mls_runs.insert_one({
        "type":       run_type,
        "timestamp":  started_at,
        "duration_s": round(duration_s, 1),
        "fetched":    fetched,
        "inserted":   inserted,
        "updated":    updated,
        "unchanged":  unchanged,
        "error":      error,
    })
    client.close()


def log_sold_progress(started_at: datetime, market: str, completed: int,
                      total: int, city: str, city_rows: int) -> None:
    """Persist a compact status record for monitoring long authorized pulls."""
    client = MongoClient(MONGO_URI)
    try:
        client[MONGO_DB].mls_runs.update_one(
            {"type": "search_sold_progress", "market": market, "started_at": started_at},
            {"$set": {
                "updated_at": datetime.now(timezone.utc), "completed_cities": completed,
                "total_cities": total, "current_city": city, "city_rows": city_rows,
                "percent_complete": round(completed * 100 / total, 1),
            }},
            upsert=True,
        )
    finally:
        client.close()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Refresh authorized ConnectMLS listing data.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--active-only", action="store_true",
        help="refresh active listings only; skip closed-sale history for this run",
    )
    mode.add_argument(
        "--sold-only", action="store_true",
        help="refresh closed-sale history only; preserve current active inventory",
    )
    parser.add_argument(
        "--sold-lookback-days", type=int, default=SOLD_LOOKBACK_DAYS,
        help="closed-sale lookback window; use 730 for the two-year resale-history refresh",
    )
    parser.add_argument(
        "--market", choices=["san-mateo", "bay-area"], default="san-mateo",
        help="geography for this refresh; bay-area requests all 101 incorporated municipalities",
    )
    args = parser.parse_args()
    print("=== search_and_store.py: MLS Fetch & Store ===")
    started_at = datetime.now(timezone.utc)
    t0         = time.time()

    print("[1/3] Loading cookies from MongoDB...")
    try:
        token = get_active_session()
    except RuntimeError as e:
        print(f"✗ {e}")
        log_run(started_at, time.time() - t0, 0, 0, 0, 0, error=str(e))
        sys.exit(1)

    session = build_session(token["cookies"])
    active_cities = BAY_AREA_CITIES if args.market == "bay-area" else TARGET_CITIES
    if not args.sold_only:
        print(f"[2/5] Fetching verified active listings (ACTV; {len(active_cities)} {args.market} cities)...")
        try:
            listings = fetch_target_active_listings(session, active_cities, statuses="ACTV")
        except MLSAuthenticationError as e:
            retire_session(MONGO_URI, MONGO_DB, token["_id"], str(e))
            message = f"MLS session rejected and marked expired: {e}"
            print(f"✗ {message}")
            log_run(started_at, time.time() - t0, 0, 0, 0, 0, error=message)
            sys.exit(1)
        except Exception as e:
            print(f"✗ Fetch failed: {e}")
            log_run(started_at, time.time() - t0, 0, 0, 0, 0, error=str(e))
            sys.exit(1)
        print("[3/5] Upserting active listings to MongoDB...")
        inserted, updated, unchanged = upsert_listings(listings)
        log_run(started_at, time.time() - t0, len(listings), inserted, updated, unchanged)
    else:
        print("[2/5] Skipping active refresh (--sold-only).")

    # ── Outreach data collection (additive — never touches the search above) ──
    # Failures here must not affect the original active search, which has
    # already completed and been logged.

    if args.active_only:
        print("[4/5] Skipping sold/closed refresh (--active-only).")
    else:
        sold_cities = BAY_AREA_CITIES if args.market == "bay-area" else TARGET_CITIES
        print(f"[4/5] Fetching sold/closed listings (SLD, last {args.sold_lookback_days} days, {len(sold_cities)} {args.market} cities)...")
        t1 = time.time()
        try:
            sold, city_errors = fetch_sold_listings(
                session, args.sold_lookback_days, sold_cities,
                progress=lambda done, total, city, rows: log_sold_progress(
                    started_at, args.market, done, total, city, rows),
            )
            ins, upd, unch = upsert_listings(sold)
            log_run(started_at, time.time() - t1, len(sold), ins, upd, unch,
                    error="; ".join(city_errors) if city_errors else None,
                    run_type="search_sold")
        except MLSAuthenticationError as e:
            retire_session(MONGO_URI, MONGO_DB, token["_id"], str(e))
            message = f"MLS session rejected and marked expired: {e}"
            print(f"✗ {message}")
            log_run(started_at, time.time() - t1, 0, 0, 0, 0, error=message,
                    run_type="search_sold")
            sys.exit(1)
        except Exception as e:
            print(f"✗ Sold fetch failed: {e}")
            log_run(started_at, time.time() - t1, 0, 0, 0, 0, error=str(e),
                    run_type="search_sold")

    if args.sold_only:
        print("[5/5] Skipping target active refresh (--sold-only).")
        print("Done.")
        return
    print("[5/5] Active inventory already refreshed in step 2 (no duplicate query).")

    print("Done.")


if __name__ == "__main__":
    main()
