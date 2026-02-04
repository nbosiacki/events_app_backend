from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
from datetime import datetime

from app.db.mongodb import get_database
from app.agents.scraper import EventScraper

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

    try:
        events = await scraper.scrape(url, source_name, max_pages, db=db)

        if not events:
            return

        for event in events:
            try:
                existing = await db.events.find_one({"source_url": event.source_url})
                if existing:
                    continue

                event_dict = event.model_dump()
                event_dict["scraped_at"] = datetime.utcnow()
                await db.events.insert_one(event_dict)

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
