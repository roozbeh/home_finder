#!/usr/bin/env python3
"""
iPronto social content generator.

Pulls live data from your MLS database and uses Claude to draft
ready-to-post content for Reddit, Instagram, and blog posts.

Usage (Docker — recommended):
    docker-compose run --rm marketing all
    docker-compose run --rm marketing reddit
    docker-compose run --rm marketing instagram
    docker-compose run --rm marketing blog

Usage (local):
    cd data_service/marketing
    python generate_social_content.py all

Output is saved to marketing/output/ and printed to stdout.
Review, edit if needed, then post.
"""

import os
import sys
import random
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Load .env / env file for local development.
# In Docker, variables are injected by docker-compose env_file — no file needed.
try:
    from dotenv import load_dotenv
    for _candidate in [
        Path(".env"),
        Path("env"),
        Path("../env"),
        Path("../../data_service/env"),
    ]:
        if _candidate.exists():
            load_dotenv(dotenv_path=_candidate)
            break
    else:
        load_dotenv()
except ImportError:
    pass  # running in Docker with env already injected

from pymongo import MongoClient
import anthropic

# ── Config ────────────────────────────────────────────────────────────────────

MONGO_URI     = os.environ["MONGO_URI"]
MONGO_DB      = os.environ.get("MONGO_DB", "mls")
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]

OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# ── DB helpers ────────────────────────────────────────────────────────────────

def get_db():
    client = MongoClient(MONGO_URI, connect=True, serverSelectionTimeoutMS=8000)
    return client[MONGO_DB]


def gather_market_stats(db) -> dict:
    """Pull a snapshot of interesting MLS stats for content generation."""
    now       = datetime.now(timezone.utc)
    week_ago  = now - timedelta(days=7)

    active_filter  = {"MLS_STATUS": {"$in": ["ACTV", "NEW", "AC"]}}
    new_filter     = {"MLS_STATUS": "NEW"}
    pending_filter = {"MLS_STATUS": {"$in": ["PCH", "CS"]}}

    active_count  = db.mls_listings.count_documents(active_filter)
    new_count     = db.mls_listings.count_documents(new_filter)
    pending_count = db.mls_listings.count_documents(pending_filter)

    price_pipeline = [
        {"$match": {**active_filter, "LIST_PRICE": {"$gt": 0}}},
        {"$group": {
            "_id": None,
            "avg": {"$avg": "$LIST_PRICE"},
            "min": {"$min": "$LIST_PRICE"},
            "max": {"$max": "$LIST_PRICE"},
        }},
    ]
    price_stats = next(iter(db.mls_listings.aggregate(price_pipeline)), {})

    dom_pipeline = [
        {"$match": {**active_filter, "DAYS_ON_MARKET": {"$gt": 0}}},
        {"$group": {"_id": None, "avg_dom": {"$avg": "$DAYS_ON_MARKET"}}},
    ]
    dom_stats = next(iter(db.mls_listings.aggregate(dom_pipeline)), {})

    city_pipeline = [
        {"$match": active_filter},
        {"$group": {"_id": "$CITY", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 6},
    ]
    top_cities = [
        {"city": r["_id"].title(), "count": r["count"]}
        for r in db.mls_listings.aggregate(city_pipeline)
        if r.get("_id")
    ]

    affordable = db.mls_listings.find_one(
        {**active_filter, "LIST_PRICE": {"$gt": 500_000}},
        {"STREET_ADDRESS": 1, "CITY": 1, "LIST_PRICE": 1,
         "BEDROOMS_TOTAL": 1, "BATHS_DISPLAY": 1, "SQFT": 1, "LISTING_ID": 1},
        sort=[("LIST_PRICE", 1)],
    )

    newest = db.mls_listings.find_one(
        new_filter,
        {"STREET_ADDRESS": 1, "CITY": 1, "LIST_PRICE": 1,
         "BEDROOMS_TOTAL": 1, "BATHS_DISPLAY": 1, "SQFT": 1,
         "LISTING_ID": 1, "thumbphoto": 1},
        sort=[("_updated_at", -1)],
    )

    price_cuts = db.mls_listings.count_documents({
        "MLS_STATUS": {"$in": ["ACTV", "AC"]},
        "_history": {"$elemMatch": {
            "field": "LIST_PRICE",
            "timestamp": {"$gte": week_ago},
        }},
    })

    return {
        "date":            now.strftime("%B %d, %Y"),
        "active_count":    active_count,
        "new_count":       new_count,
        "pending_count":   pending_count,
        "avg_price":       int(price_stats.get("avg", 0)),
        "min_price":       int(price_stats.get("min", 0)),
        "max_price":       int(price_stats.get("max", 0)),
        "avg_dom":         round(dom_stats.get("avg_dom", 0), 1),
        "top_cities":      top_cities,
        "price_cuts_week": price_cuts,
        "affordable":      affordable,
        "newest":          newest,
    }


def pick_featured_listing(db) -> dict | None:
    """Pick a visually strong active listing suitable for Instagram."""
    candidates = list(db.mls_listings.find(
        {
            "MLS_STATUS": {"$in": ["ACTV", "NEW"]},
            "LIST_PRICE": {"$gt": 800_000},
            "photos":     {"$exists": True, "$not": {"$size": 0}},
        },
        {"STREET_ADDRESS": 1, "CITY": 1, "LIST_PRICE": 1,
         "BEDROOMS_TOTAL": 1, "BATHS_DISPLAY": 1, "BATHROOMS_FULL": 1,
         "SQFT": 1, "LISTING_ID": 1, "thumbphoto": 1,
         "YEAR_BUILT": 1, "LOT_SQFT": 1, "DAYS_ON_MARKET": 1},
        sort=[("_updated_at", -1)],
        limit=20,
    ))
    return random.choice(candidates) if candidates else None


# ── Claude calls ──────────────────────────────────────────────────────────────

def call_claude(prompt: str, system: str) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    msg    = client.messages.create(
        model      = "claude-sonnet-4-6",
        max_tokens = 1200,
        system     = system,
        messages   = [{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


SYSTEM_VOICE = """You are writing marketing content for Roozbeh Zabihollahi,
a licensed Bay Area realtor (DRE# 02225608) who built iPronto
(https://ai.roozbeh.realtor), an AI-powered home search tool.

Tone: knowledgeable but approachable, honest, never salesy.
Always sound like a helpful expert, not an advertisement.
Do NOT use generic filler phrases like "dream home" or "don't miss out".
"""


# ── Generators ────────────────────────────────────────────────────────────────

def generate_reddit_post(stats: dict) -> str:
    top_cities_str = ", ".join(
        f"{c['city']} ({c['count']} listings)" for c in stats["top_cities"]
    )
    affordable = stats.get("affordable")
    affordable_line = ""
    if affordable:
        price = affordable.get("LIST_PRICE", 0)
        city  = (affordable.get("CITY") or "").title()
        beds  = affordable.get("BEDROOMS_TOTAL")
        affordable_line = (
            f"\nMost affordable active listing right now: "
            f"{int(beds) if beds else '?'}bd in {city} at ${price:,.0f}."
        )

    prompt = f"""Write a Reddit post (for r/bayarea or r/SFBayHousing) sharing this week's Bay Area housing market data.

LIVE DATA — week of {stats['date']}:
- Active listings: {stats['active_count']}
- New to market this week: {stats['new_count']}
- Pending/under contract: {stats['pending_count']}
- Average list price: ${stats['avg_price']:,.0f}
- Average days on market: {stats['avg_dom']} days
- Price reductions this week: {stats['price_cuts_week']}
- Most inventory in: {top_cities_str}{affordable_line}

Rules:
- Write a compelling Reddit title (no clickbait)
- Body: 150–250 words, share the data naturally, add 2–3 sentences of honest insight
- End with one low-key mention of iPronto (https://ai.roozbeh.realtor) as the source
- Do NOT sound like an ad
- Format output as:  TITLE: ...\n\nBODY: ...
"""
    return call_claude(prompt, SYSTEM_VOICE)


def generate_instagram_caption(listing: dict, stats: dict) -> str:
    city  = (listing.get("CITY") or "").title()
    price = listing.get("LIST_PRICE", 0)
    beds  = listing.get("BEDROOMS_TOTAL")
    baths = listing.get("BATHS_DISPLAY") or listing.get("BATHROOMS_FULL")
    sqft  = listing.get("SQFT")
    addr  = listing.get("STREET_ADDRESS", "")
    lid   = listing.get("LISTING_ID", "")
    dom   = listing.get("DAYS_ON_MARKET")
    year  = listing.get("YEAR_BUILT")

    details = f"${price:,.0f} · {city}, CA"
    if beds:  details += f" · {int(beds)} bd"
    if baths: details += f" · {baths} ba"
    if sqft:  details += f" · {int(sqft):,} sqft"
    if year:  details += f" · built {int(year)}"
    if dom:   details += f" · {int(dom)} days on market"

    prompt = f"""Write an Instagram caption for this Bay Area listing.

Listing details:
{details}
Address: {addr}, {city}
URL: https://ai.roozbeh.realtor/listing/{lid}

Market context: {stats['active_count']} active Bay Area listings right now.
Average days on market: {stats['avg_dom']} days.

Rules:
- 3–5 lines max
- Start with key stats using emojis (📍 💰 🛏 🚿 📐)
- One line of genuine insight about this listing or the local market
- End with "Chat with Maya to find similar homes → ai.roozbeh.realtor"
- Last line: 8–10 relevant hashtags
- Do NOT use "dream home", "stunning", "gorgeous", or similar clichés
"""
    return call_claude(prompt, SYSTEM_VOICE)


def generate_blog_post(stats: dict) -> str:
    top_cities_str = "\n".join(
        f"  - {c['city']}: {c['count']} active listings"
        for c in stats["top_cities"]
    )
    prompt = f"""Write a 500–700 word blog post about the current Bay Area housing market.

LIVE DATA — {stats['date']}:
- Active listings: {stats['active_count']}
- New listings this week: {stats['new_count']}
- Average list price: ${stats['avg_price']:,.0f}
- Average days on market: {stats['avg_dom']} days
- Price reductions this week: {stats['price_cuts_week']}
- Top cities by inventory:
{top_cities_str}

Rules:
- Punchy SEO-friendly title and subtitle
- Use the real data naturally — explain what numbers mean for buyers
- Add 2–3 actionable tips based on current conditions
- End with a paragraph mentioning iPronto (https://ai.roozbeh.realtor) and Roozbeh as the author
- Plain-spoken — no jargon, no fluff
- Format: TITLE:\nSUBTITLE:\n\n[body with ## subheadings]
"""
    return call_claude(prompt, SYSTEM_VOICE)


# ── Output ────────────────────────────────────────────────────────────────────

def save_and_print(content: str, content_type: str):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    filename  = OUTPUT_DIR / f"{content_type}_{timestamp}.txt"
    filename.write_text(content, encoding="utf-8")

    divider = "─" * 60
    print(f"\n{divider}")
    print(f"  {content_type.upper()}  —  saved to output/{filename.name}")
    print(divider)
    print(content)
    print(divider + "\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "all"
    if mode not in ("reddit", "instagram", "blog", "all"):
        print("Usage: generate_social_content.py [reddit|instagram|blog|all]")
        sys.exit(1)

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] Connecting to database...")
    db    = get_db()
    stats = gather_market_stats(db)

    print(f"Live stats: {stats['active_count']} active listings · "
          f"avg ${stats['avg_price']:,.0f} · {stats['avg_dom']} days on market")

    if mode in ("reddit", "all"):
        print("\nGenerating Reddit post...")
        save_and_print(generate_reddit_post(stats), "reddit")

    if mode in ("instagram", "all"):
        print("\nPicking featured listing for Instagram...")
        listing = pick_featured_listing(db)
        if listing:
            print(f"Selected: {listing.get('STREET_ADDRESS')}, "
                  f"{(listing.get('CITY') or '').title()}")
            save_and_print(generate_instagram_caption(listing, stats), "instagram")
        else:
            print("No suitable listing found (need photos + active status).")

    if mode in ("blog", "all"):
        print("\nGenerating blog post...")
        save_and_print(generate_blog_post(stats), "blog")

    print("Done. Review output above, edit as needed, then post.")


if __name__ == "__main__":
    main()
