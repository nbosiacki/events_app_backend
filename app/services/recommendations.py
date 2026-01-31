"""Event recommendation scoring based on user preferences."""

from datetime import datetime, timezone
from typing import List, Optional

from app.services.preferences import BUCKET_ORDER


def _bucket_distance(bucket_a: str, bucket_b: str) -> int:
    """Return the number of steps between two price buckets."""
    try:
        return BUCKET_ORDER.index(bucket_a) - BUCKET_ORDER.index(bucket_b)
    except ValueError:
        return 0


def _score_category(
    event_categories: List[str],
    explicit_categories: List[str],
    implicit_weights: dict,
) -> float:
    """Score category match (0–50).

    Explicit preferred category match: 50.
    Implicit: 50 * (weight / max_weight).
    Take the higher of the two.
    """
    explicit_score = 0.0
    if explicit_categories:
        for cat in event_categories:
            if cat in explicit_categories:
                explicit_score = 50.0
                break

    implicit_score = 0.0
    if implicit_weights:
        max_weight = max(implicit_weights.values()) if implicit_weights else 1.0
        for cat in event_categories:
            if cat in implicit_weights:
                score = 50.0 * (implicit_weights[cat] / max_weight)
                implicit_score = max(implicit_score, score)

    return max(explicit_score, implicit_score)


def _score_price(
    event_bucket: str,
    explicit_max_bucket: Optional[str],
    implicit_avg_bucket: Optional[str],
) -> float:
    """Score price match (0–30).

    At or below max_price_bucket: 30.
    One bucket above: 15.
    Two+ above: 0.
    If no explicit pref, use proximity to implicit avg_price_bucket.
    """
    if explicit_max_bucket and explicit_max_bucket != "premium":
        # Explicit preference set (premium means "any", so skip)
        dist = _bucket_distance(event_bucket, explicit_max_bucket)
        if dist <= 0:
            return 30.0
        elif dist == 1:
            return 15.0
        else:
            return 0.0

    if implicit_avg_bucket:
        # Use implicit: closer to avg bucket scores higher
        dist = abs(_bucket_distance(event_bucket, implicit_avg_bucket))
        if dist == 0:
            return 30.0
        elif dist == 1:
            return 20.0
        elif dist == 2:
            return 10.0
        else:
            return 0.0

    # No price preferences at all — neutral score
    return 15.0


def _score_freshness(event_start: datetime, now: datetime) -> float:
    """Score time proximity (0–20).

    Events happening sooner score higher.
    Today: 20, +7 days: 10, +14 days: 0.
    Past events: 0.
    """
    if event_start.tzinfo is None:
        event_start = event_start.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    delta_days = (event_start - now).total_seconds() / 86400

    if delta_days < 0:
        return 0.0

    # Linear decay: 20 at day 0, 0 at day 14
    score = 20.0 * max(0.0, 1.0 - delta_days / 14.0)
    return round(score, 2)


def score_events(
    events: list,
    explicit_prefs: dict,
    implicit_prefs: dict,
    now: Optional[datetime] = None,
) -> list:
    """Score and sort events by relevance to user preferences.

    Args:
        events: list of event dicts (MongoDB documents or EventResponse-like dicts)
        explicit_prefs: {preferred_categories: [...], max_price_bucket: "..."}
        implicit_prefs: {category_weights: {...}, avg_price_bucket: "..."}
        now: override current time for testing

    Returns:
        Events sorted by score descending, ties broken by datetime_start ascending.
        If no preferences exist, returns events in original order.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    explicit_categories = explicit_prefs.get("preferred_categories", [])
    explicit_max_bucket = explicit_prefs.get("max_price_bucket")
    implicit_weights = implicit_prefs.get("category_weights", {})
    implicit_avg_bucket = implicit_prefs.get("avg_price_bucket")

    has_prefs = bool(
        explicit_categories
        or (explicit_max_bucket and explicit_max_bucket != "premium")
        or implicit_weights
        or implicit_avg_bucket
    )

    if not has_prefs:
        return events

    scored = []
    for event in events:
        # Handle both dict-style and object-style access
        categories = event.get("categories", []) if isinstance(event, dict) else getattr(event, "categories", [])

        price = event.get("price", {}) if isinstance(event, dict) else getattr(event, "price", {})
        if isinstance(price, dict):
            event_bucket = price.get("bucket", "free")
        else:
            event_bucket = getattr(price, "bucket", "free")

        dt_start = event.get("datetime_start") if isinstance(event, dict) else getattr(event, "datetime_start", now)

        cat_score = _score_category(categories, explicit_categories, implicit_weights)
        price_score = _score_price(event_bucket, explicit_max_bucket, implicit_avg_bucket)
        fresh_score = _score_freshness(dt_start, now)

        total = cat_score + price_score + fresh_score
        scored.append((total, dt_start, event))

    # Sort: highest score first, then earliest datetime for ties
    scored.sort(key=lambda x: (-x[0], x[1]))

    return [item[2] for item in scored]
