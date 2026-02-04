"""
Tests for the EventScraper agent.

The Anthropic API and httpx HTTP client are mocked so these tests run
without network access or an API key.  The mock Anthropic client returns
pre-built tool_use responses that simulate Claude navigating a website
and extracting event data.

Covers:
    scrape()              – single-page scrape flow, max_pages enforcement
    _create_event()       – datetime/price parsing, missing-field rejection
    _fetch_page()         – HTML parsing, link extraction, error handling
    _handle_tool_call()   – tool dispatch and return values
    extract_urls_from_page() – link extraction and filtering
    filter_known_urls()   – batch DB pre-check
    try_site_parser()     – parser integration with health check
    scrape(db=...)        – full flow with DB pre-check and backward compat
"""

from datetime import datetime
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock
from types import SimpleNamespace

import pytest

from app.models.event import Price, EventCreate, Venue


def _make_tool_use_block(tool_id, name, input_data):
    """Build a mock content block that mimics Anthropic's ToolUseBlock.

    Anthropic responses contain content blocks with .type, .name, .input,
    and .id attributes.  This helper avoids importing Anthropic SDK types
    in tests.
    """
    block = SimpleNamespace()
    block.type = "tool_use"
    block.name = name
    block.input = input_data
    block.id = tool_id
    return block


def _make_text_block(text):
    """Build a mock text content block."""
    block = SimpleNamespace()
    block.type = "text"
    block.text = text
    return block


def _make_response(content_blocks):
    """Wrap content blocks in a mock Anthropic messages.create() response."""
    response = SimpleNamespace()
    response.content = content_blocks
    return response


class TestCreateEvent:
    """EventScraper._create_event — converting raw extracted data to EventCreate."""

    def _make_scraper(self):
        """Instantiate an EventScraper with mocked Anthropic + httpx clients."""
        with patch("app.agents.scraper.Anthropic"), \
             patch("app.agents.scraper.httpx.Client"):
            from app.agents.scraper import EventScraper
            return EventScraper()

    def test_valid_event_data(self):
        """All required fields present should produce a valid EventCreate."""
        scraper = self._make_scraper()
        data = {
            "title": "Jazz Night",
            "venue_name": "Stampen",
            "venue_address": "Stora Nygatan 5",
            "source_url": "https://example.com/jazz",
            "datetime_start": "2025-06-01T20:00:00",
            "price_amount": 150,
            "price_currency": "SEK",
            "categories": ["jazz", "music"],
        }
        event = scraper._create_event(data, "example.com")

        assert event is not None
        assert event.title == "Jazz Night"
        assert event.venue.name == "Stampen"
        assert event.venue.address == "Stora Nygatan 5"
        assert event.source_url == "https://example.com/jazz"
        assert event.price.amount == 150
        assert event.price.bucket == "standard"
        assert event.source_site == "example.com"

    def test_iso_datetime_with_z_suffix(self):
        """A datetime ending in 'Z' should be parsed correctly (replaced with +00:00)."""
        scraper = self._make_scraper()
        data = {
            "title": "Event",
            "venue_name": "Venue",
            "source_url": "https://example.com/e",
            "datetime_start": "2025-06-01T20:00:00Z",
        }
        event = scraper._create_event(data, "site")
        assert event.datetime_start.year == 2025

    def test_alternative_datetime_format(self):
        """The fallback format 'YYYY-MM-DD HH:MM' should work."""
        scraper = self._make_scraper()
        data = {
            "title": "Event",
            "venue_name": "Venue",
            "source_url": "https://example.com/e",
            "datetime_start": "2025-06-01 20:00",
        }
        event = scraper._create_event(data, "site")
        assert event.datetime_start == datetime(2025, 6, 1, 20, 0)

    def test_missing_datetime_defaults_to_now(self):
        """If datetime_start is absent, it should default to roughly now."""
        scraper = self._make_scraper()
        data = {
            "title": "Event",
            "venue_name": "Venue",
            "source_url": "https://example.com/e",
        }
        event = scraper._create_event(data, "site")
        assert isinstance(event.datetime_start, datetime)

    def test_free_event_price(self):
        """A price_amount of 0 should produce a free bucket."""
        scraper = self._make_scraper()
        data = {
            "title": "Free Event",
            "venue_name": "Park",
            "source_url": "https://example.com/free",
            "price_amount": 0,
        }
        event = scraper._create_event(data, "site")
        assert event.price.bucket == "free"
        assert event.price.amount == 0

    def test_online_event_data(self):
        """An event with is_online=True and online_link should be stored on EventCreate."""
        scraper = self._make_scraper()
        data = {
            "title": "Online Workshop",
            "venue_name": "Zoom Webinar",
            "source_url": "https://example.com/online",
            "datetime_start": "2025-06-01T18:00:00",
            "is_online": True,
            "online_link": "https://zoom.us/j/123456789",
        }
        event = scraper._create_event(data, "example.com")
        assert event is not None
        assert event.is_online is True
        assert event.online_link == "https://zoom.us/j/123456789"

    def test_in_person_event_defaults(self):
        """An event without is_online should default to False with no online_link."""
        scraper = self._make_scraper()
        data = {
            "title": "Jazz Night",
            "venue_name": "Stampen",
            "source_url": "https://example.com/jazz",
            "datetime_start": "2025-06-01T20:00:00",
        }
        event = scraper._create_event(data, "example.com")
        assert event is not None
        assert event.is_online is False
        assert event.online_link is None

    def test_missing_required_fields_returns_none(self):
        """If title, venue_name, or source_url is missing, return None."""
        scraper = self._make_scraper()

        assert scraper._create_event({"venue_name": "V", "source_url": "u"}, "s") is None
        assert scraper._create_event({"title": "T", "source_url": "u"}, "s") is None
        assert scraper._create_event({"title": "T", "venue_name": "V"}, "s") is None


class TestScrapeFlow:
    """EventScraper.scrape() — end-to-end flow with mocked Anthropic API."""

    @pytest.mark.asyncio
    async def test_single_page_scrape(self):
        """A simple scrape: fetch page → extract events → done.

        Verifies that the scraper collects EventCreate objects from the
        extract_events tool call and returns them.
        """
        with patch("app.agents.scraper.Anthropic") as MockAnthropic, \
             patch("app.agents.scraper.httpx.Client") as MockHttpClient:

            mock_client = MagicMock()
            MockAnthropic.return_value = mock_client

            # Turn 1: Claude asks to fetch the page
            # Turn 2: Claude extracts events and calls done
            mock_client.messages.create.side_effect = [
                _make_response([
                    _make_tool_use_block("t1", "fetch_page", {"url": "https://example.com"}),
                ]),
                _make_response([
                    _make_tool_use_block("t2", "extract_events", {
                        "events": [{
                            "title": "Test Event",
                            "venue_name": "Test Venue",
                            "source_url": "https://example.com/event1",
                            "datetime_start": "2025-06-01T20:00:00",
                            "price_amount": 100,
                        }],
                    }),
                    _make_tool_use_block("t3", "done", {"summary": "Found 1 event"}),
                ]),
            ]

            # Mock the HTTP client's get() for _fetch_page
            mock_http = MagicMock()
            MockHttpClient.return_value = mock_http
            mock_response = MagicMock()
            mock_response.text = "<html><head><title>Events</title></head><body><p>Event info</p></body></html>"
            mock_response.raise_for_status = MagicMock()
            mock_http.get.return_value = mock_response

            from app.agents.scraper import EventScraper
            scraper = EventScraper()
            events = await scraper.scrape("https://example.com", "example.com", max_pages=5)

            assert len(events) == 1
            assert events[0].title == "Test Event"
            assert events[0].price.bucket == "standard"

    @pytest.mark.asyncio
    async def test_max_pages_limit(self):
        """The scraper should stop fetching after max_pages pages.

        Simulate Claude requesting fetch_page repeatedly — the loop
        should break when pages_fetched reaches max_pages.
        """
        with patch("app.agents.scraper.Anthropic") as MockAnthropic, \
             patch("app.agents.scraper.httpx.Client") as MockHttpClient:

            mock_client = MagicMock()
            MockAnthropic.return_value = mock_client

            # Each turn fetches a page; after max_pages the loop exits
            mock_client.messages.create.side_effect = [
                _make_response([
                    _make_tool_use_block(f"t{i}", "fetch_page", {"url": f"https://example.com/p{i}"}),
                ])
                for i in range(10)  # More responses than max_pages
            ]

            mock_http = MagicMock()
            MockHttpClient.return_value = mock_http
            mock_response = MagicMock()
            mock_response.text = "<html><head><title>Page</title></head><body>Content</body></html>"
            mock_response.raise_for_status = MagicMock()
            mock_http.get.return_value = mock_response

            from app.agents.scraper import EventScraper
            scraper = EventScraper()
            events = await scraper.scrape("https://example.com", "example.com", max_pages=2)

            # Should have called messages.create exactly 2 times (max_pages)
            assert mock_client.messages.create.call_count == 2


class TestHandleToolCall:
    """EventScraper._handle_tool_call — tool dispatch."""

    def _make_scraper(self):
        """Instantiate a scraper with mocked dependencies."""
        with patch("app.agents.scraper.Anthropic"), \
             patch("app.agents.scraper.httpx.Client"):
            from app.agents.scraper import EventScraper
            return EventScraper()

    def test_done_tool(self):
        """The done tool should return a success JSON string."""
        scraper = self._make_scraper()
        result = scraper._handle_tool_call("done", {"summary": "All done"})
        assert '"success": true' in result

    def test_unknown_tool(self):
        """An unrecognized tool name should return an error JSON string."""
        scraper = self._make_scraper()
        result = scraper._handle_tool_call("nonexistent", {})
        assert "error" in result


# ── Phase 8: URL extraction, filtering, parser integration ──────────


class TestExtractURLs:
    """EventScraper.extract_urls_from_page — link extraction and filtering."""

    def _make_scraper(self):
        with patch("app.agents.scraper.Anthropic"), \
             patch("app.agents.scraper.httpx.Client"):
            from app.agents.scraper import EventScraper
            return EventScraper()

    def test_finds_event_links(self):
        """Should extract URLs containing event-like path segments."""
        scraper = self._make_scraper()
        html = """
        <html><body>
            <a href="/e/jazz-night-123">Jazz</a>
            <a href="/event/rock-fest">Rock</a>
            <a href="/about">About</a>
            <a href="/tickets/summer-gala">Tickets</a>
        </body></html>
        """
        urls = scraper.extract_urls_from_page(html, "https://www.eventbrite.com")
        assert len(urls) >= 2
        assert any("/e/jazz-night-123" in u for u in urls)
        assert any("/event/rock-fest" in u for u in urls)

    def test_handles_relative_urls(self):
        """Relative hrefs should be resolved to absolute URLs."""
        scraper = self._make_scraper()
        html = '<html><body><a href="/e/test-event">Test</a></body></html>'
        urls = scraper.extract_urls_from_page(html, "https://example.com")
        assert urls[0].startswith("https://example.com/e/test-event")

    def test_filters_non_event_links(self):
        """Links without event-like patterns should be excluded."""
        scraper = self._make_scraper()
        html = """
        <html><body>
            <a href="/about">About</a>
            <a href="/contact">Contact</a>
            <a href="/privacy-policy">Privacy</a>
        </body></html>
        """
        urls = scraper.extract_urls_from_page(html, "https://example.com")
        assert len(urls) == 0

    def test_deduplicates_urls(self):
        """Duplicate links should be deduplicated."""
        scraper = self._make_scraper()
        html = """
        <html><body>
            <a href="/e/jazz-123">Jazz</a>
            <a href="/e/jazz-123">Jazz Again</a>
            <a href="/e/jazz-123#details">Jazz Hash</a>
        </body></html>
        """
        urls = scraper.extract_urls_from_page(html, "https://example.com")
        assert len(urls) == 1


class TestFilterKnownURLs:
    """EventScraper.filter_known_urls — batch DB pre-check."""

    def _make_scraper(self):
        with patch("app.agents.scraper.Anthropic"), \
             patch("app.agents.scraper.httpx.Client"):
            from app.agents.scraper import EventScraper
            return EventScraper()

    @pytest.mark.asyncio
    async def test_returns_new_urls_only(self):
        """URLs already in the DB should be filtered out."""
        scraper = self._make_scraper()

        # Mock Motor cursor with async iteration
        mock_db = MagicMock()
        mock_cursor = MagicMock()

        async def async_iter():
            for doc in [{"source_url": "https://example.com/e/known"}]:
                yield doc

        mock_cursor.__aiter__ = lambda self: async_iter()
        mock_db.events.find.return_value = mock_cursor

        urls = [
            "https://example.com/e/known",
            "https://example.com/e/new-one",
            "https://example.com/e/new-two",
        ]
        result = await scraper.filter_known_urls(urls, mock_db)

        assert len(result) == 2
        assert "https://example.com/e/known" not in result
        assert "https://example.com/e/new-one" in result
        assert "https://example.com/e/new-two" in result

    @pytest.mark.asyncio
    async def test_empty_db_returns_all(self):
        """If DB has no matching URLs, all should be returned."""
        scraper = self._make_scraper()

        mock_db = MagicMock()
        mock_cursor = MagicMock()

        async def async_iter():
            return
            yield  # make this a generator

        mock_cursor.__aiter__ = lambda self: async_iter()
        mock_db.events.find.return_value = mock_cursor

        urls = ["https://example.com/e/a", "https://example.com/e/b"]
        result = await scraper.filter_known_urls(urls, mock_db)
        assert result == urls

    @pytest.mark.asyncio
    async def test_empty_input_returns_empty(self):
        """Empty URL list should return empty without querying DB."""
        scraper = self._make_scraper()
        mock_db = MagicMock()
        result = await scraper.filter_known_urls([], mock_db)
        assert result == []
        mock_db.events.find.assert_not_called()


class TestTrySiteParser:
    """EventScraper.try_site_parser — parser framework integration."""

    def _make_scraper(self):
        with patch("app.agents.scraper.Anthropic"), \
             patch("app.agents.scraper.httpx.Client"):
            from app.agents.scraper import EventScraper
            return EventScraper()

    def test_no_parser_returns_none(self):
        """URLs with no matching parser should return None."""
        scraper = self._make_scraper()
        result = scraper.try_site_parser(
            "https://unknownsite.com/events/123",
            "<html><body>Content</body></html>",
        )
        assert result is None

    def test_unhealthy_parser_returns_none(self):
        """If parser health check fails, should return None (Claude fallback)."""
        scraper = self._make_scraper()
        # Eventbrite parser exists but random HTML won't pass health check
        result = scraper.try_site_parser(
            "https://www.eventbrite.com/e/test-event",
            "<html><body><h1>Random page</h1></body></html>",
        )
        assert result is None

    def test_healthy_parser_extracts_event(self):
        """If parser is healthy and can extract, should return events."""
        scraper = self._make_scraper()

        # Build HTML that passes Eventbrite's health check (JSON-LD)
        html = """
        <html><head>
        <script type="application/ld+json">
        {
            "@type": "SocialEvent",
            "name": "Test Event",
            "description": "A test event.",
            "location": {
                "@type": "Place",
                "name": "Test Venue",
                "address": {"@type": "PostalAddress", "streetAddress": "Test Address"}
            },
            "startDate": "2025-06-01T20:00:00+02:00",
            "offers": {"lowPrice": "100.0", "priceCurrency": "SEK"},
            "image": "https://img.test.com/event.jpg",
            "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode"
        }
        </script>
        </head><body>
            <h1 data-testid="event-title">Test Event</h1>
            <div data-testid="event-tags"><a>music</a></div>
        </body></html>
        """
        result = scraper.try_site_parser(
            "https://www.eventbrite.com/e/test-event",
            html,
        )
        assert result is not None
        assert len(result) == 1
        assert result[0].title == "Test Event"


class TestScrapeWithDB:
    """EventScraper.scrape(db=...) — full flow with DB pre-check."""

    @pytest.mark.asyncio
    async def test_backward_compat_without_db(self):
        """scrape() without db should use the original Claude flow."""
        with patch("app.agents.scraper.Anthropic") as MockAnthropic, \
             patch("app.agents.scraper.httpx.Client") as MockHttpClient:

            mock_client = MagicMock()
            MockAnthropic.return_value = mock_client

            mock_client.messages.create.side_effect = [
                _make_response([
                    _make_tool_use_block("t1", "fetch_page", {"url": "https://example.com"}),
                ]),
                _make_response([
                    _make_tool_use_block("t2", "extract_events", {
                        "events": [{
                            "title": "Test",
                            "venue_name": "Venue",
                            "source_url": "https://example.com/e1",
                            "datetime_start": "2025-06-01T20:00:00",
                        }],
                    }),
                    _make_tool_use_block("t3", "done", {"summary": "Done"}),
                ]),
            ]

            mock_http = MagicMock()
            MockHttpClient.return_value = mock_http
            mock_response = MagicMock()
            mock_response.text = "<html><head><title>Events</title></head><body>Content</body></html>"
            mock_response.raise_for_status = MagicMock()
            mock_http.get.return_value = mock_response

            from app.agents.scraper import EventScraper
            scraper = EventScraper()
            # No db param — should use Claude flow
            events = await scraper.scrape("https://example.com", "example.com")
            assert len(events) == 1
            assert mock_client.messages.create.called

    @pytest.mark.asyncio
    async def test_all_urls_known_returns_empty(self):
        """When all extracted URLs are already in DB, should return empty list."""
        with patch("app.agents.scraper.Anthropic") as MockAnthropic, \
             patch("app.agents.scraper.httpx.Client") as MockHttpClient:

            MockAnthropic.return_value = MagicMock()

            mock_http = MagicMock()
            MockHttpClient.return_value = mock_http
            mock_response = MagicMock()
            mock_response.text = '<html><body><a href="/e/known-event">Event</a></body></html>'
            mock_response.raise_for_status = MagicMock()
            mock_http.get.return_value = mock_response

            from app.agents.scraper import EventScraper
            scraper = EventScraper()

            # Mock DB that knows the URL
            mock_db = MagicMock()
            mock_cursor = MagicMock()

            async def async_iter():
                yield {"source_url": "https://example.com/e/known-event"}

            mock_cursor.__aiter__ = lambda self: async_iter()
            mock_db.events.find.return_value = mock_cursor

            events = await scraper.scrape(
                "https://example.com", "example.com", db=mock_db
            )
            assert events == []
