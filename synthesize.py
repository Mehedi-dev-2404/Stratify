"""
synthesize.py — Synthesizes raw fetched data into a structured company record.
In API_MOCK_MODE=True returns fixture enriched data.
In API_MOCK_MODE=False calls Claude claude-sonnet-4-6 via the Anthropic SDK.
"""

from __future__ import annotations

import json
import re
from datetime import date

import anthropic

from config import ANTHROPIC_API_KEY, CATEGORIES, API_MOCK_MODE as MOCK_MODE

# ---------------------------------------------------------------------------
# Prompt template
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

1. website_summary (max 2 sentences, plain language): What does this company sell
   or build, and who is it for? Write for a salesperson skimming a list — no
   jargon, no filler phrases like "leverages cutting-edge AI". Be specific and
   concrete. If no website text is available, set to empty string "".

2. linkedin_summary (max 2 sentences, plain language): What has this company been
   doing lately — hiring, launching, announcing? Same plain-language rule as
   above. If the LinkedIn posts section says no data is available, set
   linkedin_summary to empty string "" — do NOT infer from the website or
   company name.

3. category: Classify the company into exactly one of the following categories.
   Use the label verbatim — do not invent new categories.
   Categories: {categories}

4. funding_status: One of "Funded", "Bootstrapped", "Public" — or empty string
   "" if funding cannot be verified from the provided sources. Do NOT use
   "Unknown", "N/A", or any other placeholder.

5. funding_amount: The MOST RECENT round amount (e.g. "$21M Series A").
   Return empty string "" if unverified. Never guess. Never return "Unknown",
   "N/A", or any placeholder.

   Date-precedence rule (IMPORTANT): LinkedIn profile funding data includes an
   announcedOn date (year embedded in the funding_amount string). Web search
   snippets may reference a MORE RECENT round. When the two sources describe
   DIFFERENT rounds or dates, always use whichever cites the LATER date/year.
   Do not assume LinkedIn is authoritative — it can be stale by months or years.

6. funding_confidence: Exactly one of these four values — no other values allowed:
   - "Verified"    — a specific dollar/euro amount AND a round type (e.g. "Series A",
                     "Seed", "IPO") are EXPLICITLY stated in at least one source.
                     Vague language like "raised significant funding" does NOT qualify.
   - "Partial"     — round type is known but amount is missing, OR amount is known
                     but round type is missing.
   - "Unverified"  — no source contained explicit funding information. In this case
                     funding_status AND funding_amount MUST both be "".
   - "Conflicting" — sources described different rounds or dates; the most recent
                     value is used in funding_status and funding_amount.

## Output format
Return ONLY valid JSON with these exact keys — no markdown fences, no extra text:
{{
  "website_summary": "...",
  "linkedin_summary": "...",
  "category": "...",
  "funding_status": "...",
  "funding_amount": "...",
  "funding_confidence": "..."
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
    "funding_confidence": "Verified",
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
            - linkedin_profile (dict)      — from fetch_company_linkedin(), or {}
            - linkedin_posts (list[str])   — from fetch_company_posts(), or []
            - website_text (str)           — from fetch_website_text()
            - funding_snippets (list[str]) — from fetch_funding_web_search(), or []

    Returns:
        dict with keys: name, domain, website_summary, linkedin_summary,
        category, funding_status, funding_amount, funding_confidence,
        last_updated.
    """
    if MOCK_MODE:
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

    # --- Real branch: call Claude claude-sonnet-4-6 ---
    posts = raw_data.get("linkedin_posts", [])
    profile = raw_data.get("linkedin_profile") or {}

    linkedin_posts_text = (
        "\n\n".join(posts)
        if posts
        else '(No LinkedIn posts available — set linkedin_summary to empty string "")'
    )
    linkedin_profile_text = (
        json.dumps(profile, indent=2)
        if profile
        else "(No LinkedIn profile available)"
    )

    website_text = raw_data.get("website_text", "").strip()
    funding_snippets = raw_data.get("funding_snippets", [])

    prompt = SYNTHESIS_PROMPT.format(
        linkedin_profile=linkedin_profile_text,
        linkedin_posts=linkedin_posts_text,
        website_text=website_text or "(No website text available)",
        funding_snippets=(
            "\n\n".join(funding_snippets)
            if funding_snippets
            else "(No funding data available)"
        ),
        categories=", ".join(CATEGORIES),
    )

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    raw_text = message.content[0].text.strip()

    # Strip ```json ... ``` fences if present
    raw_text = re.sub(r"^```(?:json)?\s*\n?", "", raw_text)
    raw_text = re.sub(r"\n?```\s*$", "", raw_text)
    raw_text = raw_text.strip()

    result = json.loads(raw_text)

    # Guard: truncate website_summary and linkedin_summary to max 2 sentences
    for field in ("website_summary", "linkedin_summary"):
        text = result.get(field, "")
        if text:
            # Split on sentence-ending punctuation; keep at most 2 sentences
            parts = re.split(r"(?<=[.!?])\s+", text.strip())
            if len(parts) > 2:
                result[field] = " ".join(parts[:2])

    # Guard: invalid category → "Other"
    if result.get("category") not in CATEGORIES:
        result["category"] = "Other"

    # Guard: coerce placeholder funding values to empty string
    _FUNDING_PLACEHOLDERS = {"unknown", "n/a", "na", "tbd", "none", "-"}
    if result.get("funding_status", "").strip().lower() in _FUNDING_PLACEHOLDERS:
        result["funding_status"] = ""
    if result.get("funding_amount", "").strip().lower() in _FUNDING_PLACEHOLDERS:
        result["funding_amount"] = ""

    # Guard: invalid confidence value → "Unverified"
    _VALID_CONFIDENCE = {"Verified", "Partial", "Unverified", "Conflicting"}
    if result.get("funding_confidence") not in _VALID_CONFIDENCE:
        result["funding_confidence"] = "Unverified"

    # Guard: "Verified" requires a number or currency symbol in funding_amount
    # — downgrade to "Partial" if the amount field lacks that signal
    if result["funding_confidence"] == "Verified":
        amount = result.get("funding_amount", "")
        if not re.search(r"[\d$£€¥₹]", amount):
            result["funding_confidence"] = "Partial"

    # Guard: "Unverified" must have blank funding fields
    if result["funding_confidence"] == "Unverified":
        result["funding_status"] = ""
        result["funding_amount"] = ""

    return {
        "name": raw_data.get("name", ""),
        "domain": raw_data.get("domain", ""),
        "website_summary": result.get("website_summary", ""),
        "linkedin_summary": result.get("linkedin_summary", ""),
        "category": result["category"],
        "funding_status": result.get("funding_status", ""),
        "funding_amount": result.get("funding_amount", ""),
        "funding_confidence": result["funding_confidence"],
        "last_updated": date.today().isoformat(),
    }
