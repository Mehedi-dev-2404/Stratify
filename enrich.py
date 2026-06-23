"""
enrich.py — Orchestrator for the Stratify enrichment pipeline.
Reads companies.csv, enriches each company in batches, and persists results.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path

# ---------------------------------------------------------------------------
# STUB COMPANY LISTS
# WIRE-IN POINT: replace _SOURCE_A / _SOURCE_B with real list-parsing from
# alexizydorczyk.com once that scraper is built. merge_sources() below reads
# only these two lists for now.
# ---------------------------------------------------------------------------
_SOURCE_A = [
    {"name": "Acme Corp", "domain": "acme.com", "linkedin_url": "https://linkedin.com/company/acme-corp"},
    {"name": "Bright Labs", "domain": "brightlabs.io", "linkedin_url": "https://linkedin.com/company/bright-labs"},
    {"name": "Nexus AI", "domain": "nexusai.co", "linkedin_url": "https://linkedin.com/company/nexus-ai"},
]

_SOURCE_B = [
    {"name": "Nexus AI", "domain": "nexusai.co", "linkedin_url": "https://linkedin.com/company/nexus-ai"},  # duplicate — deduped by domain
    {"name": "Coral Systems", "domain": "coralsystems.com", "linkedin_url": "https://linkedin.com/company/coral-systems"},
    {"name": "Vanta Health", "domain": "vantahealth.com", "linkedin_url": "https://linkedin.com/company/vanta-health"},
    {"name": "Drift Analytics", "domain": "driftanalytics.io", "linkedin_url": "https://linkedin.com/company/drift-analytics"},
]
# ---------------------------------------------------------------------------

_BASE = Path(__file__).parent
_COMPANIES_CSV = _BASE / "companies.csv"
_FAILURES_CSV = _BASE / "failures.csv"

_COMPANIES_FIELDNAMES = ["name", "domain", "linkedin_url", "source_list"]
_FAILURES_FIELDNAMES = ["name", "domain", "error"]

BATCH_SIZE = 2  # small for mock testing; increase for production runs


def _domain_to_slug(domain: str) -> str:
    return domain.replace(".", "-").replace("/", "-").strip("-")


def merge_sources() -> None:
    """Merge stub company lists, dedupe by domain, write to companies.csv.

    # WIRE-IN POINT: replace _SOURCE_A / _SOURCE_B at top of file with real
    # list-parsing from alexizydorczyk.com once that scraper is available.
    """
    seen: set[str] = set()
    merged: list[dict] = []
    for company in _SOURCE_A + _SOURCE_B:
        domain = company["domain"]
        if domain not in seen:
            seen.add(domain)
            merged.append({
                "name": company["name"],
                "domain": domain,
                "linkedin_url": company.get("linkedin_url", ""),
                "source_list": "stub",
            })

    with _COMPANIES_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_COMPANIES_FIELDNAMES)
        writer.writeheader()
        writer.writerows(merged)

    print(f"merge_sources: wrote {len(merged)} companies to {_COMPANIES_CSV}")


def main() -> None:
    """Load companies.csv and run the enrichment pipeline in batches of BATCH_SIZE.

    For each company:
      1. check_existing() — skip if fresh record found
      2. Fetch raw data from all 4 sources
      3. synthesize_company()
      4. save_company_record()
      5. update_index_csv()
      6. append_to_sheet()

    After each batch, pauses for user confirmation before continuing.
    Per-company exceptions are caught, logged to failures.csv, and skipped.
    """
    from fetchers import (
        fetch_company_linkedin,
        fetch_company_posts,
        fetch_funding_web_search,
        fetch_website_text,
    )
    from sheet_sync import (
        append_to_sheet,
        check_existing,
        save_company_record,
        update_index_csv,
    )
    from synthesize import synthesize_company

    # Read companies; auto-populate from stub lists if no data rows present
    with _COMPANIES_CSV.open(newline="") as f:
        companies = list(csv.DictReader(f))

    if not companies:
        print("companies.csv has no data rows — running merge_sources() first.")
        merge_sources()
        with _COMPANIES_CSV.open(newline="") as f:
            companies = list(csv.DictReader(f))

    if not companies:
        print("No companies found after merge_sources(). Aborting.")
        return

    total_batches = math.ceil(len(companies) / BATCH_SIZE)
    print(f"Processing {len(companies)} companies in {total_batches} batch(es) of {BATCH_SIZE}.")

    failures: list[dict] = []

    for batch_num in range(total_batches):
        batch = companies[batch_num * BATCH_SIZE : (batch_num + 1) * BATCH_SIZE]
        print(f"\n--- Batch {batch_num + 1}/{total_batches} ---")

        for company in batch:
            name = company.get("name", "")
            domain = company.get("domain", "")
            linkedin_url = company.get("linkedin_url", "")
            slug = _domain_to_slug(domain)

            try:
                existing = check_existing(slug)
                if existing:
                    print(f"  [SKIP]  {name} ({domain}) — record <30 days old")
                    continue

                print(f"  [FETCH] {name} ({domain})")
                linkedin_profile = fetch_company_linkedin(name, linkedin_url)
                linkedin_posts = fetch_company_posts(linkedin_url)
                website_text = fetch_website_text(domain)
                funding_snippets = fetch_funding_web_search(name)

                raw = {
                    "name": name,
                    "domain": domain,
                    "linkedin_url": linkedin_url,
                    "linkedin_profile": linkedin_profile,
                    "linkedin_posts": linkedin_posts,
                    "website_text": website_text,
                    "funding_snippets": funding_snippets,
                }

                enriched = synthesize_company(raw)

                save_company_record(slug, raw, enriched)
                update_index_csv(enriched)
                append_to_sheet({**enriched, "linkedin_url": linkedin_url})

                print(f"  [DONE]  {name} ({domain})")

            except Exception as e:
                error_msg = f"{type(e).__name__}: {e}"
                print(f"  [FAIL]  {name} ({domain}) — {error_msg}")
                failures.append({"name": name, "domain": domain, "error": error_msg})

        print(f"\nBatch {batch_num + 1} complete — review before continuing")
        if batch_num < total_batches - 1:
            try:
                input("Press Enter to continue to the next batch (Ctrl+C to abort)... ")
            except KeyboardInterrupt:
                print("\nAborted by user.")
                break

    if failures:
        with _FAILURES_CSV.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=_FAILURES_FIELDNAMES)
            writer.writeheader()
            writer.writerows(failures)
        print(f"\n{len(failures)} failure(s) logged to {_FAILURES_CSV}")
    else:
        print("\nAll companies processed successfully.")


if __name__ == "__main__":
    main()
