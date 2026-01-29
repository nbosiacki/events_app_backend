from anthropic import Anthropic
from datetime import datetime, timedelta
from typing import Optional
import json

from app.config import get_settings
from app.models.event import EventCreate

settings = get_settings()


class EventDeduplicator:
    """Claude-powered duplicate detection for events."""

    def __init__(self):
        self.client = Anthropic(api_key=settings.anthropic_api_key)

    async def find_duplicate(
        self, new_event: EventCreate, existing_events: list[dict]
    ) -> Optional[str]:
        """
        Check if a new event is a duplicate of an existing event.

        Args:
            new_event: The new event to check
            existing_events: List of existing events from DB (as dicts)

        Returns:
            The _id of the duplicate event if found, None otherwise
        """
        if not existing_events:
            return None

        # Filter to events with similar dates (within 24 hours)
        candidates = []
        new_dt = new_event.datetime_start
        for event in existing_events:
            existing_dt = event.get("datetime_start")
            if existing_dt:
                if isinstance(existing_dt, str):
                    existing_dt = datetime.fromisoformat(existing_dt)
                time_diff = abs((new_dt - existing_dt).total_seconds())
                if time_diff < 86400:  # Within 24 hours
                    candidates.append(event)

        if not candidates:
            return None

        # Prepare event summaries for Claude
        new_summary = self._event_summary(new_event)
        candidate_summaries = [
            {"id": str(e["_id"]), "summary": self._event_dict_summary(e)}
            for e in candidates[:10]  # Limit to 10 candidates
        ]

        prompt = f"""Determine if this new event is a duplicate of any existing events.

NEW EVENT:
{new_summary}

EXISTING EVENTS:
{json.dumps(candidate_summaries, indent=2)}

Consider an event a duplicate if:
- Same or very similar title (accounting for slight variations)
- Same venue (accounting for different spellings or abbreviations)
- Same date and approximately same time (within 1 hour)

Respond with ONLY a JSON object:
{{"is_duplicate": true/false, "duplicate_id": "id if duplicate, null otherwise", "reason": "brief explanation"}}"""

        response = self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )

        try:
            # Parse the response
            text = response.content[0].text.strip()
            # Handle markdown code blocks
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            result = json.loads(text)
            if result.get("is_duplicate") and result.get("duplicate_id"):
                return result["duplicate_id"]
        except (json.JSONDecodeError, IndexError, KeyError) as e:
            print(f"Error parsing deduplication response: {e}")

        return None

    def _event_summary(self, event: EventCreate) -> str:
        """Create a summary string for a new event."""
        return f"""Title: {event.title}
Venue: {event.venue.name}
Address: {event.venue.address or 'N/A'}
Date/Time: {event.datetime_start.isoformat()}
Price: {event.price.amount} {event.price.currency}
Source: {event.source_site}"""

    def _event_dict_summary(self, event: dict) -> str:
        """Create a summary string for an existing event dict."""
        dt = event.get("datetime_start", "")
        if hasattr(dt, "isoformat"):
            dt = dt.isoformat()
        return f"""Title: {event.get('title', '')}
Venue: {event.get('venue', {}).get('name', '')}
Date/Time: {dt}
Price: {event.get('price', {}).get('amount', 0)}"""

    async def merge_events(
        self, new_event: EventCreate, existing_event: dict
    ) -> dict:
        """
        Merge a new event with an existing one, keeping richer data.

        Returns the merged event data as a dict (for MongoDB update).
        """
        merged = dict(existing_event)

        # Keep longer description
        new_desc = new_event.description or ""
        existing_desc = existing_event.get("description") or ""
        if len(new_desc) > len(existing_desc):
            merged["description"] = new_desc

        # Merge categories
        existing_cats = set(existing_event.get("categories", []))
        new_cats = set(new_event.categories)
        merged["categories"] = list(existing_cats | new_cats)

        # Keep address if new one has it
        if new_event.venue.address and not existing_event.get("venue", {}).get(
            "address"
        ):
            merged["venue"]["address"] = new_event.venue.address

        # Keep coordinates if new one has them
        if new_event.venue.coordinates and not existing_event.get("venue", {}).get(
            "coordinates"
        ):
            merged["venue"]["coordinates"] = new_event.venue.coordinates

        return merged
