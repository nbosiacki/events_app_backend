"""
Tests for the sync API endpoints.

POST /api/sync/trigger  – requires admin key, starts background sync
GET  /api/sync/status   – requires admin key, returns last sync result
"""


class TestSyncTrigger:
    """POST /api/sync/trigger"""

    async def test_trigger_with_valid_key_returns_started(self, client):
        """A valid admin key should return {status: started}."""
        response = await client.post(
            "/api/sync/trigger",
            headers={"X-Admin-Key": "test-admin-key"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "started"

    async def test_trigger_without_key_returns_422(self, client):
        """Missing X-Admin-Key header should return 422 (missing required header)."""
        response = await client.post("/api/sync/trigger")
        assert response.status_code == 422

    async def test_trigger_with_wrong_key_returns_401(self, client):
        """An incorrect admin key should return 401."""
        response = await client.post(
            "/api/sync/trigger",
            headers={"X-Admin-Key": "wrong-key"},
        )
        assert response.status_code == 401


class TestSyncStatus:
    """GET /api/sync/status"""

    async def test_status_with_valid_key(self, client):
        """A valid admin key should return a status response."""
        response = await client.get(
            "/api/sync/status",
            headers={"X-Admin-Key": "test-admin-key"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "status" in data

    async def test_status_without_key_returns_422(self, client):
        """Missing X-Admin-Key header should return 422."""
        response = await client.get("/api/sync/status")
        assert response.status_code == 422

    async def test_status_with_wrong_key_returns_401(self, client):
        """An incorrect admin key should return 401."""
        response = await client.get(
            "/api/sync/status",
            headers={"X-Admin-Key": "wrong-key"},
        )
        assert response.status_code == 401

    async def test_initial_status_is_never_run(self, client):
        """Before any sync, status should indicate the sync has never run."""
        # Reset module-level state to simulate fresh start
        import app.api.routes.sync as sync_module
        sync_module._last_sync_result = {"status": "never_run"}

        response = await client.get(
            "/api/sync/status",
            headers={"X-Admin-Key": "test-admin-key"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "never_run"
