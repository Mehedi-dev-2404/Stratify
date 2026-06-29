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

3. category: Identify which team or function within a financial institution is
   the PRIMARY USER (buyer and daily operator) of this product, then classify
   into exactly one of the following categories. Use the label verbatim — do
   not invent new categories.
   Categories: {categories}

   Guidance for picking the primary-user category:
   - "Primary user" means the team most likely to be the actual buyer and
     heaviest daily user — not just any team that might see the output.
   - If the product could serve multiple teams, pick the team that would be the
     actual buyer and primary daily operator.
   - Examples: stress-testing / scenario analysis / credit risk → Risk Teams;
     KYC / AML / regulatory reporting / trade surveillance → Compliance Teams;
     alpha signals / trade execution / portfolio construction → Portfolio Managers / Traders;
     earnings research / document search / due diligence → Research Analysts;
     data pipelines / alternative data / quant model infrastructure → Quant / Data Engineering;
     LP reporting / investor communications / capital raising → IR / Investor Relations;
     bookkeeping / accounting / back-office finance workflows → Finance / Accounting.

4. primary_user_summary (1 sentence): Who at a financial institution is the
   primary daily user of this product? Be specific about the role and context —
   write for a salesperson. No filler. Example: "Risk managers at hedge funds
   use this to stress-test portfolios against synthetic market scenarios not
   covered by historical data."

5. funding_status: One of "Funded", "Bootstrapped", "Public" — or empty string
   "" if funding cannot be verified from the provided sources. Do NOT use
   "Unknown", "N/A", or any other placeholder.

6. funding_amount: The MOST RECENT round amount (e.g. "$21M Series A").
   Return empty string "" if unverified. Never guess. Never return "Unknown",
   "N/A", or any placeholder.

   Date-precedence rule (IMPORTANT): LinkedIn profile funding data includes an
   announcedOn date (year embedded in the funding_amount string). Web search
   snippets may reference a MORE RECENT round. When the two sources describe
   DIFFERENT rounds or dates, always use whichever cites the LATER date/year.
   Do not assume LinkedIn is authoritative — it can be stale by months or years.

7. funding_confidence: Exactly one of these four values — no other values allowed:
   - "Verified"    — a specific dollar/euro amount AND a round type (e.g. "Series A",
                     "Seed", "IPO") are EXPLICITLY stated in at least one source.
                     Vague language like "raised significant funding" does NOT qualify.
   - "Partial"     — round type is known but amount is missing, OR amount is known
                     but round type is missing.
   - "Unverified"  — no source contained explicit funding information. In this case
                     funding_status AND funding_amount MUST both be "".
   - "Conflicting" — sources described different rounds or dates; the most recent
                     value is used in funding_status and funding_amount.

8. funding_source_url: The URL of the specific web search snippet from which you
   derived funding_amount (or confirmed the most recent round).
   - If funding_amount came from a numbered web snippet above, return that
     snippet's exact URL.
   - If funding_confidence is "Conflicting" and the web search snippet had the
     more recent date (per the date-precedence rule), return that snippet's URL.
   - If no web search snippets were available, or funding came from LinkedIn
     profile data only, return "".
   - Return "" if funding_amount is empty string.

9. contradiction_notes: Flag a genuine factual conflict between the website and
   LinkedIn posts about the SAME specific claim — e.g. target customer, product
   type, pricing model, or deployment model — stated differently by each source.
   Rules (be strict — false positives are worse than missed ones):
   - Default: empty string "". Most companies will have no entry here.
   - Website copy and LinkedIn posts naturally cover different topics and use
     different tone. Do NOT flag: absence-of-mention, differing emphasis,
     marketing language vs. casual language, or general vs. specific framing.
     These are normal, not contradictions.
   - Only flag when the SAME factual claim is explicitly made in BOTH sources
     and the two versions are mutually incompatible.
   - If either website_summary or linkedin_summary is empty string (no data
     available), set contradiction_notes to "" — no comparison is possible.
   - If triggered, output exactly one sentence in this form:
     "Website: [claim]. LinkedIn: [conflicting claim]."

## Output format
Return ONLY valid JSON with these exact keys — no markdown fences, no extra text:
{{
  "website_summary": "...",
  "linkedin_summary": "...",
  "category": "...",
  "primary_user_summary": "...",
  "funding_status": "...",
  "funding_amount": "...",
  "funding_confidence": "...",
  "funding_source_url": "...",
  "contradiction_notes": "..."
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
    "category": "Portfolio Managers / Traders",
    "primary_user_summary": (
        "Portfolio managers and quant researchers at institutional asset managers "
        "use this platform to generate alpha signals from alternative data sources "
        "including satellite imagery, transaction flows, and earnings call NLP."
    ),
    "funding_status": "Funded",
    "funding_amount": "$21M Series A",
    "funding_confidence": "Verified",
    "funding_source_url": "https://techcrunch.com/2019/03/numerai-series-a-21-million",
    "contradiction_notes": "",
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
            - linkedin_profile (dict)       — from fetch_company_linkedin(), or {}
            - linkedin_posts (list[str])    — from fetch_company_posts(), or []
            - website_text (str)            — from fetch_website_text()
            - funding_snippets (list[dict]) — from fetch_funding_web_search(), or []
              Each dict has "url" and "content" keys.

    Returns:
        dict with keys: name, domain, website_summary, linkedin_summary,
        category, funding_status, funding_amount, funding_confidence,
        funding_source_url, last_updated.
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

    # Normalize: coerce any plain strings to {url, content} dicts so the list
    # is always uniform even when mixed (e.g. founder-signal strings + Tavily dicts)
    normalized_snippets: list[dict] = []
    for item in funding_snippets:
        if isinstance(item, str):
            normalized_snippets.append({"url": "", "content": item})
        elif isinstance(item, dict):
            normalized_snippets.append(
                {"url": item.get("url", ""), "content": item.get("content", "")}
            )
    funding_snippets = normalized_snippets

    if funding_snippets:
        # Format as numbered list with explicit source URLs so Claude can cite them
        formatted_lines = []
        for i, s in enumerate(funding_snippets, 1):
            url = s.get("url", "")
            content = s.get("content", "")
            formatted_lines.append(f"[{i}] URL: {url}\nContent: {content}")
        funding_snippets_text = "\n\n".join(formatted_lines)
    else:
        funding_snippets_text = "(No funding data available)"

    prompt = SYNTHESIS_PROMPT.format(
        linkedin_profile=linkedin_profile_text,
        linkedin_posts=linkedin_posts_text,
        website_text=website_text or "(No website text available)",
        funding_snippets=funding_snippets_text,
        categories=", ".join(CATEGORIES),
    )

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        temperature=0,
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

    # Guard: "Partial" only applies when status is "Funded" but round/amount
    # details are incomplete. Non-Funded status (Bootstrapped, empty) with no
    # amount has nothing to be partially known — downgrade to "Unverified".
    if result["funding_confidence"] == "Partial":
        if result.get("funding_status", "") not in ("Funded", "Public"):
            result["funding_confidence"] = "Unverified"

    # Guard: "Unverified" must have blank funding fields
    if result["funding_confidence"] == "Unverified":
        result["funding_status"] = ""
        result["funding_amount"] = ""

    # Guard: funding_source_url must be a string (Claude may omit the key)
    funding_source_url = result.get("funding_source_url", "") or ""
    if not isinstance(funding_source_url, str):
        funding_source_url = ""
    # If funding is unverified, there is no valid source URL
    if result["funding_confidence"] == "Unverified":
        funding_source_url = ""

    # Guard: contradiction_notes must be a string; blank if either summary is empty
    contradiction_notes = result.get("contradiction_notes", "") or ""
    if not isinstance(contradiction_notes, str):
        contradiction_notes = ""
    if not result.get("website_summary", "") or not result.get("linkedin_summary", ""):
        contradiction_notes = ""

    return {
        "name": raw_data.get("name", ""),
        "domain": raw_data.get("domain", ""),
        "website_summary": result.get("website_summary", ""),
        "linkedin_summary": result.get("linkedin_summary", ""),
        "contradiction_notes": contradiction_notes,
        "category": result["category"],
        "primary_user_summary": result.get("primary_user_summary", ""),
        "funding_status": result.get("funding_status", ""),
        "funding_amount": result.get("funding_amount", ""),
        "funding_confidence": result["funding_confidence"],
        "funding_source_url": funding_source_url,
        "last_updated": date.today().isoformat(),
    }
