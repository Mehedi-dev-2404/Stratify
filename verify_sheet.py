"""
verify_sheet.py — Quality gate for mock_sheet.csv.

Run this after compile_sheet.py (and add_post_dates.py) and before the sheet
goes anywhere near Dr. Cetin. Checks hard failures + warnings and prints a
clear PASS/FAIL report. Read-only: never modifies mock_sheet.csv.

Usage:
    python3 verify_sheet.py
"""

from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path

SHEET_PATH = Path(__file__).parent / "mock_sheet.csv"

EXPECTED_COLUMNS = [
    "name", "domain", "website_summary", "linkedin_product_summary",
    "linkedin_other_summary", "last_post_date", "inactive_6m", "contradiction_notes",
    "category", "primary_user_summary", "unique_value_prop", "funding_status",
    "funding_amount", "funding_confidence", "sources", "data_notes", "last_updated",
]

EXCLUDED_COMPANIES = {
    "Tornado AI", "Unique AI", "AQ22", "Causality AI", "Factonium", "Fiscal AI",
    "Model Updater", "Nosible", "Plux", "Premia", "Prymer", "Quill AI",
    "Rowspace AI", "Sigtech", "Sov.ai", "Financial AI", "Terminal X",
    "Uptrends.ai", "Entelligent", "Fey", "Fyva", "Nash", "Obi9 Technologies",
    "Parsym", "StockInsights AI", "Current", "ZeroWallStreet", "Dili AI",
    "Structify", "Catalyst Edge", "Decisional AI", "Nummo", "Zanista",
}

ROW_MIN, ROW_MAX = 130, 140
CONTRADICTION_MAX = 20
NO_VERIFIED_PAGE_WARN = 5
LAST_POST_BLANK_WARN = 40


def main() -> None:
    with SHEET_PATH.open(newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    failures: list[str] = []
    warnings: list[str] = []

    # ---- Hard failures ----
    if not (ROW_MIN <= len(rows) <= ROW_MAX):
        failures.append(
            f"Row count {len(rows)} outside {ROW_MIN}-{ROW_MAX} range"
        )

    if fieldnames != EXPECTED_COLUMNS:
        failures.append("Column order does not match expected order")
        failures.append(f"  expected: {EXPECTED_COLUMNS}")
        failures.append(f"  actual:   {fieldnames}")

    present_excluded = sorted({r.get("name", "") for r in rows} & EXCLUDED_COMPANIES)
    if present_excluded:
        failures.append(f"Excluded companies present: {present_excluded}")

    funded_blank_conf = [
        r.get("name", "")
        for r in rows
        if r.get("funding_status", "").strip() == "Funded"
        and not r.get("funding_confidence", "").strip()
    ]
    if funded_blank_conf:
        failures.append(
            f"funding_confidence blank for {len(funded_blank_conf)} Funded companies: "
            f"{funded_blank_conf}"
        )

    contradiction_count = sum(1 for r in rows if r.get("contradiction_notes", "").strip())
    if contradiction_count > CONTRADICTION_MAX:
        failures.append(
            f"contradiction_notes populated on {contradiction_count} rows "
            f"(> {CONTRADICTION_MAX}; likely column misalignment)"
        )

    # ---- Warnings ----
    no_verified_page = sum(
        1 for r in rows if "no verified page found" in r.get("data_notes", "").lower()
    )
    if no_verified_page > NO_VERIFIED_PAGE_WARN:
        warnings.append(
            f'"no verified page found" count is {no_verified_page} (> {NO_VERIFIED_PAGE_WARN})'
        )

    stale_notes = sum(1 for r in rows if "no recent posts" in r.get("data_notes", ""))
    if stale_notes:
        warnings.append(
            f'"no recent posts" stale message still present in {stale_notes} rows'
        )

    active_missing_product = [
        r.get("name", "")
        for r in rows
        if r.get("inactive_6m", "").strip() == "No"
        and not r.get("linkedin_product_summary", "").strip()
    ]
    if active_missing_product:
        warnings.append(
            f"linkedin_product_summary blank for {len(active_missing_product)} active "
            f"(inactive_6m=No) companies: {active_missing_product}"
        )

    last_post_blank = sum(1 for r in rows if not r.get("last_post_date", "").strip())
    if last_post_blank > LAST_POST_BLANK_WARN:
        warnings.append(
            f"last_post_date blank for {last_post_blank} companies (> {LAST_POST_BLANK_WARN})"
        )

    # ---- Always-print stats ----
    active = sum(1 for r in rows if r.get("inactive_6m", "").strip() == "No")
    inactive = sum(1 for r in rows if r.get("inactive_6m", "").strip() == "Yes")
    product_coverage = sum(
        1 for r in rows if r.get("linkedin_product_summary", "").strip()
    )
    conf_breakdown = Counter(r.get("funding_confidence", "").strip() or "(blank)" for r in rows)

    print("=" * 60)
    print("  mock_sheet.csv VERIFICATION REPORT")
    print("=" * 60)
    print(f"Total rows:                    {len(rows)}")
    print(f"Active (inactive_6m=No):       {active}")
    print(f"Inactive (inactive_6m=Yes):    {inactive}")
    pct = (product_coverage / len(rows) * 100) if rows else 0
    print(f"linkedin_product_summary:      {product_coverage}/{len(rows)} ({pct:.0f}%)")
    print("funding_confidence breakdown:")
    for key, val in conf_breakdown.most_common():
        print(f"    {key:<14} {val}")
    print(f"contradiction_notes populated: {contradiction_count}")
    print(f'"no verified page found":      {no_verified_page}')
    print("-" * 60)

    if failures:
        print(f"HARD FAILURES ({sum(1 for x in failures if not x.startswith('  '))}):")
        for msg in failures:
            print(f"  FAIL: {msg}" if not msg.startswith("  ") else f"    {msg.strip()}")
    else:
        print("HARD FAILURES: none")

    if warnings:
        print(f"WARNINGS ({len(warnings)}):")
        for msg in warnings:
            print(f"  WARN: {msg}")
    else:
        print("WARNINGS: none")

    print("=" * 60)
    if failures:
        print("Final verdict: ❌ NEEDS FIXING")
    else:
        print("Final verdict: ✅ READY TO SEND")
    print("=" * 60)

    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
