"""
Tests for the currency normalization service.

Covers:
    convert_to_sek()   - conversion math, SEK passthrough, unknown currency
    refresh_rates()     - API success (+ file persist), API failure (fallback from file)
    reset_rates()       - state cleanup
    Integration         - Price.from_amount after conversion produces correct bucket
"""

from unittest.mock import patch, MagicMock

from app.services.currency import (
    convert_to_sek,
    refresh_rates,
    reset_rates,
    get_rates_source,
    _FALLBACK_PATH,
)
from app.models.event import Price


def _mock_api_response(rates: dict):
    """Return a MagicMock that behaves like an httpx.Response with given rates."""
    resp = MagicMock()
    resp.json.return_value = {"base": "EUR", "rates": rates}
    resp.raise_for_status = MagicMock()
    return resp


class TestConvertToSek:
    """convert_to_sek() — currency conversion math."""

    def setup_method(self):
        reset_rates()

    def test_sek_passthrough(self):
        """SEK amount should be returned unchanged."""
        with patch("app.services.currency.httpx.get", return_value=_mock_api_response({"SEK": 11.0, "USD": 1.1})):
            refresh_rates()
        assert convert_to_sek(100.0, "SEK") == 100.0

    def test_zero_amount_passthrough(self):
        """Zero amount should always return zero, regardless of currency."""
        assert convert_to_sek(0.0, "USD") == 0.0

    def test_usd_to_sek(self):
        """USD should convert using rates['SEK'] / rates['USD']."""
        with patch("app.services.currency.httpx.get", return_value=_mock_api_response({"SEK": 11.0, "USD": 1.1})):
            refresh_rates()
        result = convert_to_sek(10.0, "USD")
        assert result == round(10.0 * (11.0 / 1.1), 2)  # 100.0

    def test_eur_to_sek(self):
        """EUR should convert using rates['SEK'] / 1.0 (EUR is base)."""
        with patch("app.services.currency.httpx.get", return_value=_mock_api_response({"SEK": 11.0, "USD": 1.1})):
            refresh_rates()
        result = convert_to_sek(10.0, "EUR")
        assert result == round(10.0 * (11.0 / 1.0), 2)  # 110.0

    def test_unknown_currency_returns_original(self):
        """Unknown currency should return amount unchanged."""
        with patch("app.services.currency.httpx.get", return_value=_mock_api_response({"SEK": 11.0, "USD": 1.1})):
            refresh_rates()
        assert convert_to_sek(50.0, "XYZ") == 50.0

    def test_case_insensitive(self):
        """Currency codes should be case-insensitive."""
        with patch("app.services.currency.httpx.get", return_value=_mock_api_response({"SEK": 11.0, "USD": 1.1})):
            refresh_rates()
        assert convert_to_sek(10.0, "usd") == convert_to_sek(10.0, "USD")

    def test_result_rounded_to_two_decimals(self):
        """Converted amount should be rounded to 2 decimal places."""
        with patch("app.services.currency.httpx.get", return_value=_mock_api_response({"SEK": 11.23, "GBP": 0.86})):
            refresh_rates()
        result = convert_to_sek(7.0, "GBP")
        assert result == round(7.0 * (11.23 / 0.86), 2)


class TestRefreshRates:
    """refresh_rates() — API fetch and fallback."""

    def setup_method(self):
        reset_rates()

    def test_api_success(self):
        """Should load rates from API and set source to 'api'."""
        with patch("app.services.currency.httpx.get", return_value=_mock_api_response({"SEK": 11.23, "USD": 1.08})), \
             patch("app.services.currency._save_fallback_rates") as mock_save:
            rates = refresh_rates()
        assert "SEK" in rates
        assert "USD" in rates
        assert "EUR" in rates  # base added automatically
        assert get_rates_source() == "api"
        mock_save.assert_called_once()

    def test_api_success_persists_to_file(self):
        """Successful fetch should write rates to fallback_rates.json."""
        with patch("app.services.currency.httpx.get", return_value=_mock_api_response({"SEK": 11.0, "USD": 1.1})), \
             patch("app.services.currency._save_fallback_rates") as mock_save:
            refresh_rates()
        saved = mock_save.call_args[0][0]
        assert saved["SEK"] == 11.0
        assert saved["EUR"] == 1.0

    def test_api_failure_uses_fallback_file(self):
        """Network error should load from fallback_rates.json."""
        with patch("app.services.currency.httpx.get", side_effect=Exception("timeout")), \
             patch("app.services.currency._load_fallback_rates", return_value={"EUR": 1.0, "SEK": 11.0, "USD": 1.1}) as mock_load:
            rates = refresh_rates()
        assert get_rates_source() == "fallback"
        assert rates["SEK"] == 11.0
        mock_load.assert_called_once()


class TestResetRates:
    """reset_rates() — state cleanup."""

    def test_reset_clears_source(self):
        """After reset, source should be 'none'."""
        with patch("app.services.currency.httpx.get", return_value=_mock_api_response({"SEK": 11.0})), \
             patch("app.services.currency._save_fallback_rates"):
            refresh_rates()
        assert get_rates_source() == "api"
        reset_rates()
        assert get_rates_source() == "none"


class TestCurrencyWithPriceBucket:
    """Integration: conversion + Price.from_amount produces correct bucket."""

    def setup_method(self):
        reset_rates()

    def test_usd_event_correct_bucket(self):
        """A $10 USD event should be bucketed based on its SEK equivalent."""
        with patch("app.services.currency.httpx.get", return_value=_mock_api_response({"SEK": 11.0, "USD": 1.1})), \
             patch("app.services.currency._save_fallback_rates"):
            refresh_rates()
        sek_amount = convert_to_sek(10.0, "USD")  # 100.0 SEK
        price = Price.from_amount(sek_amount, "SEK")
        assert price.amount == 100.0
        assert price.currency == "SEK"
        assert price.bucket == "standard"

    def test_eur_premium_event(self):
        """A 50 EUR event should land in premium bucket after conversion."""
        with patch("app.services.currency.httpx.get", return_value=_mock_api_response({"SEK": 11.0, "USD": 1.1})), \
             patch("app.services.currency._save_fallback_rates"):
            refresh_rates()
        sek_amount = convert_to_sek(50.0, "EUR")  # 550.0 SEK
        price = Price.from_amount(sek_amount, "SEK")
        assert price.bucket == "premium"

    def test_free_event_stays_free(self):
        """A 0 EUR event should remain free."""
        sek_amount = convert_to_sek(0.0, "EUR")
        price = Price.from_amount(sek_amount, "SEK")
        assert price.bucket == "free"
