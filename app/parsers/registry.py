"""
Parser registry — maps URL patterns to site-specific parsers.

Usage:
    from app.parsers import get_parser_for_url, register_parser

    parser = get_parser_for_url("https://www.eventbrite.com/e/some-event")
    if parser:
        health = parser.health_check(html, url)
        ...
"""

import re
from typing import Optional

from app.parsers.base import BaseEventParser

_registry: dict[str, tuple[list[re.Pattern], BaseEventParser]] = {}


def register_parser(parser: BaseEventParser) -> None:
    """Register a parser, compiling its URL patterns for matching."""
    compiled = [re.compile(pattern) for pattern in parser.url_patterns]
    _registry[parser.site_name] = (compiled, parser)


def get_parser_for_url(url: str) -> Optional[BaseEventParser]:
    """Return the first parser whose URL patterns match the given URL, or None."""
    for _name, (patterns, parser) in _registry.items():
        for pattern in patterns:
            if pattern.search(url):
                return parser
    return None


def get_all_parsers() -> dict[str, BaseEventParser]:
    """Return all registered parsers keyed by site_name."""
    return {name: parser for name, (_, parser) in _registry.items()}
