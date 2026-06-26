"""
merge_clay_results.py — Merges Clay-sourced LinkedIn URLs into companies.csv.

Step 2 of the hybrid LinkedIn resolver flow:

  Step 1: python update_companies_csv_linkedin.py
          → our Tavily-based resolver runs on all blank rows
          → clay_pending.csv emitted with companies still blank

  Step 2: User runs Clay on clay_pending.csv (via MCP tool in chat)
          → saves results as clay_results.csv

  Step 3: python merge_clay_results.py
          → validates Clay results with same name/domain rules as our_resolver
          → writes accepted URLs into companies.csv
          → appends to linkedin_resolution_source.csv (source="clay_fallback")
          → logs rejections to linkedin_resolution_failures.csv

clay_results.csv expected columns:
  company_name, domain, clay_linkedin_url
  (all other columns are ignored)

Clay is a second source, not exempt from validation. The same bidirectional
name/slug overlap and domain-in-context rules apply.

Run:
    python merge_clay_results.py
"""

from __future__ import annotations

import csv
import datetime
from pathlib import Path

from resolve_linkedin import validate_candidate_url
from update_companies_csv_linkedin import (
    _RESOLUTION_SOURCE_CSV,
    _RESOLUTION_SOURCE_FIELDNAMES,
)

_BASE = Path(__file__).parent
_COMPANIES_CSV = _BASE / "companies.csv"
_CLAY_RESULTS_CSV = _BASE / "clay_results.csv"
_FAILURES_CSV = _BASE / "linkedin_resolution_failures.csv"


def main() -> None:
    if not _CLAY_RESULTS_CSV.exists():
        print(
            f"ERROR: {_CLAY_RESULTS_CSV.name} not found.\n"
            "Populate it with Clay results before running this script.\n"
            "Expected columns: company_name, domain, clay_linkedin_url"
        )
        return

    with _CLAY_RESULTS_CSV.open(newline="") as f:
        clay_rows = list(csv.DictReader(f))
    print(f"clay_results.csv: {len(clay_rows)} row(s) to process\n")

    with _COMPANIES_CSV.open(newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        companies = list(reader)

    # Index by lowercase name for O(1) lookup
    company_index: dict[str, dict] = {
        r.get("name", "").strip().lower(): r for r in companies
    }

    accepted: list[dict] = []
    rejected_failures: list[dict] = []
    merged_count = 0

    for clay_row in clay_rows:
        name = clay_row.get("company_name", "").strip()
        domain = clay_row.get("domain", "").strip()
        clay_url = clay_row.get("clay_linkedin_url", "").strip()
        timestamp = datetime.datetime.utcnow().isoformat()

        if not name or not clay_url:
            print(f"  [SKIP]  empty name or URL — row: {dict(clay_row)}")
            continue

        url, rejection_reason = validate_candidate_url(
            company_name=name,
            domain=domain,
            url=clay_url,
        )

        if url:
            matched = company_index.get(name.lower())
            if matched is None:
                print(f"  [WARN]  {name:40} not found in companies.csv — skipping write")
            elif matched.get("linkedin_company_url", "").strip():
                print(f"  [SKIP]  {name:40} already has a URL — keeping existing")
            else:
                matched["linkedin_company_url"] = url
                merged_count += 1
                print(f"  [OK]    {name:40} → {url}")

            accepted.append({
                "company_name": name,
                "domain": domain,
                "linkedin_company_url": url,
                "source": "clay_fallback",
                "timestamp": timestamp,
            })
        else:
            print(f"  [FAIL]  {name:40} — {rejection_reason}")
            rejected_failures.append({
                "company_name": name,
                "domain": domain,
                "reason": f"clay_fallback rejected: {rejection_reason}",
                "timestamp": timestamp,
            })

    # Write back companies.csv
    with _COMPANIES_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(companies)
    print(f"\ncompanies.csv updated: {merged_count} new URL(s) written.")

    # Append accepted URLs to linkedin_resolution_source.csv
    if accepted:
        source_exists = _RESOLUTION_SOURCE_CSV.exists()
        with _RESOLUTION_SOURCE_CSV.open("a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=_RESOLUTION_SOURCE_FIELDNAMES)
            if not source_exists:
                writer.writeheader()
            writer.writerows(accepted)
        print(
            f"Logged {len(accepted)} clay_fallback hit(s) → "
            f"{_RESOLUTION_SOURCE_CSV.name}"
        )

    # Append rejections to linkedin_resolution_failures.csv
    if rejected_failures:
        failures_exist = _FAILURES_CSV.exists()
        with _FAILURES_CSV.open("a", newline="") as f:
            writer = csv.DictWriter(
                f, fieldnames=["company_name", "domain", "reason", "timestamp"]
            )
            if not failures_exist:
                writer.writeheader()
            writer.writerows(rejected_failures)
        print(
            f"Logged {len(rejected_failures)} Clay rejection(s) → "
            f"{_FAILURES_CSV.name}"
        )

    print(
        f"\nDone. Clay accepted: {len(accepted)} | "
        f"Clay rejected: {len(rejected_failures)} | "
        f"Written to companies.csv: {merged_count}"
    )


if __name__ == "__main__":
    main()
