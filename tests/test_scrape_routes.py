"""
Tests for the /api/scrape endpoints.

Covers:
    POST /scrape/trigger — starts a background scraping task and returns
                           status="started" immediately.

The actual scraping is done in a background task, so we only verify the
HTTP response.  The background task's run_scrape_task function is mocked
because FastAPI's test client executes background tasks inline (within
the same request), which would hit the real Anthropic API.  Agent
behavior is tested separately in test_scraper_agent.py and
test_deduplicator_agent.py.
"""

from unittest.mock import patch, AsyncMock


class TestTriggerScrape:
    """POST /api/scrape/trigger — initiate background scrape."""

    async def test_trigger_returns_started(self, client):
        """A valid scrape request should return status='started' immediately."""
        with patch("app.api.routes.scrape.run_scrape_task", new_callable=AsyncMock):
            response = await client.post("/api/scrape/trigger", json={
                "url": "https://example.com/events",
                "source_name": "example",
                "max_pages": 3,
            })
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "started"
        assert "example.com/events" in data["message"]

    async def test_trigger_default_max_pages(self, client):
        """max_pages should default to 5 when not provided."""
        with patch("app.api.routes.scrape.run_scrape_task", new_callable=AsyncMock):
            response = await client.post("/api/scrape/trigger", json={
                "url": "https://example.com/events",
                "source_name": "example",
            })
        assert response.status_code == 200
        assert "5 pages" in response.json()["message"]
