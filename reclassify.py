"""
reclassify.py — Re-runs synthesis on all cached company records using the
updated SYNTHESIS_PROMPT (primary-user / team-based categories).

Raw fetched data is NOT re-fetched. Only the Claude synthesis step runs.
Results are printed as a before/after category table and written back to
research_db/companies/*.json, research_db/index.csv, and mock_sheet.csv.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from synthesize import synthesize_company
from sheet_sync import update_index_csv

_BASE = Path(__file__).parent
_COMPANIES_DIR = _BASE / "research_db" / "companies"
_MOCK_SHEET_CSV = _BASE / "mock_sheet.csv"

_MOCK_SHEET_FIELDNAMES = [
    "name", "domain", "website_summary", "linkedin_summary",
    "category", "funding_status", "funding_amount",
    "funding_confidence", "sources", "data_notes", "last_updated",
]


def main() -> None:
    company_jsons = sorted(
        p for p in _COMPANIES_DIR.glob("*.json") if p.name != ".gitkeep"
    )
    print(f"Found {len(company_jsons)} cached company records.\n")

    results = []

    for path in company_jsons:
        record = json.loads(path.read_text())
        raw_data = record["raw"]
        old_category = record["enriched"].get("category", "")

        print(f"  [RUN] {raw_data['name']} ...", end=" ", flush=True)
        new_enriched = synthesize_company(raw_data)
        new_category = new_enriched["category"]

        # Preserve the funding_source_note built during enrich (enrich.py logic)
        new_enriched["funding_source_note"] = record["enriched"].get(
            "funding_source_note", ""
        )

        # Update JSON on disk (keep raw unchanged, update enriched)
        record["enriched"] = new_enriched
        path.write_text(json.dumps(record, indent=2))

        update_index_csv(new_enriched)
        print("OK")

        results.append({
            "name": raw_data["name"],
            "old_category": old_category,
            "new_category": new_category,
            "raw_data": raw_data,
            "new_enriched": new_enriched,
        })

    # Before/after table
    print(f"\n{'Company':<30} {'Before':<25} {'After':<30}")
    print("-" * 90)
    for r in results:
        changed = " <- CHANGED" if r["old_category"] != r["new_category"] else ""
        print(
            f"{r['name']:<30} {r['old_category']:<25} {r['new_category']:<30}{changed}"
        )

    # Rewrite mock_sheet.csv from updated results
    with _MOCK_SHEET_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=_MOCK_SHEET_FIELDNAMES, extrasaction="ignore"
        )
        writer.writeheader()
        for r in results:
            e = r["new_enriched"]
            raw = r["raw_data"]
            writer.writerow({
                "name": e.get("name", ""),
                "domain": e.get("domain", ""),
                "website_summary": e.get("website_summary", ""),
                "linkedin_summary": e.get("linkedin_summary", ""),
                "category": e.get("category", ""),
                "funding_status": e.get("funding_status", ""),
                "funding_amount": e.get("funding_amount", ""),
                "funding_confidence": e.get("funding_confidence", ""),
                "sources": e.get("funding_source_note", ""),
                "data_notes": raw.get("data_notes", ""),
                "last_updated": e.get("last_updated", ""),
            })

    print(f"\nmock_sheet.csv rewritten with {len(results)} rows.")


if __name__ == "__main__":
    main()
