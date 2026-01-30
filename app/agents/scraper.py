import httpx
from bs4 import BeautifulSoup
from anthropic import Anthropic
from datetime import datetime
from typing import Optional
import json
import re

from app.config import get_settings
from app.models.event import EventCreate, Venue, Price

settings = get_settings()


class EventScraper:
    """Claude-powered web scraper for event discovery."""

    def __init__(self):
        self.client = Anthropic(api_key=settings.anthropic_api_key)
        self.http_client = httpx.Client(
            timeout=30.0,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
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
                    from urllib.parse import urljoin

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

    async def scrape(
        self, seed_url: str, source_site: str, max_pages: int = 5
    ) -> list[EventCreate]:
        """
        Scrape events from a website using Claude as the agent.

        Args:
            seed_url: Starting URL to scrape
            source_site: Name of the source (e.g., "eventbrite.se")
            max_pages: Maximum number of pages to fetch

        Returns:
            List of EventCreate objects
        """
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
            raw_data=data,
        )

    def close(self):
        """Close the HTTP client."""
        self.http_client.close()
