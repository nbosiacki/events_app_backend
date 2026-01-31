"""Implicit preference analysis from user engagement history."""

from collections import defaultdict
from typing import List

from bson import ObjectId


# Weight multipliers for different engagement types
LIKED_WEIGHT = 1.0
ATTENDED_WEIGHT = 1.5

# Bucket ordering for price proximity calculations
BUCKET_ORDER = ["free", "budget", "standard", "premium"]


def _bucket_from_amount(amount: float) -> str:
    """Map a price amount to a bucket (mirrors Price.from_amount logic)."""
    if amount == 0:
        return "free"
    elif amount < 100:
        return "budget"
    elif amount <= 300:
        return "standard"
    else:
        return "premium"


async def analyze_implicit_preferences(user: dict, db) -> dict:
    """Analyze liked/attended events to build an implicit preference profile.

    Returns a dict with:
        category_weights: {category: weight} — higher = stronger signal
        avg_price: average price amount across engaged events
        avg_price_bucket: bucket corresponding to avg_price
    Returns empty dict values if user has no engagement history.
    """
    liked_ids = user.get("liked_events", [])
    attended_ids = user.get("attended_events", [])

    if not liked_ids and not attended_ids:
        return {
            "category_weights": {},
            "avg_price": 0.0,
            "avg_price_bucket": "free",
        }

    # Fetch event documents for both sets (deduplicate IDs that appear in both)
    all_ids = set(liked_ids) | set(attended_ids)
    valid_oids = [ObjectId(eid) for eid in all_ids if ObjectId.is_valid(eid)]

    if not valid_oids:
        return {
            "category_weights": {},
            "avg_price": 0.0,
            "avg_price_bucket": "free",
        }

    cursor = db.events.find({"_id": {"$in": valid_oids}})
    events_by_id = {}
    async for event in cursor:
        events_by_id[str(event["_id"])] = event

    # Build category weights and collect prices
    category_weights: dict[str, float] = defaultdict(float)
    prices: List[float] = []

    liked_set = set(liked_ids)
    attended_set = set(attended_ids)

    for eid, event in events_by_id.items():
        # Determine weight: use the higher weight if in both sets
        weight = 0.0
        if eid in attended_set:
            weight = ATTENDED_WEIGHT
        elif eid in liked_set:
            weight = LIKED_WEIGHT

        for category in event.get("categories", []):
            category_weights[category] += weight

        price_data = event.get("price", {})
        amount = price_data.get("amount", 0) if isinstance(price_data, dict) else 0
        prices.append(amount)

    avg_price = sum(prices) / len(prices) if prices else 0.0

    return {
        "category_weights": dict(category_weights),
        "avg_price": avg_price,
        "avg_price_bucket": _bucket_from_amount(avg_price),
    }
