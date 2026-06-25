"""
tavily_client.py — Thin Tavily search wrapper for Stratify.

Two public functions:
  - tavily_search(query, purpose)        → list[str]   (content snippets only)
  - tavily_search_results(query, purpose) → list[dict]  (raw result dicts w/ url+content)

Both are cache-first (raw_cache/{slug}_tavily_{purpose}.json).
Both are no-ops (return []) when TAVILY_API_KEY is not set or in mock mode.

Use tavily_search_results as search_fn for BOTH fetch_funding_web_search() AND
resolve_company_linkedin() — it preserves source URLs needed for provenance.
tavily_search (strings only) is kept for backward compatibility but not preferred.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from config import API_MOCK_MODE as MOCK_MODE, TAVILY_API_KEY

_CACHE_DIR = Path(__file__).parent / "raw_cache"
_MAX_RESULTS = 5


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _cache_path(query: str, purpose: str) -> Path:
    slug = _slugify(query)[:80]  # cap slug length for long queries
    return _CACHE_DIR / f"{slug}_tavily_{purpose}.json"


def _raw_search(query: str, purpose: str) -> list[dict]:
    """Call Tavily API (or read cache) and return raw result dicts.

    Each dict has at minimum: url (str), content (str), title (str).
    Returns [] on any error rather than raising, so a Tavily failure
    never breaks the enclosing pipeline.
    """
    if MOCK_MODE or not TAVILY_API_KEY:
        return []

    cache_file = _cache_path(query, purpose)
    if cache_file.exists():
        return json.loads(cache_file.read_text())

    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=TAVILY_API_KEY)
        response = client.search(query, max_results=_MAX_RESULTS)
        results: list[dict] = response.get("results", [])
    except Exception as exc:
        # Log and return empty so the pipeline continues
        print(f"  [TAVILY WARN] query={query!r}: {type(exc).__name__}: {exc}")
        return []

    cache_file.write_text(json.dumps(results, indent=2))
    return results


def tavily_search(query: str, purpose: str = "general") -> list[str]:
    """Search via Tavily and return a list of content snippet strings.

    Suitable as search_fn for fetch_funding_web_search(), which iterates
    the return value and calls .strip() on each item.

    Args:
        query:   Search query string.
        purpose: Cache namespace label (e.g. "funding", "linkedin_url").

    Returns:
        List of content/snippet strings, empty list on cache miss + error.
    """
    results = _raw_search(query, purpose)
    snippets: list[str] = []
    for item in results:
        text = (item.get("content") or item.get("snippet") or "").strip()
        if text:
            snippets.append(text)
    return snippets


def tavily_search_results(query: str, purpose: str = "general") -> list[dict]:
    """Search via Tavily and return raw result dicts (url + content keys).

    Suitable as search_fn for resolve_company_linkedin(), whose
    _extract_candidates() function scans dict 'url' and 'content' keys
    for linkedin.com/company/* patterns.

    Args:
        query:   Search query string.
        purpose: Cache namespace label (e.g. "linkedin_url").

    Returns:
        List of result dicts with at least 'url' and 'content' keys.
    """
    return _raw_search(query, purpose)
