"""
Tests for the site-specific parser framework.

Covers:
    BaseEventParser   — ABC enforcement, helper methods (_soup, _select_one, _clean_text)
    EventbriteParser  — URL pattern matching, health check (pass/fail),
                        extract_event_urls, parse_event (success + missing fields)
    ParserRegistry    — register, lookup by URL, no match returns None
"""

import pytest

from app.parsers.base import BaseEventParser, ParserHealthCheck, ParserResult
from app.parsers.eventbrite import (
    EventbriteParser,
    LISTING_SELECTORS,
    EVENT_SELECTORS,
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
    <div class="event-card-details">
        <h2>Jazz Night at Stampen</h2>
        <p>June 1, 2025</p>
    </div>
    <a class="event-card-link" href="/e/jazz-night-12345">Jazz Night</a>
    <div class="event-card-details">
        <h2>Rock Festival</h2>
        <p>June 5, 2025</p>
    </div>
    <a class="event-card-link" href="/e/rock-festival-67890">Rock Festival</a>
</body>
</html>
"""

HEALTHY_EVENT_HTML = """
<html>
<body>
    <h1 class="event-title">Jazz Night at Stampen</h1>
    <div class="event-description">A wonderful evening of jazz music in Old Town.</div>
    <div class="location-info">
        <p class="location-info__address-text">Stampen</p>
        <div class="location-info__address">Stora Nygatan 5, Stockholm</div>
    </div>
    <span class="date-info__full-datetime">2025-06-01T20:00:00</span>
    <div class="conversion-bar__panel-info"><span>150 SEK</span></div>
    <picture class="listing-hero-image"><img src="https://img.example.com/jazz.jpg" /></picture>
    <li class="tags-link"><a>jazz</a></li>
    <li class="tags-link"><a>music</a></li>
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
        html = "<html><body><div class='location-info'><p class='location-info__address-text'>Venue</p></div></body></html>"
        event = parser.parse_event(html, "https://www.eventbrite.com/e/test")
        assert event is None

    def test_parse_event_missing_venue(self):
        """parse_event should return None if venue is missing."""
        parser = EventbriteParser()
        html = "<html><body><h1 class='event-title'>Title</h1></body></html>"
        event = parser.parse_event(html, "https://www.eventbrite.com/e/test")
        assert event is None

    def test_parse_online_event(self):
        """parse_event should detect online events and set is_online=True."""
        parser = EventbriteParser()
        html = """
        <html><body>
            <h1 class="event-title">Remote Workshop</h1>
            <div class="event-description">Learn from home.</div>
            <div class="location-info">
                <p class="location-info__address-text">Online Event</p>
                <div class="location-info__address">Online</div>
            </div>
            <span class="date-info__full-datetime">2025-06-01T14:00:00</span>
            <div class="conversion-bar__panel-info"><span>Free</span></div>
            <picture class="listing-hero-image"><img src="https://img.test.com/online.jpg" /></picture>
            <li class="tags-link"><a>workshop</a></li>
        </body></html>
        """
        event = parser.parse_event(html, "https://www.eventbrite.com/e/remote-workshop")
        assert event is not None
        assert event.title == "Remote Workshop"
        assert event.is_online is True
        assert event.venue.address is None

    def test_parse_event_free_event(self):
        """parse_event should handle events with no price (free)."""
        parser = EventbriteParser()
        html = """
        <html><body>
            <h1 class="event-title">Free Event</h1>
            <div class="location-info">
                <p class="location-info__address-text">Park</p>
            </div>
        </body></html>
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
