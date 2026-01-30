"""
Tests for the /api/events endpoints.

Covers:
    GET  /api/events         – listing with date, price, pagination, and sort filters
    GET  /api/events/{id}    – single event retrieval, 404, and invalid ObjectId
    POST /api/events         – creation and duplicate source_url rejection (409)
    DELETE /api/events/{id}  – deletion, 404, and invalid ObjectId

All tests use a real MongoDB test database (stockholm_events_test) via the
setup_db fixture from conftest.py.
"""

from datetime import datetime, timezone
from bson import ObjectId


class TestGetEvents:
    """GET /api/events — list events with optional filters."""

    async def test_empty_database_returns_empty_list(self, client):
        """When no events exist, the endpoint should return an empty JSON array."""
        response = await client.get("/api/events")
        assert response.status_code == 200
        assert response.json() == []

    async def test_returns_inserted_events(self, client, sample_event):
        """After inserting an event via the sample_event fixture, it should appear."""
        response = await client.get("/api/events")
        assert response.status_code == 200

        data = response.json()
        assert len(data) == 1
        assert data[0]["title"] == "Test Concert"
        assert data[0]["venue"]["name"] == "Konserthuset"

    async def test_date_filter_matches(self, client, sample_event):
        """Filtering by the event's date should return it."""
        response = await client.get("/api/events?date=2025-03-15")
        assert response.status_code == 200
        assert len(response.json()) == 1

    async def test_date_filter_excludes(self, client, sample_event):
        """Filtering by a different date should return no results."""
        response = await client.get("/api/events?date=2025-03-16")
        assert response.status_code == 200
        assert len(response.json()) == 0

    async def test_price_bucket_filter_matches(self, client, sample_event):
        """The sample event has bucket=standard, so filtering by standard works."""
        response = await client.get("/api/events?price_bucket=standard")
        assert response.status_code == 200
        assert len(response.json()) == 1

    async def test_price_bucket_filter_excludes(self, client, sample_event):
        """Filtering by a bucket the sample event doesn't belong to returns nothing."""
        response = await client.get("/api/events?price_bucket=free")
        assert response.status_code == 200
        assert len(response.json()) == 0

    async def test_limit_parameter(self, client, setup_db):
        """The limit parameter should cap the number of returned events."""
        from app.db import mongodb

        for i in range(5):
            await mongodb.db.events.insert_one({
                "title": f"Event {i}",
                "venue": {"name": "Venue"},
                "datetime_start": datetime(2025, 4, 1, 10 + i, 0),
                "price": {"amount": 0, "currency": "SEK", "bucket": "free"},
                "source_url": f"https://example.com/e{i}",
                "source_site": "example.com",
                "categories": [],
                "scraped_at": datetime.now(timezone.utc),
            })

        response = await client.get("/api/events?limit=3")
        assert response.status_code == 200
        assert len(response.json()) == 3

    async def test_skip_parameter(self, client, setup_db):
        """The skip parameter should offset the result set."""
        from app.db import mongodb

        for i in range(5):
            await mongodb.db.events.insert_one({
                "title": f"Event {i}",
                "venue": {"name": "Venue"},
                "datetime_start": datetime(2025, 4, 1, 10 + i, 0),
                "price": {"amount": 0, "currency": "SEK", "bucket": "free"},
                "source_url": f"https://example.com/e{i}",
                "source_site": "example.com",
                "categories": [],
                "scraped_at": datetime.now(timezone.utc),
            })

        response = await client.get("/api/events?skip=3")
        assert response.status_code == 200
        assert len(response.json()) == 2

    async def test_events_sorted_by_datetime_ascending(self, client, setup_db):
        """Events should be returned sorted by datetime_start in ascending order."""
        from app.db import mongodb

        await mongodb.db.events.insert_one({
            "title": "Late Event",
            "venue": {"name": "Venue"},
            "datetime_start": datetime(2025, 4, 2, 20, 0),
            "price": {"amount": 0, "currency": "SEK", "bucket": "free"},
            "source_url": "https://example.com/late",
            "source_site": "example.com",
            "categories": [],
            "scraped_at": datetime.now(timezone.utc),
        })
        await mongodb.db.events.insert_one({
            "title": "Early Event",
            "venue": {"name": "Venue"},
            "datetime_start": datetime(2025, 4, 1, 10, 0),
            "price": {"amount": 0, "currency": "SEK", "bucket": "free"},
            "source_url": "https://example.com/early",
            "source_site": "example.com",
            "categories": [],
            "scraped_at": datetime.now(timezone.utc),
        })

        response = await client.get("/api/events")
        data = response.json()
        assert data[0]["title"] == "Early Event"
        assert data[1]["title"] == "Late Event"


class TestGetEventById:
    """GET /api/events/{event_id} — single event lookup."""

    async def test_found(self, client, sample_event):
        """A valid event ID should return the full event with status 200."""
        event_id = str(sample_event["_id"])
        response = await client.get(f"/api/events/{event_id}")
        assert response.status_code == 200
        assert response.json()["title"] == "Test Concert"

    async def test_not_found(self, client):
        """A non-existent but valid ObjectId should return 404."""
        fake_id = str(ObjectId())
        response = await client.get(f"/api/events/{fake_id}")
        assert response.status_code == 404

    async def test_invalid_object_id(self, client):
        """A malformed ID string should return 400."""
        response = await client.get("/api/events/not-a-valid-id")
        assert response.status_code == 400


class TestCreateEvent:
    """POST /api/events — event creation."""

    async def test_create_event(self, client):
        """A valid event payload should create the event and return 200."""
        payload = {
            "title": "New Event",
            "venue": {"name": "Test Venue"},
            "datetime_start": "2025-06-01T18:00:00",
            "price": {"amount": 0, "currency": "SEK", "bucket": "free"},
            "source_url": "https://example.com/new-event",
            "source_site": "example.com",
        }
        response = await client.post("/api/events", json=payload)
        assert response.status_code == 200

        data = response.json()
        assert data["title"] == "New Event"
        assert "id" in data

    async def test_duplicate_source_url_returns_409(self, client, sample_event):
        """Posting an event with an existing source_url should return 409 Conflict."""
        payload = {
            "title": "Duplicate Event",
            "venue": {"name": "Venue"},
            "datetime_start": "2025-06-01T18:00:00",
            "price": {"amount": 0, "currency": "SEK", "bucket": "free"},
            "source_url": sample_event["source_url"],
            "source_site": "example.com",
        }
        response = await client.post("/api/events", json=payload)
        assert response.status_code == 409


class TestDeleteEvent:
    """DELETE /api/events/{event_id} — event deletion."""

    async def test_delete_existing_event(self, client, sample_event):
        """Deleting an existing event should return 200 and remove it."""
        event_id = str(sample_event["_id"])
        response = await client.delete(f"/api/events/{event_id}")
        assert response.status_code == 200

        # Verify it's gone
        get_response = await client.get(f"/api/events/{event_id}")
        assert get_response.status_code == 404

    async def test_delete_not_found(self, client):
        """Deleting a non-existent event should return 404."""
        fake_id = str(ObjectId())
        response = await client.delete(f"/api/events/{fake_id}")
        assert response.status_code == 404

    async def test_delete_invalid_id(self, client):
        """Deleting with a malformed ID should return 400."""
        response = await client.delete("/api/events/bad-id")
        assert response.status_code == 400
