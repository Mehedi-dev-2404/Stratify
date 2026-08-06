"""
compile_sheet.py — Compiles mock_sheet.csv directly from cached
research_db/companies/*.json records. No Claude API calls: reads whatever
synthesis (enriched) each record already carries (freshly written by
enrich.py for re-fetched companies, unchanged for skipped ones).
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

_BASE = Path(__file__).parent
_COMPANIES_DIR = _BASE / "research_db" / "companies"
_MOCK_SHEET_CSV = _BASE / "mock_sheet.csv"

_MOCK_SHEET_FIELDNAMES = [
    "name", "domain", "website_summary", "linkedin_product_summary",
    "linkedin_other_summary", "last_post_date", "inactive_6m", "contradiction_notes",
    "category", "primary_user_summary", "funding_status", "funding_amount",
    "funding_confidence", "sources", "data_notes", "last_updated",
]
# last_post_date / inactive_6m are intentionally left blank here — run
# add_post_dates.py afterward to populate them from the posts cache (it reads
# dates straight from raw_cache/*_posts.json, which is more current than
# whatever linkedin_posts snapshot is embedded in each research_db record).


def main() -> None:
    company_jsons = sorted(
        p for p in _COMPANIES_DIR.glob("*.json") if p.name != ".gitkeep"
    )
    print(f"Found {len(company_jsons)} cached company records.")

    with _MOCK_SHEET_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=_MOCK_SHEET_FIELDNAMES, extrasaction="ignore"
        )
        writer.writeheader()
        for path in company_jsons:
            record = json.loads(path.read_text())
            raw = record.get("raw", {})
            e = record.get("enriched", {})
            writer.writerow({
                "name": e.get("name", ""),
                "domain": e.get("domain", ""),
                "website_summary": e.get("website_summary", ""),
                "linkedin_product_summary": e.get("linkedin_product_summary", ""),
                "linkedin_other_summary": e.get("linkedin_other_summary", ""),
                "contradiction_notes": e.get("contradiction_notes", ""),
                "category": e.get("category", ""),
                "primary_user_summary": e.get("primary_user_summary", ""),
                "funding_status": e.get("funding_status", ""),
                "funding_amount": e.get("funding_amount", ""),
                "funding_confidence": e.get("funding_confidence", ""),
                "sources": e.get("funding_source_note", ""),
                "data_notes": raw.get("data_notes", ""),
                "last_updated": e.get("last_updated", ""),
            })

    print(f"mock_sheet.csv written with {len(company_jsons)} rows.")


if __name__ == "__main__":
    main()
