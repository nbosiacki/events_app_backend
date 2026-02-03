"""
Eventbrite parser — site-specific scraper for eventbrite.com.

This is a stub implementation.  The CSS selectors below document the
expected page structure as of the time of writing.  Because Eventbrite
frequently changes its frontend, health_check() validates that the
selectors still work before attempting extraction.  If health_check
fails, the scraper automatically falls back to the Claude agentic flow.

To activate real parsing, update the selectors to match the current
Eventbrite HTML structure and ensure health_check passes.
"""

from datetime import datetime
from typing import Optional
from urllib.parse import urljoin

from app.models.event import EventCreate, Venue, Price
from app.parsers.base import BaseEventParser, ParserHealthCheck


# ── Selector constants ──────────────────────────────────────────────
# These document the expected Eventbrite HTML structure.
# Update when the site changes layout.

LISTING_SELECTORS = {
    "event_card": "div.event-card-details",
    "event_link": "a.event-card-link",
    "event_title": "div.event-card-details h2",
    "event_date": "div.event-card-details p",
}

EVENT_SELECTORS = {
    "title": "h1.event-title",
    "description": "div.event-description",
    "venue_name": "div.location-info p.location-info__address-text",
    "venue_address": "div.location-info div.location-info__address",
    "date": "span.date-info__full-datetime",
    "price": "div.conversion-bar__panel-info span",
    "image": "picture.listing-hero-image img",
    "category": "li.tags-link a",
}


class EventbriteParser(BaseEventParser):
    """Parser for eventbrite.com event pages."""

    @property
    def site_name(self) -> str:
        return "eventbrite.com"

    @property
    def url_patterns(self) -> list[str]:
        return [
            r"eventbrite\.com/d/.+/events",
            r"eventbrite\.com/e/",
        ]

    def health_check(self, html: str, url: str) -> ParserHealthCheck:
        """Verify that expected selectors exist in the page HTML.

        For listing pages, checks LISTING_SELECTORS.
        For event detail pages (containing /e/), checks EVENT_SELECTORS.
        """
        soup = self._soup(html)
        is_detail = "/e/" in url

        selectors = EVENT_SELECTORS if is_detail else LISTING_SELECTORS
        missing = []

        for name, selector in selectors.items():
            if not self._select_one(soup, selector):
                missing.append(f"{name}: {selector}")

        if missing:
            return ParserHealthCheck(
                is_healthy=False,
                message=f"Missing {len(missing)}/{len(selectors)} selectors on {self.site_name}",
                missing_selectors=missing,
            )

        return ParserHealthCheck(
            is_healthy=True,
            message=f"All selectors found on {self.site_name}",
        )

    def extract_event_urls(self, html: str, base_url: str) -> list[str]:
        """Extract event detail URLs from an Eventbrite listing page."""
        soup = self._soup(html)
        urls = []

        for link in self._select(soup, LISTING_SELECTORS["event_link"]):
            href = link.get("href", "")
            if href:
                full_url = urljoin(base_url, href)
                if "/e/" in full_url:
                    urls.append(full_url)

        return urls

    def parse_event(self, html: str, url: str) -> Optional[EventCreate]:
        """Parse a single Eventbrite event page into an EventCreate.

        Returns None if required fields (title, venue) cannot be extracted.
        """
        soup = self._soup(html)

        title = self._clean_text(self._select_one(soup, EVENT_SELECTORS["title"]))
        if not title:
            return None

        venue_name = self._clean_text(
            self._select_one(soup, EVENT_SELECTORS["venue_name"])
        )
        if not venue_name:
            return None

        description = self._clean_text(
            self._select_one(soup, EVENT_SELECTORS["description"])
        )
        venue_address = self._clean_text(
            self._select_one(soup, EVENT_SELECTORS["venue_address"])
        )

        # Detect online events
        is_online = False
        online_link = None
        if venue_name.lower() in ("online event", "online"):
            is_online = True
            venue_address = ""  # Will be converted to None below

        # Parse date
        date_text = self._clean_text(
            self._select_one(soup, EVENT_SELECTORS["date"])
        )
        datetime_start = datetime.now()
        if date_text:
            try:
                datetime_start = datetime.fromisoformat(date_text)
            except ValueError:
                pass  # Keep default

        # Parse price
        price_text = self._clean_text(
            self._select_one(soup, EVENT_SELECTORS["price"])
        )
        price_amount = 0.0
        if price_text:
            import re

            numbers = re.findall(r"[\d,]+\.?\d*", price_text.replace(",", ""))
            if numbers:
                try:
                    price_amount = float(numbers[0])
                except ValueError:
                    pass

        # Image
        img_tag = self._select_one(soup, EVENT_SELECTORS["image"])
        image_url = img_tag.get("src", "") if img_tag else None

        # Categories
        categories = [
            self._clean_text(tag)
            for tag in self._select(soup, EVENT_SELECTORS["category"])
        ]

        venue = Venue(name=venue_name, address=venue_address or None)
        price = Price.from_amount(price_amount, "SEK")

        return EventCreate(
            title=title,
            description=description or None,
            venue=venue,
            datetime_start=datetime_start,
            price=price,
            source_url=url,
            source_site=self.site_name,
            categories=categories,
            image_url=image_url or None,
            is_online=is_online,
            online_link=online_link,
        )
