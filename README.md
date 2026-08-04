# Stratify

Research on 169 AI hedge fund and AI financial-data companies lives in scattered LinkedIn posts, thin marketing copy, and funding announcements that go stale within months. Compiling and maintaining a structured view of that market by hand does not scale past a handful of companies. Stratify automates the pipeline: it fetches raw signal per company (website, LinkedIn company page and posts, founder profiles, funding web search), sends it to Claude for structured synthesis, and persists the result to a local research database and a Google Sheet.

It is built for whoever is tracking a fast-moving, thinly-documented market segment and needs a repeatable way to keep a structured dataset current — not a one-off scrape.

## Technical Design

**Funding data is the least reliable field in this dataset, and the pipeline treats it that way.** LinkedIn's "recent funding" field is frequently stale — a company's profile can still show a 2024 seed round months after a 2026 Series A closes. A naive pipeline would just trust whichever source it fetched first. Stratify instead cross-checks sources before deciding what to synthesize:

1. If the LinkedIn company profile carries a funding signal, and the first listed founder's LinkedIn profile independently corroborates it (two agreeing sources), the pipeline skips the Tavily web search for that company entirely — this is the deterministic cost-saving path, since Tavily calls are the most expensive step in the funding check.
2. If the two sources disagree, or neither has a clear signal, Tavily is queried and its snippets are handed to Claude alongside the LinkedIn data.
3. Claude is instructed to compare recency and resolve conflicts explicitly, tagging the result with a `funding_confidence` value (including a `Conflicting` state) rather than silently picking one number.
4. Every synthesized record carries a `funding_source_note` that names the exact URL or profile Claude relied on, so a conflicting or low-confidence figure can be traced back to its source instead of being taken on faith.

The alternative — trusting a single source or re-running every funding check through Tavily regardless of agreement — was rejected because it either produces silently stale numbers or pays for redundant API calls on the majority of companies where LinkedIn and the founder profile already agree. The two-source-agreement short-circuit is the tradeoff: cheaper for the common case, without giving up a fallback for the disputed one.

A second, smaller decision follows the same philosophy: LinkedIn company-URL resolution runs through an in-house token/substring matcher (`resolve_linkedin.py`) first, with a paid third-party resolver (Clay) as an explicit fallback only for the URLs the in-house resolver cannot confidently confirm — see Architecture below.

## Architecture

The pipeline has two independent flows: the **enrichment pipeline** (fetch → synthesize → sync) and the **LinkedIn URL resolution pipeline** (a prerequisite data-cleaning step that populates `companies.csv` before enrichment can use LinkedIn as a source).

### Enrichment pipeline (`enrich.py`)

```
companies.csv (169 companies: name, domain, founders, linkedin_company_url, ...)
      │
      ▼
for each company (batches of 10, paused for review between batches)
      │
      ├─ check_existing()            skip if a cached record is <30 days old
      │
      ├─ fetchers.py                 raw signal collection
      │     ├─ fetch_company_linkedin()      Apify — only if linkedin_company_url is set
      │     ├─ fetch_company_posts()         Apify — recent LinkedIn posts
      │     ├─ fetch_website_text()          requests + BeautifulSoup, always run
      │     ├─ fetch_founder_linkedin()      Apify — first founder only, cheap pre-check
      │     └─ fetch_funding_web_search()    Tavily — only if LinkedIn + founder don't agree
      │
      ├─ synthesize.py               synthesize_company() calls Claude with the raw
      │                              bundle, returns structured JSON: summaries,
      │                              category, primary_user_summary, funding fields
      │
      └─ sheet_sync.py               save_company_record() → research_db/companies/*.json
                                      update_index_csv()    → research_db/index.csv
                                      append_to_sheet()     → mock_sheet.csv (MOCK_MODE)
                                                               or Google Sheets (real mode)
```

All raw Apify/API responses are cached under `raw_cache/` so repeated runs and debugging don't re-hit paid APIs. Per-company records are also skipped automatically if fetched within the last 30 days (`check_existing()` in `sheet_sync.py`), making `enrich.py` safe to re-run on the full CSV without re-processing already-fresh companies.

### LinkedIn URL resolution pipeline

A hybrid, cost-tiered resolver — cheap in-house matching first, paid third-party fallback only for what it can't confirm:

```
companies.csv (rows with blank linkedin_company_url)
      │
      ▼
update_companies_csv_linkedin.py
      ├─ resolve_linkedin.py + tavily_client.py: search + validate candidate URLs
      │  (token/substring overlap between company name and LinkedIn slug, ≥70% coverage)
      ├─ retroactively re-validates ALREADY-populated URLs too, blanking and
      │  re-resolving any that fail the coverage threshold
      └─ writes companies.csv in place; still-blank rows → clay_pending.csv

clay_pending.csv
      │  (user runs Clay enrichment externally, saves output as clay_results.csv)
      ▼
merge_clay_results.py
      ├─ re-validates Clay's URLs with the same rules as the in-house resolver
      ├─ accepted URLs written into companies.csv
      └─ rejections logged to linkedin_resolution_failures.csv

linkedin_resolution_source.csv tracks, per company, whether the URL came from
the in-house resolver ("our_resolver") or the Clay fallback ("clay_fallback").
```

### Re-synthesis without re-fetching

`reclassify.py` re-runs only the Claude synthesis step against every cached JSON in `research_db/companies/`, without re-fetching any raw data. This exists because the synthesis prompt (categories, summary rules) has changed multiple times over the project's life — re-fetching 169 companies from Apify/Tavily on every prompt iteration would be wasteful and slow. It prints a before/after category table and rewrites `research_db/index.csv` and `mock_sheet.csv`.

## Tech Stack

- Python 3
- Anthropic SDK (`anthropic`) — Claude synthesis (`synthesize.py`)
- Apify client (`apify-client`) — LinkedIn company/post/founder scraping actors
- Tavily (`tavily-python`) — funding web search and LinkedIn URL discovery
- `requests` + `beautifulsoup4` — website text extraction
- `gspread` + `google-auth` — Google Sheets sync (real mode, not yet wired in)
- `python-dotenv` — environment loading

## Key Features

- **Two-tier mock mode.** `MOCK_MODE` (`config.py`) gates the persistence layer — when `True`, `sheet_sync.py` writes to local `mock_sheet.csv`/`research_db/` instead of a live Google Sheet. `API_MOCK_MODE` gates `fetchers.py` and `synthesize.py` independently — when `True`, both return fixture data instead of calling Apify/Tavily/Claude. The two flags can be combined to develop against zero API cost or run real synthesis while still writing to local files.
- **Raw response caching.** Every Apify/web fetch is cached under `raw_cache/` by company slug and source, so re-runs and prompt iteration don't re-hit paid APIs for data that hasn't changed.
- **30-day freshness check.** `check_existing()` skips any company whose cached record is under 30 days old, making the full pipeline idempotent to re-run.
- **Cross-source funding verification.** Funding status/amount is only accepted from a single source when two independent signals (LinkedIn company profile + founder profile) agree; otherwise a web search is triggered and Claude is asked to resolve conflicts explicitly via a `funding_confidence` field (see Technical Design).
- **Hybrid LinkedIn URL resolver with paid fallback.** An in-house token/substring matcher runs first and is retroactively re-validated against already-populated URLs; only unresolved rows are handed to the paid Clay fallback, with results merged back through the same validation rules.
- **Batch-and-pause orchestration.** `enrich.py` processes companies in batches of 10 and pauses for manual review between batches, with per-company exceptions caught, logged to `failures.csv`, and skipped rather than aborting the run.
- **Team-based classification.** Companies are categorized by the buyer/daily-user team (e.g. Portfolio Managers / Traders, Risk Teams, Compliance Teams) rather than product function — see `categories.json`.
- **Synthesis-only re-runs.** `reclassify.py` re-classifies all cached companies against an updated prompt without re-fetching raw data, and prints a before/after diff of category changes.

## Getting Started

### Prerequisites

- Python 3.10+ (uses `from __future__ import annotations` and `list[str]` / `dict | None` syntax)
- API keys for Anthropic, Apify, and Tavily if running outside mock mode
- A Google Cloud service account and Sheets API access if `MOCK_MODE` is ever set to `False` (not currently wired in — see below)

### Local setup

1. Clone the repository and enter the project directory.
2. Create and activate a virtual environment:
   ```
   python3 -m venv venv
   source venv/bin/activate
   ```
3. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
4. Copy the environment template and fill in keys:
   ```
   cp .env.example .env
   ```
5. Populate `companies.csv` (columns: `name, domain, founders, founder_linkedin_urls, comment, founder_dna, classification_badge, status, source_list, linkedin_company_url`) or use the existing dataset already checked into the repo.
6. Run the enrichment pipeline:
   ```
   python enrich.py
   ```

### Environment variables

| Variable | Description | Example |
|---|---|---|
| `ANTHROPIC_API_KEY` | Claude API key, used by `synthesize.py` for structured synthesis | `sk-ant-...` |
| `APIFY_API_TOKEN` | Apify API token, used by `fetchers.py` for LinkedIn scraping actors | `apify_api_...` |
| `TAVILY_API_KEY` | Tavily API key, used for funding web search and LinkedIn URL discovery | `tvly-...` |
| `GOOGLE_CREDENTIALS_JSON` | Path to a Google service account credentials file (real-mode Sheets sync only) | `path/to/google_credentials.json` |
| `GOOGLE_SHEETS_ID` | Target Google Sheet ID (real-mode Sheets sync only) | `1AbCdEfGhIjKlmNoPqRsTuV...` |

In `config.py`, `MOCK_MODE` is hardcoded `True` and `API_MOCK_MODE` is hardcoded `False` — Google Sheets sync is a stub that raises `NotImplementedError` if `MOCK_MODE` is flipped to `False` without wiring in the real `gspread` call in `sheet_sync.py`. Real API calls (Claude, Apify, Tavily) are already live under the current defaults.

## Usage

Run the full enrichment pipeline (fetch, synthesize, persist) over `companies.csv`:
```
python enrich.py
```
Processes companies in batches of 10, pausing for confirmation between batches. Adjust `TRIAL_LIMIT` and `BATCH_SIZE` at the top of `enrich.py` to change scope.

Re-run synthesis only, against a new prompt or category scheme, without re-fetching:
```
python reclassify.py
```

Resolve blank LinkedIn company URLs in `companies.csv`:
```
python update_companies_csv_linkedin.py
```
Emits `clay_pending.csv` for any rows the in-house resolver could not confirm.

Merge externally-resolved Clay results back into `companies.csv`:
```
python merge_clay_results.py
```
Expects `clay_results.csv` with columns `company_name, domain, clay_linkedin_url`.

## Project Structure

```
config.py                         env loading, MOCK_MODE / API_MOCK_MODE flags, CATEGORIES
enrich.py                         pipeline orchestrator (fetch → synthesize → sync)
fetchers.py                       Apify + web scraping fetch functions, raw_cache read/write
synthesize.py                     Claude synthesis prompt and call
sheet_sync.py                     local DB / mock sheet persistence, Google Sheets stub
reclassify.py                     synthesis-only re-run over cached records
resolve_linkedin.py               LinkedIn URL candidate search + validation logic
tavily_client.py                  Tavily API wrapper
update_companies_csv_linkedin.py  in-house LinkedIn URL resolver, Clay fallback trigger
merge_clay_results.py             merges validated Clay results into companies.csv
compile_sheet.py                  supporting sheet compilation utility
add_post_dates.py                 supporting utility for post date enrichment
categories.json                   authoritative team-based category list
companies.csv                     input dataset (169 companies)
raw_cache/                        cached raw fetch responses by company slug + source
research_db/
  index.csv                       summary index: name, domain, category, funding, last_updated
  companies/*.json                full per-company record (raw + enriched)
mock_sheet.csv                    local stand-in for the Google Sheet output
```

## Project Status

Actively developed. The fetch → synthesize → persist pipeline and the LinkedIn resolution fallback chain are functional and have processed the full 169-company dataset in mock-sheet mode. Real Google Sheets sync (`sheet_sync.py` real-mode branch) is stubbed and would need the `gspread` call implemented before `MOCK_MODE` can be safely set to `False`.

## License

MIT — see `LICENSE`.
