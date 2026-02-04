"""
Tests for the site-specific parser framework.

Covers:
    BaseEventParser   — ABC enforcement, helper methods (_soup, _select_one, _clean_text)
    EventbriteParser  — URL pattern matching, health check (pass/fail),
                        extract_event_urls, parse_event (success + missing fields)
    ParserRegistry    — register, lookup by URL, no match returns None
"""

import pytest
from unittest.mock import patch

from app.parsers.base import BaseEventParser, ParserHealthCheck, ParserResult
from app.parsers.eventbrite import (
    EventbriteParser,
    LISTING_SELECTORS,
    EVENT_TESTID_SELECTORS,
    EVENT_JSON_LD_TYPES,
)
from app.parsers.registry import (
    register_parser,
    get_parser_for_url,
    get_all_parsers,
    _registry,
)


# ── Sample HTML fixtures ────────────────────────────────────────────

HEALTHY_LISTING_HTML = """
<html>
<body>
    <a class="event-card-link" href="/e/jazz-night-12345">Jazz Night</a>
    <a class="event-card-link" href="/e/rock-festival-67890">Rock Festival</a>
</body>
</html>
"""

HEALTHY_EVENT_HTML = """
<html>
<head>
<script type="application/ld+json">
{
    "@type": "SocialEvent",
    "name": "Jazz Night at Stampen",
    "description": "A wonderful evening of jazz music in Old Town.",
    "image": "https://img.example.com/jazz.jpg",
    "location": {
        "@type": "Place",
        "name": "Stampen",
        "address": {
            "@type": "PostalAddress",
            "streetAddress": "Stora Nygatan 5, Stockholm",
            "addressLocality": "Stockholm",
            "addressCountry": "SE"
        }
    },
    "startDate": "2025-06-01T20:00:00+02:00",
    "endDate": "2025-06-01T23:00:00+02:00",
    "offers": {
        "@type": "AggregateOffer",
        "lowPrice": "150.0",
        "priceCurrency": "SEK"
    },
    "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode"
}
</script>
</head>
<body>
    <h1 data-testid="event-title">Jazz Night at Stampen</h1>
    <a data-testid="event-venue">Stampen, Stockholm</a>
    <div data-testid="event-datetime">Sunday, Jun 1 from 8:00 pm to 11:00 pm CET</div>
    <div data-testid="event-tags"><a>jazz</a><a>music</a></div>
</body>
</html>
"""

UNHEALTHY_HTML = """
<html>
<body>
    <h1>Some random page</h1>
    <p>No event structure here.</p>
</body>
</html>
"""


# ── TestBaseEventParser ─────────────────────────────────────────────

class TestBaseEventParser:
    """BaseEventParser — ABC enforcement and helper methods."""

    def test_cannot_instantiate_abc(self):
        """BaseEventParser is abstract and should not be instantiable."""
        with pytest.raises(TypeError):
            BaseEventParser()

    def test_soup_helper(self):
        """_soup() should return a BeautifulSoup object."""
        parser = EventbriteParser()
        soup = parser._soup("<html><body><p>Hello</p></body></html>")
        assert soup.find("p") is not None

    def test_select_one_found(self):
        """_select_one() should return a Tag when the selector matches."""
        parser = EventbriteParser()
        soup = parser._soup("<div class='foo'>bar</div>")
        tag = parser._select_one(soup, "div.foo")
        assert tag is not None
        assert tag.get_text() == "bar"

    def test_select_one_not_found(self):
        """_select_one() should return None when the selector doesn't match."""
        parser = EventbriteParser()
        soup = parser._soup("<div>hello</div>")
        assert parser._select_one(soup, "div.missing") is None

    def test_clean_text_with_tag(self):
        """_clean_text() should strip whitespace from a tag's text."""
        parser = EventbriteParser()
        soup = parser._soup("<p>  hello world  </p>")
        tag = soup.find("p")
        assert parser._clean_text(tag) == "hello world"

    def test_clean_text_with_none(self):
        """_clean_text(None) should return empty string."""
        parser = EventbriteParser()
        assert parser._clean_text(None) == ""

    def test_select_returns_list(self):
        """_select() should return a list of matching Tags."""
        parser = EventbriteParser()
        soup = parser._soup("<ul><li>a</li><li>b</li><li>c</li></ul>")
        tags = parser._select(soup, "li")
        assert len(tags) == 3


# ── TestEventbriteParser ────────────────────────────────────────────

class TestEventbriteParser:
    """EventbriteParser — site-specific Eventbrite scraper."""

    def test_site_name(self):
        parser = EventbriteParser()
        assert parser.site_name == "eventbrite.com"

    def test_url_pattern_listing(self):
        """Listing URL should match."""
        parser = EventbriteParser()
        import re
        patterns = [re.compile(p) for p in parser.url_patterns]
        url = "https://www.eventbrite.com/d/sweden--stockholm/events/"
        assert any(p.search(url) for p in patterns)

    def test_url_pattern_event_detail(self):
        """Event detail URL should match."""
        parser = EventbriteParser()
        import re
        patterns = [re.compile(p) for p in parser.url_patterns]
        url = "https://www.eventbrite.com/e/jazz-night-12345"
        assert any(p.search(url) for p in patterns)

    def test_url_pattern_no_match(self):
        """Non-Eventbrite URL should not match."""
        parser = EventbriteParser()
        import re
        patterns = [re.compile(p) for p in parser.url_patterns]
        url = "https://www.visitstockholm.com/events/"
        assert not any(p.search(url) for p in patterns)

    def test_url_pattern_all_events(self):
        """All-events listing URL should match."""
        parser = EventbriteParser()
        import re
        patterns = [re.compile(p) for p in parser.url_patterns]
        url = "https://www.eventbrite.com/d/sweden--stockholm/all-events/"
        assert any(p.search(url) for p in patterns)

    def test_url_pattern_category_listing(self):
        """Category listing URL should match."""
        parser = EventbriteParser()
        import re
        patterns = [re.compile(p) for p in parser.url_patterns]
        url = "https://www.eventbrite.com/d/sweden--stockholm/music/"
        assert any(p.search(url) for p in patterns)

    def test_url_pattern_regional_domains(self):
        """Regional Eventbrite domains (.se, .co.uk, .sg) should match."""
        parser = EventbriteParser()
        import re
        patterns = [re.compile(p) for p in parser.url_patterns]
        for domain in ["eventbrite.se", "eventbrite.co.uk", "eventbrite.sg"]:
            url = f"https://www.{domain}/e/some-event-12345"
            assert any(p.search(url) for p in patterns), f"{domain} should match"

    def test_get_total_pages_with_pagination(self):
        """get_total_pages should extract page count from pagination element."""
        parser = EventbriteParser()
        html = '<html><body><li data-testid="pagination-parent"><span>1</span>of 7</li></body></html>'
        assert parser.get_total_pages(html) == 7

    def test_get_total_pages_no_pagination(self):
        """get_total_pages should return 1 when no pagination element exists."""
        parser = EventbriteParser()
        html = "<html><body><p>No pagination</p></body></html>"
        assert parser.get_total_pages(html) == 1

    def test_get_page_url(self):
        """get_page_url should append ?page=N to listing URL."""
        url = EventbriteParser.get_page_url(
            "https://www.eventbrite.com/d/sweden--stockholm/all-events/", 3
        )
        assert "page=3" in url
        assert url.startswith("https://www.eventbrite.com/d/sweden--stockholm/all-events/")

    def test_get_page_url_preserves_params(self):
        """get_page_url should preserve existing query params."""
        url = EventbriteParser.get_page_url(
            "https://www.eventbrite.com/d/sweden--stockholm/all-events/?q=jazz", 2
        )
        assert "page=2" in url
        assert "q=jazz" in url

    def test_health_check_healthy_listing(self):
        """Health check should pass for HTML with all listing selectors."""
        parser = EventbriteParser()
        health = parser.health_check(HEALTHY_LISTING_HTML, "https://www.eventbrite.com/d/sweden--stockholm/events/")
        assert health.is_healthy
        assert not health.missing_selectors

    def test_health_check_healthy_event(self):
        """Health check should pass for HTML with all event selectors."""
        parser = EventbriteParser()
        health = parser.health_check(HEALTHY_EVENT_HTML, "https://www.eventbrite.com/e/jazz-night-12345")
        assert health.is_healthy
        assert not health.missing_selectors

    def test_health_check_unhealthy(self):
        """Health check should fail for HTML missing expected selectors."""
        parser = EventbriteParser()
        health = parser.health_check(UNHEALTHY_HTML, "https://www.eventbrite.com/e/jazz-night-12345")
        assert not health.is_healthy
        assert len(health.missing_selectors) > 0

    def test_extract_event_urls(self):
        """extract_event_urls should find /e/ links from listing pages."""
        parser = EventbriteParser()
        urls = parser.extract_event_urls(
            HEALTHY_LISTING_HTML,
            "https://www.eventbrite.com/d/sweden--stockholm/events/",
        )
        assert len(urls) == 2
        assert any("/e/jazz-night-12345" in u for u in urls)
        assert any("/e/rock-festival-67890" in u for u in urls)

    def test_extract_event_urls_empty(self):
        """extract_event_urls should return [] if no event links found."""
        parser = EventbriteParser()
        urls = parser.extract_event_urls(UNHEALTHY_HTML, "https://example.com")
        assert urls == []

    def test_parse_event_success(self):
        """parse_event should extract all fields from valid event HTML."""
        parser = EventbriteParser()
        event = parser.parse_event(
            HEALTHY_EVENT_HTML,
            "https://www.eventbrite.com/e/jazz-night-12345",
        )
        assert event is not None
        assert event.title == "Jazz Night at Stampen"
        assert event.venue.name == "Stampen"
        assert event.venue.address == "Stora Nygatan 5, Stockholm"
        assert event.price.amount == 150
        assert event.price.bucket == "standard"
        assert event.source_site == "eventbrite.com"
        assert "jazz" in event.categories
        assert event.image_url == "https://img.example.com/jazz.jpg"

    def test_parse_event_missing_title(self):
        """parse_event should return None if title is missing."""
        parser = EventbriteParser()
        html = "<html><body><p>No event data here</p></body></html>"
        event = parser.parse_event(html, "https://www.eventbrite.com/e/test")
        assert event is None

    def test_parse_event_missing_venue(self):
        """parse_event should return None if venue is missing."""
        parser = EventbriteParser()
        html = '<html><body><h1 data-testid="event-title">Title</h1></body></html>'
        event = parser.parse_event(html, "https://www.eventbrite.com/e/test")
        assert event is None

    def test_parse_event_testid_fallback(self):
        """parse_event should extract from data-testid when no JSON-LD."""
        parser = EventbriteParser()
        html = """
        <html><body>
            <h1 data-testid="event-title">Testid Event</h1>
            <a data-testid="event-venue">Fallback Venue</a>
            <div data-testid="event-datetime">Monday, Jun 2 from 7:00 pm</div>
            <div data-testid="event-tags"><a>tag1</a><a>tag2</a></div>
        </body></html>
        """
        event = parser.parse_event(html, "https://www.eventbrite.com/e/testid-event")
        assert event is not None
        assert event.title == "Testid Event"
        assert event.venue.name == "Fallback Venue"
        assert event.price.amount == 0
        assert event.price.bucket == "free"
        assert "tag1" in event.categories

    def test_parse_online_event(self):
        """parse_event should detect online events via eventAttendanceMode."""
        parser = EventbriteParser()
        html = """
        <html><head>
        <script type="application/ld+json">
        {
            "@type": "Event",
            "name": "Remote Workshop",
            "description": "Learn from home.",
            "image": "https://img.test.com/online.jpg",
            "location": {"@type": "VirtualLocation", "name": "Online Event", "url": "https://zoom.us/j/123"},
            "startDate": "2025-06-01T14:00:00+02:00",
            "offers": {"lowPrice": "0.0", "priceCurrency": "SEK"},
            "eventAttendanceMode": "https://schema.org/OnlineEventAttendanceMode"
        }
        </script>
        </head><body>
            <h1 data-testid="event-title">Remote Workshop</h1>
            <div data-testid="event-tags"><a>workshop</a></div>
        </body></html>
        """
        event = parser.parse_event(html, "https://www.eventbrite.com/e/remote-workshop")
        assert event is not None
        assert event.title == "Remote Workshop"
        assert event.is_online is True
        assert event.venue.address is None
        assert event.online_link == "https://zoom.us/j/123"

    def test_parse_sports_event(self):
        """parse_event should handle SportsEvent @type."""
        parser = EventbriteParser()
        html = """
        <html><head>
        <script type="application/ld+json">
        {
            "@type": "SportsEvent",
            "name": "Stockholm Marathon",
            "location": {"@type": "Place", "name": "Stadion", "address": {"streetAddress": "Olympic Way 1"}},
            "startDate": "2025-06-15T08:00:00+02:00",
            "offers": {"lowPrice": "500", "priceCurrency": "SEK"}
        }
        </script>
        </head><body></body></html>
        """
        event = parser.parse_event(html, "https://www.eventbrite.com/e/marathon-12345")
        assert event is not None
        assert event.title == "Stockholm Marathon"
        assert event.venue.name == "Stadion"

    def test_extract_event_urls_strips_tracking_params(self):
        """extract_event_urls should strip ?aff= and other tracking params."""
        parser = EventbriteParser()
        html = """
        <html><body>
            <a class="event-card-link" href="/e/event-1?aff=ebdssbdestsearch">Event 1</a>
            <a class="event-card-link" href="/e/event-1">Event 1 again</a>
        </body></html>
        """
        urls = parser.extract_event_urls(html, "https://www.eventbrite.com/d/test/")
        assert len(urls) == 1
        assert "aff=" not in urls[0]

    def test_clean_event_url_strips_aff(self):
        """_clean_event_url should strip aff param but keep other params."""
        clean = EventbriteParser._clean_event_url(
            "https://www.eventbrite.com/e/test-12345?aff=ebdssbdestsearch&page=2"
        )
        assert "aff=" not in clean
        assert "page=2" in clean

    def test_parse_event_cleans_source_url(self):
        """parse_event should strip tracking params from source_url."""
        parser = EventbriteParser()
        event = parser.parse_event(
            HEALTHY_EVENT_HTML,
            "https://www.eventbrite.com/e/jazz-night-12345?aff=ebdssbdestsearch",
        )
        assert event is not None
        assert "aff=" not in event.source_url
        assert event.source_url == "https://www.eventbrite.com/e/jazz-night-12345"

    def test_parse_event_converts_usd_to_sek(self):
        """parse_event should convert USD prices to SEK."""
        parser = EventbriteParser()
        html = """
        <html><head>
        <script type="application/ld+json">
        {
            "@type": "Event",
            "name": "USD Event",
            "location": {"@type": "Place", "name": "Venue", "address": {"streetAddress": "Addr 1"}},
            "startDate": "2025-06-01T20:00:00+02:00",
            "offers": {"lowPrice": "25.0", "priceCurrency": "USD"}
        }
        </script>
        </head><body></body></html>
        """
        with patch("app.parsers.eventbrite.convert_to_sek", return_value=250.0):
            event = parser.parse_event(html, "https://www.eventbrite.com/e/usd-event")
        assert event is not None
        assert event.price.currency == "SEK"
        assert event.price.amount == 250.0
        assert event.price.bucket == "standard"

    def test_parse_event_sek_not_converted(self):
        """parse_event should not call convert_to_sek for SEK prices."""
        parser = EventbriteParser()
        html = """
        <html><head>
        <script type="application/ld+json">
        {
            "@type": "Event",
            "name": "SEK Event",
            "location": {"@type": "Place", "name": "Venue", "address": {"streetAddress": "Addr 1"}},
            "startDate": "2025-06-01T20:00:00+02:00",
            "offers": {"lowPrice": "150.0", "priceCurrency": "SEK"}
        }
        </script>
        </head><body></body></html>
        """
        with patch("app.parsers.eventbrite.convert_to_sek") as mock_convert:
            event = parser.parse_event(html, "https://www.eventbrite.com/e/sek-event")
        mock_convert.assert_not_called()
        assert event.price.amount == 150.0
        assert event.price.currency == "SEK"

    def test_parse_event_free_event(self):
        """parse_event should handle events with no price (free)."""
        parser = EventbriteParser()
        html = """
        <html><head>
        <script type="application/ld+json">
        {
            "@type": "SocialEvent",
            "name": "Free Event",
            "location": {"@type": "Place", "name": "Park"},
            "startDate": "2025-06-01T12:00:00+02:00",
            "offers": {"lowPrice": "0.0", "priceCurrency": "SEK"}
        }
        </script>
        </head><body></body></html>
        """
        event = parser.parse_event(html, "https://www.eventbrite.com/e/free")
        assert event is not None
        assert event.price.amount == 0
        assert event.price.bucket == "free"


# ── TestParserRegistry ──────────────────────────────────────────────

class TestParserRegistry:
    """Parser registry — registration and lookup."""

    def test_eventbrite_registered_on_import(self):
        """Eventbrite parser should be auto-registered on module import."""
        parsers = get_all_parsers()
        assert "eventbrite.com" in parsers

    def test_lookup_eventbrite_listing(self):
        """get_parser_for_url should find Eventbrite for listing URLs."""
        parser = get_parser_for_url("https://www.eventbrite.com/d/sweden--stockholm/events/")
        assert parser is not None
        assert parser.site_name == "eventbrite.com"

    def test_lookup_eventbrite_detail(self):
        """get_parser_for_url should find Eventbrite for event detail URLs."""
        parser = get_parser_for_url("https://www.eventbrite.com/e/jazz-night-12345")
        assert parser is not None
        assert parser.site_name == "eventbrite.com"

    def test_lookup_no_match(self):
        """get_parser_for_url should return None for unknown URLs."""
        parser = get_parser_for_url("https://www.randomsite.com/events")
        assert parser is None

    def test_register_custom_parser(self):
        """register_parser should add a new parser to the registry."""
        class TestParser(BaseEventParser):
            @property
            def site_name(self):
                return "test-site.com"

            @property
            def url_patterns(self):
                return [r"test-site\.com/events"]

            def health_check(self, html, url):
                return ParserHealthCheck(is_healthy=True, message="OK")

            def extract_event_urls(self, html, base_url):
                return []

            def parse_event(self, html, url):
                return None

        register_parser(TestParser())

        parser = get_parser_for_url("https://test-site.com/events/123")
        assert parser is not None
        assert parser.site_name == "test-site.com"

        # Clean up
        if "test-site.com" in _registry:
            del _registry["test-site.com"]
