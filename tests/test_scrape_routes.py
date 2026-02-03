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


class TestRunScrapeTask:
    """run_scrape_task — verifies scraper is called with db parameter."""

    async def test_scraper_called_with_db(self, client):
        """run_scrape_task should pass db to scraper.scrape()."""
        with patch("app.api.routes.scrape.EventScraper") as MockScraper, \
             patch("app.api.routes.scrape.EventDeduplicator"):

            mock_instance = MockScraper.return_value
            mock_instance.scrape = AsyncMock(return_value=[])
            mock_instance.close = lambda: None

            from app.api.routes.scrape import run_scrape_task
            await run_scrape_task("https://example.com", "example", 3)

            mock_instance.scrape.assert_called_once()
            call_kwargs = mock_instance.scrape.call_args
            assert call_kwargs.kwargs.get("db") is not None
