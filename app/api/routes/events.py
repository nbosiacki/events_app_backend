from fastapi import APIRouter, Depends, Query, HTTPException
from typing import Optional, List, Literal
from datetime import datetime, date
from bson import ObjectId
import math

from app.db.mongodb import get_database
from app.models.event import EventResponse, EventCreate, Price
from app.auth.dependencies import get_current_user_optional
from app.services.preferences import analyze_implicit_preferences
from app.services.recommendations import score_events

router = APIRouter(prefix="/events", tags=["events"])


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle distance in km between two (lat, lon) points."""
    R = 6371.0
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    dφ = math.radians(lat2 - lat1)
    dλ = math.radians(lon2 - lon1)
    a = math.sin(dφ / 2) ** 2 + math.cos(φ1) * math.cos(φ2) * math.sin(dλ / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


@router.get("/cities", response_model=List[str])
async def get_cities():
    """Return a sorted list of distinct non-null city values from the events collection."""
    db = get_database()
    cities = await db.events.distinct("city", {"city": {"$nin": [None, ""]}})
    return sorted(c for c in cities if c)


@router.get("", response_model=List[EventResponse])
async def get_events(
    date: Optional[date] = Query(None, description="Filter by date (YYYY-MM-DD)"),
    price_bucket: Optional[Literal["free", "budget", "standard", "premium"]] = Query(
        None, description="Filter by price bucket"
    ),
    city: Optional[str] = Query(None, description="Filter by city (case-insensitive substring match)"),
    sort: Literal["relevance", "time", "price_asc", "price_desc", "popular", "nearby"] = Query(
        "time", description="Sort order"
    ),
    lat: Optional[float] = Query(None, description="User latitude for nearby sort"),
    lon: Optional[float] = Query(None, description="User longitude for nearby sort"),
    limit: int = Query(50, ge=1, le=100),
    skip: int = Query(0, ge=0),
    current_user: Optional[dict] = Depends(get_current_user_optional),
):
    """Get events with optional date, price, city filtering, and sort order."""
    db = get_database()
    query = {}

    if date:
        # Filter events on this date
        start_of_day = datetime.combine(date, datetime.min.time())
        end_of_day = datetime.combine(date, datetime.max.time())
        query["datetime_start"] = {"$gte": start_of_day, "$lte": end_of_day}

    if price_bucket:
        query["price.bucket"] = price_bucket

    if city:
        query["city"] = {"$regex": city, "$options": "i"}

    # For relevance sort, fetch all matching events then score and paginate in Python
    if sort == "relevance" and current_user:
        cursor = db.events.find(query).sort("datetime_start", 1)
        all_events = await cursor.to_list(length=1000)

        implicit_prefs = await analyze_implicit_preferences(current_user, db)
        explicit_prefs = current_user.get("preferences", {})

        scored = score_events(all_events, explicit_prefs, implicit_prefs)
        paginated = scored[skip : skip + limit]
        return [EventResponse.from_mongo(event) for event in paginated]

    # Nearby sort: fetch all, compute haversine distance, sort in Python
    if sort == "nearby" and lat is not None and lon is not None:
        cursor = db.events.find(query).sort("datetime_start", 1)
        all_events = await cursor.to_list(length=1000)

        def _distance(event: dict) -> float:
            coords = event.get("venue", {}).get("coordinates")
            if not coords or len(coords) < 2:
                return float("inf")
            return _haversine_km(lat, lon, coords[0], coords[1])

        sorted_events = sorted(all_events, key=_distance)
        paginated = sorted_events[skip: skip + limit]
        return [EventResponse.from_mongo(e) for e in paginated]

    # Popular sort: aggregate pipeline with computed popularity score
    if sort == "popular":
        pipeline = [
            {"$match": query},
            {"$addFields": {
                "popularity_score": {
                    "$add": [
                        {"$ifNull": ["$like_count", 0]},
                        {"$ifNull": ["$attend_count", 0]},
                    ]
                }
            }},
            {"$sort": {"popularity_score": -1, "datetime_start": 1}},
            {"$skip": skip},
            {"$limit": limit},
        ]
        events = await db.events.aggregate(pipeline).to_list(length=limit)
        return [EventResponse.from_mongo(event) for event in events]

    # DB-level sort for time and price sorts
    if sort == "price_asc":
        mongo_sort = ("price.amount", 1)
    elif sort == "price_desc":
        mongo_sort = ("price.amount", -1)
    else:
        # "time" or unauthenticated "relevance" fallback
        mongo_sort = ("datetime_start", 1)

    cursor = db.events.find(query).sort(*mongo_sort).skip(skip).limit(limit)
    events = await cursor.to_list(length=limit)

    return [EventResponse.from_mongo(event) for event in events]


@router.get("/{event_id}", response_model=EventResponse)
async def get_event(event_id: str):
    """Get a single event by ID."""
    db = get_database()

    if not ObjectId.is_valid(event_id):
        raise HTTPException(status_code=400, detail="Invalid event ID format")

    event = await db.events.find_one({"_id": ObjectId(event_id)})

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    return EventResponse.from_mongo(event)


@router.post("", response_model=EventResponse)
async def create_event(event: EventCreate):
    """Create a new event."""
    db = get_database()

    event_dict = event.model_dump()
    event_dict["scraped_at"] = datetime.utcnow()

    # Check for duplicate by source_url
    existing = await db.events.find_one({"source_url": event.source_url})
    if existing:
        raise HTTPException(
            status_code=409, detail="Event with this source URL already exists"
        )

    result = await db.events.insert_one(event_dict)
    event_dict["_id"] = result.inserted_id

    return EventResponse.from_mongo(event_dict)


@router.delete("/{event_id}")
async def delete_event(event_id: str):
    """Delete an event by ID."""
    db = get_database()

    if not ObjectId.is_valid(event_id):
        raise HTTPException(status_code=400, detail="Invalid event ID format")

    result = await db.events.delete_one({"_id": ObjectId(event_id)})

    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Event not found")

    return {"message": "Event deleted successfully"}
