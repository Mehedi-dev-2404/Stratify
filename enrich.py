"""
enrich.py — Orchestrator for the Stratify enrichment pipeline.
Reads companies.csv, enriches each company in batches, and persists results.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path
from urllib.parse import urlparse

_BASE = Path(__file__).parent
_COMPANIES_CSV = _BASE / "companies.csv"
_FAILURES_CSV = _BASE / "failures.csv"

_FAILURES_FIELDNAMES = ["name", "domain", "error"]

# Process only the first N companies from companies.csv (trial run limiter).
# Set to None to process all rows.
TRIAL_LIMIT: int | None = 10

# Number of companies per batch before pausing for user confirmation.
BATCH_SIZE: int = 10


def _domain_to_slug(domain: str) -> str:
    """Convert a domain or URL to a safe filesystem slug."""
    # Strip protocol/path if a full URL was passed
    hostname = _extract_domain(domain)
    return hostname.replace(".", "-").replace("/", "-").strip("-")


def _extract_domain(url: str) -> str:
    """Extract bare hostname from a full URL, or return input if already a domain."""
    url = url.strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme:
        return parsed.netloc
    return url


def merge_sources() -> None:
    """No-op if companies.csv already has data rows (real data is in place).

    Previously merged stub fixture lists; that logic is retired now that the
    real 169-company CSV is populated. If the file is genuinely empty this
    function prints a warning — manual CSV population is required.
    """
    with _COMPANIES_CSV.open(newline="") as f:
        rows = list(csv.DictReader(f))

    if rows:
        print(f"merge_sources: companies.csv already has {len(rows)} data rows — skipping overwrite.")
        return

    print(
        "merge_sources: companies.csv is empty. "
        "Populate it manually with the real company list before running."
    )


def main() -> None:
    """Load companies.csv and run the enrichment pipeline in batches of BATCH_SIZE.

    Real CSV columns expected:
        name, domain, founders, founder_linkedin_urls, comment,
        founder_dna, classification_badge, status, source_list,
        linkedin_company_url

    For each company:
      1. check_existing() — skip if fresh record found
      2. If linkedin_company_url is present: fetch LinkedIn profile + posts
         If blank: skip those fetches, pass empty dict / empty list
      3. fetch_website_text(domain) — always attempted
      4. fetch_funding_web_search(name) — returns snippets; search_fn wired at call site
      5. synthesize_company()
      6. save_company_record()
      7. update_index_csv()
      8. append_to_sheet()

    After each batch (except the last), pauses for user confirmation.
    Per-company exceptions are caught, logged to failures.csv, and skipped.
    """
    from fetchers import (
        fetch_company_linkedin,
        fetch_company_posts,
        fetch_founder_linkedin,
        fetch_funding_web_search,
        fetch_website_text,
        founder_profile_has_funding_signal,
    )
    from tavily_client import tavily_search
    from sheet_sync import (
        append_to_sheet,
        check_existing,
        save_company_record,
        update_index_csv,
    )
    from synthesize import synthesize_company

    # Read companies
    with _COMPANIES_CSV.open(newline="") as f:
        all_companies = list(csv.DictReader(f))

    if not all_companies:
        print("companies.csv has no data rows. Run merge_sources() or populate manually.")
        return

    # Apply trial limit
    companies = all_companies[:TRIAL_LIMIT] if TRIAL_LIMIT else all_companies
    print(
        f"Loaded {len(all_companies)} companies total. "
        f"Processing {len(companies)} (TRIAL_LIMIT={TRIAL_LIMIT})."
    )

    total_batches = math.ceil(len(companies) / BATCH_SIZE)
    print(f"Batches: {total_batches} × up to {BATCH_SIZE} companies each.")

    failures: list[dict] = []

    for batch_num in range(total_batches):
        batch = companies[batch_num * BATCH_SIZE : (batch_num + 1) * BATCH_SIZE]
        print(f"\n--- Batch {batch_num + 1}/{total_batches} ---")

        for company in batch:
            name = company.get("name", "").strip()
            domain_raw = company.get("domain", "").strip()
            domain = _extract_domain(domain_raw)  # bare hostname for fetch_website_text
            linkedin_url = company.get("linkedin_company_url", "").strip()
            slug = _domain_to_slug(domain_raw)

            # Pass-through metadata from CSV (not used by synthesize, stored in raw)
            meta = {
                "founders": company.get("founders", ""),
                "founder_linkedin_urls": company.get("founder_linkedin_urls", ""),
                "comment": company.get("comment", ""),
                "founder_dna": company.get("founder_dna", ""),
                "classification_badge": company.get("classification_badge", ""),
                "status": company.get("status", ""),
                "source_list": company.get("source_list", ""),
            }

            try:
                existing = check_existing(slug)
                if existing:
                    print(f"  [SKIP]  {name} ({domain}) — record <30 days old")
                    continue

                print(f"  [FETCH] {name} ({domain})")

                # 1. LinkedIn company profile + posts — only if URL is populated
                if linkedin_url:
                    linkedin_profile = fetch_company_linkedin(name, linkedin_url)
                    linkedin_posts = fetch_company_posts(linkedin_url)
                else:
                    linkedin_profile = {}
                    linkedin_posts = []

                # 2. Website — always attempted
                website_text = fetch_website_text(domain)

                # 3. Funding web search — always fetch Tavily so Claude can
                #    cross-check dates. LinkedIn fundingData can be stale (e.g.
                #    a 2024 round when a more recent 2026 round exists on the
                #    web). Both sources are always passed to synthesize_company()
                #    which picks the most recent one.
                #
                #    Founder LinkedIn profile is a cheap pre-check: if it
                #    carries a funding keyword AND LinkedIn profile also has
                #    funding data, we trust those two agreeing sources and skip
                #    Tavily (saves API cost for clear-cut cases).
                linkedin_has_funding = bool(
                    linkedin_profile.get("funding_status")
                    or linkedin_profile.get("funding_amount")
                )

                # Fetch first founder's LinkedIn profile and scan for keywords.
                founder_profile: dict = {}
                founder_has_funding = False
                raw_founder_urls = meta.get("founder_linkedin_urls", "").strip()
                if raw_founder_urls:
                    first_founder_url = raw_founder_urls.split(";")[0].strip()
                    if first_founder_url:
                        founder_profile = fetch_founder_linkedin(first_founder_url)
                        founder_has_funding = founder_profile_has_funding_signal(
                            founder_profile
                        )

                # Skip Tavily only when BOTH LinkedIn company profile AND founder
                # profile agree on funding — two independent sources confirming
                # the same signal. LinkedIn alone is not enough because its
                # lastFundingRound date may lag behind recent news.
                funding_snippets: list[str] = []
                if not (linkedin_has_funding and founder_has_funding):
                    funding_snippets = fetch_funding_web_search(
                        name,
                        search_fn=lambda q: tavily_search(q, purpose="funding"),
                    )

                # If founder profile carries a funding signal, include its text
                # as an additional funding snippet so Claude can assess it
                if founder_has_funding and founder_profile.get("funding_text"):
                    funding_snippets.append(
                        f"[Founder profile signal] {founder_profile['funding_text']}"
                    )

                raw = {
                    "name": name,
                    "domain": domain_raw,
                    "linkedin_url": linkedin_url,
                    "linkedin_profile": linkedin_profile,
                    "linkedin_posts": linkedin_posts,
                    "website_text": website_text,
                    "funding_snippets": funding_snippets,
                    "founder_profile": founder_profile,
                    **meta,
                }

                enriched = synthesize_company(raw)

                save_company_record(slug, raw, enriched)
                update_index_csv(enriched)
                append_to_sheet({**enriched, "linkedin_url": linkedin_url})

                print(f"  [DONE]  {name} — category: {enriched.get('category', '?')}")

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
