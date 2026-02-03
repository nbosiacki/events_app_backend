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
from bson import ObjectId
from datetime import datetime, timedelta

from app.config import get_settings
from app.agents.scraper import EventScraper
from app.agents.deduplicator import EventDeduplicator

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


async def run_scraper(url: str, source_name: str, max_pages: int = 5):
    """Run the scraper and store events."""
    print(f"Starting scraper for: {url}")
    print(f"Source: {source_name}")
    print(f"Max pages: {max_pages}")
    print("-" * 50)

    # Connect to MongoDB
    client = AsyncIOMotorClient(settings.mongodb_url)
    db = client[settings.mongodb_db_name]

    # Create indexes
    await db.events.create_index("datetime_start")
    await db.events.create_index("price.bucket")
    await db.events.create_index("source_url", unique=True)

    scraper = EventScraper()
    deduplicator = EventDeduplicator()

    try:
        # Scrape events
        print("\nScraping events...")
        events = await scraper.scrape(url, source_name, max_pages, db=db)
        print(f"Found {len(events)} events")

        if not events:
            print("No events found.")
            return

        # Get existing events for deduplication
        start_date = datetime.now() - timedelta(days=1)
        end_date = datetime.now() + timedelta(days=90)
        existing_cursor = db.events.find(
            {"datetime_start": {"$gte": start_date, "$lte": end_date}}
        )
        existing_events = await existing_cursor.to_list(length=1000)
        print(f"Found {len(existing_events)} existing events for deduplication")

        # Process events
        added = 0
        duplicates = 0
        errors = 0

        for event in events:
            try:
                # Check for duplicate by source URL first
                existing = await db.events.find_one({"source_url": event.source_url})
                if existing:
                    print(f"  Skip (URL exists): {event.title[:50]}")
                    duplicates += 1
                    continue

                # Check for semantic duplicate
                duplicate_id = await deduplicator.find_duplicate(
                    event, existing_events
                )
                if duplicate_id:
                    print(f"  Skip (duplicate): {event.title[:50]}")
                    # Optionally merge data
                    dup_event = await db.events.find_one(
                        {"_id": ObjectId(duplicate_id)}
                    )
                    if dup_event:
                        merged = await deduplicator.merge_events(event, dup_event)
                        await db.events.update_one(
                            {"_id": ObjectId(duplicate_id)}, {"$set": merged}
                        )
                    duplicates += 1
                    continue

                # Insert new event
                event_dict = event.model_dump()
                event_dict["scraped_at"] = datetime.utcnow()
                result = await db.events.insert_one(event_dict)
                print(f"  Added: {event.title[:50]}")
                added += 1

                # Add to existing events for future dedup checks
                event_dict["_id"] = result.inserted_id
                existing_events.append(event_dict)

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

    asyncio.run(run_scraper(url, name, args.max_pages))


if __name__ == "__main__":
    main()
