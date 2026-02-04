#!/usr/bin/env python3
"""
Manual scraper trigger script.

Usage:
    python -m scripts.run_scraper --url "https://eventbrite.se/d/sweden--stockholm/events/"
    python -m scripts.run_scraper --source eventbrite
"""

import asyncio
import argparse
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime

from app.config import get_settings
from app.agents.scraper import EventScraper
from app.services.currency import refresh_rates, reset_rates, get_rates_source

settings = get_settings()

# Predefined sources
SOURCES = {
    "eventbrite": {
        "url": "https://www.eventbrite.com/d/sweden--stockholm/events/",
        "name": "eventbrite.com",
    },
    "visitstockholm": {
        "url": "https://www.visitstockholm.com/events/",
        "name": "visitstockholm.com",
    },
}


async def run_scraper(url: str, source_name: str, max_pages: int = 5, parser_only: bool = False):
    """Run the scraper and store events."""
    print(f"Starting scraper for: {url}")
    print(f"Source: {source_name}")
    print(f"Max pages: {max_pages}")
    if parser_only:
        print(f"Mode: parser-only (Claude fallback disabled)")
    print("-" * 50)

    # Connect to MongoDB
    client = AsyncIOMotorClient(settings.mongodb_url)
    db = client[settings.mongodb_db_name]

    # Create indexes
    await db.events.create_index("datetime_start")
    await db.events.create_index("price.bucket")
    await db.events.create_index("source_url", unique=True)

    scraper = EventScraper()

    # Fetch exchange rates once for this session
    reset_rates()
    rates = refresh_rates()
    print(f"Exchange rates loaded ({get_rates_source()}): {len(rates)} currencies")

    try:
        # Scrape events
        print("\nScraping events...")
        events = await scraper.scrape(url, source_name, max_pages, db=db, parser_only=parser_only)
        print(f"Found {len(events)} events")

        if not events:
            print("No events found.")
            return

        # Process events (deduplicate by source_url)
        added = 0
        duplicates = 0
        errors = 0

        for event in events:
            try:
                existing = await db.events.find_one({"source_url": event.source_url})
                if existing:
                    print(f"  Skip (URL exists): {event.title[:50]}")
                    duplicates += 1
                    continue

                event_dict = event.model_dump()
                event_dict["scraped_at"] = datetime.utcnow()
                await db.events.insert_one(event_dict)
                print(f"  Added: {event.title[:50]}")
                added += 1

            except Exception as e:
                print(f"  Error: {event.title[:50]} - {e}")
                errors += 1

        print("-" * 50)
        print(f"Summary: Added {added}, Duplicates {duplicates}, Errors {errors}")

    finally:
        scraper.close()
        client.close()


def main():
    parser = argparse.ArgumentParser(description="Scrape events from websites")
    parser.add_argument("--url", help="URL to scrape")
    parser.add_argument(
        "--source",
        choices=list(SOURCES.keys()),
        help="Predefined source to scrape",
    )
    parser.add_argument(
        "--max-pages", type=int, default=5, help="Maximum pages to scrape"
    )
    parser.add_argument(
        "--parser-only",
        action="store_true",
        help="Only use site parsers, never fall back to Claude (for debugging)",
    )

    args = parser.parse_args()

    if args.source:
        source = SOURCES[args.source]
        url = source["url"]
        name = source["name"]
    elif args.url:
        url = args.url
        # Extract domain name
        from urllib.parse import urlparse

        name = urlparse(url).netloc
    else:
        parser.error("Either --url or --source is required")
        return

    asyncio.run(run_scraper(url, name, args.max_pages, parser_only=args.parser_only))


if __name__ == "__main__":
    main()
