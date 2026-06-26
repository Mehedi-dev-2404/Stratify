"""
update_companies_csv_linkedin.py — Fills in blank linkedin_company_url cells
in companies.csv by calling resolve_company_linkedin() for each row.

Also runs retroactive validation on ALL already-populated URLs: any URL whose
slug doesn't cover ≥70% of the company name tokens (token-match + substring
fallback) is blanked and logged to linkedin_resolution_failures.csv, then
re-resolved along with the other blank rows.

Run:
    python update_companies_csv_linkedin.py
"""

from __future__ import annotations

import csv
import datetime
import re
from pathlib import Path

from resolve_linkedin import resolve_company_linkedin
from tavily_client import tavily_search_results
from config import API_MOCK_MODE as MOCK_MODE

_BASE = Path(__file__).parent
_COMPANIES_CSV = _BASE / "companies.csv"
_FAILURES_CSV = _BASE / "linkedin_resolution_failures.csv"
_RESOLUTION_SOURCE_CSV = _BASE / "linkedin_resolution_source.csv"
_CLAY_PENDING_CSV = _BASE / "clay_pending.csv"

_RESOLUTION_SOURCE_FIELDNAMES = [
    "company_name", "domain", "linkedin_company_url", "source", "timestamp"
]

# Tavily is now the default search backend for LinkedIn URL resolution.
_SEARCH_FN = lambda q: tavily_search_results(q, purpose="linkedin_url")

# Maximum number of blank rows to resolve per run. Set to None to process all.
TRIAL_LIMIT: int | None = 10

# Minimum fraction of company-name tokens that must appear in the URL slug
# (via token split OR substring match) for a pre-existing URL to be kept.
_RETRO_NAME_COVERAGE_THRESHOLD: float = 0.70


# ---------------------------------------------------------------------------
# Retroactive URL validation helpers
# ---------------------------------------------------------------------------

def _slug_covers_name(url: str, company_name: str) -> float:
    """Return fraction of company name tokens covered by the LinkedIn URL slug.

    Checks two ways so concatenated slugs (e.g. "blueflameai" for "Blue Flame")
    are not incorrectly rejected:
      1. Token-level: slug split on [-_] contains the name token exactly.
      2. Substring: the name token appears anywhere inside the full raw slug.

    Returns 0.0 if url is not a valid LinkedIn company URL or has no slug.
    Returns 1.0 if company_name has no tokenisable tokens (benefit of the doubt).
    """
    m = re.search(r"linkedin\.com/company/([^/\s?#]+)", url)
    if not m:
        return 0.0
    raw_slug = m.group(1).lower()
    slug_tokens = {t for t in re.split(r"[-_]", raw_slug) if len(t) > 1}
    name_tokens = {
        t
        for t in re.sub(r"[^a-z0-9]", " ", company_name.lower()).split()
        if len(t) > 1
    }
    if not name_tokens:
        return 1.0
    if not slug_tokens and not raw_slug:
        return 0.0

    # A name token is covered if it is an exact slug token OR a substring of
    # the full raw slug (handles cases like "blueflameai" ⊇ "blue"+"flame").
    covered = sum(1 for t in name_tokens if t in slug_tokens or t in raw_slug)
    return covered / len(name_tokens)


def _retro_validate_urls(rows: list[dict]) -> list[dict]:
    """Validate all already-populated linkedin_company_url values in-place.

    For each row that has a URL, compute slug name-coverage. If coverage <
    _RETRO_NAME_COVERAGE_THRESHOLD, blank the URL and record a failure entry.

    Modifies rows in-place. Returns a list of failure dicts ready to append
    to linkedin_resolution_failures.csv.
    """
    failures: list[dict] = []
    blanked = 0

    for row in rows:
        url = row.get("linkedin_company_url", "").strip()
        if not url:
            continue  # already blank — handled by resolution loop

        name = row.get("name", "").strip()
        domain = row.get("domain", "").strip()
        coverage = _slug_covers_name(url, name)

        if coverage < _RETRO_NAME_COVERAGE_THRESHOLD:
            reason = (
                f"retro-validation: slug '{_extract_slug(url)}' covers only "
                f"{coverage:.0%} of name tokens for '{name}' "
                f"(threshold {_RETRO_NAME_COVERAGE_THRESHOLD:.0%})"
            )
            print(f"  [RETRO-INVALID] {name:40} {url} → blanked ({coverage:.0%} coverage)")
            row["linkedin_company_url"] = ""
            blanked += 1
            failures.append({
                "company_name": name,
                "domain": domain,
                "reason": reason,
                "timestamp": datetime.datetime.utcnow().isoformat(),
            })

    if blanked:
        print(f"  Retroactive validation complete: {blanked} URL(s) blanked.")
    else:
        print("  Retroactive validation: all existing URLs passed.")

    return failures


def _extract_slug(url: str) -> str:
    m = re.search(r"linkedin\.com/company/([^/\s?#]+)", url)
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# Clay fallback validator
# ---------------------------------------------------------------------------

def call_clay_fallback(
    company_name: str,
    domain: str,
    clay_data: dict,
) -> tuple[str, str]:
    """Validate Clay's returned company data and extract a LinkedIn URL if valid.

    Does NOT call Clay's API. The caller runs Clay externally and passes the
    resulting dict here for validation. Clay is a second source — the same
    name/domain match rules apply as for our_resolver.

    Expected keys in clay_data (others are ignored):
      - "linkedin_url" or "linkedin_company_url" or "linkedin": the URL Clay found
      - "name": Clay's matched company name (used as context for domain validation)
      - "website": Clay's matched website (used as domain context)

    Args:
        company_name: Original company name from companies.csv.
        domain: Original domain from companies.csv.
        clay_data: Dict returned by Clay's find-and-enrich-company endpoint.

    Returns:
        (url, rejection_reason) — url is "" on failure/rejection.
    """
    from resolve_linkedin import validate_candidate_url

    candidate_url = (
        clay_data.get("linkedin_url")
        or clay_data.get("linkedin_company_url")
        or clay_data.get("linkedin")
        or ""
    ).strip()

    if not candidate_url:
        return "", "Clay returned no LinkedIn URL"

    context = " ".join(filter(None, [
        clay_data.get("name", ""),
        clay_data.get("website", ""),
        domain,
    ]))

    return validate_candidate_url(company_name, domain, candidate_url, context)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(limit: int | None = TRIAL_LIMIT) -> None:
    """Retroactively validate existing URLs, then resolve all blank cells.

    Steps:
      1. Read companies.csv.
      2. Retroactively validate every populated linkedin_company_url against
         the company name — blank and log any that fail coverage threshold.
      3. Resolve all blank cells (including newly-blanked ones) up to `limit`.
      4. Write back companies.csv and append all failures to
         linkedin_resolution_failures.csv.

    Args:
        limit: Max number of blank rows to resolve this run. Rows already
               populated are skipped. None means resolve all blank rows.
    """
    with _COMPANIES_CSV.open(newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    if "linkedin_company_url" not in fieldnames:
        print("ERROR: 'linkedin_company_url' column not found in companies.csv")
        return

    print(f"companies.csv: {len(rows)} rows total")

    # Step 1: Retroactive validation
    all_failures = _retro_validate_urls(rows)

    # Step 2: Count blank rows after retro-validation
    to_resolve = [r for r in rows if not r.get("linkedin_company_url", "").strip()]
    already_filled = len(rows) - len(to_resolve)
    will_process = len(to_resolve) if limit is None else min(limit, len(to_resolve))
    print(
        f"{already_filled} have linkedin_company_url | "
        f"{len(to_resolve)} to resolve | processing {will_process} this run (TRIAL_LIMIT={limit})"
    )

    if not to_resolve and not all_failures:
        print("Nothing to do.")
        return

    # Step 3: Resolve blank rows
    resolved_count = 0
    failed_count = 0
    processed = 0
    newly_resolved: list[dict] = []

    for row in rows:
        if row.get("linkedin_company_url", "").strip():
            continue  # already populated

        if limit is not None and processed >= limit:
            break

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
                newly_resolved.append({
                    "company_name": name,
                    "domain": domain,
                    "linkedin_company_url": url,
                    "source": "our_resolver",
                    "timestamp": datetime.datetime.utcnow().isoformat(),
                })
            else:
                failed_count += 1
                all_failures.append({
                    "company_name": name,
                    "domain": domain,
                    "reason": rejection_reason,
                    "timestamp": datetime.datetime.utcnow().isoformat(),
                })
        except Exception as e:
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
            row["linkedin_company_url"] = ""
            failed_count += 1
            all_failures.append({
                "company_name": name,
                "domain": domain,
                "reason": f"{type(e).__name__}: {e}",
                "timestamp": datetime.datetime.utcnow().isoformat(),
            })

    # Step 4: Write back CSV
    with _COMPANIES_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    # Step 5: Track resolution sources + export Clay pending list
    if newly_resolved:
        source_exists = _RESOLUTION_SOURCE_CSV.exists()
        with _RESOLUTION_SOURCE_CSV.open("a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=_RESOLUTION_SOURCE_FIELDNAMES)
            if not source_exists:
                writer.writeheader()
            writer.writerows(newly_resolved)
        print(f"  Logged {len(newly_resolved)} our_resolver hit(s) → {_RESOLUTION_SOURCE_CSV.name}")

    still_blank = [
        {"company_name": r.get("name", ""), "domain": r.get("domain", "")}
        for r in rows
        if not r.get("linkedin_company_url", "").strip()
    ]
    if still_blank:
        with _CLAY_PENDING_CSV.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["company_name", "domain"])
            writer.writeheader()
            writer.writerows(still_blank)
        print(
            f"  {len(still_blank)} company(ies) still blank → "
            f"{_CLAY_PENDING_CSV.name} ready for Clay fallback"
        )
    else:
        print("  No companies remaining blank — Clay fallback not needed.")

    # Step 6: Append all failures
    if all_failures:
        failures_exist = _FAILURES_CSV.exists()
        with _FAILURES_CSV.open("a", newline="") as f:
            writer = csv.DictWriter(
                f, fieldnames=["company_name", "domain", "reason", "timestamp"]
            )
            if not failures_exist:
                writer.writeheader()
            writer.writerows(all_failures)
        print(f"  Logged {len(all_failures)} failure(s) → {_FAILURES_CSV.name}")

    print(
        f"\nDone. Resolved: {resolved_count} | Not found: {failed_count} | "
        f"CSV updated in place."
    )


if __name__ == "__main__":
    main()
