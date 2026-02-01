#!/usr/bin/env python3
"""
Inspect an event in the database.

Prints the event document along with like/attend counts and which users
have liked or attended it, for quick verification during development.

Usage:
    python -m scripts.inspect_event --title "Jazz Night"     # Search by title (partial match)
    python -m scripts.inspect_event --id 507f1f77bcf86cd799  # Lookup by ObjectId
    python -m scripts.inspect_event --popular                # Show top 10 by popularity
    python -m scripts.inspect_event --env test               # Target test database
"""

import asyncio
import argparse
import json
import sys
from pathlib import Path
from datetime import datetime

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def serialize(obj):
    """JSON serializer for MongoDB types."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "__str__"):
        return str(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def print_event(event, users_who_liked=None, users_who_attended=None):
    """Pretty-print a single event document."""
    title = event.get("title", "(untitled)")
    eid = str(event["_id"])
    like_count = event.get("like_count", 0)
    attend_count = event.get("attend_count", 0)

    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"  ID: {eid}")
    print(f"{'=' * 60}")
    print(json.dumps(event, indent=2, default=serialize))

    print(f"\nlike_count: {like_count}  |  attend_count: {attend_count}  |  popularity: {like_count + attend_count}")

    if users_who_liked is not None:
        if users_who_liked:
            print(f"\nLiked by ({len(users_who_liked)}):")
            for u in users_who_liked:
                print(f"  - {u['email']}")
        else:
            print("\nLiked by: (nobody)")

    if users_who_attended is not None:
        if users_who_attended:
            print(f"\nAttended by ({len(users_who_attended)}):")
            for u in users_who_attended:
                print(f"  - {u['email']}")
        else:
            print("\nAttended by: (nobody)")


async def find_users_for_event(db, event_id: str):
    """Find users who liked or attended an event."""
    liked_by = await db.users.find(
        {"liked_events": event_id},
        {"email": 1, "_id": 0},
    ).to_list(length=100)

    attended_by = await db.users.find(
        {"attended_events": event_id},
        {"email": 1, "_id": 0},
    ).to_list(length=100)

    return liked_by, attended_by


async def inspect_by_id(db, event_id: str):
    """Look up an event by ObjectId."""
    from bson import ObjectId

    if not ObjectId.is_valid(event_id):
        print(f"Invalid ObjectId: {event_id}")
        return

    event = await db.events.find_one({"_id": ObjectId(event_id)})
    if not event:
        print(f"Event not found: {event_id}")
        return

    liked_by, attended_by = await find_users_for_event(db, event_id)
    print_event(event, liked_by, attended_by)


async def inspect_by_title(db, title: str):
    """Search events by title (case-insensitive partial match)."""
    cursor = db.events.find(
        {"title": {"$regex": title, "$options": "i"}},
    ).limit(10)
    events = await cursor.to_list(length=10)

    if not events:
        print(f"No events matching: {title}")
        return

    print(f"Found {len(events)} event(s) matching '{title}':")
    for event in events:
        eid = str(event["_id"])
        liked_by, attended_by = await find_users_for_event(db, eid)
        print_event(event, liked_by, attended_by)


async def inspect_popular(db, limit: int):
    """Show top events by like_count + attend_count."""
    pipeline = [
        {"$addFields": {
            "popularity": {
                "$add": [
                    {"$ifNull": ["$like_count", 0]},
                    {"$ifNull": ["$attend_count", 0]},
                ]
            }
        }},
        {"$sort": {"popularity": -1}},
        {"$limit": limit},
    ]
    events = await db.events.aggregate(pipeline).to_list(length=limit)

    if not events:
        print("No events in database.")
        return

    print(f"Top {len(events)} events by popularity:\n")
    print(f"{'Title':<40} {'Likes':>6} {'Attends':>8} {'Score':>6}")
    print("-" * 64)
    for event in events:
        title = event.get("title", "(untitled)")[:39]
        likes = event.get("like_count", 0)
        attends = event.get("attend_count", 0)
        score = likes + attends
        print(f"{title:<40} {likes:>6} {attends:>8} {score:>6}")


async def inspect(args):
    """Main inspection logic."""
    import os

    os.environ["APP_ENV"] = args.env

    from app.config import Settings
    from motor.motor_asyncio import AsyncIOMotorClient

    settings = Settings()
    client = AsyncIOMotorClient(settings.mongodb_url)
    db = client[settings.mongodb_db_name]

    print(f"Database: {settings.mongodb_db_name}")

    try:
        if args.id:
            await inspect_by_id(db, args.id)
        elif args.title:
            await inspect_by_title(db, args.title)
        elif args.popular:
            await inspect_popular(db, args.limit)
        else:
            # Default: show popular
            await inspect_popular(db, args.limit)
    finally:
        client.close()


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Inspect events in the database")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--title",
        help="Search events by title (case-insensitive partial match)",
    )
    group.add_argument(
        "--id",
        help="Look up a specific event by ObjectId",
    )
    group.add_argument(
        "--popular",
        action="store_true",
        help="Show top events by popularity (default if no other flag given)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Number of events to show for --popular (default: 10)",
    )
    parser.add_argument(
        "--env",
        default="development",
        choices=["development", "test"],
        help="Target environment (default: development)",
    )
    args = parser.parse_args()

    asyncio.run(inspect(args))


if __name__ == "__main__":
    main()
