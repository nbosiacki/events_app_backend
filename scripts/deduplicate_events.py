#!/usr/bin/env python3
"""
One-time migration: deduplicate events in the local database.

Run this after upgrading to content_hash deduplication to clean up any
duplicates that were inserted before the new dedup logic was in place.

Two kinds of duplicates are handled:
  1. URL-variant duplicates  — same event, different tracking params in source_url
                               (e.g. ?aff=x vs ?ref=y).  Grouped by normalize_url().
  2. Content duplicates      — same event from two different source sites.
                               Grouped by content_hash (title + venue + date).

For each duplicate group the "winner" is the event with the highest combined
engagement (like_count + attend_count), with scraped_at used as a tiebreaker.
User liked_events / attended_events references that point at losers are
redirected to the winner before the losers are deleted.

Usage:
    python -m scripts.deduplicate_events                  # dev database
    python -m scripts.deduplicate_events --env seed       # seed database
    python -m scripts.deduplicate_events --dry-run        # preview only, no writes
"""

import asyncio
import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


async def deduplicate(env: str, dry_run: bool) -> None:
    os.environ["APP_ENV"] = env

    from motor.motor_asyncio import AsyncIOMotorClient
    from app.config import Settings
    from app.services.url_utils import normalize_url, make_content_hash

    settings = Settings()
    client = AsyncIOMotorClient(settings.mongodb_url)
    db = client[settings.mongodb_db_name]

    print(f"Target database: {settings.mongodb_db_name}")
    if dry_run:
        print("DRY RUN — no writes will be made\n")

    total_events = await db.events.count_documents({})
    print(f"Total events: {total_events}")

    # ------------------------------------------------------------------ #
    # Step 1: Backfill content_hash on events that don't have it yet      #
    # ------------------------------------------------------------------ #
    missing_hash = await db.events.count_documents({"content_hash": {"$exists": False}})
    if missing_hash:
        print(f"\nBackfilling content_hash on {missing_hash} events...")
        cursor = db.events.find({"content_hash": {"$exists": False}})
        backfilled = 0
        async for doc in cursor:
            title = doc.get("title", "")
            venue_name = (doc.get("venue") or {}).get("name", "")
            dt_start = doc.get("datetime_start")
            content_hash = make_content_hash(title, venue_name, dt_start)
            if not dry_run:
                await db.events.update_one(
                    {"_id": doc["_id"]},
                    {"$set": {"content_hash": content_hash}},
                )
            backfilled += 1
        print(f"  Backfilled {backfilled} events")
    else:
        print("\nAll events already have content_hash — skipping backfill")

    # Reload all events with full data for grouping
    all_events = await db.events.find({}).to_list(length=None)
    print(f"\nLoaded {len(all_events)} events for deduplication analysis")

    # ------------------------------------------------------------------ #
    # Step 2: Group by normalized source_url                              #
    # ------------------------------------------------------------------ #
    url_groups: dict[str, list] = defaultdict(list)
    for event in all_events:
        raw_url = event.get("source_url", "")
        norm = normalize_url(raw_url)
        url_groups[norm].append(event)

    url_dupes = {k: v for k, v in url_groups.items() if len(v) > 1}
    print(f"\nURL-variant duplicate groups: {len(url_dupes)}")

    url_deleted = await _merge_groups(db, url_dupes, "URL-variant", dry_run)

    # Reload after URL-variant merge before content_hash grouping
    all_events = await db.events.find({}).to_list(length=None)

    # ------------------------------------------------------------------ #
    # Step 3: Group by content_hash                                       #
    # ------------------------------------------------------------------ #
    hash_groups: dict[str, list] = defaultdict(list)
    for event in all_events:
        h = event.get("content_hash")
        if h:
            hash_groups[h].append(event)

    hash_dupes = {k: v for k, v in hash_groups.items() if len(v) > 1}
    print(f"Content-hash duplicate groups: {len(hash_dupes)}")

    hash_deleted = await _merge_groups(db, hash_dupes, "content-hash", dry_run)

    # ------------------------------------------------------------------ #
    # Step 4: Rebuild indexes                                             #
    # ------------------------------------------------------------------ #
    if not dry_run and (url_deleted + hash_deleted) > 0:
        print("\nRebuilding indexes...")
        await db.events.create_index("source_url", unique=True)
        await db.events.create_index("content_hash", unique=True, sparse=True)
        print("  Indexes rebuilt")

    final_count = await db.events.count_documents({})
    print(f"\nDone.  Events before: {total_events}  |  after: {final_count}  |  removed: {total_events - final_count}")
    client.close()


def _pick_winner(group: list) -> tuple:
    """Return (winner, losers) from a group — winner has highest engagement."""
    def score(doc):
        engagement = doc.get("like_count", 0) + doc.get("attend_count", 0)
        scraped_at = doc.get("scraped_at")
        # Use scraped_at as a tiebreaker (more recent = higher score)
        ts = scraped_at.timestamp() if scraped_at and hasattr(scraped_at, "timestamp") else 0
        return (engagement, ts)

    ranked = sorted(group, key=score, reverse=True)
    return ranked[0], ranked[1:]


async def _merge_groups(db, groups: dict, label: str, dry_run: bool) -> int:
    """Merge each duplicate group: redirect user refs, delete losers. Returns deleted count."""
    from bson import ObjectId

    deleted = 0
    for key, group in groups.items():
        winner, losers = _pick_winner(group)
        winner_id = str(winner["_id"])

        loser_ids = [str(doc["_id"]) for doc in losers]
        loser_oids = [doc["_id"] for doc in losers]

        print(
            f"  [{label}] key={key[:40]}... winner={winner_id} "
            f"(engage={winner.get('like_count',0)+winner.get('attend_count',0)}) "
            f"losers={loser_ids}"
        )

        if not dry_run:
            # Redirect user references from each loser to the winner.
            # $addToSet and $pull cannot touch the same field in one operation —
            # use two sequential updates per collection.
            for loser_id in loser_ids:
                await db.users.update_many(
                    {"liked_events": loser_id},
                    {"$addToSet": {"liked_events": winner_id}},
                )
                await db.users.update_many(
                    {"liked_events": loser_id},
                    {"$pull": {"liked_events": loser_id}},
                )
                await db.users.update_many(
                    {"attended_events": loser_id},
                    {"$addToSet": {"attended_events": winner_id}},
                )
                await db.users.update_many(
                    {"attended_events": loser_id},
                    {"$pull": {"attended_events": loser_id}},
                )

            # Merge engagement counts into winner (add loser counts, don't double-count users)
            extra_likes = sum(doc.get("like_count", 0) for doc in losers)
            extra_attends = sum(doc.get("attend_count", 0) for doc in losers)
            if extra_likes or extra_attends:
                await db.events.update_one(
                    {"_id": winner["_id"]},
                    {"$inc": {"like_count": extra_likes, "attend_count": extra_attends}},
                )

            # Delete losers
            result = await db.events.delete_many({"_id": {"$in": loser_oids}})
            deleted += result.deleted_count
        else:
            deleted += len(losers)

    return deleted


def main():
    parser = argparse.ArgumentParser(description="Deduplicate events in the local database")
    parser.add_argument(
        "--env",
        default="development",
        choices=["development", "seed"],
        help="Target environment (default: development)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be deleted without making any changes",
    )
    args = parser.parse_args()
    asyncio.run(deduplicate(args.env, args.dry_run))


if __name__ == "__main__":
    main()
