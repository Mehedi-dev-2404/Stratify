"""
synthesize.py — Synthesizes raw fetched data into a structured company record.
In MOCK_MODE=True returns fixture enriched data.
In MOCK_MODE=False calls Claude claude-sonnet-4-6 via the Anthropic SDK (Round 3).
"""

from __future__ import annotations

from datetime import date

from config import CATEGORIES, MOCK_MODE

# ---------------------------------------------------------------------------
# Prompt template (used in Round 3 when MOCK_MODE=False)
# ---------------------------------------------------------------------------

SYNTHESIS_PROMPT = """
You are a financial research analyst. Given raw data about a company, produce a
structured JSON summary. Be factual, concise, and never hallucinate.

## Input data
### LinkedIn profile
{linkedin_profile}

### Recent LinkedIn posts
{linkedin_posts}

### Website text
{website_text}

### Web search funding snippets
{funding_snippets}

## Instructions

1. website_summary (2-3 sentences): Summarise what the company does based on the
   website text. Focus on product, customer, and differentiator.

2. linkedin_summary (2-3 sentences): Summarise the company's recent activity and
   positioning based on the LinkedIn posts and profile description.

3. category: Classify the company into exactly one of the following categories.
   Use the label verbatim — do not invent new categories.
   Categories: {categories}

4. funding_status: One of "Funded", "Bootstrapped", "Public", "Unknown".
   Cross-check the linkedin_profile funding_status field against the web search
   snippets. If both agree, use that value. If they conflict, note the conflict in
   funding_source_note and use the more specific/sourced value.

5. funding_amount: The most recent total or round amount (e.g. "$21M Series A").
   Leave blank ("") if unverified by at least one source. Never guess.

6. funding_source_note: One sentence explaining the sourcing of the funding data,
   or noting any conflict between LinkedIn and web search results. Leave blank if
   both sources agree cleanly.

## Output format
Return ONLY valid JSON with these exact keys — no markdown fences, no extra text:
{{
  "website_summary": "...",
  "linkedin_summary": "...",
  "category": "...",
  "funding_status": "...",
  "funding_amount": "...",
  "funding_source_note": "..."
}}
""".strip()

# ---------------------------------------------------------------------------
# Mock fixture output
# ---------------------------------------------------------------------------

_MOCK_SYNTHESIS_RESULT = {
    "website_summary": (
        "The company builds AI-powered investment tools for institutional asset "
        "managers, leveraging alternative data — including satellite imagery, "
        "transaction flows, and NLP on earnings calls — to generate alpha signals. "
        "Founded by alumni of Renaissance Technologies, Two Sigma, and Google "
        "DeepMind, the platform compresses multi-week research cycles to hours."
    ),
    "linkedin_summary": (
        "The company actively publishes content on ML model performance, "
        "quantitative research methodology, and open roles for ML engineers. "
        "Recent posts highlight Sharpe ratio improvements from ensemble models "
        "and participation in quant finance conferences, signalling an active "
        "research culture and growth phase."
    ),
    "category": "AI Trading/Alpha Generation",
    "funding_status": "Funded",
    "funding_amount": "$21M Series A",
    "funding_source_note": (
        "LinkedIn profile and web search snippets both report $21M raised; "
        "TechCrunch (March 2019) confirms Series A led by Union Square Ventures."
    ),
}

# ---------------------------------------------------------------------------
# Public synthesis function
# ---------------------------------------------------------------------------


def synthesize_company(raw_data: dict) -> dict:
    """Synthesize raw fetched data into a structured company record.

    Args:
        raw_data: Dict with keys:
            - name (str)
            - domain (str)
            - linkedin_profile (dict)   — from fetch_company_linkedin()
            - linkedin_posts (list[str]) — from fetch_company_posts()
            - website_text (str)        — from fetch_website_text()
            - funding_snippets (list[str]) — from fetch_funding_web_search()

    Returns:
        dict with keys: name, domain, website_summary, linkedin_summary,
        category, funding_status, funding_amount, funding_source_note,
        last_updated.
    """
    if not MOCK_MODE:
        # Round 3: wire real Claude call here
        # prompt = SYNTHESIS_PROMPT.format(
        #     linkedin_profile=json.dumps(raw_data.get("linkedin_profile", {}), indent=2),
        #     linkedin_posts="\n".join(raw_data.get("linkedin_posts", [])),
        #     website_text=raw_data.get("website_text", ""),
        #     funding_snippets="\n".join(raw_data.get("funding_snippets", [])),
        #     categories=", ".join(CATEGORIES),
        # )
        # import anthropic
        # client = anthropic.Anthropic()
        # message = client.messages.create(
        #     model="claude-sonnet-4-6",
        #     max_tokens=1024,
        #     messages=[{"role": "user", "content": prompt}],
        # )
        # result = json.loads(message.content[0].text)
        raise NotImplementedError("real API call not yet wired")

    # Validate mock category is in allowed list (guards against fixture drift)
    assert _MOCK_SYNTHESIS_RESULT["category"] in CATEGORIES, (
        f"Mock category '{_MOCK_SYNTHESIS_RESULT['category']}' not in CATEGORIES"
    )

    return {
        "name": raw_data.get("name", ""),
        "domain": raw_data.get("domain", ""),
        **_MOCK_SYNTHESIS_RESULT,
        "last_updated": date.today().isoformat(),
    }
