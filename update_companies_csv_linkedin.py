"""
update_companies_csv_linkedin.py — Fills in blank linkedin_company_url cells
in companies.csv by calling resolve_company_linkedin() for each row.

Run:
    python update_companies_csv_linkedin.py

Wire a real search_fn before running with MOCK_MODE=False — see the
SEARCH_FN section below.
"""

from __future__ import annotations

import csv
from pathlib import Path

from resolve_linkedin import resolve_company_linkedin
from tavily_client import tavily_search_results
from config import API_MOCK_MODE as MOCK_MODE

_BASE = Path(__file__).parent
_COMPANIES_CSV = _BASE / "companies.csv"

# Tavily is now the default search backend for LinkedIn URL resolution.
# tavily_search_results returns raw result dicts with 'url' and 'content' keys,
# which resolve_company_linkedin's _extract_candidates() can scan for
# linkedin.com/company/* patterns.
# Returns [] automatically when TAVILY_API_KEY is unset or in mock mode.
_SEARCH_FN = lambda q: tavily_search_results(q, purpose="linkedin_url")


def main() -> None:
    """Read companies.csv, resolve missing linkedin_company_url, write back in place."""
    with _COMPANIES_CSV.open(newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    if "linkedin_company_url" not in fieldnames:
        print("ERROR: 'linkedin_company_url' column not found in companies.csv")
        return

    to_resolve = [r for r in rows if not r.get("linkedin_company_url", "").strip()]
    already_filled = len(rows) - len(to_resolve)
    print(
        f"companies.csv: {len(rows)} rows total | "
        f"{already_filled} already have linkedin_company_url | "
        f"{len(to_resolve)} to resolve"
    )

    if not to_resolve:
        print("Nothing to do.")
        return

    resolved_count = 0
    failed_count = 0

    for row in rows:
        if row.get("linkedin_company_url", "").strip():
            continue  # already populated

        name = row.get("name", "").strip()
        domain = row.get("domain", "").strip()

        try:
            url = resolve_company_linkedin(
                company_name=name,
                domain=domain,
                search_fn=_SEARCH_FN,
            )
            row["linkedin_company_url"] = url
            status = f"→ {url}" if url else "→ (not found)"
            print(f"  {name:40} {status}")
            if url:
                resolved_count += 1
            else:
                failed_count += 1
        except Exception as e:
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
            row["linkedin_company_url"] = ""
            failed_count += 1

    # Write back — preserves all columns and row order
    with _COMPANIES_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(
        f"\nDone. Resolved: {resolved_count} | Not found: {failed_count} | "
        f"CSV updated in place."
    )


if __name__ == "__main__":
    main()
