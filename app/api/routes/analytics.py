"""Admin analytics endpoints.

All endpoints require the X-Admin-Key header matching ADMIN_API_KEY.

Endpoints:
    GET /analytics/overview         — total events, users, likes, attends
    GET /analytics/events/popular   — top N events by popularity
    GET /analytics/categories       — category breakdown
    GET /analytics/venues           — venue stats
"""

from fastapi import APIRouter, Depends, Query

from app.db.mongodb import get_database
from app.auth.admin import require_admin_key

router = APIRouter(
    prefix="/analytics",
    tags=["analytics"],
    dependencies=[Depends(require_admin_key)],
)


@router.get("/overview")
async def get_overview():
    """Site-wide overview stats."""
    db = get_database()

    total_events = await db.events.count_documents({})
    total_users = await db.users.count_documents({})

    pipeline = [
        {"$group": {
            "_id": None,
            "total_likes": {"$sum": {"$ifNull": ["$like_count", 0]}},
            "total_attends": {"$sum": {"$ifNull": ["$attend_count", 0]}},
        }}
    ]
    result = await db.events.aggregate(pipeline).to_list(length=1)
    totals = result[0] if result else {"total_likes": 0, "total_attends": 0}

    return {
        "total_events": total_events,
        "total_users": total_users,
        "total_likes": totals["total_likes"],
        "total_attends": totals["total_attends"],
    }


@router.get("/events/popular")
async def get_popular_events(limit: int = Query(10, ge=1, le=50)):
    """Top N events by combined like_count + attend_count."""
    db = get_database()

    pipeline = [
        {"$addFields": {
            "popularity_score": {
                "$add": [
                    {"$ifNull": ["$like_count", 0]},
                    {"$ifNull": ["$attend_count", 0]},
                ]
            }
        }},
        {"$sort": {"popularity_score": -1}},
        {"$limit": limit},
        {"$project": {
            "_id": 0,
            "id": {"$toString": "$_id"},
            "title": 1,
            "venue": "$venue.name",
            "categories": 1,
            "like_count": {"$ifNull": ["$like_count", 0]},
            "attend_count": {"$ifNull": ["$attend_count", 0]},
            "popularity_score": 1,
            "datetime_start": 1,
        }},
    ]
    return await db.events.aggregate(pipeline).to_list(length=limit)


@router.get("/categories")
async def get_category_stats():
    """Category breakdown: event count, total likes, avg popularity per category."""
    db = get_database()

    pipeline = [
        {"$unwind": "$categories"},
        {"$group": {
            "_id": "$categories",
            "event_count": {"$sum": 1},
            "total_likes": {"$sum": {"$ifNull": ["$like_count", 0]}},
            "total_attends": {"$sum": {"$ifNull": ["$attend_count", 0]}},
            "avg_popularity": {"$avg": {
                "$add": [
                    {"$ifNull": ["$like_count", 0]},
                    {"$ifNull": ["$attend_count", 0]},
                ]
            }},
        }},
        {"$sort": {"total_likes": -1}},
        {"$project": {
            "_id": 0,
            "category": "$_id",
            "event_count": 1,
            "total_likes": 1,
            "total_attends": 1,
            "avg_popularity": {"$round": ["$avg_popularity", 1]},
        }},
    ]
    return await db.events.aggregate(pipeline).to_list(length=100)


@router.get("/venues")
async def get_venue_stats(limit: int = Query(15, ge=1, le=50)):
    """Venue breakdown: event count, total likes, top event per venue."""
    db = get_database()

    pipeline = [
        {"$addFields": {
            "popularity_score": {
                "$add": [
                    {"$ifNull": ["$like_count", 0]},
                    {"$ifNull": ["$attend_count", 0]},
                ]
            }
        }},
        {"$sort": {"popularity_score": -1}},
        {"$group": {
            "_id": "$venue.name",
            "event_count": {"$sum": 1},
            "total_likes": {"$sum": {"$ifNull": ["$like_count", 0]}},
            "total_attends": {"$sum": {"$ifNull": ["$attend_count", 0]}},
            "top_event": {"$first": "$title"},
            "top_event_score": {"$first": "$popularity_score"},
        }},
        {"$sort": {"total_likes": -1}},
        {"$limit": limit},
        {"$project": {
            "_id": 0,
            "venue": "$_id",
            "event_count": 1,
            "total_likes": 1,
            "total_attends": 1,
            "top_event": 1,
            "top_event_score": 1,
        }},
    ]
    return await db.events.aggregate(pipeline).to_list(length=limit)
