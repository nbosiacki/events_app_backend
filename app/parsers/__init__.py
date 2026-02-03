"""
Site-specific event parsers with health checks and automatic Claude fallback.

Usage:
    from app.parsers import get_parser_for_url

    parser = get_parser_for_url(url)
    if parser:
        health = parser.health_check(html, url)
        if health.is_healthy:
            event = parser.parse_event(html, url)
"""

from app.parsers.base import BaseEventParser, ParserHealthCheck, ParserResult
from app.parsers.registry import get_parser_for_url, register_parser, get_all_parsers

# Auto-register known parsers
from app.parsers.eventbrite import EventbriteParser

register_parser(EventbriteParser())

__all__ = [
    "BaseEventParser",
    "ParserHealthCheck",
    "ParserResult",
    "get_parser_for_url",
    "register_parser",
    "get_all_parsers",
]
