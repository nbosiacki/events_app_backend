"""
Base classes for site-specific event parsers.

Each parser targets a specific event website (e.g. Eventbrite, VisitStockholm)
and extracts event data using CSS selectors rather than Claude API calls.
Parsers include a health_check() that validates whether the site's HTML
structure still matches expected selectors — if it fails, the scraper
automatically falls back to the Claude agentic flow.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from bs4 import BeautifulSoup, Tag

from app.models.event import EventCreate


@dataclass
class ParserHealthCheck:
    """Result of checking whether a site's structure matches expected selectors."""

    is_healthy: bool
    message: str
    missing_selectors: list[str] = field(default_factory=list)


@dataclass
class ParserResult:
    """Output from a parser's extraction attempt."""

    events: list[EventCreate] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class BaseEventParser(ABC):
    """Abstract base class for site-specific event parsers.

    Subclasses must define:
        site_name      — human-readable site identifier
        url_patterns   — list of regex patterns matching URLs this parser handles
        health_check() — verify the page structure still matches expectations
        extract_event_urls() — pull event detail URLs from a listing page
        parse_event()  — extract a single EventCreate from an event detail page
    """

    @property
    @abstractmethod
    def site_name(self) -> str:
        """Human-readable name for this site (e.g. 'eventbrite.com')."""
        ...

    @property
    @abstractmethod
    def url_patterns(self) -> list[str]:
        """Regex patterns matching URLs this parser can handle."""
        ...

    @abstractmethod
    def health_check(self, html: str, url: str) -> ParserHealthCheck:
        """Check whether the page HTML still matches expected selectors.

        Returns a ParserHealthCheck indicating whether the parser can
        reliably extract data from this page structure.
        """
        ...

    @abstractmethod
    def extract_event_urls(self, html: str, base_url: str) -> list[str]:
        """Extract event detail URLs from a listing page."""
        ...

    @abstractmethod
    def parse_event(self, html: str, url: str) -> Optional[EventCreate]:
        """Parse a single event detail page into an EventCreate.

        Returns None if required fields cannot be extracted.
        """
        ...

    # ── Pagination ─────────────────────────────────────────────────────

    def get_total_pages(self, html: str) -> int:
        """Return the total number of listing pages.

        Override in subclasses that support paginated listing pages.
        Default: 1 (no pagination).
        """
        return 1

    # ── Concrete helpers ──────────────────────────────────────────────

    def _soup(self, html: str) -> BeautifulSoup:
        """Parse HTML into a BeautifulSoup tree."""
        return BeautifulSoup(html, "lxml")

    def _select_one(self, soup: BeautifulSoup, selector: str) -> Optional[Tag]:
        """Run a CSS selector and return the first match or None."""
        return soup.select_one(selector)

    def _select(self, soup: BeautifulSoup, selector: str) -> list[Tag]:
        """Run a CSS selector and return all matches."""
        return soup.select(selector)

    def _clean_text(self, tag: Optional[Tag]) -> str:
        """Extract stripped text from a tag, returning '' if tag is None."""
        if tag is None:
            return ""
        return tag.get_text(strip=True)
