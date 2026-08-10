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

# Synonym map: normalises common name abbreviations before token comparison so
# that "labs"/"lab" match "laboratories"/"laboratory" in slugs (and vice versa).
_TOKEN_SYNONYMS: dict[str, str] = {
    "labs": "laboratories",
    "lab": "laboratory",
}

# Generic tokens that LinkedIn often appends to slugs but that are NOT meaningful
# distinguishers (e.g. "carousel-tech" for a company called "Carousel").
# Excluded from the slug_coverage denominator to avoid penalising valid matches.
_GENERIC_SLUG_TOKENS: frozenset[str] = frozenset({
    "tech", "ai", "inc", "hq", "io", "co", "app", "group", "api",
    "llc", "ltd", "corp", "global", "labs", "official",
})


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
    """Check whether a LinkedIn URL slug plausibly belongs to the company.

    Requires TWO fractions to both clear 70%:

      - name_coverage — fraction of company-name tokens accounted for by the
        slug. A token counts if it is an exact slug token OR the slug's
        concatenated form begins with it (so "scend"→"scendinc" and the whole
        name "blueflame"→"blueflameai" match). This is what the old exact-set
        intersection got wrong: it rejected valid concatenated / TLD-suffixed
        slugs like "scendinc", "cliftonai", or "complexiti.ai".

      - slug_precision — fraction of the slug's NON-generic tokens that are
        explained by a name token. This is the guard that keeps
        "banker" from matching "american-banker": the extra "american" token is
        unexplained, so precision = 0.5 and the match is rejected. It also kills
        "insidecatalyst"/"catalyst-marketing-llc" for a company named "Catalyst".

    Accommodations applied first:
      1. Synonym normalisation — "labs"→"laboratories" so abbreviation variants
         match.
      2. Slugs are split on '-', '_' AND '.' so TLD-style slugs like
         "complexiti.ai" tokenise correctly.
      3. Generic suffix tokens ("tech", "ai", "inc", "llc", …) are excluded from
         the precision denominator unless the name itself contains them.
    """
    raw_slug = _get_url_slug(url).lower()
    if not raw_slug:
        return False

    # Split on -, _ and . so "complexiti.ai" → {complexiti, ai}
    slug_tokens_list = [
        _TOKEN_SYNONYMS.get(t, t)
        for t in re.split(r"[-_.]", raw_slug)
        if len(t) > 1
    ]
    name_tokens_list = [
        _TOKEN_SYNONYMS.get(t, t)
        for t in re.sub(r"[^a-z0-9]", " ", company_name.lower()).split()
        if len(t) > 1
    ]
    if not name_tokens_list or not slug_tokens_list:
        return False

    slug_tokens = set(slug_tokens_list)
    name_tokens = set(name_tokens_list)

    # Concatenated (separator-free) forms for prefix matching.
    concat_slug = re.sub(r"[^a-z0-9]", "", raw_slug)          # "scendinc"
    concat_name = "".join(name_tokens_list)                   # "scend"

    # --- name_coverage ---
    # Whole-name concatenation aligns with the slug prefix (either direction):
    # catches "blueflame"→"blueflameai" and "scend"→"scendinc".
    if concat_slug.startswith(concat_name) or concat_name.startswith(concat_slug):
        name_coverage = 1.0
    else:
        covered = sum(
            1 for t in name_tokens
            if t in slug_tokens or concat_slug.startswith(t)
        )
        name_coverage = covered / len(name_tokens)

    # --- slug_precision ---
    # A slug token is "explained" if it equals / prefixes / is prefixed by a
    # name token (handles minor variants), ignoring generic suffixes.
    generic_noise = _GENERIC_SLUG_TOKENS - name_tokens
    meaningful_slug = [t for t in slug_tokens if t not in generic_noise] or list(slug_tokens)

    def _explained(tok: str) -> bool:
        if tok in name_tokens:
            return True
        # single concatenated slug token consumed by the full name prefix
        if len(meaningful_slug) == 1 and (
            concat_slug.startswith(concat_name) or concat_name.startswith(concat_slug)
        ):
            return True
        return any(tok.startswith(nt) or nt.startswith(tok) for nt in name_tokens)

    explained = sum(1 for t in meaningful_slug if _explained(t))
    slug_precision = explained / len(meaningful_slug)

    return name_coverage >= 0.7 and slug_precision >= 0.7


def _domain_in_context(domain: str, context: str) -> bool:
    """Check whether the company's bare domain appears in the result text."""
    parsed = urlparse(domain.strip().rstrip("/"))
    bare = (parsed.netloc or domain.strip()).lower().lstrip("www.")
    if not bare or "." not in bare:
        return False
    return bare in context.lower()


def _domain_slug_direct_match(domain: str, url: str) -> bool:
    """Check if the domain directly encodes the LinkedIn slug.

    LinkedIn often assigns slugs of the form <name>-<tld> for companies whose
    name matches their domain (e.g. causaility.ai → causaility-ai). Converting
    the bare domain (dots → hyphens) and comparing to the slug catches these
    cases without requiring name-token overlap.

    This check is deliberately exact (no substring) to prevent false positives
    like "banker.so" → "banker-so" matching "american-banker".
    """
    raw_slug = _get_url_slug(url)
    if not raw_slug:
        return False
    parsed = urlparse(domain.strip().rstrip("/"))
    bare = (parsed.netloc or domain.strip()).lower().lstrip("www.")
    if not bare or "." not in bare:
        return False
    return bare.replace(".", "-") == raw_slug.lower()


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
    domain_slug_ok = _domain_slug_direct_match(domain, url)

    score = (2 if slug_ok else 0) + (2 if domain_ok else 0) + (2 if domain_slug_ok else 0)

    if score == 0:
        reason = (
            f"slug '{_get_url_slug(url)}' doesn't bidirectionally match "
            f"'{company_name}', domain '{domain}' not found in result context, "
            f"and domain doesn't directly map to slug"
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

    # Recall fallback: if the name-only query surfaced no candidate that passes
    # scoring, retry with a domain-scoped query (mirrors the manual
    # "site:linkedin.com/company <domain>" trick). Catches pages that the
    # name-only search never returned — e.g. a company whose LinkedIn slug is
    # keyed off its domain rather than a searchable brand name.
    def _any_passes(cands: list[dict]) -> bool:
        return any(_score_candidate(c, company_name, domain)[0] > 0 for c in cands)

    if domain and not _any_passes(candidates):
        parsed = urlparse(domain.strip().rstrip("/"))
        bare = (parsed.netloc or domain.strip()).lower().lstrip("www.")
        if bare:
            fallback_query = f'site:linkedin.com/company {company_name} {bare}'
            fallback_results = search_fn(fallback_query)
            seen_urls = {c["url"] for c in candidates}
            for c in _extract_candidates_with_context(fallback_results):
                if c["url"] not in seen_urls:
                    seen_urls.add(c["url"])
                    candidates.append(c)

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
