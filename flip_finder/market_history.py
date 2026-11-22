"""Read-only Mongo runner and private CSV/Markdown outputs for resale history."""
from __future__ import annotations

import csv
from dataclasses import asdict
from pathlib import Path
from typing import Any

from pymongo import MongoClient

from .flips import identify_short_hold_resales, summarize_flip_markets
from .normalize import normalize_mongo_listing


HISTORY_PROJECTION: dict[str, int] = {
    "LISTING_ID": 1, "DCID": 1, "MLS_STATUS": 1, "RAW_STATUS": 1,
    "CLOSE_PRICE": 1, "CLOSE_DATE": 1, "STREET_ADDRESS": 1, "CITY": 1,
    "POSTAL_CODE": 1, "ZIP": 1, "ZIP_CODE": 1, "APN": 1, "PARCEL_NUMBER": 1,
    "SUBDIVISION_NAME": 1, "PROPERTY_TYPE": 1, "BUILDING_TYPE": 1,
    "details.status": 1, "details.CLOSE_PRICE": 1, "details.CLOSE_DATE": 1,
    "details.STREET_ADDRESS": 1, "details.CITY": 1, "details.POSTAL_CODE": 1,
    "details.APN": 1, "details.PARCEL_NUMBER": 1, "details.SUBDIVISION_NAME": 1, "details.PROPERTY_TYPE": 1,
}


def load_closed_listings(uri: str, database: str, collection: str,
                         city: str | None = None) -> list:
    """Load only fields required for matching; this function never writes."""
    client = MongoClient(uri)
    try:
        query: dict[str, Any] = {"$or": [{"MLS_STATUS": {"$in": ["SLD", "SOLD", "CLOSED"]}},
                                            {"details.status": {"$in": ["SLD", "SOLD", "CLOSED"]}}]}
        if city:
            query["CITY"] = city
        return [normalize_mongo_listing(row) for row in client[database][collection].find(query, HISTORY_PROJECTION)]
    finally:
        client.close()


def write_history_outputs(listings: list, output_dir: Path,
                          max_hold_days: int = 365) -> tuple[Path, Path, Path, Path]:
    """Create private, non-versioned analysis outputs."""
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    pairs = identify_short_hold_resales(listings, max_hold_days=max_hold_days)
    pair_path = output_dir / "observed_short_hold_resales.csv"
    city_path = output_dir / "city_short_hold_summary.csv"
    subdivision_path = output_dir / "subdivision_short_hold_summary.csv"
    report_path = output_dir / "market_history.md"
    with pair_path.open("w", newline="") as handle:
        rows = [asdict(pair) for pair in pairs]
        if rows:
            writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
            writer.writeheader(); writer.writerows(rows)
    city_rows = [asdict(item) for item in summarize_flip_markets(pairs, by="city")]
    with city_path.open("w", newline="") as handle:
        if city_rows:
            writer = csv.DictWriter(handle, fieldnames=city_rows[0].keys())
            writer.writeheader(); writer.writerows(city_rows)
    subdivision_rows = [asdict(item) for item in summarize_flip_markets(pairs, by="subdivision")]
    with subdivision_path.open("w", newline="") as handle:
        if subdivision_rows:
            writer = csv.DictWriter(handle, fieldnames=subdivision_rows[0].keys())
            writer.writeheader(); writer.writerows(subdivision_rows)
    report_path.write_text(
        "# Observed Short-Hold Resale History\n\n"
        f"- Closed listing events examined: {len(listings)}\n"
        f"- Matched short-hold resale pairs (14–{max_hold_days} days): {len(pairs)}\n"
        "- Metrics are observed gross price changes only. They exclude renovations, fees, financing, holding costs, taxes, and any unobserved concessions. A pair is not proof of a flip.\n"
        "- APN matches are preferred. Address matches are research leads only and require human confirmation.\n"
        "- Neighborhood summaries appear only where the licensed source supplies a subdivision/neighborhood value; this tool does not infer neighborhoods from an address.\n"
    )
    return pair_path, city_path, subdivision_path, report_path
