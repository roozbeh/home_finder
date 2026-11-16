#!/usr/bin/env python3
"""Classify MLS photos for FlipDB pairs without storing any image bytes.

This is intentionally a manual, isolated worker. It reads authorized gallery
references already stored in Atlas, downloads one resized image at a time, asks
the vision API for a fixed room category, and persists only derived metadata.
"""

import argparse
import base64
import io
import os
from datetime import datetime, timezone

import anthropic
import requests
from PIL import Image, ImageOps
from pymongo import MongoClient


MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.environ.get("MONGO_DB", "mls")
FLIPDB_DATABASE = os.environ.get("FLIPDB_DATABASE", "flip_finder")
MODEL = os.environ.get("PHOTO_CATEGORIZER_MODEL", "claude-haiku-4-5-20251001")
CATEGORIES = (
    "exterior",
    "kitchen",
    "bathroom",
    "bedroom",
    "living_room",
    "dining_room",
    "laundry",
    "garage",
    "other",
)


def gallery_urls(document: dict) -> list[str]:
    """Return verified full-gallery URLs only; never infer unavailable images."""
    return list(dict.fromkeys(
        url for url in document.get("photos", [])
        if isinstance(url, str) and url.startswith("https://") and "/HD_" in url
    ))


def representative_indices(length: int, maximum: int) -> list[int]:
    """Sample across a gallery rather than analyzing only its first rooms."""
    if length <= maximum:
        return list(range(length))
    return sorted({round(index * (length - 1) / (maximum - 1)) for index in range(maximum)})


def resized_image_source(response: requests.Response) -> dict:
    """Turn one remote image into a bounded, in-memory JPEG API payload."""
    image = Image.open(io.BytesIO(response.content))
    image = ImageOps.exif_transpose(image).convert("RGB")
    image.thumbnail((768, 768))
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=82, optimize=True)
    return {
        "type": "base64",
        "media_type": "image/jpeg",
        "data": base64.b64encode(output.getvalue()).decode("ascii"),
    }


def parse_response(text: str) -> dict:
    """Parse a deliberately simple, line-oriented model response."""
    fields = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip().lower()] = value.strip()
    result = {
        "category": fields.get("category", "other").lower().replace(" ", "_"),
        "confidence": fields.get("confidence", 0),
        "visible_features": fields.get("features", ""),
    }
    if result["category"] not in CATEGORIES:
        result["category"] = "other"
    try:
        result["confidence"] = max(0, min(1, float(result.get("confidence", 0))))
    except (TypeError, ValueError):
        result["confidence"] = 0
    result["visible_features"] = str(result.get("visible_features", ""))[:240]
    return result


def classify(client: anthropic.Anthropic, image_source: dict) -> dict:
    prompt = (
        "Classify this real-estate listing photo into exactly one category. "
        f"Allowed categories: {', '.join(CATEGORIES)}. "
        "Return exactly these three lines and nothing else:\n"
        "CATEGORY: one allowed category\n"
        "CONFIDENCE: decimal from 0 to 1\n"
        "FEATURES: at most 12 plain words\n"
        "Use other for hallways, closets, unclear images, or digitally altered "
        "images where the room is not clear. Describe only plainly visible features; "
        "do not estimate renovation cost or infer hidden work."
    )
    message = client.messages.create(
        model=MODEL,
        max_tokens=120,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": image_source},
                {"type": "text", "text": prompt},
            ],
        }],
    )
    return parse_response(message.content[0].text)


def main() -> None:
    parser = argparse.ArgumentParser(description="Classify FlipDB MLS gallery photos.")
    parser.add_argument("--pair-key", required=True, help="FlipDB pair_key from the review URL")
    parser.add_argument(
        "--max-photos-per-side", type=int, default=8,
        help="representative photos to classify from each listing side (default: 8)",
    )
    parser.add_argument("--refresh", action="store_true", help="reclassify existing photo URLs")
    args = parser.parse_args()
    if args.max_photos_per_side < 1:
        parser.error("--max-photos-per-side must be at least 1")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        parser.error("ANTHROPIC_API_KEY is required for this manual categorization run")

    mongo = MongoClient(MONGO_URI)
    mls = mongo[MONGO_DB].mls_listings
    result_db = mongo[FLIPDB_DATABASE]
    pair = result_db.flipdb.find_one({"pair_key": args.pair_key, "active": True}, {"_id": 0})
    if not pair:
        parser.error("active FlipDB pair was not found")

    vision = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    classifications = result_db.photo_classifications
    http = requests.Session()
    attempted = succeeded = skipped = 0

    for side, listing_id in (("buy", pair["buy_mls"]), ("sell", pair["sell_mls"])):
        listing = mls.find_one({"LISTING_ID": listing_id}, {"_id": 0, "photos": 1})
        photos = gallery_urls(listing or {})
        if not photos:
            print(f"  {side}: no verified gallery references for {listing_id}")
            continue
        indices = representative_indices(len(photos), args.max_photos_per_side)
        print(f"  {side}: classifying {len(indices)} representative photos from {len(photos)}")
        for index in indices:
            photo_url = photos[index]
            identity = {"pair_key": args.pair_key, "side": side, "photo_url": photo_url}
            if not args.refresh and classifications.find_one(identity, {"_id": 1}):
                skipped += 1
                continue
            attempted += 1
            try:
                response = http.get(photo_url, timeout=30)
                response.raise_for_status()
                result = classify(vision, resized_image_source(response))
                classifications.update_one(
                    identity,
                    {"$set": {
                        **identity,
                        "listing_id": listing_id,
                        "photo_index": index,
                        "category": result["category"],
                        "confidence": result["confidence"],
                        "visible_features": result["visible_features"],
                        "model": MODEL,
                        "classified_at": datetime.now(timezone.utc),
                    }},
                    upsert=True,
                )
                succeeded += 1
                print(f"    ✓ {side} photo {index + 1}: {result['category']} ({result['confidence']:.2f})")
            except Exception as exc:
                print(f"    ✗ {side} photo {index + 1}: {exc}")

    mongo.close()
    print(f"Done. Attempted: {attempted}; classified: {succeeded}; already cached: {skipped}.")


if __name__ == "__main__":
    main()
