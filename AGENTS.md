# AGENTS.md - fathom-stories

## Project Overview
Python 3.12 FastAPI app, SQLite, OpenAI-compatible LLM (via LangChain `ChatOpenAI`), Playwright scraping with the bypass-paywalls Chrome extension. Vanilla JS frontend. Event-first news tracker: LLM auto-groups new articles into ongoing stories.

This is a **fork of Fathom** (`/home/thankfulcarp/fathom`). The scraper, RSS pipeline, and bypass-paywalls extension are reused verbatim from Fathom.

## Build & Run Commands

```bash
# Install
pip install -r requirements.txt
playwright install chromium

# One-time setup
cp .env.example .env
# (edit .env: set OPENAI_API_KEY and RSS_FEED_URLS)

# Initialize
python -m app.cli init-db

# Fetch a batch of articles (one-shot)
python -m app.cli fetch

# Group them into events
python -m app.cli group

# Run the main app (with scheduler)
python -m app.cli serve
# Reader UI: http://localhost:8000
```

## CLI Reference

| Command | Purpose |
|---------|---------|
| `python -m app.cli init-db` | Create tables |
| `python -m app.cli seed-feeds` | Add feeds from `RSS_FEED_URLS` |
| `python -m app.cli cleanup-bad` | Delete articles with scraping errors or below `MIN_ARTICLE_WORD_COUNT` |
| `python -m app.cli migrate-visitor-id` | Drop & recreate `article_reads` with `visitor_id` column (per-browser read state) |
| `python -m app.cli fetch` | One-shot RSS fetch + scrape |
| `python -m app.cli group` | One-shot live grouping (existing events only, no new events) |
| `python -m app.cli regroup` | One-shot forced regroup (creates new events when 2+ ungrouped match) |
| `python -m app.cli recluster [--apply]` | One-shot daily recluster; `--apply` auto-applies cool/revive |
| `python -m app.cli lifecycle` | One-shot archive/cool tick |
| `python -m app.cli summarize <event_id>` | Generate/regenerate event summary |
| `python -m app.cli stats` | Show counts |
| `python -m app.cli serve` | Run main API on `$MAIN_PORT` (default 8000) with scheduler |

## Code Style

Same as Fathom:

**Imports** (3 sections, blank lines between):
```python
# Standard library
import logging
from datetime import datetime
from typing import List, Optional

# Third-party
from fastapi import FastAPI, HTTPException
from sqlalchemy.orm import Session

# Local application
from .routers import article_routes
```

**Type Hints**: Required for all function parameters and returns.
**Naming**: `snake_case` for functions/variables, `PascalCase` for classes, `UPPER_SNAKE_CASE` for constants.
**No comments** in code — self-documenting only.
**Logging**: `logger = logging.getLogger(__name__)` at module top.
**Error handling**: Specific exception types, log with `exc_info=True`, rollback DB on failure.

## Project Structure
```
app/
├── main_api.py           # FastAPI entry — reader UI + main API
├── config.py             # Env config with defaults
├── cli.py                # argparse entry points
├── dependencies.py       # LLM DI
├── security.py           # verify_event_exists helpers
├── sanitizer.py          # bleach HTML allowlist
├── schemas/              # Pydantic models
├── database/             # SQLAlchemy models + KV store
├── routers/              # FastAPI routers
│   ├── events.py         # /api/events
│   ├── articles.py       # /api/articles
│   └── grouping.py       # /api/grouping
├── grouping/             # LLM event-detection engine
│   ├── engine.py         # assign_new_articles
│   ├── recluster.py      # daily_recluster diff
│   ├── lifecycle.py      # archive/cool/revive
│   ├── feedback.py       # record_correction + few-shot
│   ├── prompts.py        # prompt builders
│   └── summarizer.py     # 3-section JSON event summary
├── rss_client.py         # Fathom verbatim
├── scraper.py            # Fathom verbatim (Playwright + Readability + extension)
├── tasks.py              # APScheduler triggers
└── summarizer.py         # LLM init + per-article summary

frontend/                 # Reader UI (port 8000)
data/                     # SQLite DB (gitignored)
scraper_assistant/        # bypass-paywalls extension (gitignored)
```

## Key Conventions

1. **No comments** — Self-documenting code only.
2. **Logging over print** — Use `logger` from `logging.getLogger(__name__)`.
3. **Environment config** — All settings via `.env`, never hardcoded.
4. **Graceful degradation** — LLM failures log error and return meaningful message.
5. **Single user** — No auth, no `user_id` columns. Read state is per-browser via an anonymous `fathom_visitor_id` cookie (uuid4, HttpOnly, 1-year) so the site can be shared with friends without their reads colliding with yours.

## Grouping Flow

**Live assigner** runs after each fetch:
- Pulls ungrouped articles (`event_id IS NULL`)
- Sends: list of articles + active events + 5 most recent `GroupingFeedback` rows
- LLM returns per-article decision: existing | uncategorized + importance_score
- Apply decisions in a single transaction (live pass never creates events)

**Hourly regrouper** runs as `regroup_uncategorized`:
- Pulls ungrouped articles (up to 100) + active + recently-archived events
- Sends full context to LLM, gets back per-article decision: existing | new | uncategorized
- 2+ articles sharing a new name create a fresh Event
- **Followed by an LLM dedup pass** over all active events to merge semantic duplicates (confidence threshold 0.7)

**Daily reclusterer** runs at 03:00 UTC:
- Pulls last-14-days articles + active/archived (last 30d) events
- Sends full context to LLM, gets back: merge_candidates, split_candidates, reviving_events, new_events
- Writes to `recluster_proposals` table (currently write-only — no UI reads them; user removed the admin panel)
- **`revive` happens ONLY in recluster** — live assigner never revives archived events; the regrouper does it via `find_or_create_event`

**Hourly lifecycle** — two states only (`active` / `archived`, no cooling):
- Each new article **resets** the event's `expires_at = max(article.published_date, now) + EVENT_TTL_RESET_HOURS` (default 48h)
- `active` → `archived` when `expires_at < now` (eager reaper in `lifecycle.tick()`, lazy reaper in `list_events`)
- The regrouper is the only revival path for archived events: a new matching article from the inbox brings them back via `find_or_create_event()`
- Empty-event reaper: deletes 0-article events older than `PURGE_EMPTY_FLOOR_SECONDS` (30s) — defends against `move_article` / `split_event` / `create_event` artifacts
- Ancient-archive purger: hard-deletes archived events older than `PURGE_ARCHIVE_AFTER_DAYS` (180d) and their recluster proposals

**Reader-driven corrections** (move-to-event, remove-from-event) write `GroupingFeedback` rows. Top 5 most recent are injected into the next LLM call as few-shot examples (the "editor corrections are ground truth" section).

## LLM Configuration

Uses **OpenAI-compatible API** via LangChain's `ChatOpenAI`. Gemini is explicitly not supported.

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | (required) | API key |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | API base URL |
| `DEFAULT_SUMMARY_MODEL_NAME` | `gpt-4o-mini` | Per-event summary |
| `DEFAULT_GROUPING_MODEL_NAME` | `gpt-4o-mini` | Live grouping + recluster |
| `SUMMARY_LLM_TEMPERATURE` | `0.2` | |
| `GROUPING_LLM_TEMPERATURE` | `0.1` | |

## Git Workflow
- **Commits**: Small, frequent. Prefixes: `Fix:`, `Add:`, `Refactor:`, `Update:`
- **Push**: `git push origin main` (after creating the GitHub repo)
- **Gitignore**: `.venv/`, `data/*.db`, `.env`, `scraper_assistant/`, `__pycache__/`
