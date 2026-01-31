"""Tests for the implicit preference analysis service."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.preferences import analyze_implicit_preferences


def _make_event(eid, categories, price_amount=0):
    """Helper to create a minimal event doc."""
    from bson import ObjectId

    return {
        "_id": ObjectId(eid) if ObjectId.is_valid(eid) else eid,
        "categories": categories,
        "price": {"amount": price_amount, "currency": "SEK", "bucket": "free"},
    }


def _mock_db(events):
    """Create a mock db where events.find returns an async iterable of events."""
    db = MagicMock()
    cursor = AsyncMock()

    # Make the cursor work as an async iterator
    cursor.__aiter__ = MagicMock(return_value=iter(events))
    cursor.__anext__ = AsyncMock(side_effect=StopAsyncIteration)

    # Motor cursor supports async for via __aiter__
    async def async_iter():
        for e in events:
            yield e

    db.events.find.return_value = async_iter()
    return db


class TestAnalyzeImplicitPreferences:
    async def test_empty_engagement(self):
        """Returns empty profile when user has no liked/attended events."""
        user = {"liked_events": [], "attended_events": []}
        db = MagicMock()

        result = await analyze_implicit_preferences(user, db)

        assert result["category_weights"] == {}
        assert result["avg_price"] == 0.0
        assert result["avg_price_bucket"] == "free"

    async def test_no_engagement_keys(self):
        """Returns empty profile when user doc lacks engagement fields."""
        user = {}
        db = MagicMock()

        result = await analyze_implicit_preferences(user, db)

        assert result["category_weights"] == {}

    async def test_liked_category_weights(self):
        """Liked events contribute weight of 1.0 per category."""
        eid = "507f1f77bcf86cd799439011"
        events = [_make_event(eid, ["music", "art"])]
        user = {"liked_events": [eid], "attended_events": []}
        db = _mock_db(events)

        result = await analyze_implicit_preferences(user, db)

        assert result["category_weights"]["music"] == 1.0
        assert result["category_weights"]["art"] == 1.0

    async def test_attended_category_weights(self):
        """Attended events contribute weight of 1.5 per category."""
        eid = "507f1f77bcf86cd799439011"
        events = [_make_event(eid, ["food"])]
        user = {"liked_events": [], "attended_events": [eid]}
        db = _mock_db(events)

        result = await analyze_implicit_preferences(user, db)

        assert result["category_weights"]["food"] == 1.5

    async def test_mixed_engagement(self):
        """Both liked and attended events contribute to weights."""
        eid1 = "507f1f77bcf86cd799439011"
        eid2 = "507f1f77bcf86cd799439012"
        events = [
            _make_event(eid1, ["music"], price_amount=100),
            _make_event(eid2, ["music", "art"], price_amount=200),
        ]
        user = {"liked_events": [eid1], "attended_events": [eid2]}
        db = _mock_db(events)

        result = await analyze_implicit_preferences(user, db)

        # music: 1.0 (liked) + 1.5 (attended) = 2.5
        assert result["category_weights"]["music"] == 2.5
        # art: 1.5 (attended only)
        assert result["category_weights"]["art"] == 1.5

    async def test_avg_price(self):
        """Computes average price across engaged events."""
        eid1 = "507f1f77bcf86cd799439011"
        eid2 = "507f1f77bcf86cd799439012"
        events = [
            _make_event(eid1, ["music"], price_amount=100),
            _make_event(eid2, ["art"], price_amount=300),
        ]
        user = {"liked_events": [eid1, eid2], "attended_events": []}
        db = _mock_db(events)

        result = await analyze_implicit_preferences(user, db)

        assert result["avg_price"] == 200.0
        assert result["avg_price_bucket"] == "standard"

    async def test_avg_price_bucket_free(self):
        """Average of free events maps to free bucket."""
        eid = "507f1f77bcf86cd799439011"
        events = [_make_event(eid, ["comedy"], price_amount=0)]
        user = {"liked_events": [eid], "attended_events": []}
        db = _mock_db(events)

        result = await analyze_implicit_preferences(user, db)

        assert result["avg_price"] == 0.0
        assert result["avg_price_bucket"] == "free"

    async def test_event_in_both_liked_and_attended(self):
        """Event in both sets uses higher weight (attended=1.5)."""
        eid = "507f1f77bcf86cd799439011"
        events = [_make_event(eid, ["theater"], price_amount=150)]
        user = {"liked_events": [eid], "attended_events": [eid]}
        db = _mock_db(events)

        result = await analyze_implicit_preferences(user, db)

        # Should use attended weight (1.5) since it's higher
        assert result["category_weights"]["theater"] == 1.5

    async def test_invalid_event_ids_skipped(self):
        """Invalid ObjectId strings are filtered out gracefully."""
        user = {"liked_events": ["not-a-valid-id"], "attended_events": []}
        db = _mock_db([])

        result = await analyze_implicit_preferences(user, db)

        assert result["category_weights"] == {}
