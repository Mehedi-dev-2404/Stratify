"""
fetchers.py — Raw data fetching for Stratify.
In MOCK_MODE=True all functions return fixture data.
In MOCK_MODE=False they call real external APIs (Apify, requests/BS4).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from config import APIFY_API_TOKEN, MOCK_MODE

# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

_CACHE_DIR = Path(__file__).parent / "raw_cache"


def _cache_path(slug: str, source: str) -> Path:
    """Return the expected cache file path for a given company slug and source."""
    return _CACHE_DIR / f"{slug}_{source}.json"


def _slugify(text: str) -> str:
    """Convert a string to a safe filesystem slug."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _apify_run_sync(actor_id: str, run_input: dict) -> list[dict]:
    """Run an Apify actor synchronously and return the dataset items.

    Uses the Apify REST API synchronous run endpoint (runs actor and waits for
    completion, returning the dataset contents in one call).

    Args:
        actor_id: Apify actor identifier, e.g. "harvestapi/linkedin-company".
        run_input: Actor input dict (will be JSON-encoded).

    Returns:
        List of dataset item dicts from the completed run.

    Raises:
        RuntimeError: If the Apify API returns a non-200 status.
    """
    url = f"https://api.apify.com/v2/acts/{actor_id}/run-sync-get-dataset-items"
    params = {"token": APIFY_API_TOKEN}
    response = requests.post(url, params=params, json=run_input, timeout=120)
    if response.status_code != 200:
        raise RuntimeError(
            f"Apify actor '{actor_id}' failed "
            f"({response.status_code}): {response.text[:300]}"
        )
    return response.json()


# ---------------------------------------------------------------------------
# Mock fixture data
# ---------------------------------------------------------------------------

_MOCK_LINKEDIN_PROFILES: dict[str, dict] = {
    "numerai": {
        "name": "Numerai",
        "industry": "Financial Services / AI",
        "employee_count": "51-200",
        "funding_status": "Funded",
        "funding_amount": "$21M",
        "description": (
            "Numerai is a San Francisco-based AI hedge fund that crowdsources "
            "machine learning models from a global community of data scientists. "
            "Participants submit encrypted predictions on financial data to earn "
            "the NMR cryptocurrency token. The aggregate meta-model drives real "
            "capital allocation in equities markets."
        ),
        "follower_count": 28400,
        "headquarters": "San Francisco, CA",
        "founded_year": 2015,
    },
    "kavout": {
        "name": "Kavout",
        "industry": "Financial Technology / AI",
        "employee_count": "11-50",
        "funding_status": "Funded",
        "funding_amount": "$8M",
        "description": (
            "Kavout is an AI-driven investment intelligence platform that applies "
            "machine learning and big-data analytics to equity research and stock "
            "ranking. Its flagship Kai Score ranks stocks daily using hundreds of "
            "signals across price, fundamental, and alternative data sources."
        ),
        "follower_count": 6800,
        "headquarters": "Seattle, WA",
        "founded_year": 2016,
    },
}

_MOCK_POSTS: list[str] = [
    (
        "Excited to share that our latest ML model ensemble achieved a "
        "Sharpe ratio improvement of 0.18 on out-of-sample equity data. "
        "Alternative data signals continue to outperform traditional factors. "
        "#MachineLearning #Quant #AlphaGeneration"
    ),
    (
        "We're hiring senior ML engineers and quantitative researchers. "
        "Come help us build the next generation of AI-driven portfolio construction. "
        "Remote-friendly. Link in bio. #QuantFinance #AIJobs"
    ),
    (
        "Our founder will be speaking at QuantMinds International next month "
        "on the topic of federated learning applied to financial prediction markets. "
        "Register here: [link] #QuantMinds #FederatedLearning"
    ),
    (
        "New blog post: 'Why cross-sectional neutralisation matters more than ever "
        "in crowded factor spaces.' Read it on our website. "
        "#AlphaDecay #FactorInvesting #AI"
    ),
]

_MOCK_WEBSITE_TEXT = (
    "We are building the future of systematic investment management through "
    "advanced machine learning and alternative data analytics. Our platform "
    "ingests terabytes of structured and unstructured data daily — from satellite "
    "imagery and credit-card transaction flows to earnings call transcripts and "
    "social sentiment — to generate high-conviction alpha signals. Designed for "
    "institutional asset managers, family offices, and proprietary trading desks, "
    "our tools reduce research cycle time from weeks to hours and surface "
    "non-consensus opportunities before they become consensus. Founded by a team "
    "of former Renaissance Technologies, Two Sigma, and Google DeepMind alumni."
)

_MOCK_FUNDING_SNIPPETS: list[str] = [
    (
        "Numerai raises $21 million Series A led by Union Square Ventures, "
        "with participation from Placeholder VC and CoinFund. The round will "
        "fund expansion of the data science tournament platform and grow the "
        "team. (TechCrunch, March 2019)"
    ),
    (
        "AI hedge fund platform secures fresh capital to scale its crowdsourced "
        "prediction infrastructure. Investors cited the network effect of 10,000+ "
        "contributing data scientists as a key moat. (Bloomberg, April 2019)"
    ),
]

# ---------------------------------------------------------------------------
# Public fetch functions
# ---------------------------------------------------------------------------


def fetch_company_linkedin(company_name: str, linkedin_url: str) -> dict:
    """Fetch company profile data from LinkedIn via Apify harvestapi/linkedin-company.

    Args:
        company_name: Human-readable company name (used for cache key).
        linkedin_url: Full LinkedIn company page URL.

    Returns:
        dict with keys: name, industry, employee_count, funding_status,
        funding_amount, description, follower_count, headquarters, founded_year.
    """
    if MOCK_MODE:
        slug = _slugify(company_name)
        fixture = _MOCK_LINKEDIN_PROFILES.get(slug, _MOCK_LINKEDIN_PROFILES["numerai"])
        return fixture

    # --- Real branch ---
    slug = _slugify(company_name)
    cache_file = _cache_path(slug, "linkedin")

    if cache_file.exists():
        return json.loads(cache_file.read_text())

    items = _apify_run_sync(
        "harvestapi/linkedin-company",
        {"urls": [linkedin_url]},
    )

    raw = items[0] if items else {}

    # Map Apify response fields → internal schema.
    # Apify harvestapi/linkedin-company returns camelCase fields; use .get()
    # with safe defaults throughout so missing fields never raise KeyError.
    result = {
        "name": raw.get("name") or raw.get("companyName") or company_name,
        "industry": raw.get("industry") or "",
        "employee_count": (
            raw.get("employeeCount")
            or raw.get("staffCount")
            or raw.get("companySize")
            or ""
        ),
        # LinkedIn profiles rarely expose funding; leave blank if unverified.
        "funding_status": raw.get("fundingStatus") or "",
        "funding_amount": raw.get("fundingAmount") or "",
        "description": raw.get("description") or raw.get("tagline") or "",
        "follower_count": raw.get("followerCount") or raw.get("followersCount") or 0,
        "headquarters": _extract_headquarters(raw),
        "founded_year": raw.get("foundedYear") or raw.get("founded") or None,
    }

    cache_file.write_text(json.dumps(result, indent=2))
    return result


def _extract_headquarters(raw: dict) -> str:
    """Extract a headquarters string from Apify's nested location fields."""
    # harvestapi may return a flat 'headquarter' string or a nested dict
    hq = raw.get("headquarter") or raw.get("headquarters") or raw.get("location")
    if isinstance(hq, str):
        return hq
    if isinstance(hq, dict):
        parts = [
            hq.get("city") or "",
            hq.get("geographicArea") or hq.get("state") or "",
            hq.get("country") or "",
        ]
        return ", ".join(p for p in parts if p)
    return ""


def fetch_company_posts(linkedin_url: str, max_posts: int = 10) -> list[str]:
    """Fetch recent LinkedIn posts for a company via Apify harvestapi/linkedin-company-posts.

    Args:
        linkedin_url: Full LinkedIn company page URL.
        max_posts: Maximum number of recent posts to retrieve.

    Returns:
        List of post text strings (most recent first).
    """
    if MOCK_MODE:
        return _MOCK_POSTS[:max_posts]

    # --- Real branch ---
    slug = _slugify(linkedin_url)
    cache_file = _cache_path(slug, "posts")

    if cache_file.exists():
        return json.loads(cache_file.read_text())

    items = _apify_run_sync(
        "harvestapi/linkedin-company-posts",
        {
            "urls": [linkedin_url],
            "maxPosts": max_posts,
            "scrapeReactions": False,
            "scrapeComments": False,
        },
    )

    # Each item may have a 'text', 'content', or 'postText' field.
    posts: list[str] = []
    for item in items:
        text = (
            item.get("text")
            or item.get("content")
            or item.get("postText")
            or ""
        )
        text = text.strip()
        if text:
            posts.append(text)

    result = posts[:max_posts]
    cache_file.write_text(json.dumps(result, indent=2))
    return result


def fetch_website_text(domain: str) -> str:
    """Fetch and extract visible text from a company's homepage (and /about).

    Tries https first, falls back to http. Fetches / and /about, concatenates
    visible text, strips boilerplate tags, and caps output at ~3000 characters.

    Args:
        domain: Company domain (e.g. "example.com"), without protocol prefix.

    Returns:
        Plain text extracted from the homepage HTML, capped at 3000 chars.
    """
    if MOCK_MODE:
        return _MOCK_WEBSITE_TEXT

    # --- Real branch ---
    slug = _slugify(domain)
    cache_file = _cache_path(slug, "website")

    if cache_file.exists():
        return cache_file.read_text()

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; StratifyBot/1.0; research purposes)"
        )
    }

    def _fetch_page(url: str) -> str:
        """Fetch a single URL and return stripped visible text, or '' on error."""
        try:
            resp = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
            if resp.status_code != 200:
                return ""
            soup = BeautifulSoup(resp.text, "html.parser")
            # Remove script, style, nav, footer, header — low-signal noise
            for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
                tag.decompose()
            text = soup.get_text(separator=" ")
            # Collapse whitespace
            text = re.sub(r"\s+", " ", text).strip()
            return text
        except Exception:
            return ""

    # Try https, fall back to http
    for scheme in ("https", "http"):
        base_url = f"{scheme}://{domain}"
        home_text = _fetch_page(base_url)
        if home_text:
            break
    else:
        home_text = ""

    about_text = ""
    if home_text:
        # Only attempt /about if homepage succeeded (same scheme)
        about_text = _fetch_page(f"{base_url}/about")

    combined = " ".join(filter(None, [home_text, about_text]))
    result = combined[:3000]

    cache_file.write_text(result)
    return result


def fetch_funding_web_search(company_name: str) -> list[str]:
    """Search the web for funding information about a company.

    NOTE: Web search is intentionally deferred to a later round. This function
    will be wired to a search API (e.g. Tavily or SerpAPI) separately from the
    Apify work in Round 3. The mock branch remains available for pipeline testing.

    Args:
        company_name: Human-readable company name.

    Returns:
        List of text snippets mentioning funding rounds, investors, or amounts.
    """
    if MOCK_MODE:
        slug = _slugify(company_name)
        cache_file = _cache_path(slug, "funding")
        # TODO: if cache_file.exists(): return json.loads(cache_file.read_text())
        # TODO: cache_file.write_text(json.dumps(_MOCK_FUNDING_SNIPPETS, indent=2))
        return _MOCK_FUNDING_SNIPPETS

    # Web search API not yet wired — deferred to post-Round 3.
    raise NotImplementedError(
        "fetch_funding_web_search real branch is intentionally deferred. "
        "Wire a search API (Tavily / SerpAPI) in a dedicated round."
    )
