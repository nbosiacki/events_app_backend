"""
Tests for the seed data generation logic.

Covers:
    TestGenerateEventDict  – single event generation: required fields, venue
                             sourcing, price bucket correctness, source_url
                             uniqueness, datetime bounds and rounding
    TestGenerateEvents     – batch generation: count, sort order, past/future
                             coverage, price bucket distribution
    TestTemplateData       – template array completeness and well-formedness

These are pure unit tests — no database or async fixtures needed.  The
generation functions are fully deterministic given a fixed random seed.
"""

from datetime import datetime, timedelta

from scripts.seed_data import (
    CATEGORIES,
    CATEGORY_IMAGE_URLS,
    EVENT_TEMPLATES,
    SOURCE_SITES,
    VENUES,
    generate_event_dict,
    generate_events,
)
from app.models.event import Price


class TestGenerateEventDict:
    """Verify single event dict generation produces valid, schema-compliant data."""

    def test_returns_all_required_fields(self):
        """Generated dict must have every field the MongoDB event schema requires."""
        now = datetime.utcnow()
        event = generate_event_dict(0, now, now + timedelta(days=7))
        required_keys = {
            "title", "description", "venue", "datetime_start",
            "datetime_end", "price", "source_url", "source_site",
            "categories", "scraped_at",
        }
        assert required_keys.issubset(event.keys())

    def test_venue_has_name_and_address(self):
        """Venue sub-document must contain a name from the template array."""
        now = datetime.utcnow()
        event = generate_event_dict(0, now, now + timedelta(days=7))
        assert "name" in event["venue"]
        assert event["venue"]["name"] in [v["name"] for v in VENUES]
        assert "address" in event["venue"]

    def test_price_bucket_matches_amount(self):
        """Price bucket must agree with Price.from_amount for any generated amount."""
        now = datetime.utcnow()
        for i in range(20):
            event = generate_event_dict(i, now, now + timedelta(days=7))
            amount = event["price"]["amount"]
            expected = Price.from_amount(amount)
            assert event["price"]["bucket"] == expected.bucket

    def test_source_url_contains_index(self):
        """source_url must embed the index to guarantee uniqueness across a batch."""
        now = datetime.utcnow()
        event = generate_event_dict(42, now, now + timedelta(days=7))
        assert "-42" in event["source_url"]

    def test_unique_source_urls_across_batch(self):
        """Every event in a batch must have a distinct source_url."""
        now = datetime.utcnow()
        events = [generate_event_dict(i, now, now + timedelta(days=7)) for i in range(50)]
        urls = [e["source_url"] for e in events]
        assert len(urls) == len(set(urls))

    def test_datetime_within_range(self):
        """datetime_start must fall within the provided date range."""
        start = datetime(2025, 6, 1)
        end = datetime(2025, 6, 14)
        for i in range(20):
            event = generate_event_dict(i, start, end)
            assert start <= event["datetime_start"] <= end

    def test_datetime_end_after_start(self):
        """datetime_end must always be strictly after datetime_start."""
        now = datetime.utcnow()
        for i in range(20):
            event = generate_event_dict(i, now, now + timedelta(days=7))
            assert event["datetime_end"] > event["datetime_start"]

    def test_categories_not_empty(self):
        """Every generated event must have at least one category."""
        now = datetime.utcnow()
        for i in range(20):
            event = generate_event_dict(i, now, now + timedelta(days=7))
            assert len(event["categories"]) >= 1

    def test_price_amount_rounded_to_ten(self):
        """Prices should be rounded to the nearest 10 SEK for realism."""
        now = datetime.utcnow()
        for i in range(30):
            event = generate_event_dict(i, now, now + timedelta(days=7))
            assert event["price"]["amount"] % 10 == 0

    def test_datetime_snapped_to_half_hour(self):
        """datetime_start minutes should be either 0 or 30 (no odd times)."""
        now = datetime.utcnow()
        for i in range(20):
            event = generate_event_dict(i, now, now + timedelta(days=7))
            assert event["datetime_start"].minute in (0, 30)
            assert event["datetime_start"].second == 0

    def test_image_url_present(self):
        """Every generated event should have an image_url field."""
        now = datetime.utcnow()
        for i in range(20):
            event = generate_event_dict(i, now, now + timedelta(days=7))
            assert "image_url" in event
            assert event["image_url"] is not None

    def test_image_url_matches_category(self):
        """image_url should correspond to the event's primary category."""
        now = datetime.utcnow()
        for i in range(30):
            event = generate_event_dict(i, now, now + timedelta(days=7))
            category = event["categories"][0]
            expected_url = CATEGORY_IMAGE_URLS[category]
            assert event["image_url"] == expected_url


class TestGenerateEvents:
    """Verify batch event generation."""

    def test_default_count(self):
        """Default generation produces 50 events."""
        events = generate_events()
        assert len(events) == 50

    def test_custom_count(self):
        """Count is a minimum — output may exceed it to guarantee daily online events."""
        events = generate_events(count=10)
        # 22 days in range (-7 to +14) means at least 22 online events
        assert len(events) >= 10

    def test_sorted_by_datetime_start(self):
        """Events must be sorted chronologically by datetime_start."""
        events = generate_events(count=25)
        for i in range(len(events) - 1):
            assert events[i]["datetime_start"] <= events[i + 1]["datetime_start"]

    def test_includes_past_and_future_events(self):
        """Date range must span both past and future from now."""
        events = generate_events(count=50)
        now = datetime.utcnow()
        has_past = any(e["datetime_start"] < now for e in events)
        has_future = any(e["datetime_start"] > now for e in events)
        assert has_past, "Expected at least one past event"
        assert has_future, "Expected at least one future event"

    def test_price_bucket_distribution(self):
        """Multiple price buckets should be represented across generated events."""
        events = generate_events(count=80)
        buckets = {e["price"]["bucket"] for e in events}
        # With varied price ranges across templates, at least 3 of 4 buckets
        assert len(buckets) >= 3


class TestTemplateData:
    """Verify the template arrays are well-formed and have enough variety."""

    def test_minimum_venue_count(self):
        """Must have at least 10 venues for variety."""
        assert len(VENUES) >= 10

    def test_all_venues_have_name(self):
        """Every venue template must include a non-empty name."""
        for v in VENUES:
            assert "name" in v and v["name"]

    def test_minimum_category_count(self):
        """Must have at least 8 categories."""
        assert len(CATEGORIES) >= 8

    def test_minimum_template_count(self):
        """Must have at least 8 event template groups (one per category minimum)."""
        assert len(EVENT_TEMPLATES) >= 8

    def test_all_templates_have_required_keys(self):
        """Every template must define titles, descriptions, price_range, duration_hours, categories."""
        for t in EVENT_TEMPLATES:
            assert "titles" in t and len(t["titles"]) >= 2
            assert "descriptions" in t and len(t["descriptions"]) >= 2
            assert "price_range" in t
            assert "duration_hours" in t
            assert "categories" in t and len(t["categories"]) >= 1

    def test_source_sites_not_empty(self):
        """At least one source site must be defined."""
        assert len(SOURCE_SITES) >= 1

    def test_includes_like_and_attend_counts(self):
        """Every generated event must include like_count and attend_count as non-negative ints."""
        now = datetime.utcnow()
        for i in range(30):
            event = generate_event_dict(i, now, now + timedelta(days=7))
            assert "like_count" in event
            assert "attend_count" in event
            assert isinstance(event["like_count"], int)
            assert isinstance(event["attend_count"], int)
            assert event["like_count"] >= 0
            assert event["attend_count"] >= 0

    def test_has_is_online_field(self):
        """Every generated event must include the is_online field."""
        now = datetime.utcnow()
        for i in range(30):
            event = generate_event_dict(i, now, now + timedelta(days=7))
            assert "is_online" in event
            assert isinstance(event["is_online"], bool)

    def test_online_events_have_no_address(self):
        """Online events should have a venue with no address."""
        now = datetime.utcnow()
        for i in range(100):
            event = generate_event_dict(i, now, now + timedelta(days=7))
            if event["is_online"]:
                assert event["venue"]["address"] is None
                assert "online_link" in event
                assert event["online_link"] is not None

    def test_includes_online_events(self):
        """Every calendar day in the range should have at least one online event."""
        events = generate_events(count=50)
        online = [e for e in events if e["is_online"]]
        # 22 days in the range (-7 to +14 inclusive), one online event per day
        assert len(online) >= 22, f"Expected at least 22 online events (one per day), got {len(online)}"
        # Verify each day is covered
        online_days = {e["datetime_start"].date() for e in online}
        assert len(online_days) >= 22

    def test_events_have_city_field(self):
        """Every generated event must include a city field."""
        now = datetime.utcnow()
        for i in range(30):
            event = generate_event_dict(i, now, now + timedelta(days=7))
            assert "city" in event

    def test_multiple_cities_represented(self):
        """Generated events should span multiple Swedish cities."""
        events = generate_events(count=80)
        cities = {e["city"] for e in events if e["city"] is not None}
        assert len(cities) >= 3, f"Expected events from at least 3 cities, got: {cities}"

    def test_venues_have_city_field(self):
        """All venue templates must include a city key."""
        for v in VENUES:
            assert "city" in v

    def test_venues_have_country_field(self):
        """All venue templates must include a country key."""
        for v in VENUES:
            assert "country" in v

    def test_physical_venues_have_country_sweden(self):
        """All physical venue templates should have country='Sweden'."""
        for v in VENUES:
            if v.get("address") is not None:
                assert v["country"] == "Sweden"

    def test_generated_event_has_tickets_available(self):
        """Every generated event must include a tickets_available field."""
        now = datetime.utcnow()
        for i in range(30):
            event = generate_event_dict(i, now, now + timedelta(days=7))
            assert "tickets_available" in event

    def test_tickets_available_is_bool_or_none(self):
        """tickets_available must be True, False, or None."""
        now = datetime.utcnow()
        for i in range(50):
            event = generate_event_dict(i, now, now + timedelta(days=7))
            assert event["tickets_available"] in (True, False, None)

    def test_generated_event_venue_has_country(self):
        """Venue sub-document in generated events must include a country key."""
        now = datetime.utcnow()
        for i in range(30):
            event = generate_event_dict(i, now, now + timedelta(days=7))
            assert "country" in event["venue"]
