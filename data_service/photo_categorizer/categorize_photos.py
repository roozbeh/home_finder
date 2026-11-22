#!/usr/bin/env python3
"""Bounded photo classification and Flip insight generation."""
from __future__ import annotations

import argparse
import base64
import io
import math
import os
import re
from collections import defaultdict
from datetime import datetime, timezone

import requests
from PIL import Image, ImageOps
from pymongo import MongoClient

from photo_inference import Inference, InferenceError

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.environ.get("MONGO_DB", "mls")
FLIPDB_DATABASE = os.environ.get("FLIPDB_DATABASE", "flip_finder")
MODEL = os.environ.get("PHOTO_CATEGORIZER_MODEL", "claude-haiku-4-5-20251001")
OLLAMA_MODEL = os.environ.get("PHOTO_CATEGORIZER_OLLAMA_MODEL", "gemma4:12b")
OLLAMA_URL = os.environ.get("PHOTO_CATEGORIZER_OLLAMA_URL", "http://host.docker.internal:11434")
CATEGORIES = ("exterior", "kitchen", "bathroom", "bedroom", "living_room", "dining_room", "laundry", "garage", "other")

def number(value, default=0.0):
    try: return float(str(value).replace("$", "").replace(",", ""))
    except (TypeError, ValueError): return default

def money(value): return f"${value:,.0f}"

def gallery_urls(document):
    # Authorized feeds use distinct full-gallery URL shapes.  Do not accept
    # numbered-path guesses, but retain all verified provider formats.
    return list(dict.fromkeys(url for url in document.get("photos", []) if isinstance(url, str) and url.startswith("https://") and ("/HD_" in url or bool(re.search(r"/bigphoto/[^?]+\.jpe?g(?:\?|$)", url, re.I)) or "media.mlslmedia.com/property/" in url or bool(re.match(r"https://media\.crmls\.org/medias/[0-9a-fA-F-]{36}\.jpe?g", url)))))

def representative_indices(length, maximum):
    if length <= maximum: return list(range(length))
    if maximum == 1: return [0]
    return sorted({round(index * (length - 1) / (maximum - 1)) for index in range(maximum)})

def preflight_gallery(http, side, listing, maximum):
    """Verify that selected MLS references still return image bytes without using inference."""
    photos = gallery_urls(listing)
    selected = [photos[index] for index in representative_indices(len(photos), maximum)]
    reachable = 0
    failures = defaultdict(int)
    for url in selected:
        try:
            response = http.get(url, timeout=15, stream=True)
            content_type = response.headers.get("content-type", "")
            if response.ok and content_type.startswith("image/"):
                reachable += 1
            else:
                failures[f"HTTP {response.status_code}"] += 1
            response.close()
        except requests.RequestException as exc:
            failures[type(exc).__name__] += 1
    reason = ", ".join(f"{count} {status}" for status, count in sorted(failures.items())) or "ok"
    print(f"  {side}: {reachable}/{len(selected)} selected images reachable ({len(photos)} stored; {reason})")
    return reachable, len(selected)

def image_data_url(response):
    return jpeg_source(response.content)


def jpeg_source(content):
    image = ImageOps.exif_transpose(Image.open(io.BytesIO(content))).convert("RGB")
    # Room-level classification does not need high-resolution finishes. Keep
    # the visual token budget modest; original MLS URLs remain untouched.
    image.thumbnail((384, 384))
    output = io.BytesIO(); image.save(output, format="JPEG", quality=82, optimize=True)
    return {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(output.getvalue()).decode("ascii")}

def classify(inference, image_source):
    prompt = ("Classify this real-estate photo. Return JSON only: category, confidence, visible_features. "
              f"category is exactly one of {', '.join(CATEGORIES)}. confidence is 0 to 1. "
              "Describe only visible features in at most 12 words. Use other if unclear. "
              "Do not infer hidden work or estimate cost.")
    schema = {
        'type': 'object',
        'properties': {
            'category': {'type': 'string', 'enum': CATEGORIES},
            'confidence': {'type': 'number', 'minimum': 0, 'maximum': 1},
            'visible_features': {'type': 'string'},
        },
        'required': ['category', 'confidence', 'visible_features'],
    }
    data = inference.json(prompt, tokens=256 if inference.local else 140, image=image_source, schema=schema)
    category = str(data.get('category', '')).lower().replace(' ', '_')
    raw_confidence = data.get('confidence')
    confidence = number(raw_confidence, math.nan) if type(raw_confidence) is not bool else math.nan
    feature = data.get('visible_features')
    if (category not in CATEGORIES or not math.isfinite(confidence) or not 0 <= confidence <= 1
            or not isinstance(feature, str) or not feature.strip()):
        raise ValueError('Invalid photo classification')
    return {'category': category, 'confidence': confidence, 'visible_features': feature.strip()[:240]}

def photo_summary(rows):
    groups = defaultdict(lambda: defaultdict(list))
    for row in rows:
        feature = str(row.get("visible_features") or "").strip()
        if feature: groups[row.get("category", "other")][row.get("side", "unknown")].append(feature)
    return "\n".join(f"{category}: purchase [{'; '.join(sides.get('buy', [])[:12]) or 'no classified photo'}] | resale [{'; '.join(sides.get('sell', [])[:12]) or 'no classified photo'}]" for category, sides in sorted(groups.items()))[:14000]

def estimate(inference, pair, buy, sell, rows):
    prompt = f"""You are a cautious residential-renovation estimator. Compare derived visible observations from purchase and resale MLS photos. Return JSON only with summary, scope, scope_note, confidence, renovation_low, renovation_high, observations. scope is exactly cosmetic, moderate interior, or substantial rehab. confidence is LOW, MEDIUM, or HIGH. observations is at most six objects with area and detail.

Estimate visible work only. Do not claim actual spend, hidden systems, permits, layout changes, or timing photos cannot establish. Use broad Bay Area contractor-led ranges. If evidence is weak, make the range wider and confidence lower.
Purchase MLS remarks: {((buy.get('details') or {}).get('PUBLIC_REMARKS') or '')[:3000]}
Resale MLS remarks: {((sell.get('details') or {}).get('PUBLIC_REMARKS') or '')[:3000]}
Purchase price: {pair.get('buy_price')}; resale price: {pair.get('sell_price')}; hold days: {pair.get('hold_days')}
Derived photo observations (not images):
{photo_summary(rows)}"""
    data = inference.json(prompt, tokens=1800 if inference.local else 1100)
    low, high = (number(data.get(key), math.nan) for key in ('renovation_low', 'renovation_high'))
    if not (math.isfinite(low) and math.isfinite(high) and 0 <= low <= high and data.get('summary')):
        raise ValueError('The model returned an invalid renovation assessment')
    confidence = str(data.get("confidence", "LOW")).upper()
    scope = str(data.get("scope", "moderate interior")).lower()
    observations = [{"area":str(item["area"])[:60], "detail":str(item["detail"])[:300]} for item in data.get("observations", [])[:6] if isinstance(item, dict) and item.get("area") and item.get("detail")]
    return {"summary":str(data.get("summary", "Visible MLS evidence was assessed conservatively."))[:1000], "scope":scope.title() if scope in {"cosmetic", "moderate interior", "substantial rehab"} else "Moderate Interior", "scope_note":str(data.get("scope_note", "Visible-work estimate only."))[:500], "confidence":confidence if confidence in {"LOW", "MEDIUM", "HIGH"} else "LOW", "renovation_low":low, "renovation_high":high, "observations":observations}

def save_insight(db, pair, buy, assessment, inference):
    purchase, sale, days = number(pair.get("buy_price")), number(pair.get("sell_price")), number(pair.get("hold_days"))
    hoa_monthly = number((buy.get("details") or {}).get("HOA_FEE"))
    interest_rate, tax_rate, purchase_rate, sale_rate = .10, .011, .01, .065
    low, high = assessment["renovation_low"], assessment["renovation_high"]; midpoint = (low + high) / 2
    interest, tax, hoa = purchase * interest_rate * days / 365, purchase * tax_rate * days / 365, hoa_monthly * 12 * days / 365
    purchase_close, sale_close = purchase * purchase_rate, sale * sale_rate
    result = lambda renovation: sale - purchase - purchase_close - interest - tax - hoa - sale_close - renovation
    lines = [{"label":"Property purchased", "amount":-purchase}, {"label":"Purchase closing costs", "amount":-purchase_close, "note":f"{purchase_rate:.1%} of purchase price"}, {"label":"Cost of renovations", "amount":-midpoint, "range":f"{money(low)}–{money(high)}"}, {"label":"Cost of money", "amount":-interest, "note":f"{interest_rate:.1%} annual rate × {days:.0f} days"}, {"label":"Property tax", "amount":-tax, "note":f"{tax_rate:.1%} annual estimate × {days:.0f} days"}, {"label":"HOA carrying cost", "amount":-hoa, "note":f"{money(hoa_monthly)}/month × {days:.0f} days"}, {"label":"Sale closing costs", "amount":-sale_close, "note":f"{sale_rate:.1%} of sale price"}, {"label":"Sold price", "amount":sale}]
    insight = {"pair_key":pair["pair_key"], "schema_version":1, "method":(
        "ollama_visual_assessment_from_classified_mls_photos_and_remarks" if inference.local
        else "claude_haiku_visual_assessment_from_classified_mls_photos_and_remarks"
    ), "model":inference.model, "created_at":datetime.now(timezone.utc), **{key:assessment[key] for key in ("confidence", "summary", "scope", "scope_note", "observations")}, "financials":{"lines":lines, "result_midpoint":result(midpoint), "result_range":f"Range with renovation estimate: {money(result(high))} to {money(result(low))}"}, "assumptions":[f"{days:.0f}-day hold from the stored FlipDB record.", f"Purchase price finances at {interest_rate:.1%} simple annual interest for the full hold.", f"Property tax uses {tax_rate:.1%} of purchase price, prorated for the hold.", f"HOA uses the purchase MLS fee of {money(hoa_monthly)} per month, prorated for the hold.", f"Closing estimates use {purchase_rate:.1%} on purchase and {sale_rate:.1%} on resale.", "Renovation range estimates visible work only and does not prove actual spend."], "disclaimer":"Estimated all-in result only. Excludes insurance, utilities, staging, marketing, special assessments, income taxes, and unobserved work; it is not actual spend, a settlement statement, or net profit."}
    db.renovation_insights.update_one({"pair_key":pair["pair_key"]}, {"$set":insight}, upsert=True)

def main():
    parser = argparse.ArgumentParser(description="Classify MLS photos and optionally save a visual-work insight.")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--pair-key")
    target.add_argument("--listing-id", help="classify a single active MLS listing")
    parser.add_argument("--max-photos-per-side", "--max-photos", dest="max_photos_per_side", type=int, default=8)
    parser.add_argument("--max-api-requests", type=int, default=129, help="hard request budget, including one insight request")
    parser.add_argument("--refresh", action="store_true"); parser.add_argument("--generate-insight", action="store_true")
    parser.add_argument("--local", action="store_true", help="use local Ollama for photos and insights")
    parser.add_argument("--model", help="override the chosen provider's model")
    parser.add_argument("--ollama-url", default=OLLAMA_URL, help="Ollama URL visible from Docker")
    parser.add_argument("--ollama-timeout", default=600, type=int, help="local request timeout in seconds")
    parser.add_argument("--preflight", action="store_true", help="verify selected gallery URLs without inference or writes")
    args = parser.parse_args()
    if not 1 <= args.max_photos_per_side <= 64: parser.error("--max-photos-per-side must be between 1 and 64")
    if args.max_api_requests < 1: parser.error("--max-api-requests must be positive")
    if not args.preflight and not args.local and not os.environ.get("ANTHROPIC_API_KEY"):
        parser.error("ANTHROPIC_API_KEY is required for Anthropic")
    if args.ollama_timeout < 1:
        parser.error("--ollama-timeout must be positive")
    model = args.model or (OLLAMA_MODEL if args.local else MODEL)
    inference = None if args.preflight else Inference(
        local=args.local, model=model, ollama_url=args.ollama_url, ollama_timeout=args.ollama_timeout,
    )
    if args.local and inference is not None:
        try:
            inference.verify_vision()
        except InferenceError as exc:
            parser.error(str(exc))
    if args.listing_id:
        from active_listing import run_active
        run_active(args, inference)
        return
    mongo = MongoClient(MONGO_URI); mls, db = mongo[MONGO_DB].mls_listings, mongo[FLIPDB_DATABASE]
    pair = db.flipdb.find_one({"pair_key":args.pair_key, "active":True}, {"_id":0})
    if not pair: parser.error("active FlipDB pair was not found")
    buy, sell = mls.find_one({"LISTING_ID":pair["buy_mls"]}, {"_id":0}) or {}, mls.find_one({"LISTING_ID":pair["sell_mls"]}, {"_id":0}) or {}
    http = requests.Session()
    if args.preflight:
        print("Gallery preflight (no AI calls and no database writes):")
        buy_ok, buy_total = preflight_gallery(http, "buy", buy, args.max_photos_per_side)
        sell_ok, sell_total = preflight_gallery(http, "sell", sell, args.max_photos_per_side)
        mongo.close()
        if not buy_ok or not sell_ok:
            raise SystemExit("Backfill required: both purchase and resale need at least one reachable image.")
        print("Preflight passed.")
        return
    plan = sum(len(representative_indices(len(gallery_urls(doc)), args.max_photos_per_side)) for doc in (buy, sell)); needed = plan + (1 if args.generate_insight else 0)
    if needed > args.max_api_requests: parser.error(f"request budget exceeded: this run may need {needed}, budget is {args.max_api_requests}")
    print(f"{inference.provider} model {inference.model}; planned upper bound: {needed} requests (cache may reduce this).")
    classifications = db.photo_classifications
    attempted = succeeded = skipped = 0
    selected_queries = []
    for side, listing_id, listing in (("buy", pair["buy_mls"], buy), ("sell", pair["sell_mls"], sell)):
        photos = gallery_urls(listing); indices = representative_indices(len(photos), args.max_photos_per_side); print(f"  {side}: processing {len(indices)} photos from {len(photos)}")
        selected_queries.append({'side': side, 'photo_url': {'$in': [photos[index] for index in indices]}})
        for index in indices:
            url = photos[index]; identity = {"pair_key":args.pair_key, "side":side, "photo_url":url}
            if not args.refresh and classifications.find_one({**identity, "model":inference.model}, {"_id":1}): skipped += 1; continue
            attempted += 1
            try:
                response = http.get(url, timeout=30); response.raise_for_status(); label = classify(inference, image_data_url(response))
                classifications.update_one(identity, {"$set":{**identity, "listing_id":listing_id, "photo_index":index, **label, "model":inference.model, "classified_at":datetime.now(timezone.utc)}}, upsert=True); succeeded += 1; print(f"    ✓ {side} photo {index + 1}: {label['category']} ({label['confidence']:.2f})")
            except InferenceError:
                raise
            except Exception as exc:
                print(f"    ✗ {side} photo {index + 1}: {exc}")
    if args.generate_insight:
        rows = list(classifications.find(
            {"pair_key":args.pair_key, "model":inference.model, "$or":selected_queries},
            {"_id":0, "side":1, "category":1, "visible_features":1},
        ))
        if not rows:
            raise RuntimeError("no photo classifications available for this model; insight was not saved")
        assessment = estimate(inference, pair, buy, sell, rows)
        save_insight(db, pair, buy, assessment, inference)
        print(f"Saved {inference.provider} insight: {money(assessment['renovation_low'])}–"
              f"{money(assessment['renovation_high'])}; {assessment['confidence']} confidence.")
    mongo.close(); print(f"Done. Attempted: {attempted}; classified: {succeeded}; already cached: {skipped}.")

if __name__ == "__main__": main()
