"""
config.py — Loads environment variables and exposes project-wide constants.
Reads from .env if present; falls back gracefully if not.
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env file if it exists (silently skips if missing)
load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=False)

# API keys
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
APIFY_API_TOKEN: str = os.getenv("APIFY_API_TOKEN", "")
TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")
GOOGLE_CREDENTIALS_JSON: str = os.getenv("GOOGLE_CREDENTIALS_JSON", "")
SHEET_ID: str = os.getenv("GOOGLE_SHEETS_ID", "")

# MOCK_MODE = True keeps sheet_sync.py writing to mock_sheet.csv (local CSV).
# Do not flip this to False — sheet_sync real Sheets wiring is deferred.
MOCK_MODE: bool = True

# API_MOCK_MODE controls fetchers.py and synthesize.py only.
# False = call real Apify / Anthropic APIs. True = return fixture data.
API_MOCK_MODE: bool = False

# Load categories from categories.json
_categories_path = Path(__file__).parent / "categories.json"
with open(_categories_path, "r") as _f:
    CATEGORIES: list[str] = json.load(_f)
