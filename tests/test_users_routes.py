"""
Tests for the /api/users endpoints.

Covers:
    GET    /users/{user_id}                – own profile, other user (403), unauthenticated
    PUT    /users/{user_id}/preferences    – update preferences, other user (403)
    POST   /users/{user_id}/like/{event}   – like event, idempotent, nonexistent event (404)
    DELETE /users/{user_id}/like/{event}   – unlike event, idempotent, other user (403)
    POST   /users/{user_id}/attend/{event} – mark attended, idempotent, other user (403)

All routes require authentication and enforce that users can only modify their
own data. Tests use a real MongoDB test database.
"""

from bson import ObjectId


class TestGetUser:
    """GET /api/users/{user_id} — user profile retrieval."""

    async def test_get_own_profile(self, client, test_user, auth_headers):
        """A user should be able to retrieve their own profile."""
        user_id = str(test_user["_id"])
        response = await client.get(f"/api/users/{user_id}", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["email"] == "test@example.com"

    async def test_get_other_user_returns_403(self, client, auth_headers):
        """Attempting to view another user's profile should return 403 Forbidden."""
        other_id = str(ObjectId())
        response = await client.get(f"/api/users/{other_id}", headers=auth_headers)
        assert response.status_code == 403

    async def test_unauthenticated_returns_401(self, client, test_user):
        """Accessing the endpoint without a token should return 401."""
        user_id = str(test_user["_id"])
        response = await client.get(f"/api/users/{user_id}")
        assert response.status_code == 401

    async def test_invalid_user_id_returns_400(self, client, auth_headers):
        """A malformed user ID should return 400."""
        response = await client.get("/api/users/bad-id", headers=auth_headers)
        assert response.status_code == 400

    async def test_sensitive_fields_stripped(self, client, test_user, auth_headers):
        """Sensitive fields should be None (not populated with real values).

        The User model includes these as Optional fields, so they show up
        as null in JSON.  The key check is that the real hash/token values
        are never returned to the client.
        """
        user_id = str(test_user["_id"])
        response = await client.get(f"/api/users/{user_id}", headers=auth_headers)
        data = response.json()

        assert data.get("password_hash") is None
        assert data.get("password_reset_token") is None


class TestUpdatePreferences:
    """PUT /api/users/{user_id}/preferences — preference updates."""

    async def test_update_own_preferences(self, client, test_user, auth_headers):
        """A user should be able to update their own preferences."""
        user_id = str(test_user["_id"])
        response = await client.put(
            f"/api/users/{user_id}/preferences",
            headers=auth_headers,
            json={
                "preferred_categories": ["music", "art"],
                "max_price_bucket": "budget",
                "preferred_areas": ["Södermalm"],
            },
        )
        assert response.status_code == 200

        data = response.json()
        assert data["preferences"]["preferred_categories"] == ["music", "art"]
        assert data["preferences"]["max_price_bucket"] == "budget"
        assert data["preferences"]["preferred_areas"] == ["Södermalm"]

    async def test_update_other_user_returns_403(self, client, auth_headers):
        """Trying to update another user's preferences should return 403."""
        other_id = str(ObjectId())
        response = await client.put(
            f"/api/users/{other_id}/preferences",
            headers=auth_headers,
            json={
                "preferred_categories": [],
                "max_price_bucket": "premium",
                "preferred_areas": [],
            },
        )
        assert response.status_code == 403


class TestLikeEvent:
    """POST /api/users/{user_id}/like/{event_id} — event liking."""

    async def test_like_event_success(self, client, test_user, sample_event, auth_headers):
        """Liking an existing event should succeed and add it to liked_events."""
        user_id = str(test_user["_id"])
        event_id = str(sample_event["_id"])

        response = await client.post(
            f"/api/users/{user_id}/like/{event_id}",
            headers=auth_headers,
        )
        assert response.status_code == 200

    async def test_like_event_idempotent(self, client, test_user, sample_event, auth_headers):
        """Liking the same event twice should not create duplicates in liked_events."""
        from app.db import mongodb

        user_id = str(test_user["_id"])
        event_id = str(sample_event["_id"])

        await client.post(f"/api/users/{user_id}/like/{event_id}", headers=auth_headers)
        await client.post(f"/api/users/{user_id}/like/{event_id}", headers=auth_headers)

        user = await mongodb.db.users.find_one({"_id": test_user["_id"]})
        assert user["liked_events"].count(event_id) == 1

    async def test_like_nonexistent_event_returns_404(self, client, test_user, auth_headers):
        """Liking an event that doesn't exist should return 404."""
        user_id = str(test_user["_id"])
        fake_event = str(ObjectId())

        response = await client.post(
            f"/api/users/{user_id}/like/{fake_event}",
            headers=auth_headers,
        )
        assert response.status_code == 404

    async def test_like_other_user_returns_403(self, client, sample_event, auth_headers):
        """Trying to like an event on behalf of another user should return 403."""
        other_id = str(ObjectId())
        event_id = str(sample_event["_id"])

        response = await client.post(
            f"/api/users/{other_id}/like/{event_id}",
            headers=auth_headers,
        )
        assert response.status_code == 403


class TestUnlikeEvent:
    """DELETE /api/users/{user_id}/like/{event_id} — unlike an event."""

    async def test_unlike_event_success(self, client, test_user, sample_event, auth_headers):
        """Unliking a previously liked event should succeed."""
        user_id = str(test_user["_id"])
        event_id = str(sample_event["_id"])

        # Like first
        await client.post(f"/api/users/{user_id}/like/{event_id}", headers=auth_headers)

        # Unlike
        response = await client.delete(
            f"/api/users/{user_id}/like/{event_id}", headers=auth_headers
        )
        assert response.status_code == 200

    async def test_unlike_removes_from_array(self, client, test_user, sample_event, auth_headers):
        """After unliking, the event should no longer be in liked_events."""
        from app.db import mongodb

        user_id = str(test_user["_id"])
        event_id = str(sample_event["_id"])

        await client.post(f"/api/users/{user_id}/like/{event_id}", headers=auth_headers)
        await client.delete(f"/api/users/{user_id}/like/{event_id}", headers=auth_headers)

        user = await mongodb.db.users.find_one({"_id": test_user["_id"]})
        assert event_id not in user["liked_events"]

    async def test_unlike_idempotent(self, client, test_user, sample_event, auth_headers):
        """Unliking an event that isn't liked should still return 200 (no-op)."""
        user_id = str(test_user["_id"])
        event_id = str(sample_event["_id"])

        response = await client.delete(
            f"/api/users/{user_id}/like/{event_id}", headers=auth_headers
        )
        assert response.status_code == 200

    async def test_unlike_other_user_returns_403(self, client, sample_event, auth_headers):
        """Trying to unlike on behalf of another user should return 403."""
        other_id = str(ObjectId())
        event_id = str(sample_event["_id"])

        response = await client.delete(
            f"/api/users/{other_id}/like/{event_id}", headers=auth_headers
        )
        assert response.status_code == 403

    async def test_unlike_unauthenticated_returns_401(self, client, test_user, sample_event):
        """Calling unlike without a token should return 401."""
        user_id = str(test_user["_id"])
        event_id = str(sample_event["_id"])

        response = await client.delete(f"/api/users/{user_id}/like/{event_id}")
        assert response.status_code == 401

    async def test_unlike_invalid_id_returns_400(self, client, auth_headers):
        """A malformed user or event ID should return 400."""
        response = await client.delete(
            "/api/users/bad-id/like/also-bad", headers=auth_headers
        )
        assert response.status_code == 400


class TestAttendEvent:
    """POST /api/users/{user_id}/attend/{event_id} — event attendance marking."""

    async def test_attend_event_success(self, client, test_user, sample_event, auth_headers):
        """Marking attendance for an existing event should succeed."""
        user_id = str(test_user["_id"])
        event_id = str(sample_event["_id"])

        response = await client.post(
            f"/api/users/{user_id}/attend/{event_id}",
            headers=auth_headers,
        )
        assert response.status_code == 200

    async def test_attend_event_idempotent(self, client, test_user, sample_event, auth_headers):
        """Attending the same event twice should not create duplicates."""
        from app.db import mongodb

        user_id = str(test_user["_id"])
        event_id = str(sample_event["_id"])

        await client.post(f"/api/users/{user_id}/attend/{event_id}", headers=auth_headers)
        await client.post(f"/api/users/{user_id}/attend/{event_id}", headers=auth_headers)

        user = await mongodb.db.users.find_one({"_id": test_user["_id"]})
        assert user["attended_events"].count(event_id) == 1

    async def test_attend_other_user_returns_403(self, client, sample_event, auth_headers):
        """Marking attendance for another user should return 403."""
        other_id = str(ObjectId())
        event_id = str(sample_event["_id"])

        response = await client.post(
            f"/api/users/{other_id}/attend/{event_id}",
            headers=auth_headers,
        )
        assert response.status_code == 403
