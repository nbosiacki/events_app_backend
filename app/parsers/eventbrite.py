"""
Eventbrite parser — site-specific scraper for eventbrite.com.

Extracts event data using two strategies:
  1. JSON-LD structured data (schema.org, published for SEO — most reliable)
  2. data-testid DOM selectors (semi-stable fallback)

health_check() verifies that at least one strategy works before
attempting extraction.  If health_check fails, the scraper automatically
falls back to the Claude agentic flow.
"""

import json
import re
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin, urlparse, urlencode, parse_qs, urlunparse

from bs4 import BeautifulSoup

from app.models.event import EventCreate, Venue, Price
from app.parsers.base import BaseEventParser, ParserHealthCheck


# ── Selector constants ──────────────────────────────────────────────
# Primary: JSON-LD structured data (schema.org, published for SEO)
# Secondary: data-testid attributes (semi-stable, used by Eventbrite's own tests)

EVENT_JSON_LD_TYPES = [
    "Event", "SocialEvent", "MusicEvent", "BusinessEvent",
    "EducationEvent", "ExhibitionEvent", "Festival", "Hackathon",
    "SportsEvent", "TheaterEvent", "DanceEvent", "ComedyEvent",
    "FoodEvent", "LiteraryEvent", "ScreeningEvent", "VisualArtsEvent",
]

EVENT_TESTID_SELECTORS = {
    "title": '[data-testid="event-title"]',
    "venue": '[data-testid="event-venue"]',
    "datetime": '[data-testid="event-datetime"]',
    "tags": '[data-testid="event-tags"]',
}

LISTING_SELECTORS = {
    "event_link": "a.event-card-link",
}


class EventbriteParser(BaseEventParser):
    """Parser for eventbrite.com event pages."""

    @property
    def site_name(self) -> str:
        return "eventbrite.com"

    @property
    def url_patterns(self) -> list[str]:
        return [
            r"eventbrite\.[a-z.]+/d/.+/",
            r"eventbrite\.[a-z.]+/e/",
        ]

    def _extract_json_ld(
        self, soup: BeautifulSoup, target_types: list[str]
    ) -> Optional[dict]:
        """Extract JSON-LD structured data matching one of the target @type values."""
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
            except (json.JSONDecodeError, TypeError):
                continue

            items = data if isinstance(data, list) else [data]
            for item in items:
                item_type = item.get("@type", "")
                if isinstance(item_type, list):
                    if any(t in target_types for t in item_type):
                        return item
                elif item_type in target_types:
                    return item
        return None

    def get_total_pages(self, html: str) -> int:
        """Detect total listing pages from Eventbrite's pagination element."""
        soup = self._soup(html)
        pag = soup.select_one('[data-testid="pagination-parent"]')
        if pag:
            text = pag.get_text(strip=True)
            # Text is like "1of 7" or "2of 7"
            match = re.search(r"of\s*(\d+)", text)
            if match:
                return int(match.group(1))
        return 1

    @staticmethod
    def get_page_url(base_url: str, page: int) -> str:
        """Append ?page=N to a listing URL, preserving existing query params."""
        parsed = urlparse(base_url)
        params = parse_qs(parsed.query)
        params["page"] = [str(page)]
        new_query = urlencode(params, doseq=True)
        return urlunparse(parsed._replace(query=new_query))

    def health_check(self, html: str, url: str) -> ParserHealthCheck:
        """Verify that event data can be extracted from the page.

        Checks for JSON-LD structured data (primary) or data-testid
        attributes (secondary).  For listing pages, checks for event
        card links or JSON-LD ItemList.
        """
        soup = self._soup(html)
        is_detail = "/e/" in url

        if is_detail:
            json_ld = self._extract_json_ld(soup, EVENT_JSON_LD_TYPES)
            has_testid_title = self._select_one(
                soup, EVENT_TESTID_SELECTORS["title"]
            )

            if json_ld and json_ld.get("name") and json_ld.get("startDate"):
                return ParserHealthCheck(
                    is_healthy=True,
                    message=f"JSON-LD structured data found on {self.site_name}",
                )
            elif has_testid_title:
                return ParserHealthCheck(
                    is_healthy=True,
                    message=f"data-testid selectors found on {self.site_name} (no JSON-LD)",
                )
            else:
                missing = []
                if not json_ld:
                    missing.append("json_ld: <script type=application/ld+json> with Event type")
                if not has_testid_title:
                    missing.append('testid: [data-testid="event-title"]')
                return ParserHealthCheck(
                    is_healthy=False,
                    message=f"Missing event data sources on {self.site_name}",
                    missing_selectors=missing,
                )
        else:
            has_links = bool(self._select(soup, LISTING_SELECTORS["event_link"]))
            json_ld = self._extract_json_ld(soup, ["ItemList"])

            if has_links or json_ld:
                return ParserHealthCheck(
                    is_healthy=True,
                    message=f"Listing data found on {self.site_name}",
                )
            else:
                return ParserHealthCheck(
                    is_healthy=False,
                    message=f"No listing structure found on {self.site_name}",
                    missing_selectors=[
                        "event_link: a.event-card-link",
                        "json_ld: ItemList",
                    ],
                )

    @staticmethod
    def _clean_event_url(url: str) -> str:
        """Strip tracking query params (aff, etc.) from Eventbrite event URLs."""
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        # Remove known tracking params
        for key in ("aff", "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"):
            params.pop(key, None)
        clean_query = urlencode(params, doseq=True)
        return urlunparse(parsed._replace(query=clean_query))

    def extract_event_urls(self, html: str, base_url: str) -> list[str]:
        """Extract event detail URLs from an Eventbrite listing page."""
        soup = self._soup(html)
        seen = set()
        urls = []

        def _add(url: str) -> None:
            clean = self._clean_event_url(url)
            if clean not in seen:
                seen.add(clean)
                urls.append(clean)

        # Primary: a.event-card-link elements
        for link in self._select(soup, LISTING_SELECTORS["event_link"]):
            href = link.get("href", "")
            if href:
                full_url = urljoin(base_url, href)
                if "/e/" in full_url:
                    _add(full_url)

        # Fallback: JSON-LD ItemList
        if not urls:
            json_ld = self._extract_json_ld(soup, ["ItemList"])
            if json_ld:
                for item in json_ld.get("itemListElement", []):
                    item_data = item.get("item", item)
                    item_url = item_data.get("url", "")
                    if item_url and "/e/" in item_url:
                        _add(item_url)

        return urls

    def parse_event(self, html: str, url: str) -> Optional[EventCreate]:
        """Parse a single Eventbrite event page into an EventCreate.

        Primary: JSON-LD structured data.
        Secondary: data-testid DOM selectors.
        Returns None if required fields (title, venue) cannot be extracted.
        """
        soup = self._soup(html)
        json_ld = self._extract_json_ld(soup, EVENT_JSON_LD_TYPES)

        # ── Title ──
        title = (json_ld.get("name", "") if json_ld else "").strip()
        if not title:
            title = self._clean_text(
                self._select_one(soup, EVENT_TESTID_SELECTORS["title"])
            )
        if not title:
            return None

        # ── Venue ──
        venue_name = None
        venue_address = None
        location = json_ld.get("location", {}) if json_ld else {}

        if isinstance(location, dict):
            venue_name = (location.get("name") or "").strip()
            address_obj = location.get("address", {})
            if isinstance(address_obj, dict):
                venue_address = (address_obj.get("streetAddress") or "").strip() or None
            elif isinstance(address_obj, str):
                venue_address = address_obj.strip() or None

        if not venue_name:
            venue_text = self._clean_text(
                self._select_one(soup, EVENT_TESTID_SELECTORS["venue"])
            )
            if venue_text:
                venue_name = venue_text

        if not venue_name:
            return None

        # ── Online event detection ──
        is_online = False
        online_link = None
        attendance_mode = json_ld.get("eventAttendanceMode", "") if json_ld else ""

        if "OnlineEventAttendanceMode" in attendance_mode or "MixedEventAttendanceMode" in attendance_mode:
            is_online = True
            if isinstance(location, dict) and location.get("url"):
                online_link = location["url"]
        elif venue_name.lower() in ("online event", "online"):
            is_online = True

        if is_online:
            venue_address = None

        # ── Dates ──
        # Strip timezone info to stay consistent with the rest of the codebase
        # which uses naive datetimes throughout (seed data, queries, etc.)
        datetime_start = None
        datetime_end = None
        if json_ld:
            for field, target in [("startDate", "start"), ("endDate", "end")]:
                raw = json_ld.get(field, "")
                if raw:
                    try:
                        parsed = datetime.fromisoformat(raw)
                        parsed = parsed.replace(tzinfo=None)
                        if target == "start":
                            datetime_start = parsed
                        else:
                            datetime_end = parsed
                    except ValueError:
                        pass

        if not datetime_start:
            datetime_start = datetime.now()

        # ── Price ──
        price_amount = 0.0
        price_currency = "SEK"
        if json_ld:
            offers = json_ld.get("offers", {})
            if isinstance(offers, list) and offers:
                offers = offers[0]
            if isinstance(offers, dict):
                low = offers.get("lowPrice") or offers.get("price")
                if low is not None:
                    try:
                        price_amount = float(low)
                    except (ValueError, TypeError):
                        pass
                price_currency = offers.get("priceCurrency") or "SEK"

        # ── Description ──
        description = None
        if json_ld:
            description = (json_ld.get("description") or "").strip() or None

        # ── Image ──
        image_url = None
        if json_ld:
            image_url = (json_ld.get("image") or "").strip() or None

        # ── Categories (from data-testid only, not in JSON-LD) ──
        categories = []
        tags_container = self._select_one(soup, EVENT_TESTID_SELECTORS["tags"])
        if tags_container:
            for tag in tags_container.find_all("a") or tags_container.find_all("span"):
                text = tag.get_text(strip=True)
                if text:
                    categories.append(text)

        # ── Build result ──
        venue = Venue(name=venue_name, address=venue_address)
        price = Price.from_amount(price_amount, price_currency)

        return EventCreate(
            title=title,
            description=description,
            venue=venue,
            datetime_start=datetime_start,
            datetime_end=datetime_end,
            price=price,
            source_url=self._clean_event_url(url),
            source_site=self.site_name,
            categories=categories,
            image_url=image_url,
            is_online=is_online,
            online_link=online_link,
        )
