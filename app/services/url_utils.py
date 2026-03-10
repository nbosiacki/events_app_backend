"""URL normalization and content fingerprinting for event deduplication."""

import hashlib
import re
from datetime import datetime
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse

_TRACKING_PARAMS = frozenset({
    "aff", "ref", "fbclid", "source",
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
})


def normalize_url(url: str) -> str:
    """Strip tracking params, normalize trailing slash, lowercase scheme/host."""
    try:
        parsed = urlparse(url)
        if not parsed.netloc:
            return url
        scheme = parsed.scheme.lower() or "https"
        netloc = parsed.netloc.lower()
        path = parsed.path.rstrip("/")
        filtered = {
            k: v
            for k, v in parse_qs(parsed.query).items()
            if k.lower() not in _TRACKING_PARAMS
        }
        query = urlencode(sorted(filtered.items()), doseq=True)
        return urlunparse((scheme, netloc, path, parsed.params, query, ""))
    except Exception:
        return url


def _normalize_text(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^\w\s]", "", s)
    s = re.sub(r"\s+", " ", s)
    return s


def make_content_hash(title: str, venue_name: str, dt_start) -> str:
    """Generate a stable fingerprint from (title, venue, date) for cross-URL dedup."""
    t = _normalize_text(title or "")
    v = _normalize_text(venue_name or "")
    if isinstance(dt_start, datetime):
        d = dt_start.date().isoformat()
    else:
        d = str(dt_start)[:10]
    raw = f"{t}|{v}|{d}"
    return hashlib.md5(raw.encode()).hexdigest()
