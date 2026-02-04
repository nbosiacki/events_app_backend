#!/usr/bin/env python3
"""
Backfill like_count and attend_count on event documents.

Computes the correct values by scanning all user.liked_events and
user.attended_events arrays, then updates each event document.

Usage:
    python -m scripts.backfill_counts
    python -m scripts.backfill_counts --env test
"""

import asyncio
import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


async def backfill(env: str):
    os.environ["APP_ENV"] = env

    from motor.motor_asyncio import AsyncIOMotorClient
    from app.config import Settings

    settings = Settings()
    client = AsyncIOMotorClient(settings.mongodb_url)
    db = client[settings.mongodb_db_name]

    like_counts: dict[str, int] = defaultdict(int)
    attend_counts: dict[str, int] = defaultdict(int)

    # Scan all users for engagement data
    async for user in db.users.find({}, {"liked_events": 1, "attended_events": 1}):
        for eid in user.get("liked_events", []):
            like_counts[eid] += 1
        for eid in user.get("attended_events", []):
            attend_counts[eid] += 1

    # Update every event with its computed counts
    updated = 0
    async for event in db.events.find({}, {"_id": 1}):
        eid = str(event["_id"])
        lc = like_counts.get(eid, 0)
        ac = attend_counts.get(eid, 0)
        await db.events.update_one(
            {"_id": event["_id"]},
            {"$set": {"like_count": lc, "attend_count": ac}},
        )
        updated += 1

    print(f"Updated {updated} events in {settings.mongodb_db_name}")
    client.close()


def main():
    parser = argparse.ArgumentParser(description="Backfill event popularity counts")
    parser.add_argument(
        "--env",
        default="development",
        choices=["development", "test", "seed"],
        help="Target environment (default: development)",
    )
    args = parser.parse_args()
    asyncio.run(backfill(args.env))


if __name__ == "__main__":
    main()
