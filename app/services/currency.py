"""Currency normalization for scraped event prices.

Exchange rates are fetched once per scraper session from frankfurter.app,
then reused as static rates for all conversions in that session.
On success the rates are persisted to fallback_rates.json so the
fallback is always from the most recent successful fetch.
"""

import json
import logging
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

FRANKFURTER_URL = "https://api.frankfurter.app/latest"
_FALLBACK_PATH = Path(__file__).parent / "fallback_rates.json"

# Module-level state, populated once per scraper session.
_rates: Optional[dict[str, float]] = None
_rates_source: str = "none"


def _load_fallback_rates() -> dict[str, float]:
    """Read the persisted fallback rates from disk."""
    try:
        return json.loads(_FALLBACK_PATH.read_text())
    except Exception as e:
        logger.warning("Could not read fallback_rates.json: %s", e)
        return {"EUR": 1.0, "SEK": 11.23, "USD": 1.08}


def _save_fallback_rates(rates: dict[str, float]) -> None:
    """Persist rates to disk so the fallback stays fresh."""
    try:
        _FALLBACK_PATH.write_text(json.dumps(rates, indent=2) + "\n")
    except Exception as e:
        logger.warning("Could not write fallback_rates.json: %s", e)


def refresh_rates() -> dict[str, float]:
    """Fetch current EUR-based exchange rates from frankfurter.app.

    Called once at the start of each scraper session.  On success the
    rates are also written to fallback_rates.json.  On failure the
    most recently persisted rates are loaded instead.
    """
    global _rates, _rates_source
    try:
        response = httpx.get(FRANKFURTER_URL, timeout=10.0)
        response.raise_for_status()
        data = response.json()
        _rates = data.get("rates", {})
        _rates["EUR"] = 1.0  # base currency not included in response
        _rates_source = "api"
        _save_fallback_rates(_rates)
        logger.info("Exchange rates loaded from API: %d currencies", len(_rates))
    except Exception as e:
        logger.warning("Failed to fetch exchange rates: %s. Using fallback rates.", e)
        _rates = _load_fallback_rates()
        _rates_source = "fallback"
    return _rates


def get_rates() -> dict[str, float]:
    """Return the current rate table, refreshing if not yet loaded."""
    if _rates is None:
        return refresh_rates()
    return _rates


def get_rates_source() -> str:
    """Return how rates were obtained: 'api', 'fallback', or 'none'."""
    return _rates_source


def convert_to_sek(amount: float, currency: str) -> float:
    """Convert an amount from *currency* to SEK.

    Returns the amount unchanged if it is already SEK, zero, or the
    currency is unrecognised (with a warning log).

    Conversion: ``amount * (rates["SEK"] / rates[currency])``
    """
    currency = currency.upper().strip()
    if currency == "SEK" or amount == 0:
        return amount

    rates = get_rates()
    sek_rate = rates.get("SEK")
    source_rate = rates.get(currency)

    if sek_rate is None or source_rate is None:
        logger.warning(
            "Unknown currency '%s', cannot convert. Returning original amount %s.",
            currency,
            amount,
        )
        return amount

    return round(amount * (sek_rate / source_rate), 2)


def reset_rates() -> None:
    """Clear cached rates.  Used in tests and between scraper sessions."""
    global _rates, _rates_source
    _rates = None
    _rates_source = "none"
