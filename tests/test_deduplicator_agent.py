"""
Tests for the EventDeduplicator agent.

The Anthropic API is mocked so these tests run without network access
or an API key.

Covers:
    find_duplicate()  – short-circuit on empty list, date matching, no match,
                        JSON parse error handling
    merge_events()    – description length preference, category union,
                        address/coordinates backfill
    _event_summary()  – string formatting for new events
    _event_dict_summary() – string formatting for existing event dicts
"""

from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
from types import SimpleNamespace

import pytest
from bson import ObjectId

from app.models.event import EventCreate, Venue, Price


def _make_event(**overrides) -> EventCreate:
    """Build an EventCreate with sensible defaults.

    Accepts keyword overrides so each test only specifies the fields
    it cares about.
    """
    defaults = {
        "title": "Test Event",
        "venue": Venue(name="Test Venue", address="Test Address"),
        "datetime_start": datetime(2025, 6, 1, 20, 0),
        "price": Price.from_amount(100),
        "source_url": "https://example.com/test",
        "source_site": "example.com",
        "categories": ["music"],
    }
    defaults.update(overrides)
    return EventCreate(**defaults)


def _make_existing_event(**overrides) -> dict:
    """Build an existing event dict (as returned from MongoDB).

    Includes an _id field like a real MongoDB document.
    """
    defaults = {
        "_id": ObjectId(),
        "title": "Existing Event",
        "venue": {"name": "Existing Venue"},
        "datetime_start": datetime(2025, 6, 1, 20, 30),
        "price": {"amount": 100, "currency": "SEK", "bucket": "standard"},
        "source_url": "https://example.com/existing",
        "source_site": "example.com",
        "categories": ["art"],
        "description": "Short desc",
    }
    defaults.update(overrides)
    return defaults


def _mock_anthropic_response(text):
    """Build a mock Anthropic response with a single text content block."""
    block = SimpleNamespace(text=text)
    response = SimpleNamespace(content=[block])
    return response


class TestFindDuplicate:
    """EventDeduplicator.find_duplicate — semantic duplicate detection."""

    @pytest.mark.asyncio
    async def test_empty_existing_events_short_circuits(self):
        """When there are no existing events, return None immediately without calling Claude."""
        with patch("app.agents.deduplicator.Anthropic") as MockAnthropic:
            mock_client = MagicMock()
            MockAnthropic.return_value = mock_client

            from app.agents.deduplicator import EventDeduplicator
            dedup = EventDeduplicator()

            result = await dedup.find_duplicate(_make_event(), [])

            assert result is None
            mock_client.messages.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_date_match_short_circuits(self):
        """If no existing events are within 24 hours, skip the Claude call."""
        with patch("app.agents.deduplicator.Anthropic") as MockAnthropic:
            mock_client = MagicMock()
            MockAnthropic.return_value = mock_client

            from app.agents.deduplicator import EventDeduplicator
            dedup = EventDeduplicator()

            far_away = _make_existing_event(
                datetime_start=datetime(2025, 8, 1, 20, 0),
            )
            result = await dedup.find_duplicate(_make_event(), [far_away])

            assert result is None
            mock_client.messages.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_duplicate_found(self):
        """When Claude identifies a duplicate, return the matching event's _id."""
        with patch("app.agents.deduplicator.Anthropic") as MockAnthropic:
            mock_client = MagicMock()
            MockAnthropic.return_value = mock_client

            dup_id = str(ObjectId())
            mock_client.messages.create.return_value = _mock_anthropic_response(
                f'{{"is_duplicate": true, "duplicate_id": "{dup_id}", "reason": "Same event"}}'
            )

            from app.agents.deduplicator import EventDeduplicator
            dedup = EventDeduplicator()

            existing = _make_existing_event(_id=ObjectId(dup_id))
            result = await dedup.find_duplicate(_make_event(), [existing])

            assert result == dup_id

    @pytest.mark.asyncio
    async def test_no_duplicate_found(self):
        """When Claude says it's not a duplicate, return None."""
        with patch("app.agents.deduplicator.Anthropic") as MockAnthropic:
            mock_client = MagicMock()
            MockAnthropic.return_value = mock_client

            mock_client.messages.create.return_value = _mock_anthropic_response(
                '{"is_duplicate": false, "duplicate_id": null, "reason": "Different events"}'
            )

            from app.agents.deduplicator import EventDeduplicator
            dedup = EventDeduplicator()

            existing = _make_existing_event()
            result = await dedup.find_duplicate(_make_event(), [existing])

            assert result is None

    @pytest.mark.asyncio
    async def test_json_parse_error_returns_none(self):
        """If Claude returns unparseable text, gracefully return None."""
        with patch("app.agents.deduplicator.Anthropic") as MockAnthropic:
            mock_client = MagicMock()
            MockAnthropic.return_value = mock_client

            mock_client.messages.create.return_value = _mock_anthropic_response(
                "This is not valid JSON at all"
            )

            from app.agents.deduplicator import EventDeduplicator
            dedup = EventDeduplicator()

            existing = _make_existing_event()
            result = await dedup.find_duplicate(_make_event(), [existing])

            assert result is None

    @pytest.mark.asyncio
    async def test_markdown_wrapped_json(self):
        """Claude sometimes wraps JSON in markdown code blocks — should still parse."""
        with patch("app.agents.deduplicator.Anthropic") as MockAnthropic:
            mock_client = MagicMock()
            MockAnthropic.return_value = mock_client

            dup_id = str(ObjectId())
            mock_client.messages.create.return_value = _mock_anthropic_response(
                f'```json\n{{"is_duplicate": true, "duplicate_id": "{dup_id}", "reason": "Same"}}\n```'
            )

            from app.agents.deduplicator import EventDeduplicator
            dedup = EventDeduplicator()

            existing = _make_existing_event(_id=ObjectId(dup_id))
            result = await dedup.find_duplicate(_make_event(), [existing])

            assert result == dup_id


class TestMergeEvents:
    """EventDeduplicator.merge_events — merging new data into existing events."""

    @pytest.mark.asyncio
    async def test_keeps_longer_description(self):
        """The merged event should keep whichever description is longer."""
        with patch("app.agents.deduplicator.Anthropic"):
            from app.agents.deduplicator import EventDeduplicator
            dedup = EventDeduplicator()

            new = _make_event(description="This is a much longer and more detailed description of the event")
            existing = _make_existing_event(description="Short")
            merged = await dedup.merge_events(new, existing)

            assert merged["description"] == new.description

    @pytest.mark.asyncio
    async def test_keeps_existing_longer_description(self):
        """If the existing description is longer, keep it."""
        with patch("app.agents.deduplicator.Anthropic"):
            from app.agents.deduplicator import EventDeduplicator
            dedup = EventDeduplicator()

            new = _make_event(description="Short")
            existing = _make_existing_event(description="This is the longer existing description that should be kept")
            merged = await dedup.merge_events(new, existing)

            assert merged["description"] == existing["description"]

    @pytest.mark.asyncio
    async def test_unions_categories(self):
        """Categories from both events should be merged (set union)."""
        with patch("app.agents.deduplicator.Anthropic"):
            from app.agents.deduplicator import EventDeduplicator
            dedup = EventDeduplicator()

            new = _make_event(categories=["music", "jazz"])
            existing = _make_existing_event(categories=["music", "live"])
            merged = await dedup.merge_events(new, existing)

            assert set(merged["categories"]) == {"music", "jazz", "live"}

    @pytest.mark.asyncio
    async def test_fills_missing_address(self):
        """If the existing event has no address but the new one does, backfill it."""
        with patch("app.agents.deduplicator.Anthropic"):
            from app.agents.deduplicator import EventDeduplicator
            dedup = EventDeduplicator()

            new = _make_event(venue=Venue(name="Venue", address="123 Main St"))
            existing = _make_existing_event(venue={"name": "Venue"})
            merged = await dedup.merge_events(new, existing)

            assert merged["venue"]["address"] == "123 Main St"

    @pytest.mark.asyncio
    async def test_does_not_overwrite_existing_address(self):
        """If the existing event already has an address, don't replace it."""
        with patch("app.agents.deduplicator.Anthropic"):
            from app.agents.deduplicator import EventDeduplicator
            dedup = EventDeduplicator()

            new = _make_event(venue=Venue(name="Venue", address="New Address"))
            existing = _make_existing_event(venue={"name": "Venue", "address": "Original Address"})
            merged = await dedup.merge_events(new, existing)

            assert merged["venue"]["address"] == "Original Address"


class TestEventSummary:
    """EventDeduplicator._event_summary and _event_dict_summary — formatting."""

    def _make_dedup(self):
        """Instantiate a deduplicator with mocked Anthropic client."""
        with patch("app.agents.deduplicator.Anthropic"):
            from app.agents.deduplicator import EventDeduplicator
            return EventDeduplicator()

    def test_event_summary_format(self):
        """_event_summary should produce a readable multi-line string."""
        dedup = self._make_dedup()
        event = _make_event(
            title="Jazz Night",
            venue=Venue(name="Stampen", address="Stora Nygatan 5"),
        )
        summary = dedup._event_summary(event)

        assert "Jazz Night" in summary
        assert "Stampen" in summary
        assert "Stora Nygatan 5" in summary

    def test_event_dict_summary_format(self):
        """_event_dict_summary should produce a readable string from a raw dict."""
        dedup = self._make_dedup()
        existing = _make_existing_event(title="Rock Concert")
        summary = dedup._event_dict_summary(existing)

        assert "Rock Concert" in summary
