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

## Running as a Docker container via Arcane (canonical deployment)

The app runs as a Docker container (`fathom-stories:local`) managed by **Arcane**, the project's container orchestrator. The container is built from this repo's `Dockerfile` and `docker-compose.yml`, and is recreated by Arcane whenever the image is rebuilt.

**Build the image** (from the repo root):

```bash
docker build -t fathom-stories:local .
```

**Recreate the container** through Arcane — it will detect the new image and swap the running container. (Arcane invokes the equivalent of `docker compose up -d` against the project's `docker-compose.yml`.)

**Status / logs**:

```bash
docker ps --filter name=fathom-stories
docker logs --tail 50 -f fathom-stories
```

**Bind mounts** (from `docker-compose.yml`):

- `./data` → `/app/data` — SQLite database persists across rebuilds.
- `./logs` → `/app/logs` — log files persist across rebuilds.

The application code and frontend are **baked into the image** at build time, so a rebuild is required after every code change. The `.env` file is read from the repo root via `env_file:`; restart the container after editing it.

## Legacy: systemd user unit

`~/.config/systemd/user/fathom-stories.service` is preserved for reference but **disabled**. The Docker container is the canonical deployment.

To re-enable the systemd unit (e.g. for ad-hoc development without Docker):

```bash
systemctl --user enable --now fathom-stories.service
docker compose down   # or stop the container via Arcane
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
| `python -m app.cli disband-same-source [--apply]` | One-shot: disband events with ≤2 articles from a single `publisher_name` (1- and 2-article same-source events); pushes their articles back to the inbox as `proposed_event_name` singletons. Dry-run by default. |
| `python -m app.cli lifecycle` | One-shot archive/cool tick |
| `python -m app.cli summarize <event_id>` | Generate/regenerate event summary |
| `python -m app.cli stats` | Show counts |
| `python -m app.cli serve` | Run main API on `$MAIN_PORT` (default 8800) with scheduler. Usually run via systemd — see the "Running as a systemd service" section. |

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

## Event Ranking & Per-Browser Knobs

The event bar uses one of two sort modes, both per-browser-overridable via 6 sliders in the menu (Sort → Shaped):

| Knob | Server default | Range | Effect |
|---|---|---|---|
| `Log base` | `SCORE_LOG_BASE=2.0` | 1.1–10 | Compresses/expands the magnitude curve (2 = doubling, 3 = tripling) |
| `Freshness half-life (h)` | `SCORE_FRESHNESS_HALF_LIFE_HOURS=8` | 1–96 | Score halves every N hours of quiet |
| `Importance floor` | `SCORE_IMPORTANCE_FLOOR=0.5` | 0–1 | Baseline importance floor (0 = pure importance, 1 = no importance effect) |
| `Magnitude cap` | `SCORE_MAGNITUDE_CAP=6.0` | 1–20 | Caps log-based magnitude (lower = no mega-event dominance) |
| `New event boost window (h)` | `SCORE_NEW_EVENT_BOOST_HOURS=6.0` | 0–48 | Newly-created events get a multiplier that decays linearly to 1× over this many hours. 0 = off |
| `New event boost multiplier` | `SCORE_NEW_EVENT_BOOST_MAX=5.0` | 1–10 | Multiplier at creation; 1.0 = no effect |
| `Read-all demotion` | `SCORE_READ_ALL_DEMOTION=0.3` | 0–1 | Multiplier on score when `article_count > 0 AND unread_count == 0`. 1.0 = no effect. 0 = fully-read events drop to score 0 (cycle past). New articles on a fully-read event restore normal ranking automatically. |

**Default sort mode is `normal`** (primary = `article_count` desc, tie = `last_article_at` desc).
**Shaped sort** uses `log(count)/log(base) × freshness × importance`, also capped.

**The new-event boost applies in both modes.** In score mode, the multiplier is folded into `score_value`. In normal mode, it's added to the primary key as `(boost - 1) × 1000` so a brand-new 2-article event can overtake a mature 388-article event for the first few hours after creation. **The read-all demotion applies in both modes** as a multiplier on the final score when `article_count > 0 AND unread_count == 0`. All knobs are persisted in `localStorage` under `fathom.scoreKnobs` and forwarded as query params to `/api/events`.

## Grouping Flow

**Live assigner** runs after each fetch:
- Pulls ungrouped articles (`event_id IS NULL` AND `published_date >= now() - LIVE_GROUP_WINDOW_HOURS`, default 24h)
- Caps at `LIVE_GROUP_MAX_ARTICLES` (default 100) and batches at `LIVE_GROUP_BATCH_SIZE` (default 20) per LLM call to stay under provider context windows
- Articles older than `LIVE_GROUP_WINDOW_HOURS` are intentionally left untouched in the DB (existing 30-day cleanup handles them); they are not deleted
- Sends: list of articles + active events + 5 most recent `GroupingFeedback` rows
- LLM returns per-article decision: existing | uncategorized + importance_score
- On a per-batch `OpenAIContextOverflowError` (or `context_length_exceeded` in the error message), retries that batch once with `snippet_chars=200` instead of 500
- Apply decisions in a single transaction (live pass never creates events)

**Hourly regrouper** runs as `regroup_uncategorized`:
- Pulls ungrouped articles (up to 100) + active + recently-archived events
- Sends full context to LLM, gets back per-article decision: existing | new | uncategorized
- 2+ articles sharing a new name create a fresh Event
- **Followed by an LLM dedup pass** over all active events to merge semantic duplicates (confidence threshold 0.7)

**Distinct-source rule** (toggle: `REQUIRE_DISTINCT_SOURCES`, default `true`):
- A new Event is only created when the proposing articles include at least 2 distinct `publisher_name` values.
- Live assigner: 1-article `new` decisions become `proposed_event_name` singletons (no Event row).
- Regrouper: 1-article clusters become singletons (unchanged). 2+ article clusters whose articles share one `publisher_name` also become singletons.
- Singleton articles stay in the inbox with `proposed_event_name` set; the next regroup pass can re-merge them with a real cross-source companion.
- Existing same-source 1- and 2-article events can be cleaned up retroactively with `python -m app.cli disband-same-source [--apply]`. 3+ article events are never touched, even if all from one source.

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

**Event bar layout** (frontend, `frontend/js/eventTabs.js`):
- Bar is partitioned into 4 sections in order: **Inbox** → **Top N** (N events with the highest `article_count`, with `last_article_at` as tiebreaker; N is computed from the viewport width so the bar fits without overflow at common desktop widths) → **Most Recent** (single event with the latest `created_at`, marked with a "New" pill in the bottom-right and a tinted background; if it overlaps a Top N card, the (N+1)th-by-count takes its place so Top N + Most Recent are always N+1 distinct cards) → **Minor Events drawer toggle** (a dashed-border card showing the count of all remaining events; click pops a dropdown menu).
- **Elastic spacing:** cards inside Top N stay tight (8px gap), but the gaps between Inbox→Top N, Top N→Most Recent, and Most Recent→Drawer are elastic via `margin-left: auto` on `data-group="top-start"`, `data-group="most-recent"`, and `data-group="drawer"`. Inbox always sits at the left edge, the Drawer toggle always sits at the right edge.
- **Drawer is a floating dropdown menu** anchored under the toggle: `position: absolute; top: calc(100% + 6px); right: 0; width: 192px; max-height: 60vh; z-index: 200;`. Pops down over the reader pane with a 200ms max-height + opacity transition. Cards inside are 180px wide, single column, vertically scrollable when the minor list overflows 60vh.
- Every event card is `width: 180px; flex: 0 0 180px;` (fixed). Titles clamp to 2 lines with `-webkit-line-clamp`. The "New" tag on the most-recent card uses `--accent` background and sits in the bottom-right. The minor-toggle card has a dashed border to distinguish it from real events.
- Bar scrolls horizontally on very narrow viewports (<580px); the toggle is at the right of the scroll content in that case.

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
