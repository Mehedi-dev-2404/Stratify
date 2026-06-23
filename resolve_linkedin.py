"""
resolve_linkedin.py — Resolves a company name + domain to a LinkedIn company page URL.

Uses dependency-injected search_fn so the caller controls which search API
is used (Tavily, SerpAPI, Claude web_search tool, etc.).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

from config import API_MOCK_MODE as MOCK_MODE

_CACHE_DIR = Path(__file__).parent / "raw_cache"

# ---------------------------------------------------------------------------
# Mock fixtures — keyed by slugified company name
# ---------------------------------------------------------------------------

_MOCK_URLS: dict[str, str] = {
    "numerai": "https://www.linkedin.com/company/numerai/",
    "kavout": "https://www.linkedin.com/company/kavout/",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LINKEDIN_COMPANY_RE = re.compile(
    r"https?://(?:www\.)?linkedin\.com/company/[^\s\"'<>]+"
)


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _cache_path(slug: str) -> Path:
    return _CACHE_DIR / f"{slug}_linkedin_url.json"


def _is_valid_company_url(url: str) -> bool:
    """Return True only if url matches linkedin.com/company/* (not /in/* profiles)."""
    return bool(_LINKEDIN_COMPANY_RE.match(url.strip()))


def _extract_candidates(raw_results: list[str] | list[dict] | str) -> list[str]:
    """Pull all linkedin.com/company/* URLs out of whatever the search_fn returns.

    Accepts:
      - list of URL strings
      - list of dicts with a 'url' or 'link' key
      - a single string blob (extracts via regex)
    """
    urls: list[str] = []

    if isinstance(raw_results, str):
        urls = _LINKEDIN_COMPANY_RE.findall(raw_results)
    elif isinstance(raw_results, list):
        for item in raw_results:
            if isinstance(item, str):
                urls += _LINKEDIN_COMPANY_RE.findall(item)
            elif isinstance(item, dict):
                for key in ("url", "link", "href"):
                    val = item.get(key, "")
                    if val:
                        urls += _LINKEDIN_COMPANY_RE.findall(val)
                # Also scan any text/snippet fields in case URL is embedded
                for key in ("snippet", "text", "description", "content"):
                    val = item.get(key, "")
                    if val:
                        urls += _LINKEDIN_COMPANY_RE.findall(val)

    return [u for u in urls if _is_valid_company_url(u)]


# ---------------------------------------------------------------------------
# Public resolver
# ---------------------------------------------------------------------------


def resolve_company_linkedin(
    company_name: str,
    domain: str,
    search_fn: Callable[[str], list[str] | list[dict] | str] | None = None,
) -> str:
    """Resolve a LinkedIn company page URL for a given company.

    Args:
        company_name: Human-readable company name.
        domain: Company website domain (used in query + cache key).
        search_fn: Callable that accepts a query string and returns search
            results in any of these forms:
              - list of URL strings
              - list of result dicts (with 'url'/'link' and optional 'snippet')
              - raw string blob containing URLs
            Required when MOCK_MODE=False. Ignored in MOCK_MODE=True.

    Returns:
        LinkedIn company page URL string, or "" if none found.
    """
    slug = _slugify(domain or company_name)
    cache_file = _cache_path(slug)

    if MOCK_MODE:
        name_slug = _slugify(company_name)
        return _MOCK_URLS.get(name_slug, "")

    # --- Real branch ---
    if cache_file.exists():
        cached = json.loads(cache_file.read_text())
        return cached.get("url", "")

    if search_fn is None:
        raise ValueError(
            "search_fn is required when MOCK_MODE=False. "
            "Pass a callable that takes a query string and returns search results."
        )

    query = f"{company_name} linkedin company page"
    raw_results = search_fn(query)
    candidates = _extract_candidates(raw_results)

    url = candidates[0] if candidates else ""
    confidence = "high" if url else "none"

    cache_payload = {
        "url": url,
        "confidence": confidence,
        "source": "web_search",
        "query": query,
    }
    cache_file.write_text(json.dumps(cache_payload, indent=2))

    return url
