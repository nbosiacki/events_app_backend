"""Tests for the event recommendation scoring service."""

import pytest
from datetime import datetime, timezone, timedelta

from app.services.recommendations import score_events, _score_category, _score_price, _score_freshness


class TestScoreCategory:
    def test_explicit_match(self):
        """Explicit preferred category scores 50."""
        score = _score_category(["music"], ["music", "art"], {})
        assert score == 50.0

    def test_explicit_no_match(self):
        """No match with explicit categories scores 0."""
        score = _score_category(["food"], ["music", "art"], {})
        assert score == 0.0

    def test_implicit_full_weight(self):
        """Highest-weighted implicit category scores 50."""
        score = _score_category(["music"], [], {"music": 3.0, "art": 1.0})
        assert score == 50.0

    def test_implicit_partial_weight(self):
        """Lower-weighted implicit category scores proportionally."""
        score = _score_category(["art"], [], {"music": 4.0, "art": 2.0})
        assert score == 25.0  # 50 * (2/4)

    def test_explicit_beats_lower_implicit(self):
        """Explicit match (50) beats lower implicit score."""
        score = _score_category(["art"], ["art"], {"music": 4.0, "art": 1.0})
        assert score == 50.0

    def test_no_preferences(self):
        """No preferences at all scores 0."""
        score = _score_category(["music"], [], {})
        assert score == 0.0

    def test_empty_event_categories(self):
        """Event with no categories scores 0."""
        score = _score_category([], ["music"], {"music": 3.0})
        assert score == 0.0


class TestScorePrice:
    def test_within_max_bucket(self):
        """Event at or below max_price_bucket scores 30."""
        assert _score_price("free", "standard", None) == 30.0
        assert _score_price("budget", "standard", None) == 30.0
        assert _score_price("standard", "standard", None) == 30.0

    def test_one_above_max_bucket(self):
        """Event one bucket above max scores 15."""
        assert _score_price("premium", "standard", None) == 15.0
        assert _score_price("standard", "budget", None) == 15.0

    def test_two_above_max_bucket(self):
        """Event two+ buckets above max scores 0."""
        assert _score_price("premium", "budget", None) == 0.0
        assert _score_price("premium", "free", None) == 0.0

    def test_premium_max_ignored(self):
        """max_price_bucket='premium' means 'any' — uses implicit instead."""
        # With implicit avg bucket of "budget", "budget" event scores 30
        assert _score_price("budget", "premium", "budget") == 30.0

    def test_implicit_exact_match(self):
        """Implicit avg bucket exact match scores 30."""
        assert _score_price("standard", None, "standard") == 30.0

    def test_implicit_one_away(self):
        """Implicit avg bucket one step away scores 20."""
        assert _score_price("budget", None, "standard") == 20.0

    def test_implicit_two_away(self):
        """Implicit avg bucket two steps away scores 10."""
        assert _score_price("free", None, "standard") == 10.0

    def test_no_price_preferences(self):
        """No price preferences gives neutral 15."""
        assert _score_price("standard", None, None) == 15.0


class TestScoreFreshness:
    def test_today(self):
        """Event happening now scores 20."""
        now = datetime(2025, 6, 15, 12, 0, tzinfo=timezone.utc)
        score = _score_freshness(now, now)
        assert score == 20.0

    def test_seven_days(self):
        """Event in 7 days scores ~10."""
        now = datetime(2025, 6, 15, 12, 0, tzinfo=timezone.utc)
        event_time = now + timedelta(days=7)
        score = _score_freshness(event_time, now)
        assert score == 10.0

    def test_fourteen_days(self):
        """Event in 14 days scores 0."""
        now = datetime(2025, 6, 15, 12, 0, tzinfo=timezone.utc)
        event_time = now + timedelta(days=14)
        score = _score_freshness(event_time, now)
        assert score == 0.0

    def test_past_event(self):
        """Past event scores 0."""
        now = datetime(2025, 6, 15, 12, 0, tzinfo=timezone.utc)
        event_time = now - timedelta(days=1)
        score = _score_freshness(event_time, now)
        assert score == 0.0


class TestScoreEvents:
    def _make_event(self, title, categories, bucket="free", amount=0, days_from_now=0, now=None):
        if now is None:
            now = datetime(2025, 6, 15, 12, 0, tzinfo=timezone.utc)
        return {
            "title": title,
            "categories": categories,
            "price": {"amount": amount, "bucket": bucket},
            "datetime_start": now + timedelta(days=days_from_now),
        }

    def test_no_preferences_passthrough(self):
        """With no preferences, events returned in original order."""
        now = datetime(2025, 6, 15, 12, 0, tzinfo=timezone.utc)
        events = [
            self._make_event("A", ["music"], now=now),
            self._make_event("B", ["art"], now=now),
        ]

        result = score_events(events, {}, {}, now=now)

        assert [e["title"] for e in result] == ["A", "B"]

    def test_explicit_category_ranks_higher(self):
        """Events matching explicit preferred category rank higher."""
        now = datetime(2025, 6, 15, 12, 0, tzinfo=timezone.utc)
        events = [
            self._make_event("Art Show", ["art"], now=now),
            self._make_event("Jazz Night", ["music"], now=now),
        ]
        explicit = {"preferred_categories": ["music"]}

        result = score_events(events, explicit, {}, now=now)

        assert result[0]["title"] == "Jazz Night"

    def test_implicit_category_ranks_higher(self):
        """Events matching implicit category weights rank higher."""
        now = datetime(2025, 6, 15, 12, 0, tzinfo=timezone.utc)
        events = [
            self._make_event("Art Show", ["art"], now=now),
            self._make_event("Jazz Night", ["music"], now=now),
        ]
        implicit = {"category_weights": {"music": 5.0, "art": 1.0}}

        result = score_events(events, {}, implicit, now=now)

        assert result[0]["title"] == "Jazz Night"

    def test_price_affects_ranking(self):
        """Events within price budget rank higher."""
        now = datetime(2025, 6, 15, 12, 0, tzinfo=timezone.utc)
        events = [
            self._make_event("Expensive", ["music"], bucket="premium", amount=500, now=now),
            self._make_event("Cheap", ["music"], bucket="budget", amount=50, now=now),
        ]
        explicit = {"preferred_categories": ["music"], "max_price_bucket": "budget"}

        result = score_events(events, explicit, {}, now=now)

        assert result[0]["title"] == "Cheap"

    def test_freshness_breaks_ties(self):
        """Among equally-scored events, sooner events rank higher."""
        now = datetime(2025, 6, 15, 12, 0, tzinfo=timezone.utc)
        events = [
            self._make_event("Later", ["music"], days_from_now=7, now=now),
            self._make_event("Sooner", ["music"], days_from_now=1, now=now),
        ]
        explicit = {"preferred_categories": ["music"]}

        result = score_events(events, explicit, {}, now=now)

        assert result[0]["title"] == "Sooner"

    def test_combined_scoring(self):
        """Full scoring with category + price + freshness."""
        now = datetime(2025, 6, 15, 12, 0, tzinfo=timezone.utc)
        events = [
            self._make_event("Perfect Match", ["music"], bucket="budget", amount=50, days_from_now=1, now=now),
            self._make_event("Wrong Category", ["food"], bucket="budget", amount=50, days_from_now=1, now=now),
            self._make_event("Too Expensive", ["music"], bucket="premium", amount=500, days_from_now=1, now=now),
        ]
        explicit = {"preferred_categories": ["music"], "max_price_bucket": "standard"}
        implicit = {"category_weights": {"music": 3.0}}

        result = score_events(events, explicit, implicit, now=now)

        assert result[0]["title"] == "Perfect Match"
