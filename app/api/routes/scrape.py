from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
from datetime import datetime, timedelta
from bson import ObjectId

from app.db.mongodb import get_database
from app.agents.scraper import EventScraper
from app.agents.deduplicator import EventDeduplicator

router = APIRouter(prefix="/scrape", tags=["scrape"])


class ScrapeRequest(BaseModel):
    url: str
    source_name: str
    max_pages: int = 5


class ScrapeStatus(BaseModel):
    status: str
    message: str


async def run_scrape_task(url: str, source_name: str, max_pages: int):
    """Background task to run the scraper."""
    db = get_database()
    scraper = EventScraper()
    deduplicator = EventDeduplicator()

    try:
        events = await scraper.scrape(url, source_name, max_pages, db=db)

        if not events:
            return

        # Get existing events for deduplication
        start_date = datetime.now() - timedelta(days=1)
        end_date = datetime.now() + timedelta(days=90)
        existing_cursor = db.events.find(
            {"datetime_start": {"$gte": start_date, "$lte": end_date}}
        )
        existing_events = await existing_cursor.to_list(length=1000)

        for event in events:
            try:
                # Check for duplicate by source URL
                existing = await db.events.find_one({"source_url": event.source_url})
                if existing:
                    continue

                # Check for semantic duplicate
                duplicate_id = await deduplicator.find_duplicate(event, existing_events)
                if duplicate_id:
                    dup_event = await db.events.find_one({"_id": ObjectId(duplicate_id)})
                    if dup_event:
                        merged = await deduplicator.merge_events(event, dup_event)
                        await db.events.update_one(
                            {"_id": ObjectId(duplicate_id)}, {"$set": merged}
                        )
                    continue

                # Insert new event
                event_dict = event.model_dump()
                event_dict["scraped_at"] = datetime.utcnow()
                result = await db.events.insert_one(event_dict)
                event_dict["_id"] = result.inserted_id
                existing_events.append(event_dict)

            except Exception as e:
                print(f"Error processing event: {e}")

    finally:
        scraper.close()


@router.post("/trigger", response_model=ScrapeStatus)
async def trigger_scrape(request: ScrapeRequest, background_tasks: BackgroundTasks):
    """Trigger a background scraping task."""
    background_tasks.add_task(
        run_scrape_task, request.url, request.source_name, request.max_pages
    )
    return ScrapeStatus(
        status="started",
        message=f"Scraping {request.url} in background (max {request.max_pages} pages)",
    )
