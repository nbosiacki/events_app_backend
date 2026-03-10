"""Second Motor client for the external scraper MongoDB (read-only during sync).

If SCRAPER_MONGODB_URL is not configured, all functions return None gracefully
and the sync service skips cleanly.
"""

import logging
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

client: Optional[AsyncIOMotorClient] = None
db: Optional[AsyncIOMotorDatabase] = None


async def connect_to_scraper_mongo() -> None:
    """Open the scraper MongoDB connection, if configured."""
    global client, db
    from app.config import get_settings

    settings = get_settings()
    if not settings.scraper_mongodb_url or not settings.scraper_mongodb_db_name:
        logger.info("Scraper MongoDB not configured — sync will be skipped")
        return

    client = AsyncIOMotorClient(settings.scraper_mongodb_url)
    db = client[settings.scraper_mongodb_db_name]
    logger.info("Connected to scraper MongoDB: %s", settings.scraper_mongodb_db_name)


async def close_scraper_mongo_connection() -> None:
    """Close the scraper MongoDB connection."""
    global client, db
    if client is not None:
        client.close()
        client = None
        db = None


def get_scraper_database() -> Optional[AsyncIOMotorDatabase]:
    """Return the scraper database, or None if not configured."""
    return db
