from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.db.mongodb import connect_to_mongo, close_mongo_connection
from app.db.scraper_db import connect_to_scraper_mongo, close_scraper_mongo_connection
from app.api.routes import auth, events, users, analytics, sync
from app.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_to_mongo()
    await connect_to_scraper_mongo()

    # Daily sync at 03:00 UTC
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from app.api.routes.sync import _do_sync

    scheduler = AsyncIOScheduler()
    scheduler.add_job(_do_sync, "cron", hour=3, minute=0)
    scheduler.start()

    yield

    scheduler.shutdown()
    await close_scraper_mongo_connection()
    await close_mongo_connection()


app = FastAPI(
    title="Sweden Events API",
    description="API for discovering events across Sweden",
    version="1.0.0",
    lifespan=lifespan,
)

# Configure CORS
_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth.router, prefix="/api")
app.include_router(events.router, prefix="/api")
app.include_router(users.router, prefix="/api")
app.include_router(sync.router, prefix="/api")
app.include_router(analytics.router, prefix="/api")


@app.get("/")
async def root():
    return {"message": "Sweden Events API", "docs": "/docs"}


@app.get("/health")
@app.get("/api/health")
async def health():
    return {"status": "healthy"}
