"""Sync service: pull new/updated events from the external scraper MongoDB.

Called once per day by APScheduler (03:00 UTC) and on-demand via the admin API.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.services.currency import refresh_rates, convert_to_sek
from app.services.url_utils import normalize_url, make_content_hash
from app.models.event import Price

logger = logging.getLogger(__name__)


async def run_sync(scraper_db, local_db, full: bool = False) -> dict:
    """Sync events from the scraper DB into the local DB.

    Args:
        scraper_db: AsyncIOMotorDatabase for the external scraper MongoDB.
        local_db:   AsyncIOMotorDatabase for the local app MongoDB.
        full:       If True, sync all events regardless of age (initial/catch-up sync).

    Returns:
        Summary dict: {inserted, updated, skipped, errors, synced_at}
    """
    inserted = 0
    updated = 0
    skipped = 0
    errors = 0

    # Refresh exchange rates once at the start of each sync run
    refresh_rates()

    # Full sync: pull all events; incremental: only events touched in the last 25 hours
    if full:
        query: dict = {}
    else:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=25)
        query = {
            "$or": [
                {"scraped_at": {"$gte": cutoff}},
                {"updated_at": {"$gte": cutoff}},
            ]
        }

    try:
        cursor = scraper_db.scraped_events.find(query)
        scraper_events = await cursor.to_list(length=None)
    except Exception as e:
        logger.error("Failed to query scraper DB: %s", e)
        return {"inserted": 0, "updated": 0, "skipped": 0, "errors": 1, "synced_at": datetime.now(timezone.utc).isoformat()}

    for doc in scraper_events:
        try:
            source_url = doc.get("source_url")
            if not source_url:
                skipped += 1
                continue

            norm_url = normalize_url(source_url)
            event_doc = _build_event_doc(doc, norm_url)
            content_hash = event_doc["content_hash"]

            # Skip cross-URL content duplicates (same event from a different source_url)
            content_dupe = await local_db.events.find_one(
                {"content_hash": content_hash, "source_url": {"$ne": norm_url}}
            )
            if content_dupe is not None:
                logger.debug(
                    "Skipping content duplicate: %s (matches existing %s)",
                    norm_url,
                    content_dupe.get("source_url"),
                )
                skipped += 1
                continue

            # Atomic upsert on normalized source_url.
            # $setOnInsert ensures like_count/attend_count start at 0 and are never
            # overwritten on subsequent updates.
            update_fields = {k: v for k, v in event_doc.items() if k not in ("like_count", "attend_count")}
            update_fields["synced_at"] = datetime.now(timezone.utc)
            result = await local_db.events.update_one(
                {"source_url": norm_url},
                {
                    "$set": update_fields,
                    "$setOnInsert": {"like_count": 0, "attend_count": 0},
                },
                upsert=True,
            )

            if result.upserted_id is not None:
                inserted += 1
            else:
                updated += 1

        except Exception as e:
            logger.error("Error processing event %s: %s", doc.get("source_url", "<unknown>"), e)
            errors += 1

    synced_at = datetime.now(timezone.utc).isoformat()
    logger.info("Sync complete: inserted=%d updated=%d skipped=%d errors=%d", inserted, updated, skipped, errors)
    return {"inserted": inserted, "updated": updated, "skipped": skipped, "errors": errors, "synced_at": synced_at}


def _build_event_doc(doc: dict, norm_url: str) -> dict:
    """Convert a scraper event doc into the local event schema."""
    # Price normalization
    price_data = doc.get("price", {})
    amount = float(price_data.get("amount") or 0)
    currency = price_data.get("currency") or "SEK"
    sek_amount = convert_to_sek(amount, currency)
    price = Price.from_amount(sek_amount)

    # Venue and city extraction
    venue_data = doc.get("venue", {})
    city: Optional[str] = None
    if isinstance(venue_data, dict):
        city = venue_data.get("city") or None

    title = doc.get("title", "")
    venue_name = venue_data.get("name", "") if isinstance(venue_data, dict) else str(venue_data)
    dt_start = doc.get("datetime_start")

    return {
        "title": title,
        "description": doc.get("description"),
        "venue": {
            "name": venue_name,
            "address": venue_data.get("address") if isinstance(venue_data, dict) else None,
            "coordinates": venue_data.get("coordinates") if isinstance(venue_data, dict) else None,
            "country": venue_data.get("country") if isinstance(venue_data, dict) else None,
        },
        "city": city,
        "datetime_start": dt_start,
        "datetime_end": doc.get("datetime_end"),
        "price": price.model_dump(),
        "source_url": norm_url,
        "source_site": doc.get("source_site", ""),
        "categories": doc.get("categories", []),
        "image_url": doc.get("image_url"),
        "is_online": doc.get("is_online", False),
        "online_link": doc.get("online_link"),
        "tickets_available": doc.get("tickets_available"),
        "like_count": 0,
        "attend_count": 0,
        "scraped_at": doc.get("scraped_at", datetime.now(timezone.utc)),
        "content_hash": make_content_hash(title, venue_name, dt_start),
        "raw_data": None,
    }
