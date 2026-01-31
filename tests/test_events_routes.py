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


class TestSortParameter:
    """GET /api/events?sort=... — sort order tests."""

    async def test_sort_by_time_default(self, client, setup_db):
        """Default sort (time) returns events by datetime_start ascending."""
        from app.db import mongodb

        await mongodb.db.events.insert_one({
            "title": "Late",
            "venue": {"name": "V"},
            "datetime_start": datetime(2025, 4, 1, 20, 0),
            "price": {"amount": 100, "currency": "SEK", "bucket": "standard"},
            "source_url": "https://example.com/late",
            "source_site": "example.com",
            "categories": [],
            "scraped_at": datetime.now(timezone.utc),
        })
        await mongodb.db.events.insert_one({
            "title": "Early",
            "venue": {"name": "V"},
            "datetime_start": datetime(2025, 4, 1, 10, 0),
            "price": {"amount": 50, "currency": "SEK", "bucket": "budget"},
            "source_url": "https://example.com/early",
            "source_site": "example.com",
            "categories": [],
            "scraped_at": datetime.now(timezone.utc),
        })

        response = await client.get("/api/events?sort=time")
        data = response.json()
        assert data[0]["title"] == "Early"
        assert data[1]["title"] == "Late"

    async def test_sort_by_price_asc(self, client, setup_db):
        """sort=price_asc returns cheapest events first."""
        from app.db import mongodb

        await mongodb.db.events.insert_one({
            "title": "Expensive",
            "venue": {"name": "V"},
            "datetime_start": datetime(2025, 4, 1, 18, 0),
            "price": {"amount": 500, "currency": "SEK", "bucket": "premium"},
            "source_url": "https://example.com/expensive",
            "source_site": "example.com",
            "categories": [],
            "scraped_at": datetime.now(timezone.utc),
        })
        await mongodb.db.events.insert_one({
            "title": "Cheap",
            "venue": {"name": "V"},
            "datetime_start": datetime(2025, 4, 1, 18, 0),
            "price": {"amount": 50, "currency": "SEK", "bucket": "budget"},
            "source_url": "https://example.com/cheap",
            "source_site": "example.com",
            "categories": [],
            "scraped_at": datetime.now(timezone.utc),
        })

        response = await client.get("/api/events?sort=price_asc")
        data = response.json()
        assert data[0]["title"] == "Cheap"
        assert data[1]["title"] == "Expensive"

    async def test_sort_by_price_desc(self, client, setup_db):
        """sort=price_desc returns most expensive events first."""
        from app.db import mongodb

        await mongodb.db.events.insert_one({
            "title": "Cheap",
            "venue": {"name": "V"},
            "datetime_start": datetime(2025, 4, 1, 18, 0),
            "price": {"amount": 50, "currency": "SEK", "bucket": "budget"},
            "source_url": "https://example.com/cheap",
            "source_site": "example.com",
            "categories": [],
            "scraped_at": datetime.now(timezone.utc),
        })
        await mongodb.db.events.insert_one({
            "title": "Expensive",
            "venue": {"name": "V"},
            "datetime_start": datetime(2025, 4, 1, 18, 0),
            "price": {"amount": 500, "currency": "SEK", "bucket": "premium"},
            "source_url": "https://example.com/expensive",
            "source_site": "example.com",
            "categories": [],
            "scraped_at": datetime.now(timezone.utc),
        })

        response = await client.get("/api/events?sort=price_desc")
        data = response.json()
        assert data[0]["title"] == "Expensive"
        assert data[1]["title"] == "Cheap"

    async def test_sort_relevance_unauthenticated_falls_back_to_time(self, client, setup_db):
        """sort=relevance without auth token falls back to time sort."""
        from app.db import mongodb

        await mongodb.db.events.insert_one({
            "title": "Late",
            "venue": {"name": "V"},
            "datetime_start": datetime(2025, 4, 1, 20, 0),
            "price": {"amount": 0, "currency": "SEK", "bucket": "free"},
            "source_url": "https://example.com/late",
            "source_site": "example.com",
            "categories": ["music"],
            "scraped_at": datetime.now(timezone.utc),
        })
        await mongodb.db.events.insert_one({
            "title": "Early",
            "venue": {"name": "V"},
            "datetime_start": datetime(2025, 4, 1, 10, 0),
            "price": {"amount": 0, "currency": "SEK", "bucket": "free"},
            "source_url": "https://example.com/early",
            "source_site": "example.com",
            "categories": ["art"],
            "scraped_at": datetime.now(timezone.utc),
        })

        response = await client.get("/api/events?sort=relevance")
        assert response.status_code == 200
        data = response.json()
        # Falls back to time sort: Early before Late
        assert data[0]["title"] == "Early"
        assert data[1]["title"] == "Late"

    async def test_sort_relevance_authenticated(self, client, test_user, auth_headers, setup_db):
        """sort=relevance with auth token uses preference scoring."""
        from app.db import mongodb

        # Give the test user a preference for music
        await mongodb.db.users.update_one(
            {"_id": test_user["_id"]},
            {"$set": {"preferences.preferred_categories": ["music"]}},
        )

        await mongodb.db.events.insert_one({
            "title": "Art Show",
            "venue": {"name": "V"},
            "datetime_start": datetime(2025, 4, 1, 10, 0),
            "price": {"amount": 0, "currency": "SEK", "bucket": "free"},
            "source_url": "https://example.com/art",
            "source_site": "example.com",
            "categories": ["art"],
            "scraped_at": datetime.now(timezone.utc),
        })
        await mongodb.db.events.insert_one({
            "title": "Jazz Night",
            "venue": {"name": "V"},
            "datetime_start": datetime(2025, 4, 1, 20, 0),
            "price": {"amount": 0, "currency": "SEK", "bucket": "free"},
            "source_url": "https://example.com/jazz",
            "source_site": "example.com",
            "categories": ["music"],
            "scraped_at": datetime.now(timezone.utc),
        })

        response = await client.get("/api/events?sort=relevance", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        # Jazz Night should rank higher due to music preference
        assert data[0]["title"] == "Jazz Night"

    async def test_relevance_reflects_preference_update(self, client, test_user, auth_headers, setup_db):
        """Updating preferences via API changes relevance sort on next fetch."""
        from app.db import mongodb

        # Insert two events: art event earlier, music event later
        await mongodb.db.events.insert_one({
            "title": "Art Show",
            "venue": {"name": "V"},
            "datetime_start": datetime(2025, 4, 1, 10, 0),
            "price": {"amount": 0, "currency": "SEK", "bucket": "free"},
            "source_url": "https://example.com/art-pref",
            "source_site": "example.com",
            "categories": ["art"],
            "scraped_at": datetime.now(timezone.utc),
        })
        await mongodb.db.events.insert_one({
            "title": "Jazz Night",
            "venue": {"name": "V"},
            "datetime_start": datetime(2025, 4, 1, 20, 0),
            "price": {"amount": 0, "currency": "SEK", "bucket": "free"},
            "source_url": "https://example.com/jazz-pref",
            "source_site": "example.com",
            "categories": ["music"],
            "scraped_at": datetime.now(timezone.utc),
        })

        # Before preference update: no preferred categories → passthrough (time order)
        response = await client.get("/api/events?sort=relevance", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data[0]["title"] == "Art Show", "Without preferences, should fall back to time order"
        assert data[1]["title"] == "Jazz Night"

        # Update preferences to prefer music via the API
        user_id = str(test_user["_id"])
        pref_response = await client.put(
            f"/api/users/{user_id}/preferences",
            json={
                "preferred_categories": ["music"],
                "max_price_bucket": "premium",
                "preferred_areas": [],
            },
            headers=auth_headers,
        )
        assert pref_response.status_code == 200

        # After preference update: music event should rank first
        response = await client.get("/api/events?sort=relevance", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data[0]["title"] == "Jazz Night", "After setting music preference, music event should rank first"
        assert data[1]["title"] == "Art Show"


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

    async def test_create_event_with_image_url(self, client):
        """An event payload with image_url should store and return it."""
        payload = {
            "title": "Art Show",
            "venue": {"name": "Moderna Museet"},
            "datetime_start": "2025-06-01T18:00:00",
            "price": {"amount": 0, "currency": "SEK", "bucket": "free"},
            "source_url": "https://example.com/art-show",
            "source_site": "example.com",
            "image_url": "https://picsum.photos/seed/art/600/400",
        }
        response = await client.post("/api/events", json=payload)
        assert response.status_code == 200
        assert response.json()["image_url"] == "https://picsum.photos/seed/art/600/400"

    async def test_create_event_without_image_url(self, client):
        """An event without image_url should default to None."""
        payload = {
            "title": "No Image Event",
            "venue": {"name": "Test Venue"},
            "datetime_start": "2025-06-01T18:00:00",
            "price": {"amount": 0, "currency": "SEK", "bucket": "free"},
            "source_url": "https://example.com/no-image",
            "source_site": "example.com",
        }
        response = await client.post("/api/events", json=payload)
        assert response.status_code == 200
        assert response.json()["image_url"] is None

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
