# fathom-stories

Event-first news tracker. Fork of [Fathom](https://github.com/...) with the primary axis flipped: instead of an article feed with events as a side feature, the UI centers on **ongoing news events** (wars, presidencies, disasters) and the LLM auto-groups incoming articles into those events.

The scraper, RSS pipeline, and bypass-paywalls Chrome extension are reused **verbatim** from Fathom.

## What it does

- Pulls articles from a `.env`-configured list of RSS feeds
- Scrapes full text using Playwright + the bypass-paywalls extension (same as Fathom)
- LLM auto-assigns each new article to an existing event, a new event, or "uncategorized"
- LLM also scores each article's importance (0-1) for bubble size in the timeline
- Per-event: regenerating 3-section JSON summary (timeline, cross-source synthesis, progressive update)
- Admin panel: move/rename/split/merge/delete events, with corrections fed back to the LLM as few-shot examples
- Daily recluster: surfaces merge/split/revive candidates for admin approval (revive is recluster-only)
- Hourly lifecycle: events with no new articles for 7+ days auto-archive

## Quick start

```bash
pip install -r requirements.txt
playwright install chromium

cp .env.example .env
# Edit .env: set OPENAI_API_KEY and RSS_FEED_URLS

python -m app.cli init-db
python -m app.cli seed-feeds
python -m app.cli fetch       # one-shot RSS + scrape
python -m app.cli group       # one-shot LLM grouping
python -m app.cli serve       # reader UI: http://localhost:8000
python -m app.cli admin       # admin UI:  http://localhost:8001
```

## Architecture

See `AGENTS.md` for the full project structure and CLI reference.

## Differences from Fathom

| Fathom | fathom-stories |
|---|---|
| Article feed, events are secondary | Events are primary, articles are evidence |
| Per-user accounts, JWT, login | Single-user, no auth |
| Two SQLite DBs (`newsai.db`, `settings.db`) | One SQLite DB, settings in a `kv_settings` table |
| Per-article tags, summaries, chat | No tags/chat. Per-event summary only |
| Manually curated events | LLM auto-groups + admin corrections |
| Multi-event articles (m:n) | One canonical event per article |
| No lifecycle | Auto-archive after 7 days, revive on recluster |
| Docker-first | CLI-first (Docker later) |
| Admin baked into the same UI | Admin on a separate port (8001) |
