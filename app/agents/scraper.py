import csv
import httpx
from bs4 import BeautifulSoup
from anthropic import Anthropic
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse
import json
import re

from app.config import get_settings
from app.models.event import EventCreate, Venue, Price

settings = get_settings()


class EventScraper:
    """Claude-powered web scraper for event discovery.

    Supports a three-tier scraping strategy:
    1. URL pre-check — skip events already in the database
    2. Site parser — use CSS selectors for known sites (fast, free)
    3. Claude fallback — agentic scraping for unknown sites or when parsers fail
    """

    def __init__(self):
        self.client = Anthropic(api_key=settings.anthropic_api_key)
        self.http_client = httpx.Client(
            timeout=30.0,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            },
        )

    def _fetch_page(self, url: str) -> dict:
        """Fetch and parse a web page."""
        try:
            response = self.http_client.get(url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")

            # Remove script and style elements
            for element in soup(["script", "style", "nav", "footer", "header"]):
                element.decompose()

            # Get text content, limiting size
            text = soup.get_text(separator="\n", strip=True)
            text = re.sub(r"\n{3,}", "\n\n", text)  # Remove excessive newlines
            text = text[:15000]  # Limit to ~15k chars

            # Extract links
            links = []
            for a in soup.find_all("a", href=True)[:50]:
                href = a["href"]
                if href.startswith("/"):
                    href = urljoin(url, href)
                if href.startswith("http"):
                    links.append({"text": a.get_text(strip=True)[:100], "url": href})

            return {
                "success": True,
                "url": url,
                "title": soup.title.string if soup.title else "",
                "text": text,
                "links": links,
            }
        except Exception as e:
            return {"success": False, "url": url, "error": str(e)}

    def _fetch_raw_html(self, url: str) -> Optional[str]:
        """Fetch a page and return its raw HTML, or None on error."""
        try:
            response = self.http_client.get(url)
            response.raise_for_status()
            return response.text
        except Exception:
            return None

    def extract_urls_from_page(self, html: str, base_url: str) -> list[str]:
        """Extract event-like URLs from a page's links.

        Filters for links that look like event detail pages based on
        common URL patterns (e.g., /e/, /event/, /events/).
        """
        soup = BeautifulSoup(html, "lxml")
        urls = set()

        event_patterns = re.compile(
            r"/e/|/event/|/events/[^?#]*[a-z0-9-]$|/event-|tickets",
            re.IGNORECASE,
        )

        for a in soup.find_all("a", href=True):
            href = a["href"]
            full_url = urljoin(base_url, href)

            # Only keep http(s) URLs on the same domain or known event sites
            parsed = urlparse(full_url)
            if not parsed.scheme.startswith("http"):
                continue

            if event_patterns.search(full_url):
                # Normalize: strip fragment and trailing slash
                clean = full_url.split("#")[0].rstrip("/")
                urls.add(clean)

        return sorted(urls)

    async def filter_known_urls(self, urls: list[str], db) -> list[str]:
        """Batch-check URLs against the database and return only new ones.

        Args:
            urls: Candidate event URLs
            db: Motor database instance

        Returns:
            URLs not already present as source_url in the events collection
        """
        if not urls:
            return []

        cursor = db.events.find(
            {"source_url": {"$in": urls}},
            {"source_url": 1, "_id": 0},
        )
        existing = {doc["source_url"] async for doc in cursor}
        return [u for u in urls if u not in existing]

    def try_site_parser(self, url: str, html: str) -> Optional[list[EventCreate]]:
        """Attempt to parse events using a registered site-specific parser.

        Returns a list of EventCreate if the parser is healthy and extraction
        succeeds, or None if no parser matches or the health check fails.
        """
        from app.parsers import get_parser_for_url

        parser = get_parser_for_url(url)
        if not parser:
            return None

        health = parser.health_check(html, url)
        if not health.is_healthy:
            print(
                f"  Parser {parser.site_name} health check failed: {health.message}"
            )
            return None

        # Try listing page extraction
        event_urls = parser.extract_event_urls(html, url)
        if event_urls:
            events = []
            for event_url in event_urls:
                event_html = self._fetch_raw_html(event_url)
                if not event_html:
                    continue
                event = parser.parse_event(event_html, event_url)
                if event:
                    events.append(event)
            return events if events else None

        # Try parsing the page directly as a single event
        event = parser.parse_event(html, url)
        return [event] if event else None

    def _get_tools(self):
        """Define tools available to Claude for scraping."""
        return [
            {
                "name": "fetch_page",
                "description": "Fetch and parse a web page, returning its text content and links.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "The URL to fetch",
                        }
                    },
                    "required": ["url"],
                },
            },
            {
                "name": "extract_events",
                "description": "Extract structured event data from page content. Call this when you have found event information.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "events": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "title": {"type": "string"},
                                    "description": {"type": "string"},
                                    "venue_name": {"type": "string"},
                                    "venue_address": {"type": "string"},
                                    "datetime_start": {
                                        "type": "string",
                                        "description": "ISO format datetime",
                                    },
                                    "datetime_end": {"type": "string"},
                                    "price_amount": {"type": "number"},
                                    "price_currency": {"type": "string"},
                                    "source_url": {"type": "string"},
                                    "categories": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "image_url": {
                                        "type": "string",
                                        "description": "URL of the event's thumbnail or hero image",
                                    },
                                    "is_online": {
                                        "type": "boolean",
                                        "description": "True if the event is online or virtual",
                                    },
                                    "online_link": {
                                        "type": "string",
                                        "description": "URL for joining online/virtual events (e.g., Zoom, YouTube link)",
                                    },
                                },
                                "required": ["title", "venue_name", "source_url"],
                            },
                        }
                    },
                    "required": ["events"],
                },
            },
            {
                "name": "done",
                "description": "Call this when you have finished scraping and extracted all available events.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "summary": {
                            "type": "string",
                            "description": "Brief summary of what was found",
                        }
                    },
                    "required": ["summary"],
                },
            },
        ]

    def _handle_tool_call(self, tool_name: str, tool_input: dict) -> str:
        """Execute a tool and return result."""
        if tool_name == "fetch_page":
            result = self._fetch_page(tool_input["url"])
            return json.dumps(result)
        elif tool_name == "extract_events":
            # Just acknowledge - we'll collect these in the main loop
            return json.dumps({"success": True, "count": len(tool_input["events"])})
        elif tool_name == "done":
            return json.dumps({"success": True})
        return json.dumps({"error": f"Unknown tool: {tool_name}"})

    async def _scrape_with_claude(
        self, seed_url: str, source_site: str, max_pages: int = 5
    ) -> list[EventCreate]:
        """Original Claude agentic scrape flow."""
        all_events = []
        messages = [
            {
                "role": "user",
                "content": f"""You are an event scraper. Your task is to find and extract event information from the website starting at: {seed_url}

Instructions:
1. First, fetch the seed page using fetch_page
2. Look for event listings - these typically have titles, dates, venues, and prices
3. Extract event details using extract_events when you find them
4. If you see pagination or links to more events, follow them (up to {max_pages} pages total)
5. When done, call the done tool

Focus on events in Stockholm, Sweden. Extract as much detail as possible including:
- Event title
- Venue name and address
- Date and time (convert to ISO format YYYY-MM-DDTHH:MM:SS)
- Price (0 if free, otherwise the numeric amount)
- Categories/tags
- Event URL
- Whether the event is online/virtual (is_online)
- If online, the meeting/streaming link (online_link)

Start by fetching the seed page.""",
            }
        ]

        pages_fetched = 0

        while pages_fetched < max_pages:
            response = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=4096,
                tools=self._get_tools(),
                messages=messages,
            )

            # Process the response
            tool_calls = [
                block for block in response.content if block.type == "tool_use"
            ]

            if not tool_calls:
                # No more tool calls, we're done
                break

            # Handle each tool call
            tool_results = []
            done = False

            for tool_call in tool_calls:
                tool_name = tool_call.name
                tool_input = tool_call.input

                if tool_name == "fetch_page":
                    pages_fetched += 1

                if tool_name == "extract_events":
                    # Collect the extracted events
                    for event_data in tool_input.get("events", []):
                        try:
                            event = self._create_event(event_data, source_site)
                            if event:
                                all_events.append(event)
                        except Exception as e:
                            print(f"Error creating event: {e}")

                if tool_name == "done":
                    done = True

                result = self._handle_tool_call(tool_name, tool_input)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_call.id,
                        "content": result,
                    }
                )

            # Add assistant message and tool results
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})

            if done:
                break

        return all_events

    async def scrape(
        self, seed_url: str, source_site: str, max_pages: int = 5, db=None,
        parser_only: bool = False,
    ) -> list[EventCreate]:
        """
        Scrape events from a website using a three-tier strategy.

        When db is provided:
        1. Fetch the listing page and extract event URLs
        2. Pre-check URLs against the database, skipping known ones
        3. For each new URL, try the site-specific parser first
        4. Fall back to Claude agentic scraping for remaining URLs

        When db is None, uses the original Claude-only flow for backward
        compatibility (unless parser_only is True).

        Args:
            seed_url: Starting URL to scrape
            source_site: Name of the source (e.g., "eventbrite.com")
            max_pages: Maximum number of pages to fetch
            db: Optional Motor database instance for URL pre-checking
            parser_only: If True, never fall back to Claude — only use
                site parsers.  Useful for debugging parser extraction.

        Returns:
            List of EventCreate objects
        """
        if db is None:
            if parser_only:
                print("  parser-only mode: skipping Claude (no db provided)")
            else:
                return await self._scrape_with_claude(seed_url, source_site, max_pages)

        all_events = []
        claude_urls = []

        # Step 1: Fetch listing page
        html = self._fetch_raw_html(seed_url)
        if not html:
            print(f"  Failed to fetch listing page: {seed_url}")
            if parser_only:
                return []
            return await self._scrape_with_claude(seed_url, source_site, max_pages)

        # Step 2: Extract event URLs (with pagination)
        # Prefer parser's extract_event_urls() over generic regex when available
        from app.parsers import get_parser_for_url as _get_parser
        listing_parser = _get_parser(seed_url)

        if listing_parser:
            event_urls_set = set(listing_parser.extract_event_urls(html, seed_url))
        else:
            event_urls_set = set(self.extract_urls_from_page(html, seed_url))
        print(f"  Page 1: {len(event_urls_set)} event URLs")

        # Check for additional listing pages
        if listing_parser:
            total_pages = listing_parser.get_total_pages(html)
            pages_to_fetch = min(total_pages, max_pages)
            if pages_to_fetch > 1:
                print(f"  Pagination: {total_pages} pages detected, fetching {pages_to_fetch}")
                for page in range(2, pages_to_fetch + 1):
                    page_url = listing_parser.get_page_url(seed_url, page)
                    page_html = self._fetch_raw_html(page_url)
                    if page_html:
                        page_urls = set(listing_parser.extract_event_urls(page_html, page_url))
                        new = page_urls - event_urls_set
                        event_urls_set |= page_urls
                        print(f"  Page {page}: {len(new)} new URLs ({len(event_urls_set)} total)")

        event_urls = sorted(event_urls_set)
        print(f"  Total unique event URLs: {len(event_urls)}")

        if not event_urls:
            if parser_only:
                print("  parser-only mode: no event URLs found, nothing to parse")
                return []
            # No extractable URLs — fall back to Claude for the whole flow
            return await self._scrape_with_claude(seed_url, source_site, max_pages)

        # Step 3: Pre-check against database
        if db is not None:
            new_urls = await self.filter_known_urls(event_urls, db)
            skipped = len(event_urls) - len(new_urls)
            print(f"  URL pre-check: {len(new_urls)} new, {skipped} already known")
        else:
            new_urls = event_urls

        if not new_urls:
            print("  All URLs already known — nothing to scrape")
            return []

        # Step 4: Try site parser, then Claude fallback
        failed_parses = []

        for url in new_urls:
            event_html = self._fetch_raw_html(url)
            if not event_html:
                claude_urls.append(url)
                failed_parses.append((url, "fetch_failed"))
                continue

            parsed = self.try_site_parser(url, event_html)
            if parsed:
                print(f"  Parser extracted {len(parsed)} event(s) from {url}")
                all_events.extend(parsed)
            else:
                claude_urls.append(url)
                failed_parses.append((url, "parser_failed"))

        # Step 5: Run Claude on remaining URLs
        if claude_urls and not parser_only:
            print(f"  Claude fallback for {len(claude_urls)} URL(s)")
            claude_events = await self._scrape_with_claude(
                seed_url, source_site, max_pages
            )
            all_events.extend(claude_events)
        elif claude_urls and parser_only:
            print(f"  parser-only mode: skipped Claude fallback for {len(claude_urls)} URL(s)")

        # Step 6: Write failed parses to CSV for troubleshooting
        if failed_parses:
            self._write_failed_csv(failed_parses, source_site)

        return all_events

    def _write_failed_csv(
        self, failed: list[tuple[str, str]], source_site: str
    ) -> None:
        """Write failed parse URLs to a timestamped CSV for troubleshooting."""
        logs_dir = Path(__file__).parent.parent.parent / "logs"
        logs_dir.mkdir(exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_path = logs_dir / f"failed_parses_{timestamp}.csv"

        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["url", "reason", "source_site", "timestamp"])
            ts = datetime.now().isoformat()
            for url, reason in failed:
                writer.writerow([url, reason, source_site, ts])

        print(f"  Failed parses logged to {csv_path} ({len(failed)} URLs)")

    def _create_event(
        self, data: dict, source_site: str
    ) -> Optional[EventCreate]:
        """Create an EventCreate from extracted data."""
        title = data.get("title")
        venue_name = data.get("venue_name")
        source_url = data.get("source_url")

        if not title or not venue_name or not source_url:
            return None

        # Parse datetime
        datetime_start = None
        if data.get("datetime_start"):
            try:
                datetime_start = datetime.fromisoformat(
                    data["datetime_start"].replace("Z", "+00:00")
                )
            except ValueError:
                # Try alternative parsing
                try:
                    datetime_start = datetime.strptime(
                        data["datetime_start"], "%Y-%m-%d %H:%M"
                    )
                except ValueError:
                    datetime_start = datetime.now()

        if not datetime_start:
            datetime_start = datetime.now()

        datetime_end = None
        if data.get("datetime_end"):
            try:
                datetime_end = datetime.fromisoformat(
                    data["datetime_end"].replace("Z", "+00:00")
                )
            except ValueError:
                pass

        # Parse price
        price_amount = float(data.get("price_amount", 0) or 0)
        price = Price.from_amount(
            price_amount, data.get("price_currency", "SEK") or "SEK"
        )

        venue = Venue(
            name=venue_name,
            address=data.get("venue_address"),
        )

        return EventCreate(
            title=title,
            description=data.get("description"),
            venue=venue,
            datetime_start=datetime_start,
            datetime_end=datetime_end,
            price=price,
            source_url=source_url,
            source_site=source_site,
            categories=data.get("categories", []),
            image_url=data.get("image_url"),
            is_online=bool(data.get("is_online", False)),
            online_link=data.get("online_link"),
            raw_data=data,
        )

    def close(self):
        """Close the HTTP client."""
        self.http_client.close()
