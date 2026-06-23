# Stratify Build Rounds

## Round 1 — Scaffold (2026-06-22)
Status: complete

Created full project skeleton:
- `.env.example`, `.gitignore`, `requirements.txt`, `categories.json`
- `config.py` — loads env + categories, sets MOCK_MODE=True
- `companies.csv` — header-only input file
- `fetchers.py` — stubs: fetch_company_linkedin, fetch_company_posts, fetch_website_text, fetch_funding_web_search
- `synthesize.py` — stub: synthesize_company
- `sheet_sync.py` — stubs: append_to_sheet, save_company_record, update_index_csv, check_existing
- `enrich.py` — stubs: merge_sources, main
- `raw_cache/`, `research_db/companies/` — empty dirs with .gitkeep
- `research_db/index.csv` — header-only index
