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
import datetime
from pathlib import Path

from resolve_linkedin import resolve_company_linkedin
from tavily_client import tavily_search_results
from config import API_MOCK_MODE as MOCK_MODE

_BASE = Path(__file__).parent
_COMPANIES_CSV = _BASE / "companies.csv"
_FAILURES_CSV = _BASE / "linkedin_resolution_failures.csv"

# Tavily is now the default search backend for LinkedIn URL resolution.
# tavily_search_results returns raw result dicts with 'url' and 'content' keys,
# which resolve_company_linkedin's _extract_candidates() can scan for
# linkedin.com/company/* patterns.
# Returns [] automatically when TAVILY_API_KEY is unset or in mock mode.
_SEARCH_FN = lambda q: tavily_search_results(q, purpose="linkedin_url")

# Maximum number of blank rows to resolve per run. Set to None to process all.
TRIAL_LIMIT: int | None = 10


def main(limit: int | None = TRIAL_LIMIT) -> None:
    """Read companies.csv, resolve missing linkedin_company_url, write back in place.

    Args:
        limit: Max number of blank rows to resolve this run. Rows already
               populated are skipped and do not count toward the limit.
               Remaining blank rows beyond the limit are left unchanged.
               None means process all blank rows.
    """
    with _COMPANIES_CSV.open(newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    if "linkedin_company_url" not in fieldnames:
        print("ERROR: 'linkedin_company_url' column not found in companies.csv")
        return

    to_resolve = [r for r in rows if not r.get("linkedin_company_url", "").strip()]
    already_filled = len(rows) - len(to_resolve)
    will_process = len(to_resolve) if limit is None else min(limit, len(to_resolve))
    print(
        f"companies.csv: {len(rows)} rows total | "
        f"{already_filled} already have linkedin_company_url | "
        f"{len(to_resolve)} to resolve | processing {will_process} this run (TRIAL_LIMIT={limit})"
    )

    if not to_resolve:
        print("Nothing to do.")
        return

    resolved_count = 0
    failed_count = 0
    processed = 0
    failures: list[dict] = []

    for row in rows:
        if row.get("linkedin_company_url", "").strip():
            continue  # already populated

        if limit is not None and processed >= limit:
            break  # leave remaining blank rows untouched

        name = row.get("name", "").strip()
        domain = row.get("domain", "").strip()

        processed += 1
        try:
            url, rejection_reason = resolve_company_linkedin(
                company_name=name,
                domain=domain,
                search_fn=_SEARCH_FN,
            )
            row["linkedin_company_url"] = url
            status = f"→ {url}" if url else f"→ (not found: {rejection_reason})"
            print(f"  {name:40} {status}")
            if url:
                resolved_count += 1
            else:
                failed_count += 1
                failures.append({
                    "company_name": name,
                    "domain": domain,
                    "reason": rejection_reason,
                    "timestamp": datetime.datetime.utcnow().isoformat(),
                })
        except Exception as e:
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
            row["linkedin_company_url"] = ""
            failed_count += 1
            failures.append({
                "company_name": name,
                "domain": domain,
                "reason": f"{type(e).__name__}: {e}",
                "timestamp": datetime.datetime.utcnow().isoformat(),
            })

    # Write back — preserves all columns and row order
    with _COMPANIES_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    # Append failures to linkedin_resolution_failures.csv
    if failures:
        failures_exist = _FAILURES_CSV.exists()
        with _FAILURES_CSV.open("a", newline="") as f:
            writer = csv.DictWriter(
                f, fieldnames=["company_name", "domain", "reason", "timestamp"]
            )
            if not failures_exist:
                writer.writeheader()
            writer.writerows(failures)
        print(f"  Logged {len(failures)} failure(s) → {_FAILURES_CSV.name}")

    print(
        f"\nDone. Resolved: {resolved_count} | Not found: {failed_count} | "
        f"CSV updated in place."
    )


if __name__ == "__main__":
    main()
