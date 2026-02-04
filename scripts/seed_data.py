#!/usr/bin/env python3
"""
Seed the development database with realistic dummy events.

Generates template-based Stockholm events (no external API calls) and inserts
them into MongoDB.  Events span from 7 days in the past to 14 days in the
future so the frontend DatePicker always has data to show.

Usage:
    python -m scripts.seed_data                      # Insert 50 events into dev DB
    python -m scripts.seed_data --count 100           # Insert 100 events
    python -m scripts.seed_data --clear               # Wipe all events, then insert 50
    python -m scripts.seed_data --clear --count 25    # Wipe, then insert 25
    python -m scripts.seed_data --env test            # Target the test database instead
"""

import asyncio
import argparse
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List

# Add backend to path (same pattern as run_scraper.py)
sys.path.insert(0, str(Path(__file__).parent.parent))

from motor.motor_asyncio import AsyncIOMotorClient
from app.models.event import Price
from app.auth.password import hash_password

# ---------------------------------------------------------------------------
# Template data
# ---------------------------------------------------------------------------

VENUES = [
    {"name": "Konserthuset", "address": "Hötorget 8, 111 57 Stockholm", "coordinates": [59.3346, 18.0632]},
    {"name": "Södra Teatern", "address": "Mosebacke Torg 1-3, 116 46 Stockholm", "coordinates": [59.3182, 18.0730]},
    {"name": "Fotografiska", "address": "Stadsgårdshamnen 22, 116 45 Stockholm", "coordinates": [59.3180, 18.0856]},
    {"name": "Kungsträdgården", "address": "Jussi Björlings Allé 2, 111 47 Stockholm", "coordinates": [59.3308, 18.0717]},
    {"name": "Avicii Arena", "address": "Globentorget 2, 121 77 Stockholm", "coordinates": [59.2937, 18.0831]},
    {"name": "Moderna Museet", "address": "Exercisplan 4, 111 49 Stockholm", "coordinates": [59.3261, 18.0845]},
    {"name": "Kulturhuset Stadsteatern", "address": "Sergels Torg 3, 111 57 Stockholm", "coordinates": [59.3325, 18.0649]},
    {"name": "Debaser Strand", "address": "Hornstulls Strand 4, 117 39 Stockholm", "coordinates": [59.3158, 18.0344]},
    {"name": "Mosebacke Etablissement", "address": "Mosebacke Torg 3, 116 46 Stockholm", "coordinates": [59.3183, 18.0733]},
    {"name": "Berns Salonger", "address": "Berzelii Park, 111 47 Stockholm", "coordinates": [59.3327, 18.0728]},
    {"name": "Stockholm Stadion", "address": "Lidingövägen 1, 114 33 Stockholm", "coordinates": [59.3454, 18.0793]},
    {"name": "Dramaten", "address": "Nybroplan, 111 47 Stockholm", "coordinates": [59.3318, 18.0770]},
    {"name": "Skansen", "address": "Djurgårdsslätten 49-51, 115 21 Stockholm", "coordinates": [59.3264, 18.1036]},
    {"name": "Vasamuseet", "address": "Galärvarvsvägen 14, 115 21 Stockholm", "coordinates": [59.3280, 18.0914]},
    {"name": "Münchenbryggeriet", "address": "Torkel Knutssonsgatan 2, 118 25 Stockholm", "coordinates": [59.3171, 18.0580]},
    {"name": "Online Event", "address": None, "coordinates": None},
    {"name": "Zoom Webinar", "address": None, "coordinates": None},
]

CATEGORIES = [
    "music",
    "art",
    "food",
    "sports",
    "theater",
    "film",
    "comedy",
    "workshop",
    "festival",
    "family",
]

EVENT_TEMPLATES = [
    {
        "titles": [
            "Live Jazz Evening",
            "Stockholm Symphony Orchestra",
            "Acoustic Night",
            "Electronic Music Showcase",
            "Nordic Folk Concert",
            "Indie Rock Night",
        ],
        "descriptions": [
            "Join us for an unforgettable evening of live music in the heart of Stockholm.",
            "Experience world-class musicians performing in one of Stockholm's most iconic venues.",
            "A night of incredible sound and atmosphere. Doors open one hour before showtime.",
        ],
        "price_range": (0, 450),
        "duration_hours": (1.5, 4),
        "categories": ["music"],
    },
    {
        "titles": [
            "Contemporary Art Exhibition",
            "Photography Vernissage",
            "Nordic Design Showcase",
            "Sculpture Walk",
            "Watercolor Workshop",
            "Street Art Tour",
        ],
        "descriptions": [
            "Explore cutting-edge contemporary art from Scandinavian and international artists.",
            "A curated collection of works exploring themes of identity, nature, and urban life.",
            "Discover the vibrant art scene of Stockholm through this guided experience.",
        ],
        "price_range": (0, 200),
        "duration_hours": (1, 3),
        "categories": ["art"],
    },
    {
        "titles": [
            "Swedish Fika Workshop",
            "Nordic Tasting Menu Experience",
            "Street Food Festival",
            "Wine and Cheese Evening",
            "Stockholm Food Walk",
            "Craft Beer Tasting",
        ],
        "descriptions": [
            "Taste the best of Swedish cuisine with locally sourced ingredients.",
            "A culinary journey through Nordic flavors and traditions.",
            "Sample a curated selection of food and drinks from Stockholm's top producers.",
        ],
        "price_range": (50, 500),
        "duration_hours": (1.5, 3),
        "categories": ["food"],
    },
    {
        "titles": [
            "Stockholm Marathon",
            "AIK vs Djurgården Derby",
            "Morning Yoga in the Park",
            "Padel Tournament",
            "Open Water Swimming",
            "CrossFit Competition",
        ],
        "descriptions": [
            "Get active and join one of Stockholm's most popular sporting events.",
            "Cheer on the athletes in this exciting competition.",
            "Whether you're a participant or spectator, this event is not to be missed.",
        ],
        "price_range": (0, 350),
        "duration_hours": (1, 5),
        "categories": ["sports"],
    },
    {
        "titles": [
            "Hamlet at Dramaten",
            "Improv Comedy Night",
            "Contemporary Dance Performance",
            "Children's Theater: Pippi Longstocking",
            "One-Woman Show: Northern Lights",
            "Musical: Mamma Mia!",
        ],
        "descriptions": [
            "A stunning theatrical performance by some of Sweden's finest actors.",
            "Be captivated by this powerful and moving stage production.",
            "An evening of world-class theater in Stockholm's historic theater district.",
        ],
        "price_range": (100, 600),
        "duration_hours": (1.5, 3),
        "categories": ["theater"],
    },
    {
        "titles": [
            "Swedish Film Premiere",
            "Outdoor Cinema: Classic Bergman",
            "Documentary Screening and Q&A",
            "Short Film Festival",
            "Anime Night",
            "Silent Film with Live Orchestra",
        ],
        "descriptions": [
            "Watch an acclaimed film in a unique Stockholm setting.",
            "A special screening followed by a discussion with the filmmakers.",
            "Cinema lovers unite for this carefully curated film program.",
        ],
        "price_range": (0, 180),
        "duration_hours": (1.5, 3),
        "categories": ["film"],
    },
    {
        "titles": [
            "Stand-Up Stockholm",
            "Comedy Club Open Mic",
            "English Language Comedy Night",
            "Satire Show: Swedish Politics",
            "Comedy Roast Battle",
        ],
        "descriptions": [
            "Laugh out loud with Stockholm's funniest comedians.",
            "An evening of non-stop comedy featuring both established and up-and-coming acts.",
            "The perfect night out — great laughs guaranteed.",
        ],
        "price_range": (0, 200),
        "duration_hours": (1, 2.5),
        "categories": ["comedy"],
    },
    {
        "titles": [
            "Pottery Workshop",
            "Creative Writing Masterclass",
            "Introduction to Swedish Cooking",
            "Woodworking for Beginners",
            "Digital Photography Basics",
            "Candle Making Workshop",
        ],
        "descriptions": [
            "Learn a new skill in this hands-on workshop led by experienced instructors.",
            "A creative and engaging session perfect for beginners and enthusiasts alike.",
            "All materials included. No prior experience necessary.",
        ],
        "price_range": (150, 500),
        "duration_hours": (2, 4),
        "categories": ["workshop"],
    },
    {
        "titles": [
            "Midsommar Festival",
            "Stockholm Culture Night",
            "Winter Light Festival",
            "Stockholm Music and Arts",
            "National Day Celebrations",
        ],
        "descriptions": [
            "A large-scale celebration bringing together music, art, food, and community.",
            "Stockholm's biggest cultural gathering of the year.",
            "Experience the festival atmosphere with performances, markets, and activities for all ages.",
        ],
        "price_range": (0, 400),
        "duration_hours": (4, 10),
        "categories": ["festival"],
    },
    {
        "titles": [
            "Family Day at Skansen",
            "Children's Science Workshop",
            "Fairy Tale Reading Hour",
            "Family Treasure Hunt",
            "Kids Art Studio",
            "Puppet Theater Show",
        ],
        "descriptions": [
            "A fun-filled day for the whole family with activities for all ages.",
            "Bring the kids for an educational and entertaining experience.",
            "Perfect for families looking for a memorable day out in Stockholm.",
        ],
        "price_range": (0, 200),
        "duration_hours": (1.5, 4),
        "categories": ["family"],
    },
    {
        "titles": [
            "Virtual Coding Workshop",
            "Online Photography Masterclass",
            "Remote Wine Tasting",
            "Digital Art Live Stream",
            "Virtual Book Club Meeting",
        ],
        "descriptions": [
            "Join from the comfort of your home for this engaging online event.",
            "A virtual experience connecting participants across Stockholm and beyond.",
            "Log in and learn — all you need is an internet connection.",
        ],
        "price_range": (0, 200),
        "duration_hours": (1, 2.5),
        "categories": ["workshop"],
        "is_online": True,
    },
]

SOURCE_SITES = [
    "eventbrite.com",
    "visitstockholm.com",
    "stockholmlive.com",
    "alltistockholm.se",
    "ticnet.se",
]

# Deterministic placeholder images per category (picsum.photos with seeded IDs)
CATEGORY_IMAGE_URLS = {
    "music": "https://picsum.photos/seed/music/600/400",
    "art": "https://picsum.photos/seed/art/600/400",
    "food": "https://picsum.photos/seed/food/600/400",
    "sports": "https://picsum.photos/seed/sports/600/400",
    "theater": "https://picsum.photos/seed/theater/600/400",
    "film": "https://picsum.photos/seed/film/600/400",
    "comedy": "https://picsum.photos/seed/comedy/600/400",
    "workshop": "https://picsum.photos/seed/workshop/600/400",
    "festival": "https://picsum.photos/seed/festival/600/400",
    "family": "https://picsum.photos/seed/family/600/400",
}


# ---------------------------------------------------------------------------
# Generation functions (pure, testable without DB)
# ---------------------------------------------------------------------------

def generate_event_dict(
    index: int,
    date_range_start: datetime,
    date_range_end: datetime,
    template_override: dict | None = None,
) -> dict:
    """Generate a single event document from templates.

    Picks a random event template, venue, and source site, then randomizes the
    datetime (snapped to 30-minute boundaries), price (rounded to nearest 10 SEK),
    and duration within the template's configured ranges.

    Args:
        index: Unique index used to build a unique source_url.
        date_range_start: Earliest possible event start datetime.
        date_range_end: Latest possible event start datetime.
        template_override: If provided, use this template instead of picking randomly.

    Returns:
        A dict matching the MongoDB event document schema, ready for insert_one().
    """
    template = template_override if template_override is not None else random.choice(EVENT_TEMPLATES)
    title = random.choice(template["titles"])
    description = random.choice(template["descriptions"])
    is_online = template.get("is_online", False)

    # Pick an appropriate venue based on online status
    if is_online:
        online_venues = [v for v in VENUES if v.get("address") is None]
        venue = random.choice(online_venues)
    else:
        physical_venues = [v for v in VENUES if v.get("address") is not None]
        venue = random.choice(physical_venues)

    source_site = random.choice(SOURCE_SITES)

    # Random datetime within range, snapped to nearest 30 minutes
    total_seconds = int((date_range_end - date_range_start).total_seconds())
    random_offset = random.randint(0, max(total_seconds, 0))
    dt_start = date_range_start + timedelta(seconds=random_offset)
    dt_start = dt_start.replace(
        minute=(0 if dt_start.minute < 30 else 30), second=0, microsecond=0
    )

    # Duration from template range
    min_hours, max_hours = template["duration_hours"]
    duration = timedelta(hours=random.uniform(min_hours, max_hours))
    dt_end = dt_start + duration

    # Price rounded to nearest 10 SEK
    min_price, max_price = template["price_range"]
    amount = round(random.uniform(min_price, max_price) / 10) * 10
    price = Price.from_amount(float(amount))

    # Unique source_url using index
    slug = title.lower().replace(" ", "-").replace(":", "").replace("'", "")
    source_url = f"https://{source_site}/events/{slug}-{index}"

    # Image URL from category mapping
    category = template["categories"][0] if template["categories"] else None
    image_url = CATEGORY_IMAGE_URLS.get(category)

    # Popularity counts — skewed distribution: most events low, some high
    like_count = random.choices(
        [0, random.randint(1, 5), random.randint(5, 20), random.randint(20, 100)],
        weights=[30, 40, 20, 10],
    )[0]
    attend_count = random.choices(
        [0, random.randint(1, 3), random.randint(3, 15), random.randint(15, 50)],
        weights=[40, 35, 15, 10],
    )[0]

    # Online events get a meeting link
    online_link = None
    if is_online:
        online_link = f"https://zoom.us/j/{random.randint(100000000, 999999999)}"

    return {
        "title": title,
        "description": description,
        "venue": {
            "name": venue["name"],
            "address": venue.get("address"),
            "coordinates": venue.get("coordinates"),
        },
        "datetime_start": dt_start,
        "datetime_end": dt_end,
        "price": price.model_dump(),
        "source_url": source_url,
        "source_site": source_site,
        "categories": template["categories"],
        "image_url": image_url,
        "is_online": is_online,
        "online_link": online_link,
        "like_count": like_count,
        "attend_count": attend_count,
        "scraped_at": datetime.utcnow(),
        "raw_data": None,
    }


def generate_events(count: int = 50) -> List[dict]:
    """Generate a list of event dicts spanning past and future dates.

    The date range covers 7 days in the past through 14 days ahead.  This
    ensures the frontend DatePicker (which shows 7 days from today) always
    has events to display, and some past events exist for testing date filters.

    Guarantees at least one online event per calendar day in the range so that
    online event functionality can always be tested regardless of which day
    is selected in the UI.

    Args:
        count: Minimum number of events to generate. May be exceeded if the
            date range spans more days than count (one online event per day).

    Returns:
        List of event dicts sorted by datetime_start ascending.
    """
    now = datetime.utcnow()
    date_range_start = now - timedelta(days=7)
    date_range_end = now + timedelta(days=14)

    online_template = next(t for t in EVENT_TEMPLATES if t.get("is_online"))

    events = []
    index = 0

    # Guarantee one online event per calendar day
    current_day = date_range_start.replace(hour=0, minute=0, second=0, microsecond=0)
    end_day = date_range_end.replace(hour=0, minute=0, second=0, microsecond=0)
    while current_day <= end_day:
        day_start = current_day.replace(hour=9)
        day_end = current_day.replace(hour=22)
        events.append(generate_event_dict(index, day_start, day_end, template_override=online_template))
        index += 1
        current_day += timedelta(days=1)

    # Fill remaining slots with random events
    remaining = max(0, count - len(events))
    for _ in range(remaining):
        events.append(generate_event_dict(index, date_range_start, date_range_end))
        index += 1

    events.sort(key=lambda e: e["datetime_start"])
    return events


# ---------------------------------------------------------------------------
# Dev user
# ---------------------------------------------------------------------------

DEV_USER_EMAIL = "dev@example.com"
DEV_USER_PASSWORD = "DevPass1"
DEV_USER_NAME = "Dev User"


def generate_dev_user() -> dict:
    """Generate a dev user document with known credentials.

    Credentials:
        email:    dev@example.com
        password: DevPass1

    Returns:
        A dict matching the MongoDB user document schema.
    """
    return {
        "email": DEV_USER_EMAIL,
        "name": DEV_USER_NAME,
        "password_hash": hash_password(DEV_USER_PASSWORD),
        "email_verified": True,
        "created_at": datetime.utcnow(),
        "preferences": {
            "preferred_categories": [],
            "max_price_bucket": "premium",
            "preferred_areas": [],
        },
        "liked_events": [],
        "attended_events": [],
        "failed_login_attempts": 0,
        "locked_until": None,
        "last_login": None,
        "auth_providers": [],
    }


# ---------------------------------------------------------------------------
# Database operations
# ---------------------------------------------------------------------------

async def seed_database(count: int, clear: bool, env: str):
    """Connect to MongoDB, optionally clear events, then insert generated events.

    Creates its own Motor client (does not use the app's connect_to_mongo) so
    the script runs standalone without starting the full FastAPI application.

    Args:
        count: Number of events to generate and insert.
        clear: If True, delete all existing events before inserting.
        env: Target environment — "development" or "test".
    """
    import os

    os.environ["APP_ENV"] = env

    # Instantiate Settings directly (bypass get_settings LRU cache) so the
    # APP_ENV override set above takes effect.
    from app.config import Settings

    settings = Settings()

    client = AsyncIOMotorClient(settings.mongodb_url)
    db = client[settings.mongodb_db_name]

    # Ensure indexes exist (same as mongodb.py connect_to_mongo)
    await db.events.create_index("datetime_start")
    await db.events.create_index("price.bucket")
    await db.events.create_index("source_url", unique=True)
    await db.users.create_index("email", unique=True)

    try:
        if clear:
            result = await db.events.delete_many({})
            print(f"Cleared {result.deleted_count} existing events from {settings.mongodb_db_name}")
            user_result = await db.users.delete_many({})
            print(f"Cleared {user_result.deleted_count} existing users")

        events = generate_events(count)

        print(f"Generated {len(events)} events")
        print(f"Date range: {events[0]['datetime_start'].strftime('%Y-%m-%d')} to {events[-1]['datetime_start'].strftime('%Y-%m-%d')}")
        print(f"Target database: {settings.mongodb_db_name}")
        print("-" * 50)

        inserted = 0
        skipped = 0

        for event in events:
            try:
                await db.events.insert_one(event)
                inserted += 1
            except Exception:
                skipped += 1
                print(f"  Skipped (duplicate URL): {event['title']}")

        print("-" * 50)
        print(f"Done: Inserted {inserted}, Skipped {skipped}")

        # Seed dev user
        dev_user = generate_dev_user()
        try:
            await db.users.insert_one(dev_user)
            print(f"\nDev user created: {DEV_USER_EMAIL} / {DEV_USER_PASSWORD}")
        except Exception:
            print(f"\nDev user already exists: {DEV_USER_EMAIL} / {DEV_USER_PASSWORD}")

    finally:
        client.close()


def main():
    """CLI entry point for the seed data script."""
    parser = argparse.ArgumentParser(description="Seed the database with dummy events")
    parser.add_argument(
        "--count",
        type=int,
        default=50,
        help="Number of events to generate (default: 50)",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear all existing events before seeding",
    )
    parser.add_argument(
        "--env",
        default="seed",
        choices=["development", "test", "seed"],
        help="Target environment (default: seed)",
    )
    args = parser.parse_args()

    asyncio.run(seed_database(args.count, args.clear, args.env))


if __name__ == "__main__":
    main()
