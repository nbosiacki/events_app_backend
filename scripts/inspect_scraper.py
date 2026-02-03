#!/usr/bin/env python3
"""
Developer inspection tool for the scraper pipeline.

Four modes for debugging and validating site parsers:

    --preview <url>   Fetch page, show HTTP info, text excerpt, links,
                      matched parser, and health check result.
    --health <url>    Quick parser health check — which parsers match,
                      which selectors are found/missing.
    --dry-run <url>   Full scrape pipeline without DB writes — shows
                      extracted events as JSON, stats on parser vs Claude.
    --compare <url>   Side-by-side: extracted fields vs raw HTML excerpts.
                      Opens the URL in a browser for visual comparison.

Usage:
    python -m scripts.inspect_scraper --preview "https://www.eventbrite.com/d/sweden--stockholm/events/"
    python -m scripts.inspect_scraper --health "https://www.eventbrite.com/e/some-event"
    python -m scripts.inspect_scraper --dry-run "https://www.eventbrite.com/d/sweden--stockholm/events/"
    python -m scripts.inspect_scraper --compare "https://www.eventbrite.com/e/some-event"
"""

import asyncio
import argparse
import json
import sys
import webbrowser
from pathlib import Path
from datetime import datetime

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
from bs4 import BeautifulSoup

# ── ANSI colors ─────────────────────────────────────────────────────

BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
CYAN = "\033[36m"
RESET = "\033[0m"


def _colored(text: str, color: str) -> str:
    return f"{color}{text}{RESET}"


def _header(text: str) -> str:
    return f"\n{BOLD}{CYAN}{'=' * 60}{RESET}\n{BOLD}{CYAN}  {text}{RESET}\n{BOLD}{CYAN}{'=' * 60}{RESET}"


def _section(text: str) -> str:
    return f"\n{BOLD}{BLUE}--- {text} ---{RESET}"


# ── HTTP client ─────────────────────────────────────────────────────

def fetch_page(url: str) -> tuple[str, dict]:
    """Fetch a URL and return (html, info_dict)."""
    client = httpx.Client(
        timeout=30.0,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        },
    )
    try:
        response = client.get(url)
        info = {
            "status_code": response.status_code,
            "content_type": response.headers.get("content-type", ""),
            "content_length": len(response.text),
            "url": str(response.url),
        }
        return response.text, info
    finally:
        client.close()


# ── Preview mode ────────────────────────────────────────────────────

def run_preview(url: str, verbose: bool):
    """Fetch page, show HTTP info, text excerpt, links, parser match."""
    print(_header(f"Preview: {url}"))

    html, info = fetch_page(url)

    print(_section("HTTP Info"))
    print(f"  Status:         {info['status_code']}")
    print(f"  Content-Type:   {info['content_type']}")
    print(f"  Content-Length:  {info['content_length']:,} chars")
    print(f"  Final URL:      {info['url']}")

    soup = BeautifulSoup(html, "lxml")
    title = soup.title.string if soup.title else "(no title)"
    print(f"  Page Title:     {title}")

    # Text excerpt
    for element in soup(["script", "style", "nav", "footer", "header"]):
        element.decompose()
    text = soup.get_text(separator="\n", strip=True)
    lines = [line for line in text.split("\n") if line.strip()]

    print(_section(f"Text Excerpt (first 20 lines of {len(lines)} total)"))
    for line in lines[:20]:
        print(f"  {DIM}{line[:120]}{RESET}")

    # Links
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)[:60]
        links.append((href, text))

    print(_section(f"Links ({len(links)} total, showing first 20)"))
    for href, text in links[:20]:
        print(f"  {BLUE}{href[:80]}{RESET}  {DIM}{text}{RESET}")

    # Parser match
    from app.parsers import get_parser_for_url, get_all_parsers

    print(_section("Parser Match"))
    parser = get_parser_for_url(url)
    if parser:
        print(f"  Matched: {_colored(parser.site_name, GREEN)}")
        health = parser.health_check(html, url)
        status = _colored("HEALTHY", GREEN) if health.is_healthy else _colored("UNHEALTHY", RED)
        print(f"  Health:  {status} — {health.message}")
        if health.missing_selectors:
            for sel in health.missing_selectors:
                print(f"    {RED}Missing: {sel}{RESET}")
    else:
        print(f"  {YELLOW}No parser matches this URL{RESET}")
        print(f"  Registered parsers: {', '.join(get_all_parsers().keys())}")

    # URL extraction
    from app.agents.scraper import EventScraper
    with _suppress_anthropic():
        scraper = EventScraper.__new__(EventScraper)
        scraper.http_client = None
        event_urls = scraper.extract_urls_from_page(html, url)

    print(_section(f"Extracted Event URLs ({len(event_urls)})"))
    for eu in event_urls[:15]:
        print(f"  {eu}")
    if len(event_urls) > 15:
        print(f"  ... and {len(event_urls) - 15} more")


# ── Health mode ─────────────────────────────────────────────────────

def run_health(url: str, verbose: bool):
    """Quick parser health check."""
    print(_header(f"Health Check: {url}"))

    html, info = fetch_page(url)
    print(f"  Fetched {info['content_length']:,} chars (HTTP {info['status_code']})")

    from app.parsers import get_parser_for_url, get_all_parsers

    parser = get_parser_for_url(url)
    if not parser:
        print(f"\n  {YELLOW}No parser matches this URL.{RESET}")
        print(f"  Registered parsers:")
        for name, p in get_all_parsers().items():
            print(f"    {name}: {p.url_patterns}")
        return

    print(f"\n  Parser: {_colored(parser.site_name, CYAN)}")
    health = parser.health_check(html, url)

    if health.is_healthy:
        print(f"  Status: {_colored('HEALTHY', GREEN)}")
        print(f"  {health.message}")
    else:
        print(f"  Status: {_colored('UNHEALTHY', RED)}")
        print(f"  {health.message}")
        print(f"\n  Missing selectors:")
        for sel in health.missing_selectors:
            print(f"    {RED}✗{RESET} {sel}")

    # Show what selectors were found
    if verbose:
        soup = BeautifulSoup(html, "lxml")
        is_detail = "/e/" in url
        from app.parsers.eventbrite import LISTING_SELECTORS, EVENT_SELECTORS
        selectors = EVENT_SELECTORS if is_detail else LISTING_SELECTORS

        print(_section("Selector Details"))
        for name, selector in selectors.items():
            matches = soup.select(selector)
            if matches:
                text = matches[0].get_text(strip=True)[:60]
                print(f"  {GREEN}✓{RESET} {name}: {selector}  →  {DIM}{text}{RESET}")
            else:
                print(f"  {RED}✗{RESET} {name}: {selector}")


# ── Dry-run mode ────────────────────────────────────────────────────

def run_dry_run(url: str, verbose: bool):
    """Full pipeline without DB writes."""
    print(_header(f"Dry Run: {url}"))

    html, info = fetch_page(url)
    print(f"  Fetched {info['content_length']:,} chars (HTTP {info['status_code']})")

    from app.parsers import get_parser_for_url

    # Extract URLs
    with _suppress_anthropic():
        from app.agents.scraper import EventScraper
        scraper = EventScraper.__new__(EventScraper)
        scraper.http_client = httpx.Client(
            timeout=30.0,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"},
        )

    event_urls = scraper.extract_urls_from_page(html, url)
    print(f"\n  Extracted {len(event_urls)} event URL(s)")

    # Try parser
    parser = get_parser_for_url(url)
    parser_events = []
    parser_failures = []

    if parser:
        print(f"  Parser: {parser.site_name}")
        health = parser.health_check(html, url)
        status = _colored("PASS", GREEN) if health.is_healthy else _colored("FAIL", RED)
        print(f"  Health: {status} — {health.message}")

        if not health.is_healthy:
            print(f"  → Claude fallback would be triggered")

        # Try parsing each event URL
        for eu in event_urls[:10]:
            try:
                event_html, _ = fetch_page(eu)
                event = parser.parse_event(event_html, eu)
                if event:
                    parser_events.append(event)
                else:
                    parser_failures.append(eu)
            except Exception as e:
                parser_failures.append(f"{eu} ({e})")
    else:
        print(f"  {YELLOW}No parser — all URLs would go to Claude{RESET}")
        parser_failures = event_urls

    # Summary
    print(_section("Results"))
    print(f"  Parser extracted:  {_colored(str(len(parser_events)), GREEN)} events")
    print(f"  Parser failed:     {_colored(str(len(parser_failures)), RED if parser_failures else DIM)} URLs")
    print(f"  Would use Claude:  {_colored(str(len(parser_failures)), YELLOW)} URLs")

    if parser_events:
        print(_section("Extracted Events"))
        for i, event in enumerate(parser_events, 1):
            print(f"\n  {BOLD}Event {i}:{RESET}")
            print(f"    Title:    {event.title}")
            print(f"    Venue:    {event.venue.name}")
            print(f"    Date:     {event.datetime_start}")
            print(f"    Price:    {event.price.amount} {event.price.currency} ({event.price.bucket})")
            print(f"    URL:      {event.source_url}")
            if event.categories:
                print(f"    Tags:     {', '.join(event.categories)}")

    scraper.http_client.close()


# ── Compare mode ────────────────────────────────────────────────────

def run_compare(url: str, verbose: bool):
    """Side-by-side: extracted data vs raw HTML excerpts."""
    print(_header(f"Compare: {url}"))

    html, info = fetch_page(url)
    soup = BeautifulSoup(html, "lxml")

    from app.parsers import get_parser_for_url

    parser = get_parser_for_url(url)

    # Left column: extracted data
    print(_section("Extracted Data"))
    if parser:
        event = parser.parse_event(html, url)
        if event:
            fields = {
                "Title": event.title,
                "Description": (event.description or "")[:100],
                "Venue": event.venue.name,
                "Address": event.venue.address or "(none)",
                "Date": str(event.datetime_start),
                "Price": f"{event.price.amount} {event.price.currency} ({event.price.bucket})",
                "Categories": ", ".join(event.categories) if event.categories else "(none)",
                "Image": event.image_url or "(none)",
                "Source URL": event.source_url,
            }
            max_key = max(len(k) for k in fields)
            for key, value in fields.items():
                print(f"  {CYAN}{key:<{max_key}}{RESET}  {value}")
        else:
            print(f"  {RED}Parser returned None — could not extract event{RESET}")
    else:
        print(f"  {YELLOW}No parser available for this URL{RESET}")

    # Right column: raw HTML excerpts
    print(_section("Raw HTML Excerpts"))

    # Show key structural elements
    for tag_name in ["h1", "h2", "time", "address", "meta[name=description]"]:
        elements = soup.select(tag_name)
        if elements:
            for el in elements[:3]:
                text = el.get_text(strip=True)[:80]
                raw = str(el)[:120]
                print(f"  {GREEN}{tag_name}{RESET}: {text}")
                if verbose:
                    print(f"    {DIM}{raw}{RESET}")
        else:
            print(f"  {DIM}{tag_name}: (not found){RESET}")

    # Show parser-specific selectors if available
    if parser:
        from app.parsers.eventbrite import EVENT_SELECTORS
        print(_section("Parser Selectors vs HTML"))
        for name, selector in EVENT_SELECTORS.items():
            matches = soup.select(selector)
            if matches:
                text = matches[0].get_text(strip=True)[:60]
                print(f"  {GREEN}✓{RESET} {name}: {DIM}{text}{RESET}")
            else:
                print(f"  {RED}✗{RESET} {name}: {DIM}(not found){RESET}")

    # Open in browser
    print(f"\n  Opening {url} in browser for visual comparison...")
    webbrowser.open(url)


# ── Helpers ─────────────────────────────────────────────────────────

class _suppress_anthropic:
    """Context manager to import EventScraper without needing an Anthropic key."""

    def __enter__(self):
        from unittest.mock import patch
        self._patches = [
            patch("app.agents.scraper.Anthropic"),
        ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *args):
        for p in self._patches:
            p.stop()


def serialize(obj):
    """JSON serializer for non-standard types."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "__str__"):
        return str(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


# ── CLI ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Developer inspection tool for the scraper pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m scripts.inspect_scraper --preview "https://www.eventbrite.com/d/sweden--stockholm/events/"
  python -m scripts.inspect_scraper --health "https://www.eventbrite.com/e/some-event" --verbose
  python -m scripts.inspect_scraper --dry-run "https://www.eventbrite.com/d/sweden--stockholm/events/"
  python -m scripts.inspect_scraper --compare "https://www.eventbrite.com/e/some-event"
        """,
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--preview", metavar="URL", help="Preview a page: HTTP info, text, links, parser match")
    group.add_argument("--health", metavar="URL", help="Quick parser health check")
    group.add_argument("--dry-run", metavar="URL", help="Full pipeline without DB writes")
    group.add_argument("--compare", metavar="URL", help="Side-by-side data vs HTML, opens browser")

    parser.add_argument("--verbose", "-v", action="store_true", help="Show extra details")
    parser.add_argument(
        "--env",
        default="development",
        choices=["development", "test"],
        help="Target environment (default: development)",
    )

    args = parser.parse_args()

    import os
    os.environ["APP_ENV"] = args.env

    if args.preview:
        run_preview(args.preview, args.verbose)
    elif args.health:
        run_health(args.health, args.verbose)
    elif args.dry_run:
        run_dry_run(args.dry_run, args.verbose)
    elif args.compare:
        run_compare(args.compare, args.verbose)


if __name__ == "__main__":
    main()
