"""
Tests for Pydantic models: Price bucketing, Venue construction, and
EventResponse.from_mongo conversion.

These are pure unit tests — no database or network access needed.
"""

from datetime import datetime
from bson import ObjectId

from app.models.event import Price, Venue, EventResponse


class TestPriceFromAmount:
    """Verify Price.from_amount assigns the correct bucket based on SEK thresholds.

    Buckets:
        free     — amount == 0
        budget   — 0 < amount < 100
        standard — 100 <= amount <= 300
        premium  — amount > 300
    """

    def test_free_bucket_for_zero(self):
        """Exactly 0 SEK should be classified as free."""
        price = Price.from_amount(0)
        assert price.bucket == "free"
        assert price.amount == 0
        assert price.currency == "SEK"

    def test_budget_bucket_below_100(self):
        """Any positive amount under 100 SEK falls into budget."""
        price = Price.from_amount(99.99)
        assert price.bucket == "budget"

    def test_standard_bucket_at_100(self):
        """100 SEK is the lower boundary of the standard bucket."""
        price = Price.from_amount(100)
        assert price.bucket == "standard"

    def test_standard_bucket_at_300(self):
        """300 SEK is the upper boundary of the standard bucket (inclusive)."""
        price = Price.from_amount(300)
        assert price.bucket == "standard"

    def test_premium_bucket_above_300(self):
        """Anything above 300 SEK is premium."""
        price = Price.from_amount(300.01)
        assert price.bucket == "premium"

    def test_custom_currency(self):
        """Currency should be passed through regardless of bucket."""
        price = Price.from_amount(50, "EUR")
        assert price.currency == "EUR"
        assert price.bucket == "budget"


class TestVenue:
    """Verify Venue model construction with required and optional fields."""

    def test_minimal_venue(self):
        """A venue only requires a name; address and coordinates are optional."""
        venue = Venue(name="Test Venue")
        assert venue.name == "Test Venue"
        assert venue.address is None
        assert venue.coordinates is None

    def test_full_venue(self):
        """All fields should be stored correctly when provided."""
        venue = Venue(
            name="Konserthuset",
            address="Hötorget 8",
            coordinates=[59.3346, 18.0632],
        )
        assert venue.name == "Konserthuset"
        assert venue.address == "Hötorget 8"
        assert venue.coordinates == [59.3346, 18.0632]


class TestEventResponseFromMongo:
    """Verify EventResponse.from_mongo correctly converts raw MongoDB documents.

    MongoDB stores _id as ObjectId and may omit optional fields. from_mongo
    must handle both cases and produce a JSON-serializable response.
    """

    def test_full_document(self):
        """All fields present — should map 1:1 with ObjectId stringified."""
        doc = {
            "_id": ObjectId(),
            "title": "Jazz Night",
            "description": "Live jazz",
            "venue": {"name": "Stampen", "address": "Stora Nygatan 5"},
            "datetime_start": datetime(2025, 6, 1, 20, 0),
            "datetime_end": datetime(2025, 6, 1, 23, 0),
            "price": {"amount": 150, "currency": "SEK", "bucket": "standard"},
            "source_url": "https://example.com/jazz",
            "source_site": "example.com",
            "categories": ["jazz", "music"],
            "scraped_at": datetime(2025, 5, 1, 12, 0),
        }
        event = EventResponse.from_mongo(doc)

        assert event.id == str(doc["_id"])
        assert event.title == "Jazz Night"
        assert event.description == "Live jazz"
        assert event.venue.name == "Stampen"
        assert event.venue.address == "Stora Nygatan 5"
        assert event.datetime_start == datetime(2025, 6, 1, 20, 0)
        assert event.datetime_end == datetime(2025, 6, 1, 23, 0)
        assert event.price.amount == 150
        assert event.price.bucket == "standard"
        assert event.source_url == "https://example.com/jazz"
        assert event.categories == ["jazz", "music"]

    def test_missing_optional_fields(self):
        """Optional fields (description, datetime_end, categories) may be absent."""
        doc = {
            "_id": ObjectId(),
            "title": "Open Mic",
            "venue": {"name": "Bar X"},
            "datetime_start": datetime(2025, 6, 2, 19, 0),
            "price": {"amount": 0, "currency": "SEK", "bucket": "free"},
            "source_url": "https://example.com/open-mic",
            "source_site": "example.com",
            "scraped_at": datetime(2025, 5, 1, 12, 0),
        }
        event = EventResponse.from_mongo(doc)

        assert event.description is None
        assert event.datetime_end is None
        assert event.categories == []

    def test_scraped_at_defaults_when_missing(self):
        """If scraped_at is absent from the document, from_mongo falls back to utcnow."""
        doc = {
            "_id": ObjectId(),
            "title": "Fallback Test",
            "venue": {"name": "Somewhere"},
            "datetime_start": datetime(2025, 6, 3, 18, 0),
            "price": {"amount": 0, "currency": "SEK", "bucket": "free"},
            "source_url": "https://example.com/fallback",
            "source_site": "example.com",
        }
        event = EventResponse.from_mongo(doc)

        # scraped_at should be a datetime, roughly "now"
        assert isinstance(event.scraped_at, datetime)
