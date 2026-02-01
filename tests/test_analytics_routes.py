"""
Tests for the /api/analytics endpoints.

Covers:
    TestAdminAuth          – missing key (422), invalid key (401), valid key (200)
    TestOverview           – empty DB zeros, correct totals with data
    TestPopularEvents      – sorted by popularity_score descending
    TestCategoryStats      – correct per-category breakdown
    TestVenueStats         – correct per-venue breakdown

All endpoints require the X-Admin-Key header. Tests use the key set in
conftest.py (ADMIN_API_KEY=test-admin-key).
"""

from datetime import datetime, timezone


ADMIN_HEADERS = {"X-Admin-Key": "test-admin-key"}


class TestAdminAuth:
    """X-Admin-Key authentication for all analytics endpoints."""

    async def test_missing_key_returns_422(self, client):
        """Omitting the X-Admin-Key header should return 422 (validation error)."""
        response = await client.get("/api/analytics/overview")
        assert response.status_code == 422

    async def test_invalid_key_returns_401(self, client):
        """A wrong admin key should return 401 Unauthorized."""
        response = await client.get(
            "/api/analytics/overview",
            headers={"X-Admin-Key": "wrong-key"},
        )
        assert response.status_code == 401

    async def test_valid_key_returns_200(self, client):
        """A correct admin key should grant access."""
        response = await client.get(
            "/api/analytics/overview",
            headers=ADMIN_HEADERS,
        )
        assert response.status_code == 200


class TestOverview:
    """GET /api/analytics/overview — site-wide stats."""

    async def test_empty_database_returns_zeros(self, client):
        """With no data, all counters should be zero."""
        response = await client.get(
            "/api/analytics/overview",
            headers=ADMIN_HEADERS,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total_events"] == 0
        assert data["total_users"] == 0
        assert data["total_likes"] == 0
        assert data["total_attends"] == 0

    async def test_returns_correct_totals(self, client, setup_db):
        """Overview should reflect inserted events and users."""
        from app.db import mongodb

        await mongodb.db.events.insert_one({
            "title": "Event A",
            "venue": {"name": "V"},
            "datetime_start": datetime(2025, 4, 1, 18, 0),
            "price": {"amount": 0, "currency": "SEK", "bucket": "free"},
            "source_url": "https://example.com/a",
            "source_site": "example.com",
            "categories": ["music"],
            "scraped_at": datetime.now(timezone.utc),
            "like_count": 5,
            "attend_count": 3,
        })
        await mongodb.db.events.insert_one({
            "title": "Event B",
            "venue": {"name": "V"},
            "datetime_start": datetime(2025, 4, 2, 18, 0),
            "price": {"amount": 0, "currency": "SEK", "bucket": "free"},
            "source_url": "https://example.com/b",
            "source_site": "example.com",
            "categories": ["art"],
            "scraped_at": datetime.now(timezone.utc),
            "like_count": 2,
            "attend_count": 1,
        })

        response = await client.get(
            "/api/analytics/overview",
            headers=ADMIN_HEADERS,
        )
        data = response.json()
        assert data["total_events"] == 2
        assert data["total_likes"] == 7
        assert data["total_attends"] == 4


class TestPopularEvents:
    """GET /api/analytics/events/popular — top events by popularity."""

    async def test_sorted_by_popularity(self, client, setup_db):
        """Events should be returned in descending order of popularity_score."""
        from app.db import mongodb

        await mongodb.db.events.insert_one({
            "title": "Low Pop",
            "venue": {"name": "V"},
            "datetime_start": datetime(2025, 4, 1, 18, 0),
            "price": {"amount": 0, "currency": "SEK", "bucket": "free"},
            "source_url": "https://example.com/low",
            "source_site": "example.com",
            "categories": [],
            "scraped_at": datetime.now(timezone.utc),
            "like_count": 1,
            "attend_count": 0,
        })
        await mongodb.db.events.insert_one({
            "title": "High Pop",
            "venue": {"name": "V"},
            "datetime_start": datetime(2025, 4, 1, 20, 0),
            "price": {"amount": 0, "currency": "SEK", "bucket": "free"},
            "source_url": "https://example.com/high",
            "source_site": "example.com",
            "categories": [],
            "scraped_at": datetime.now(timezone.utc),
            "like_count": 10,
            "attend_count": 5,
        })

        response = await client.get(
            "/api/analytics/events/popular",
            headers=ADMIN_HEADERS,
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["title"] == "High Pop"
        assert data[0]["popularity_score"] == 15
        assert data[1]["title"] == "Low Pop"

    async def test_limit_parameter(self, client, setup_db):
        """The limit parameter should cap results."""
        from app.db import mongodb

        for i in range(5):
            await mongodb.db.events.insert_one({
                "title": f"Event {i}",
                "venue": {"name": "V"},
                "datetime_start": datetime(2025, 4, 1, 10 + i, 0),
                "price": {"amount": 0, "currency": "SEK", "bucket": "free"},
                "source_url": f"https://example.com/pop-{i}",
                "source_site": "example.com",
                "categories": [],
                "scraped_at": datetime.now(timezone.utc),
                "like_count": i,
                "attend_count": 0,
            })

        response = await client.get(
            "/api/analytics/events/popular?limit=3",
            headers=ADMIN_HEADERS,
        )
        assert len(response.json()) == 3


class TestCategoryStats:
    """GET /api/analytics/categories — per-category breakdown."""

    async def test_correct_category_breakdown(self, client, setup_db):
        """Should correctly aggregate counts per category."""
        from app.db import mongodb

        await mongodb.db.events.insert_one({
            "title": "Music Event",
            "venue": {"name": "V"},
            "datetime_start": datetime(2025, 4, 1, 18, 0),
            "price": {"amount": 0, "currency": "SEK", "bucket": "free"},
            "source_url": "https://example.com/cat-music",
            "source_site": "example.com",
            "categories": ["music"],
            "scraped_at": datetime.now(timezone.utc),
            "like_count": 10,
            "attend_count": 5,
        })
        await mongodb.db.events.insert_one({
            "title": "Another Music Event",
            "venue": {"name": "V"},
            "datetime_start": datetime(2025, 4, 2, 18, 0),
            "price": {"amount": 0, "currency": "SEK", "bucket": "free"},
            "source_url": "https://example.com/cat-music2",
            "source_site": "example.com",
            "categories": ["music"],
            "scraped_at": datetime.now(timezone.utc),
            "like_count": 4,
            "attend_count": 2,
        })
        await mongodb.db.events.insert_one({
            "title": "Art Event",
            "venue": {"name": "V"},
            "datetime_start": datetime(2025, 4, 3, 18, 0),
            "price": {"amount": 0, "currency": "SEK", "bucket": "free"},
            "source_url": "https://example.com/cat-art",
            "source_site": "example.com",
            "categories": ["art"],
            "scraped_at": datetime.now(timezone.utc),
            "like_count": 1,
            "attend_count": 0,
        })

        response = await client.get(
            "/api/analytics/categories",
            headers=ADMIN_HEADERS,
        )
        assert response.status_code == 200
        data = response.json()

        cats = {c["category"]: c for c in data}
        assert "music" in cats
        assert cats["music"]["event_count"] == 2
        assert cats["music"]["total_likes"] == 14
        assert cats["music"]["total_attends"] == 7

        assert "art" in cats
        assert cats["art"]["event_count"] == 1
        assert cats["art"]["total_likes"] == 1


class TestVenueStats:
    """GET /api/analytics/venues — per-venue breakdown."""

    async def test_correct_venue_breakdown(self, client, setup_db):
        """Should correctly aggregate counts per venue with top event."""
        from app.db import mongodb

        await mongodb.db.events.insert_one({
            "title": "Big Concert",
            "venue": {"name": "Konserthuset"},
            "datetime_start": datetime(2025, 4, 1, 18, 0),
            "price": {"amount": 0, "currency": "SEK", "bucket": "free"},
            "source_url": "https://example.com/ven-big",
            "source_site": "example.com",
            "categories": ["music"],
            "scraped_at": datetime.now(timezone.utc),
            "like_count": 20,
            "attend_count": 10,
        })
        await mongodb.db.events.insert_one({
            "title": "Small Concert",
            "venue": {"name": "Konserthuset"},
            "datetime_start": datetime(2025, 4, 2, 18, 0),
            "price": {"amount": 0, "currency": "SEK", "bucket": "free"},
            "source_url": "https://example.com/ven-small",
            "source_site": "example.com",
            "categories": ["music"],
            "scraped_at": datetime.now(timezone.utc),
            "like_count": 2,
            "attend_count": 1,
        })
        await mongodb.db.events.insert_one({
            "title": "Art Show",
            "venue": {"name": "Moderna Museet"},
            "datetime_start": datetime(2025, 4, 3, 18, 0),
            "price": {"amount": 0, "currency": "SEK", "bucket": "free"},
            "source_url": "https://example.com/ven-art",
            "source_site": "example.com",
            "categories": ["art"],
            "scraped_at": datetime.now(timezone.utc),
            "like_count": 5,
            "attend_count": 3,
        })

        response = await client.get(
            "/api/analytics/venues",
            headers=ADMIN_HEADERS,
        )
        assert response.status_code == 200
        data = response.json()

        venues = {v["venue"]: v for v in data}

        assert "Konserthuset" in venues
        assert venues["Konserthuset"]["event_count"] == 2
        assert venues["Konserthuset"]["total_likes"] == 22
        assert venues["Konserthuset"]["total_attends"] == 11
        assert venues["Konserthuset"]["top_event"] == "Big Concert"

        assert "Moderna Museet" in venues
        assert venues["Moderna Museet"]["event_count"] == 1
