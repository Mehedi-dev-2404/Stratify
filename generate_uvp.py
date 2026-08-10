"""
generate_uvp.py — Generates a unique_value_prop (UVP) for every company,
comparing each company against its CATEGORY PEERS rather than describing it in
isolation (per Dr. Onur's spec: "here are 20 companies in category 1, out of
these 3 have unique propositions").

Approach:
  - Group all research_db companies by `category`.
  - For each category, send every company's name + website_summary +
    linkedin_product_summary to Claude in ONE call, so the model can
    differentiate them relative to each other.
  - Claude returns, per company, a one-sentence UVP that states what makes it
    distinct within its category (or notes it's a "me-too" if it isn't).
  - Write the result into each record's enriched["unique_value_prop"].

Re-runnable: safe to run monthly. Reads/writes research_db only; the sheet is
refreshed separately (add_uvp_to_sheet in the compile step).

Usage:
    python3 generate_uvp.py
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

import anthropic

from config import ANTHROPIC_API_KEY
from compile_sheet import EXCLUDED_COMPANIES

_COMPANIES_DIR = Path(__file__).parent / "research_db" / "companies"

_PROMPT = """You are a competitive-intelligence analyst covering AI tools for financial institutions.

Below are companies that ALL serve the same buyer category: "{category}".
Your job: for EACH company, write ONE sentence (max 25 words) stating its UNIQUE
value proposition RELATIVE TO THE OTHER COMPANIES IN THIS LIST — what it does
that the others in this category do not, or does distinctly better/differently.

Rules:
- Compare within this list. Do NOT describe the company in isolation.
- Be concrete and specific (a real differentiator: a data source, a workflow, a
  user, a modality). No filler like "leverages cutting-edge AI".
- If a company has NO meaningful differentiator versus its peers here, say so
  plainly, e.g. "Undifferentiated — overlaps heavily with [peer(s)] on [what]."
- Base it ONLY on the provided text. Never invent facts.

Companies:
{companies_block}

Return ONLY valid JSON (no markdown fences): an object mapping each company's
exact name to its one-sentence UVP string. Include every company.
"""


def _company_line(idx: int, e: dict) -> str:
    parts = [f"{idx}. {e.get('name','')}"]
    ws = (e.get("website_summary") or "").strip()
    ps = (e.get("linkedin_product_summary") or "").strip()
    if ws:
        parts.append(f"   website: {ws}")
    if ps:
        parts.append(f"   product posts: {ps}")
    return "\n".join(parts)


def _strip_fences(text: str) -> str:
    text = re.sub(r"^```(?:json)?\s*\n?", "", text.strip())
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def main() -> None:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    # Load records, group by category
    records: list[tuple[Path, dict]] = []
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for p in sorted(_COMPANIES_DIR.glob("*.json")):
        if p.name == ".gitkeep":
            continue
        d = json.loads(p.read_text())
        if d.get("enriched", {}).get("name", "") in EXCLUDED_COMPANIES:
            continue  # removed companies are not part of the deliverable / peer set
        records.append((p, d))
        by_cat[d.get("enriched", {}).get("category", "Other")].append(d["enriched"])

    print(f"{len(records)} companies across {len(by_cat)} categories.")

    uvp_map: dict[str, str] = {}
    for category, companies in sorted(by_cat.items(), key=lambda x: -len(x[1])):
        block = "\n".join(_company_line(i, e) for i, e in enumerate(companies, 1))
        prompt = _PROMPT.format(category=category, companies_block=block)
        print(f"  [{category}] {len(companies)} companies → calling Claude...")
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8192,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        try:
            result = json.loads(_strip_fences(msg.content[0].text))
        except json.JSONDecodeError as exc:
            print(f"    [WARN] {category}: could not parse JSON ({exc}); skipping")
            continue
        got = 0
        for e in companies:
            uvp = result.get(e.get("name", ""), "")
            if uvp:
                uvp_map[e["name"]] = uvp.strip()
                got += 1
        print(f"    got UVPs for {got}/{len(companies)}")

    # Write back into records
    written = 0
    for p, d in records:
        name = d.get("enriched", {}).get("name", "")
        if name in uvp_map:
            d["enriched"]["unique_value_prop"] = uvp_map[name]
            p.write_text(json.dumps(d, indent=2, ensure_ascii=False))
            written += 1

    print(f"Wrote unique_value_prop into {written} records.")


if __name__ == "__main__":
    main()
