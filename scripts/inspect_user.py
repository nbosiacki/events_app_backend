#!/usr/bin/env python3
"""
Inspect a user account in the database.

Prints the user document (excluding password_hash) along with resolved
liked/attended event titles for quick verification during development.

Usage:
    python -m scripts.inspect_user                          # Inspect dev@example.com
    python -m scripts.inspect_user --email user@example.com # Inspect specific user
    python -m scripts.inspect_user --env test               # Target test database
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


async def inspect(email: str, env: str):
    """Look up a user by email and print their account data."""
    import os

    os.environ["APP_ENV"] = env

    from app.config import Settings
    from motor.motor_asyncio import AsyncIOMotorClient
    from bson import ObjectId

    settings = Settings()
    client = AsyncIOMotorClient(settings.mongodb_url)
    db = client[settings.mongodb_db_name]

    try:
        user = await db.users.find_one({"email": email})
        if not user:
            print(f"User not found: {email}")
            print(f"Database: {settings.mongodb_db_name}")
            return

        # Remove sensitive fields
        user.pop("password_hash", None)
        user.pop("password_reset_token", None)
        user.pop("password_reset_expires", None)
        user.pop("email_verification_token", None)
        user.pop("email_verification_expires", None)

        print(f"Database: {settings.mongodb_db_name}")
        print(f"User: {email}")
        print("=" * 50)
        print(json.dumps(user, indent=2, default=serialize))

        # Resolve liked event titles
        liked = user.get("liked_events", [])
        if liked:
            print(f"\nLiked events ({len(liked)}):")
            for eid in liked:
                if ObjectId.is_valid(eid):
                    event = await db.events.find_one({"_id": ObjectId(eid)})
                    title = event["title"] if event else "(deleted)"
                else:
                    title = "(invalid id)"
                print(f"  - {eid}: {title}")
        else:
            print("\nLiked events: (none)")

        # Resolve attended event titles
        attended = user.get("attended_events", [])
        if attended:
            print(f"\nAttended events ({len(attended)}):")
            for eid in attended:
                if ObjectId.is_valid(eid):
                    event = await db.events.find_one({"_id": ObjectId(eid)})
                    title = event["title"] if event else "(deleted)"
                else:
                    title = "(invalid id)"
                print(f"  - {eid}: {title}")
        else:
            print("\nAttended events: (none)")

    finally:
        client.close()


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Inspect a user account")
    parser.add_argument(
        "--email",
        default="dev@example.com",
        help="Email of the user to inspect (default: dev@example.com)",
    )
    parser.add_argument(
        "--env",
        default="development",
        choices=["development", "test"],
        help="Target environment (default: development)",
    )
    args = parser.parse_args()

    asyncio.run(inspect(args.email, args.env))


if __name__ == "__main__":
    main()
