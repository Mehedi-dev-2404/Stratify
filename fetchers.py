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

from config import APIFY_API_TOKEN, API_MOCK_MODE as MOCK_MODE

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
        actor_id: Apify actor identifier in owner~name format,
            e.g. "harvestapi~linkedin-company".
        run_input: Actor input dict (will be JSON-encoded).

    Returns:
        List of dataset item dicts from the completed run.
        Returns [] if APIFY_API_TOKEN is not set.

    Raises:
        RuntimeError: If the Apify API returns a non-200 status.
    """
    if not APIFY_API_TOKEN:
        print(f"  [APIFY WARN] APIFY_API_TOKEN not set — skipping actor '{actor_id}'")
        return []

    # Actor IDs must use ~ as the username separator in the URL path.
    # Normalise any / separators to ~ to avoid broken URL paths.
    safe_actor_id = actor_id.replace("/", "~")
    url = f"https://api.apify.com/v2/acts/{safe_actor_id}/run-sync-get-dataset-items"
    params = {"token": APIFY_API_TOKEN}
    response = requests.post(url, params=params, json=run_input, timeout=120)
    # Apify returns 200 on success; 201 when the run completes with an empty
    # dataset. Both are non-error responses — treat them the same.
    if response.status_code not in (200, 201):
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

# Funding-signal keywords for founder profile screening
_FUNDING_KEYWORDS_RE = re.compile(
    r"\b(raised|raising|funding|funded|series\s+[a-e]|seed\s+round|"
    r"investment|investor|venture|vc|pre-seed|angel\s+round|"
    r"million|billion|\$\d|\€\d|£\d)\b",
    re.IGNORECASE,
)

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
        {"companies": [linkedin_url]},
    )

    raw = items[0] if items else {}

    # Map Apify response fields → internal schema.
    # harvestapi/linkedin-company returns camelCase fields with a rich
    # fundingData.lastFundingRound sub-object. Use .get() with safe defaults
    # throughout so missing fields never raise KeyError.
    funding_status, funding_amount = _extract_funding(raw)

    result = {
        "name": raw.get("name") or raw.get("companyName") or company_name,
        "industry": raw.get("industry") or "",
        "employee_count": (
            raw.get("employeeCount")
            or raw.get("staffCount")
            or raw.get("companySize")
            or ""
        ),
        "funding_status": funding_status,
        "funding_amount": funding_amount,
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


# Mapping from Apify's SCREAMING_SNAKE_CASE fundingType to human-readable labels
_FUNDING_TYPE_MAP: dict[str, str] = {
    "SEED": "Seed",
    "ANGEL": "Angel",
    "PRE_SEED": "Pre-Seed",
    "SERIES_A": "Series A",
    "SERIES_B": "Series B",
    "SERIES_C": "Series C",
    "SERIES_D": "Series D",
    "SERIES_E": "Series E",
    "SERIES_F": "Series F",
    "VENTURE": "Venture",
    "PRIVATE_EQUITY": "Private Equity",
    "DEBT_FINANCING": "Debt Financing",
    "CONVERTIBLE_NOTE": "Convertible Note",
    "GRANT": "Grant",
    "IPO": "IPO",
    "POST_IPO_EQUITY": "Post-IPO Equity",
    "POST_IPO_DEBT": "Post-IPO Debt",
    "SECONDARY_MARKET": "Secondary Market",
    "NON_EQUITY_ASSISTANCE": "Non-Equity Assistance",
    "CORPORATE_ROUND": "Corporate Round",
    "SERIES_UNKNOWN": "Venture (undisclosed series)",
    "UNDISCLOSED": "Undisclosed",
}


def _extract_funding(raw: dict) -> tuple[str, str]:
    """Extract funding_status and funding_amount from harvestapi fundingData.

    Reads the nested fundingData.lastFundingRound structure confirmed by live
    Apify testing:
        fundingData.lastFundingRound.fundingType          — e.g. "SERIES_A"
        fundingData.lastFundingRound.moneyRaised.amount   — e.g. 5000000
        fundingData.lastFundingRound.moneyRaised.currencyCode — e.g. "USD"
        fundingData.lastFundingRound.announcedOn          — {year, month, day}

    Returns:
        (funding_status, funding_amount) — both "" if not available.
    """
    funding_data = raw.get("fundingData") or {}
    last_round = funding_data.get("lastFundingRound") or {}

    funding_type_raw = last_round.get("fundingType") or ""
    funding_type = _FUNDING_TYPE_MAP.get(funding_type_raw, funding_type_raw)

    # Map funding type → status label
    if funding_type_raw == "IPO":
        funding_status = "Public"
    elif funding_type_raw:
        funding_status = "Funded"
    else:
        funding_status = ""

    # Build amount string: "$5M Series A (2023)"
    money = last_round.get("moneyRaised") or {}
    amount_raw = money.get("amount")
    currency = money.get("currencyCode") or "USD"
    announced = last_round.get("announcedOn") or {}
    year = announced.get("year") or ""

    amount_str = ""
    if amount_raw:
        # Convert to millions with 1 decimal place if ≥ 1M, else thousands
        try:
            amount_num = float(amount_raw)
        except (TypeError, ValueError):
            amount_num = None

        if amount_num is not None:
            currency_sym = {"USD": "$", "EUR": "€", "GBP": "£"}.get(currency, currency + " ")
            if amount_num >= 1_000_000:
                amount_str = f"{currency_sym}{amount_num / 1_000_000:.1f}M"
            elif amount_num >= 1_000:
                amount_str = f"{currency_sym}{amount_num / 1_000:.0f}K"
            else:
                amount_str = f"{currency_sym}{amount_num:.0f}"

    funding_amount = " ".join(filter(None, [amount_str, funding_type, f"({year})" if year else ""]))

    return funding_status, funding_amount


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
            "targetUrls": [linkedin_url],
            "maxPosts": max_posts,
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


def fetch_founder_linkedin(founder_url: str) -> dict:
    """Fetch a founder's personal LinkedIn profile via Apify harvestapi/linkedin-profile.

    Args:
        founder_url: Full LinkedIn personal profile URL (linkedin.com/in/...).

    Returns:
        dict with keys: name, headline, summary, current_company, funding_text.
        funding_text is a concatenation of headline + summary for keyword scanning.
        Returns empty dict on failure rather than raising, so a missing profile
        never blocks the main enrichment pipeline.
    """
    if MOCK_MODE:
        return {
            "name": "Fixture Founder",
            "headline": "Co-Founder & CEO at Acme | Previously Goldman Sachs",
            "summary": "Building AI tools for systematic investors.",
            "current_company": "Acme",
            "funding_text": "Co-Founder & CEO at Acme | Previously Goldman Sachs Building AI tools for systematic investors.",
        }

    # --- Real branch ---
    slug = _slugify(founder_url)
    cache_file = _cache_path(slug, "founder_profile")

    if cache_file.exists():
        return json.loads(cache_file.read_text())

    try:
        items = _apify_run_sync(
            "harvestapi/linkedin-profile",
            {"profileUrls": [founder_url]},
        )
    except Exception:
        # Never let a failed founder fetch crash the pipeline
        return {}

    raw = items[0] if items else {}

    headline = raw.get("headline") or raw.get("title") or ""
    summary = (
        raw.get("summary")
        or raw.get("about")
        or raw.get("description")
        or ""
    )
    current_company = ""
    positions = raw.get("positions") or raw.get("currentPositions") or []
    if isinstance(positions, list) and positions:
        current_company = (
            positions[0].get("companyName")
            or positions[0].get("company")
            or ""
        )

    result = {
        "name": raw.get("fullName") or raw.get("name") or "",
        "headline": headline,
        "summary": summary,
        "current_company": current_company,
        "funding_text": f"{headline} {summary}".strip(),
    }

    cache_file.write_text(json.dumps(result, indent=2))
    return result


def founder_profile_has_funding_signal(profile: dict) -> bool:
    """Return True if a founder profile's text contains funding-related keywords."""
    text = profile.get("funding_text", "")
    return bool(_FUNDING_KEYWORDS_RE.search(text))


def fetch_funding_web_search(
    company_name: str,
    search_fn=None,
) -> list[str]:
    """Search the web for funding information about a company.

    Args:
        company_name: Human-readable company name.
        search_fn: Callable[[str], list[str]] that accepts a query string and
            returns a list of text snippets. Injected by the caller so no
            specific search API is hardcoded here. Required in real mode;
            ignored in MOCK_MODE. Example compatible APIs: Tavily, SerpAPI,
            Brave Search.

    Returns:
        List of raw text snippets mentioning funding rounds, investors, or amounts.
        Synthesize.py is responsible for interpreting and cross-checking these.
    """
    slug = _slugify(company_name)
    cache_file = _cache_path(slug, "funding")

    if MOCK_MODE:
        if cache_file.exists():
            return json.loads(cache_file.read_text())
        cache_file.write_text(json.dumps(_MOCK_FUNDING_SNIPPETS, indent=2))
        return _MOCK_FUNDING_SNIPPETS

    # --- Real branch ---
    if cache_file.exists():
        return json.loads(cache_file.read_text())

    if search_fn is None:
        raise ValueError(
            "fetch_funding_web_search requires a search_fn callable in real mode. "
            "Pass search_fn=<your_search_callable> from the call site."
        )

    queries = [
        f"{company_name} funding raised",
        f"{company_name} series A B C seed",
    ]

    snippets: list[str] = []
    seen: set[str] = set()
    for query in queries:
        results = search_fn(query)
        for snippet in results:
            snippet = snippet.strip()
            if snippet and snippet not in seen:
                seen.add(snippet)
                snippets.append(snippet)

    cache_file.write_text(json.dumps(snippets, indent=2))
    return snippets
