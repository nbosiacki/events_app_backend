"""Admin-protected sync endpoints.

POST /api/sync/trigger  — start a background sync, returns {status: "started"}
GET  /api/sync/status   — return the last sync result (timestamp + counts)
"""

from fastapi import APIRouter, BackgroundTasks, Depends

from app.auth.admin import require_admin_key

router = APIRouter(prefix="/sync", tags=["sync"])

# Module-level state: last sync result
_last_sync_result: dict = {"status": "never_run"}


async def _do_sync(full: bool = False) -> None:
    """Background task: run the sync and store the result."""
    global _last_sync_result
    from app.db.mongodb import get_database
    from app.db.scraper_db import get_scraper_database
    from app.services.sync import run_sync

    scraper_db = get_scraper_database()
    if scraper_db is None:
        _last_sync_result = {"status": "skipped", "reason": "scraper DB not configured"}
        return

    local_db = get_database()
    result = await run_sync(scraper_db, local_db, full=full)
    _last_sync_result = {"status": "completed", **result}


@router.post("/trigger", dependencies=[Depends(require_admin_key)])
async def trigger_sync(background_tasks: BackgroundTasks, full: bool = False):
    """Trigger a background sync from the external scraper DB.

    Pass ?full=true to sync all events regardless of age (initial or catch-up sync).
    """
    background_tasks.add_task(_do_sync, full)
    return {"status": "started", "full": full}


@router.get("/status", dependencies=[Depends(require_admin_key)])
async def sync_status():
    """Return the result of the last sync run."""
    return _last_sync_result
