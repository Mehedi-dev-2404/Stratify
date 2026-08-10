"""
add_post_dates.py — Standalone script to add last_post_date and inactive_6m
columns to mock_sheet.csv, using fresh LinkedIn post data (with dates) from
fetchers.py. Makes no Claude API calls.

Usage:
    python3 add_post_dates.py
"""

from __future__ import annotations

import csv
import re
from datetime import datetime, timedelta, timezone

from fetchers import fetch_company_posts

SHEET_PATH = "mock_sheet.csv"
LINKEDIN_URL_RE = re.compile(r"https://www\.linkedin\.com/company/[^\s|]+")
INACTIVE_THRESHOLD_DAYS = 182  # ~6 months


def extract_linkedin_url(sources: str) -> str | None:
    match = LINKEDIN_URL_RE.search(sources or "")
    return match.group() if match else None


def compute_last_post_date(posts: list[dict]) -> str:
    """Return the most recent post date (YYYY-MM-DD), or "" if none available."""
    dates = [p["date"][:10] for p in posts if p.get("date")]
    return max(dates) if dates else ""


def compute_inactive_6m(last_post_date: str, today: datetime) -> str:
    if not last_post_date:
        return "Yes"
    post_dt = datetime.strptime(last_post_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return "Yes" if (today - post_dt) > timedelta(days=INACTIVE_THRESHOLD_DAYS) else "No"


def main() -> None:
    with open(SHEET_PATH, newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames)
        rows = list(reader)

    # Insert last_post_date + inactive_6m right after linkedin_other_summary,
    # unless the columns are already present (compile_sheet.py emits them blank).
    if "last_post_date" in fieldnames and "inactive_6m" in fieldnames:
        new_fieldnames = fieldnames
    else:
        insert_at = fieldnames.index("linkedin_other_summary") + 1
        new_fieldnames = (
            fieldnames[:insert_at] + ["last_post_date", "inactive_6m"] + fieldnames[insert_at:]
        )

    today = datetime.now(timezone.utc)
    processed = 0
    populated = 0
    inactive_yes = 0
    inactive_no = 0

    for row in rows:
        linkedin_url = extract_linkedin_url(row.get("sources", ""))
        if not linkedin_url:
            row["last_post_date"] = ""
            row["inactive_6m"] = "Yes"
            inactive_yes += 1
            continue

        processed += 1
        posts = fetch_company_posts(linkedin_url)
        last_post_date = compute_last_post_date(posts)
        inactive = compute_inactive_6m(last_post_date, today)

        row["last_post_date"] = last_post_date
        row["inactive_6m"] = inactive

        if last_post_date:
            populated += 1
        if inactive == "Yes":
            inactive_yes += 1
        else:
            inactive_no += 1

        print(f"  {row['name']}: last_post_date={last_post_date or '(none)'} inactive_6m={inactive}")

    with open(SHEET_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=new_fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print()
    print(f"Rows in sheet: {len(rows)}")
    print(f"Companies processed (LinkedIn URL found): {processed}")
    print(f"last_post_date populated: {populated}")
    print(f"inactive_6m = Yes: {inactive_yes}")
    print(f"inactive_6m = No: {inactive_no}")


if __name__ == "__main__":
    main()
