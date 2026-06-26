"""
resolve_linkedin.py — Resolves a company name + domain to a LinkedIn company page URL.

Uses dependency-injected search_fn so the caller controls which search API
is used (Tavily, SerpAPI, Claude web_search tool, etc.).

Validation requires at least one STRONG signal per candidate:
  - URL slug matches the company name (bidirectional token overlap ≥ 70%)
  - OR the company's bare domain appears in the result's title/content

Name-in-context alone is insufficient — too many false positives for
common-word names like "Banker" matching "American Banker".
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

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


def _get_url_slug(url: str) -> str:
    """Extract the company slug from a LinkedIn company URL."""
    m = re.search(r"linkedin\.com/company/([^/\s?#]+)", url)
    return m.group(1) if m else ""


def _url_slug_matches_name(url: str, company_name: str) -> bool:
    """Check whether the LinkedIn URL slug bidirectionally overlaps the company name.

    Requires ≥70% token coverage in BOTH directions to avoid:
      "banker" matching "american-banker" (slug_coverage = 0.5, fails).
    """
    raw_slug = _get_url_slug(url)
    if not raw_slug:
        return False
    slug_tokens = {t for t in re.split(r"[-_]", raw_slug.lower()) if len(t) > 1}
    name_tokens = {
        t
        for t in re.sub(r"[^a-z0-9]", " ", company_name.lower()).split()
        if len(t) > 1
    }
    if not name_tokens or not slug_tokens:
        return False
    overlap = name_tokens & slug_tokens
    name_coverage = len(overlap) / len(name_tokens)
    slug_coverage = len(overlap) / len(slug_tokens)
    return name_coverage >= 0.7 and slug_coverage >= 0.7


def _domain_in_context(domain: str, context: str) -> bool:
    """Check whether the company's bare domain appears in the result text."""
    parsed = urlparse(domain.strip().rstrip("/"))
    bare = (parsed.netloc or domain.strip()).lower().lstrip("www.")
    if not bare or "." not in bare:
        return False
    return bare in context.lower()


def _extract_candidates_with_context(
    raw_results: list[str] | list[dict] | str,
) -> list[dict]:
    """Extract LinkedIn company URLs paired with their source result's title + content.

    Returns list of dicts: {url, title, content}
    Each candidate carries the context from the result it was found in,
    enabling name/domain validation downstream.
    """
    candidates: list[dict] = []

    if isinstance(raw_results, str):
        for url in _LINKEDIN_COMPANY_RE.findall(raw_results):
            if _is_valid_company_url(url):
                candidates.append({"url": url, "title": "", "content": raw_results[:500]})
        return candidates

    if not isinstance(raw_results, list):
        return candidates

    for item in raw_results:
        if isinstance(item, str):
            for url in _LINKEDIN_COMPANY_RE.findall(item):
                if _is_valid_company_url(url):
                    candidates.append({"url": url, "title": "", "content": item[:500]})
            continue

        if not isinstance(item, dict):
            continue

        item_title = item.get("title", "") or ""
        item_content = (
            item.get("content", "")
            or item.get("snippet", "")
            or item.get("description", "")
            or ""
        )
        context = f"{item_title} {item_content}"

        # Check if the result's own URL is a LinkedIn company page
        item_url = item.get("url", "") or item.get("link", "") or item.get("href", "") or ""
        if item_url and _is_valid_company_url(item_url):
            candidates.append({
                "url": item_url,
                "title": item_title,
                "content": item_content,
            })

        # Also scan title and content for embedded LinkedIn company URLs
        for embedded_url in _LINKEDIN_COMPANY_RE.findall(context):
            if _is_valid_company_url(embedded_url) and embedded_url != item_url:
                candidates.append({
                    "url": embedded_url,
                    "title": item_title,
                    "content": item_content,
                })

    # Deduplicate by URL, keeping first occurrence (highest-ranked result)
    seen: set[str] = set()
    deduped = []
    for c in candidates:
        if c["url"] not in seen:
            seen.add(c["url"])
            deduped.append(c)
    return deduped


def _score_candidate(
    candidate: dict,
    company_name: str,
    domain: str,
) -> tuple[int, str]:
    """Score a candidate against the target company. Returns (score, rejection_reason).

    score > 0 means accepted. score == 0 means rejected.

    Strong signals (each worth 2 points):
      - URL slug bidirectionally matches company name tokens (≥70% overlap)
      - Company's bare domain appears in result title/content

    At least one strong signal is required. Name-in-context alone is
    deliberately excluded to prevent false positives on common-word names.
    """
    url = candidate["url"]
    context = f"{candidate.get('title', '')} {candidate.get('content', '')}"

    slug_ok = _url_slug_matches_name(url, company_name)
    domain_ok = _domain_in_context(domain, context)

    score = (2 if slug_ok else 0) + (2 if domain_ok else 0)

    if score == 0:
        reason = (
            f"slug '{_get_url_slug(url)}' doesn't bidirectionally match "
            f"'{company_name}' and domain '{domain}' not found in result context"
        )
        return 0, reason

    return score, ""


# ---------------------------------------------------------------------------
# Public resolver
# ---------------------------------------------------------------------------


def _extract_candidates(raw_results: list[str] | list[dict] | str) -> list[str]:
    """Backward-compatible helper — returns URL strings only (no context).

    Prefer _extract_candidates_with_context for validation-aware use.
    """
    return [c["url"] for c in _extract_candidates_with_context(raw_results)]


def resolve_company_linkedin(
    company_name: str,
    domain: str,
    search_fn: Callable[[str], list[str] | list[dict] | str] | None = None,
) -> tuple[str, str]:
    """Resolve a LinkedIn company page URL for a given company.

    Args:
        company_name: Human-readable company name.
        domain: Company website domain (used in query + cache key).
        search_fn: Callable that accepts a query string and returns search
            results in any of these forms:
              - list of URL strings
              - list of result dicts (with 'url'/'link' and optional 'title'/'content')
              - raw string blob containing URLs
            Required when MOCK_MODE=False. Ignored in MOCK_MODE=True.

    Returns:
        (url, rejection_reason) — url is "" on failure/rejection.
        rejection_reason is "" on success, a description string on rejection.
    """
    slug = _slugify(domain or company_name)
    cache_file = _cache_path(slug)

    if MOCK_MODE:
        name_slug = _slugify(company_name)
        url = _MOCK_URLS.get(name_slug, "")
        return url, ""

    # --- Real branch ---
    if cache_file.exists():
        cached = json.loads(cache_file.read_text())
        return cached.get("url", ""), cached.get("rejection_reason", "")

    if search_fn is None:
        raise ValueError(
            "search_fn is required when MOCK_MODE=False. "
            "Pass a callable that takes a query string and returns search results."
        )

    query = f"{company_name} linkedin company page"
    raw_results = search_fn(query)
    candidates = _extract_candidates_with_context(raw_results)

    # Score all candidates, pick the highest-scoring one that passes
    best_url = ""
    best_score = 0
    rejection_reason = "no linkedin.com/company/* URLs found in search results"

    for candidate in candidates:
        score, reason = _score_candidate(candidate, company_name, domain)
        if score > best_score:
            best_score = score
            best_url = candidate["url"]
            rejection_reason = reason  # "" if accepted

    if not best_url:
        # Summarise all candidate URLs that were found but rejected
        if candidates:
            slugs = ", ".join(_get_url_slug(c["url"]) for c in candidates[:3])
            rejection_reason = (
                f"found candidate(s) [{slugs}] but none matched name/domain — "
                + rejection_reason
            )

    confidence = "high" if best_url else "none"
    cache_payload = {
        "url": best_url,
        "confidence": confidence,
        "rejection_reason": rejection_reason if not best_url else "",
        "source": "web_search",
        "query": query,
    }
    cache_file.write_text(json.dumps(cache_payload, indent=2))

    return best_url, rejection_reason if not best_url else ""


def validate_candidate_url(
    company_name: str,
    domain: str,
    url: str,
    context: str = "",
) -> tuple[str, str]:
    """Validate a LinkedIn company URL against name and domain signals.

    Applies the same scoring rules as resolve_company_linkedin — bidirectional
    slug/name token overlap ≥70% OR domain present in context.

    Args:
        company_name: Human-readable company name.
        domain: Company website domain.
        url: Candidate LinkedIn company URL to validate.
        context: Optional text surrounding the URL (e.g. page title + snippet)
            used for domain-in-context signal.

    Returns:
        (url, "") if valid, ("", rejection_reason) if rejected.
    """
    if not _is_valid_company_url(url):
        return "", f"not a valid linkedin.com/company/* URL: {url!r}"
    candidate = {"url": url, "title": "", "content": context}
    score, reason = _score_candidate(candidate, company_name, domain)
    if score > 0:
        return url, ""
    return "", reason
