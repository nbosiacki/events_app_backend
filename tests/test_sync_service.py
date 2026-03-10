"""
Tests for the sync service (app/services/sync.py) and URL utilities
(app/services/url_utils.py).

The sync service tests use mock scraper/local DB objects — no external MongoDB
connection needed. They verify insert/update/skip/error counts, deduplication
(URL normalization and content-hash), currency conversion, city propagation,
and like_count/attend_count preservation.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.sync import run_sync, _build_event_doc
from app.services.url_utils import normalize_url, make_content_hash
from app.models.event import Price


def make_scraper_doc(**overrides) -> dict:
    """Return a minimal scraper event document."""
    base = {
        "source_url": "https://example.com/event-1",
        "source_site": "example.com",
        "title": "Test Event",
        "description": "A test event",
        "venue": {"name": "Test Venue", "address": "123 Main St", "city": "Stockholm"},
        "datetime_start": datetime(2025, 6, 1, 18, 0, tzinfo=timezone.utc),
        "price": {"amount": 100.0, "currency": "SEK"},
        "categories": ["music"],
        "is_online": False,
        "scraped_at": datetime.now(timezone.utc),
    }
    base.update(overrides)
    return base


def make_mock_db(events: list, content_hash_dupe=None, upserted_id="new_id"):
    """Build a minimal mock async Motor database.

    Args:
        events:            Docs returned by the scraper cursor.
        content_hash_dupe: Document returned by the content_hash find_one check.
                           None means no cross-URL duplicate found.
        upserted_id:       Value of result.upserted_id from update_one (upsert).
                           Set to None to simulate an update (document already existed).
    """
    # Scraper DB mock
    scraper_db = MagicMock()
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=events)
    scraper_db.scraped_events.find = MagicMock(return_value=cursor)

    # Local DB mock
    local_db = MagicMock()
    local_db.events.find_one = AsyncMock(return_value=content_hash_dupe)

    upsert_result = MagicMock()
    upsert_result.upserted_id = upserted_id
    local_db.events.update_one = AsyncMock(return_value=upsert_result)

    return scraper_db, local_db


class TestRunSyncCounts:
    """Verify insert/update/skip/error summary counts."""

    async def test_inserts_new_event(self):
        """A doc with an unknown source_url should be inserted via upsert."""
        doc = make_scraper_doc()
        scraper_db, local_db = make_mock_db([doc], content_hash_dupe=None, upserted_id="new_id")

        with patch("app.services.sync.refresh_rates"):
            result = await run_sync(scraper_db, local_db)

        assert result["inserted"] == 1
        assert result["updated"] == 0
        assert result["skipped"] == 0
        assert result["errors"] == 0
        local_db.events.update_one.assert_called_once()

    async def test_updates_existing_event(self):
        """A doc with a known source_url should trigger an update (upserted_id=None)."""
        doc = make_scraper_doc()
        scraper_db, local_db = make_mock_db([doc], content_hash_dupe=None, upserted_id=None)

        with patch("app.services.sync.refresh_rates"):
            result = await run_sync(scraper_db, local_db)

        assert result["inserted"] == 0
        assert result["updated"] == 1
        local_db.events.update_one.assert_called_once()

    async def test_skips_doc_without_source_url(self):
        """A doc missing source_url should be skipped, not inserted or counted as error."""
        doc = make_scraper_doc()
        doc.pop("source_url")
        scraper_db, local_db = make_mock_db([doc])

        with patch("app.services.sync.refresh_rates"):
            result = await run_sync(scraper_db, local_db)

        assert result["skipped"] == 1
        assert result["inserted"] == 0
        local_db.events.update_one.assert_not_called()

    async def test_counts_errors_on_exception(self):
        """An exception during upsert should be caught and counted as an error."""
        doc = make_scraper_doc()
        scraper_db, local_db = make_mock_db([doc], content_hash_dupe=None)
        local_db.events.update_one = AsyncMock(side_effect=Exception("DB error"))

        with patch("app.services.sync.refresh_rates"):
            result = await run_sync(scraper_db, local_db)

        assert result["errors"] == 1
        assert result["inserted"] == 0

    async def test_returns_synced_at(self):
        """Result should always include a synced_at timestamp string."""
        scraper_db, local_db = make_mock_db([])
        with patch("app.services.sync.refresh_rates"):
            result = await run_sync(scraper_db, local_db)
        assert "synced_at" in result
        assert isinstance(result["synced_at"], str)

    async def test_handles_scraper_db_query_failure(self):
        """If scraper DB query fails, return error summary immediately."""
        scraper_db = MagicMock()
        cursor = MagicMock()
        cursor.to_list = AsyncMock(side_effect=Exception("Connection refused"))
        scraper_db.scraped_events.find = MagicMock(return_value=cursor)
        local_db = MagicMock()

        with patch("app.services.sync.refresh_rates"):
            result = await run_sync(scraper_db, local_db)

        assert result["errors"] == 1
        assert result["inserted"] == 0


class TestDeduplication:
    """Dedup via URL normalization and content fingerprinting."""

    async def test_skips_content_hash_duplicate(self):
        """A doc whose content_hash matches an existing event with a different source_url
        should be skipped (cross-site/cross-URL duplicate)."""
        doc = make_scraper_doc(source_url="https://othersource.com/event-1")
        existing = {"_id": "abc", "source_url": "https://example.com/event-1", "content_hash": "xyz"}
        scraper_db, local_db = make_mock_db([doc], content_hash_dupe=existing)

        with patch("app.services.sync.refresh_rates"):
            result = await run_sync(scraper_db, local_db)

        assert result["skipped"] == 1
        assert result["inserted"] == 0
        local_db.events.update_one.assert_not_called()

    async def test_content_hash_same_url_not_skipped(self):
        """A doc whose content_hash matches an event with the SAME source_url
        is a normal update — should not be skipped."""
        doc = make_scraper_doc()
        # find_one returns None (no DIFFERENT-url match found)
        scraper_db, local_db = make_mock_db([doc], content_hash_dupe=None, upserted_id=None)

        with patch("app.services.sync.refresh_rates"):
            result = await run_sync(scraper_db, local_db)

        assert result["updated"] == 1
        assert result["skipped"] == 0

    async def test_upsert_uses_normalized_url(self):
        """The upsert filter must use the normalized URL, not the raw one."""
        raw_url = "https://Example.com/Event-1/?aff=newsletter&utm_source=email"
        expected_norm = "https://example.com/Event-1"
        doc = make_scraper_doc(source_url=raw_url)
        scraper_db, local_db = make_mock_db([doc], content_hash_dupe=None, upserted_id="new_id")

        with patch("app.services.sync.refresh_rates"):
            await run_sync(scraper_db, local_db)

        call_args = local_db.events.update_one.call_args
        filter_doc = call_args[0][0]
        assert filter_doc["source_url"] == expected_norm

    async def test_stored_source_url_is_normalized(self):
        """The source_url stored in $set should be the normalized form."""
        raw_url = "https://example.com/event/?ref=homepage&utm_campaign=summer"
        doc = make_scraper_doc(source_url=raw_url)
        scraper_db, local_db = make_mock_db([doc], content_hash_dupe=None, upserted_id="new_id")

        with patch("app.services.sync.refresh_rates"):
            await run_sync(scraper_db, local_db)

        call_args = local_db.events.update_one.call_args
        set_fields = call_args[0][1]["$set"]
        assert "ref" not in set_fields["source_url"]
        assert "utm_campaign" not in set_fields["source_url"]


class TestLikeAttendCountPreservation:
    """On update, like_count and attend_count must not be overwritten."""

    async def test_preserve_counts_via_set_on_insert(self):
        """like_count and attend_count must be in $setOnInsert (not $set)."""
        doc = make_scraper_doc()
        scraper_db, local_db = make_mock_db([doc], content_hash_dupe=None, upserted_id="new_id")

        with patch("app.services.sync.refresh_rates"):
            await run_sync(scraper_db, local_db)

        call_args = local_db.events.update_one.call_args
        update_op = call_args[0][1]
        assert "like_count" not in update_op["$set"]
        assert "attend_count" not in update_op["$set"]
        assert update_op["$setOnInsert"]["like_count"] == 0
        assert update_op["$setOnInsert"]["attend_count"] == 0


class TestCurrencyConversion:
    """Prices in non-SEK currencies should be converted to SEK."""

    def test_sek_price_unchanged(self):
        """SEK prices should pass through convert_to_sek unchanged."""
        doc = make_scraper_doc(price={"amount": 200.0, "currency": "SEK"})

        with patch("app.services.sync.convert_to_sek", return_value=200.0) as mock_convert:
            _build_event_doc(doc, doc["source_url"])
            mock_convert.assert_called_once_with(200.0, "SEK")

    def test_usd_price_converted(self):
        """USD prices should be passed to convert_to_sek for conversion."""
        doc = make_scraper_doc(price={"amount": 20.0, "currency": "USD"})

        with patch("app.services.sync.convert_to_sek", return_value=220.0) as mock_convert:
            event = _build_event_doc(doc, doc["source_url"])
            mock_convert.assert_called_once_with(20.0, "USD")
            assert event["price"]["amount"] == 220.0

    def test_bucket_recomputed_after_conversion(self):
        """price.bucket must reflect the converted SEK amount."""
        doc = make_scraper_doc(price={"amount": 10.0, "currency": "USD"})

        with patch("app.services.sync.convert_to_sek", return_value=350.0):
            event = _build_event_doc(doc, doc["source_url"])
            assert event["price"]["bucket"] == "premium"


class TestNewFields:
    """venue.country, tickets_available, and content_hash should be in built doc."""

    def test_country_extracted_from_venue(self):
        """venue.country should be included in the built event doc."""
        doc = make_scraper_doc(venue={"name": "Venue", "address": "Addr", "city": "Stockholm", "country": "Sweden"})
        with patch("app.services.sync.convert_to_sek", return_value=0.0):
            event = _build_event_doc(doc, doc["source_url"])
        assert event["venue"]["country"] == "Sweden"

    def test_country_none_when_missing(self):
        """venue.country should be None when absent from scraper doc."""
        doc = make_scraper_doc(venue={"name": "Venue", "address": "Addr", "city": "Stockholm"})
        with patch("app.services.sync.convert_to_sek", return_value=0.0):
            event = _build_event_doc(doc, doc["source_url"])
        assert event["venue"]["country"] is None

    def test_tickets_available_true(self):
        """tickets_available=True should be passed through."""
        doc = make_scraper_doc(tickets_available=True)
        with patch("app.services.sync.convert_to_sek", return_value=0.0):
            event = _build_event_doc(doc, doc["source_url"])
        assert event["tickets_available"] is True

    def test_tickets_available_false(self):
        """tickets_available=False (sold out) should be passed through."""
        doc = make_scraper_doc(tickets_available=False)
        with patch("app.services.sync.convert_to_sek", return_value=0.0):
            event = _build_event_doc(doc, doc["source_url"])
        assert event["tickets_available"] is False

    def test_tickets_available_none_when_missing(self):
        """tickets_available should default to None when absent."""
        doc = make_scraper_doc()
        with patch("app.services.sync.convert_to_sek", return_value=0.0):
            event = _build_event_doc(doc, doc["source_url"])
        assert event["tickets_available"] is None

    def test_content_hash_present_in_built_doc(self):
        """_build_event_doc must include a content_hash string."""
        doc = make_scraper_doc()
        with patch("app.services.sync.convert_to_sek", return_value=0.0):
            event = _build_event_doc(doc, doc["source_url"])
        assert "content_hash" in event
        assert isinstance(event["content_hash"], str)
        assert len(event["content_hash"]) == 32  # MD5 hex digest

    def test_content_hash_stable_across_calls(self):
        """The same event doc should always produce the same content_hash."""
        doc = make_scraper_doc()
        with patch("app.services.sync.convert_to_sek", return_value=0.0):
            e1 = _build_event_doc(doc, doc["source_url"])
            e2 = _build_event_doc(doc, doc["source_url"])
        assert e1["content_hash"] == e2["content_hash"]


class TestCityPropagation:
    """city field must be extracted from venue.city in the scraper doc."""

    def test_city_extracted_from_venue(self):
        """city should come from venue.city when present."""
        doc = make_scraper_doc(venue={"name": "Venue", "address": "Addr", "city": "Gothenburg"})
        with patch("app.services.sync.convert_to_sek", return_value=0.0):
            event = _build_event_doc(doc, doc["source_url"])
        assert event["city"] == "Gothenburg"

    def test_city_none_when_missing(self):
        """city should be None when venue has no city field."""
        doc = make_scraper_doc(venue={"name": "Venue", "address": "Addr"})
        with patch("app.services.sync.convert_to_sek", return_value=0.0):
            event = _build_event_doc(doc, doc["source_url"])
        assert event["city"] is None

    def test_city_none_for_online_event(self):
        """Online events with no city in venue should have city=None."""
        doc = make_scraper_doc(
            venue={"name": "Zoom Webinar"},
            is_online=True,
        )
        with patch("app.services.sync.convert_to_sek", return_value=0.0):
            event = _build_event_doc(doc, doc["source_url"])
        assert event["city"] is None


class TestNormalizeUrl:
    """Unit tests for the normalize_url utility."""

    def test_strips_utm_params(self):
        url = "https://example.com/event?utm_source=email&utm_campaign=spring"
        assert normalize_url(url) == "https://example.com/event"

    def test_strips_aff_and_ref(self):
        url = "https://eventbrite.com/e/123?aff=newsletter&ref=homepage"
        assert normalize_url(url) == "https://eventbrite.com/e/123"

    def test_strips_fbclid(self):
        url = "https://example.com/event?fbclid=abc123"
        assert normalize_url(url) == "https://example.com/event"

    def test_preserves_non_tracking_params(self):
        url = "https://example.com/events?category=music&city=stockholm"
        result = normalize_url(url)
        assert "category=music" in result
        assert "city=stockholm" in result

    def test_strips_trailing_slash(self):
        assert normalize_url("https://example.com/event/") == "https://example.com/event"

    def test_lowercases_scheme_and_host(self):
        assert normalize_url("HTTPS://Example.COM/event") == "https://example.com/event"

    def test_invalid_url_returned_unchanged(self):
        assert normalize_url("not a url") == "not a url"


class TestMakeContentHash:
    """Unit tests for the make_content_hash utility."""

    def test_same_inputs_same_hash(self):
        dt = datetime(2025, 6, 1, 18, 0, tzinfo=timezone.utc)
        h1 = make_content_hash("Jazz Night", "Fasching", dt)
        h2 = make_content_hash("Jazz Night", "Fasching", dt)
        assert h1 == h2

    def test_case_insensitive(self):
        dt = datetime(2025, 6, 1, tzinfo=timezone.utc)
        h1 = make_content_hash("Jazz Night", "FASCHING", dt)
        h2 = make_content_hash("JAZZ NIGHT", "fasching", dt)
        assert h1 == h2

    def test_punctuation_insensitive(self):
        dt = datetime(2025, 6, 1, tzinfo=timezone.utc)
        h1 = make_content_hash("Jazz Night!", "Fasching", dt)
        h2 = make_content_hash("Jazz Night", "Fasching", dt)
        assert h1 == h2

    def test_different_title_different_hash(self):
        dt = datetime(2025, 6, 1, tzinfo=timezone.utc)
        h1 = make_content_hash("Jazz Night", "Fasching", dt)
        h2 = make_content_hash("Rock Night", "Fasching", dt)
        assert h1 != h2

    def test_different_date_different_hash(self):
        dt1 = datetime(2025, 6, 1, tzinfo=timezone.utc)
        dt2 = datetime(2025, 6, 2, tzinfo=timezone.utc)
        h1 = make_content_hash("Jazz Night", "Fasching", dt1)
        h2 = make_content_hash("Jazz Night", "Fasching", dt2)
        assert h1 != h2

    def test_time_of_day_ignored(self):
        """Two events on the same date but different times should have the same hash."""
        dt1 = datetime(2025, 6, 1, 18, 0, tzinfo=timezone.utc)
        dt2 = datetime(2025, 6, 1, 20, 0, tzinfo=timezone.utc)
        h1 = make_content_hash("Jazz Night", "Fasching", dt1)
        h2 = make_content_hash("Jazz Night", "Fasching", dt2)
        assert h1 == h2

    def test_returns_32_char_hex(self):
        dt = datetime(2025, 6, 1, tzinfo=timezone.utc)
        h = make_content_hash("Event", "Venue", dt)
        assert len(h) == 32
        assert all(c in "0123456789abcdef" for c in h)

    def test_handles_none_inputs(self):
        """None title or venue_name should not raise."""
        dt = datetime(2025, 6, 1, tzinfo=timezone.utc)
        h = make_content_hash(None, None, dt)
        assert isinstance(h, str) and len(h) == 32
