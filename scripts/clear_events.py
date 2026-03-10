#!/usr/bin/env python3
"""
Clear all events from a database.

Useful after parser improvements to let the scraper re-populate
with corrected data.  Does NOT touch the users collection.

Usage:
    python -m scripts.clear_events                  # Clear stockholm_events_dev
    python -m scripts.clear_events --env seed       # Clear stockholm_events_seed
"""

import asyncio
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


async def clear(env: str):
    os.environ["APP_ENV"] = env

    from motor.motor_asyncio import AsyncIOMotorClient
    from app.config import Settings

    settings = Settings()
    client = AsyncIOMotorClient(settings.mongodb_url)
    db = client[settings.mongodb_db_name]

    count = await db.events.count_documents({})
    if count == 0:
        print(f"No events in {settings.mongodb_db_name}")
        client.close()
        return

    result = await db.events.delete_many({})
    print(f"Deleted {result.deleted_count} events from {settings.mongodb_db_name}")
    client.close()


def main():
    parser = argparse.ArgumentParser(description="Clear all events from a database")
    parser.add_argument(
        "--env",
        default="development",
        choices=["development", "test", "seed"],
        help="Target environment (default: development)",
    )
    args = parser.parse_args()
    asyncio.run(clear(args.env))


if __name__ == "__main__":
    main()
