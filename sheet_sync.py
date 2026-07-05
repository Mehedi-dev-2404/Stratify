"""
sheet_sync.py — Persistence layer for Stratify.
In MOCK_MODE, writes to local CSV/JSON files instead of Google Sheets.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from config import MOCK_MODE

_BASE = Path(__file__).parent
_RESEARCH_DB = _BASE / "research_db"
_COMPANIES_DIR = _RESEARCH_DB / "companies"
_INDEX_CSV = _RESEARCH_DB / "index.csv"
_MOCK_SHEET_CSV = _BASE / "mock_sheet.csv"

_MOCK_SHEET_FIELDNAMES = [
    "name", "domain", "website_summary", "linkedin_product_summary",
    "linkedin_other_summary", "contradiction_notes",
    "category", "primary_user_summary", "funding_status", "funding_amount",
    "funding_confidence", "sources", "data_notes", "last_updated",
]

_INDEX_FIELDNAMES = [
    "name", "domain", "category", "funding_status", "funding_amount", "last_updated",
]


def append_to_sheet(company_row: dict) -> None:
    """Append a single enriched company record as a new row.

    In MOCK_MODE: writes to mock_sheet.csv instead of Google Sheets.
    In real mode: calls gspread to append to SHEET_ID.
    """
    if MOCK_MODE:
        file_exists = _MOCK_SHEET_CSV.exists()
        with _MOCK_SHEET_CSV.open("a", newline="") as f:
            writer = csv.DictWriter(
                f, fieldnames=_MOCK_SHEET_FIELDNAMES, extrasaction="ignore"
            )
            if not file_exists:
                writer.writeheader()
            writer.writerow(company_row)
    else:
        # TODO: wire in real gspread call using SHEET_ID and GOOGLE_CREDENTIALS_JSON
        raise NotImplementedError("Real Google Sheets API not wired in yet")


def save_company_record(company_slug: str, raw: dict, enriched: dict) -> None:
    """Write research_db/companies/{slug}.json with full record schema.

    Works the same in mock or real mode — local file I/O only.

    Schema: name, domain, linkedin_url, raw{}, enriched{}, fetched_at, source_list.
    """
    _COMPANIES_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "name": enriched.get("name") or raw.get("linkedin_profile", {}).get("name", ""),
        "domain": enriched.get("domain", ""),
        "linkedin_url": raw.get("linkedin_url", ""),
        "raw": raw,
        "enriched": enriched,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source_list": [k for k in raw if k != "linkedin_url"],
    }
    path = _COMPANIES_DIR / f"{company_slug}.json"
    path.write_text(json.dumps(record, indent=2))


def update_index_csv(company_record: dict) -> None:
    """Append or update a row in research_db/index.csv matched on domain.

    Works the same in mock or real mode — local file I/O only.
    """
    domain = company_record.get("domain", "")
    row = {k: company_record.get(k, "") for k in _INDEX_FIELDNAMES}

    rows: list[dict] = []
    if _INDEX_CSV.exists():
        with _INDEX_CSV.open(newline="") as f:
            rows = list(csv.DictReader(f))

    updated = False
    for i, existing in enumerate(rows):
        if existing.get("domain") == domain:
            rows[i] = row
            updated = True
            break
    if not updated:
        rows.append(row)

    with _INDEX_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_INDEX_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def check_existing(company_slug: str) -> dict | None:
    """Return parsed JSON dict if record exists and fetched_at is <30 days old.

    Returns None if the file is missing, malformed, or stale.
    """
    path = _COMPANIES_DIR / f"{company_slug}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        fetched_at_str = data.get("fetched_at", "")
        fetched_at = datetime.fromisoformat(fetched_at_str)
        age_days = (datetime.now(timezone.utc) - fetched_at).days
        if age_days < 30:
            return data
    except (ValueError, KeyError, TypeError):
        return None
    return None
