# fathom-stories

Event-first news tracker. Fork of [Fathom](https://github.com/...) with the primary axis flipped: instead of an article feed with events as a side feature, the UI centers on **ongoing news events** (wars, presidencies, disasters) and the LLM auto-groups incoming articles into those events.

The scraper, RSS pipeline, and bypass-paywalls Chrome extension are reused **verbatim** from Fathom.

## What it does

- Pulls articles from a `.env`-configured list of RSS feeds
- Scrapes full text using Playwright + the bypass-paywalls extension (same as Fathom)
- LLM auto-assigns each new article to an existing event, a new event, or "uncategorized"
- LLM also scores each article's importance (0-1) for bubble size in the timeline
- Per-event: regenerating 3-section JSON summary (timeline, cross-source synthesis, progressive update)
- Move/remove from event, with corrections fed back to the LLM as few-shot examples
- Daily recluster: surfaces merge/split/revive candidates (currently write-only, no UI)
- Hourly lifecycle: events with no new articles for 7+ days auto-archive
- Per-browser ranking knobs (Sort → Shaped): log base, freshness half-life, importance floor, magnitude cap, new-event boost window + multiplier, read-all demotion — all persisted in `localStorage` and forwarded to the API as query params

## Quick start (Docker, recommended)

The app runs as a single `docker compose` service. The SQLite database, logs,
and configuration are all bind-mounted from the project directory, so you can
edit code, back up `data/stories.db`, or swap `.env` settings without rebuilding
the image.

```bash
cp .env.example .env
# Edit .env: set OPENAI_API_KEY and RSS_FEED_URLS

docker compose up -d --build
# Reader UI: http://localhost:8800  (override via MAIN_PORT in .env)

# Common commands
docker compose logs -f app
docker compose restart app
docker compose down
docker compose exec app python -m app.cli stats
docker compose exec app python -m app.cli fetch   # one-shot RSS + scrape
docker compose exec app python -m app.cli group   # one-shot LLM grouping
```

The bypass-paywalls Chrome extension is packaged as `docker/scraper_assistant.tar.gz`
at build time and unpacked into `/app/scraper_assistant` inside the image, so
scraping behavior matches the pre-Docker setup exactly. To upgrade the extension,
update the tarball (e.g. `tar -czf docker/scraper_assistant.tar.gz -C scraper_assistant .`)
and rebuild.

### Configuration knobs (no rebuild needed)

All knobs live in `.env`. Restart the container (`docker compose restart app`) after editing.

| Variable | Default | What it does |
|---|---|---|
| `OPENAI_API_KEY` | (required) | LLM provider key |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | Any OpenAI-compatible endpoint |
| `DEFAULT_SUMMARY_MODEL_NAME` | `xiaomi/mimo-v2.5-pro` | Per-event summary model |
| `DEFAULT_GROUPING_MODEL_NAME` | `xiaomi/mimo-v2.5-pro` | Live grouping + recluster model |
| `DEFAULT_CHAT_MODEL_NAME` | `xiaomi/mimo-v2.5-pro` | Per-event chat model |
| `RSS_FEED_URLS` | (none) | Comma-separated feed URLs or JSON list |
| `DEFAULT_RSS_FETCH_INTERVAL_MINUTES` | `30` | Fetch cadence |
| `LIVE_GROUP_WINDOW_HOURS` | `24` | Live pass only considers ungrouped articles published within this window |
| `LIVE_GROUP_MAX_ARTICLES` | `200` | Per-tick cap on ungrouped articles fed to the live LLM |
| `LIVE_GROUP_BATCH_SIZE` | `200` | Articles per individual LLM call within a tick |
| `SCORE_LOG_BASE` | `2.0` | Log-base for the shaped-score magnitude curve |
| `SCORE_FRESHNESS_HALF_LIFE_HOURS` | `8` | Score halves every N hours of quiet |
| `SCORE_IMPORTANCE_FLOOR` | `0.5` | Baseline importance floor |
| `SCORE_MAGNITUDE_CAP` | `6.0` | Caps log-based magnitude |
| `SCORE_NEW_EVENT_BOOST_HOURS` | `6.0` | Newly-created events get a multiplier that decays to 1× over this many hours |
| `SCORE_NEW_EVENT_BOOST_MAX` | `5.0` | Multiplier at creation; 1.0 = no effect |
| `SCORE_READ_ALL_DEMOTION` | `0.3` | Multiplier when all articles in an event are read; 1.0 = no effect |
| `MAIN_PORT` | `8800` | Host port for the reader UI |

The last four `SCORE_*` knobs can also be tuned per-browser via sliders in the menu (Sort → Shaped) and are persisted in `localStorage` under `fathom.scoreKnobs`.

## Quick start (bare Python, optional)

If you'd rather run the app without Docker:

```bash
pip install -r requirements.txt
playwright install chromium

cp .env.example .env
# Edit .env: set OPENAI_API_KEY and RSS_FEED_URLS

python -m app.cli init-db
python -m app.cli seed-feeds
python -m app.cli fetch       # one-shot RSS + scrape
python -m app.cli group       # one-shot LLM grouping
python -m app.cli serve       # reader UI: http://localhost:8800
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
| Manually curated events | LLM auto-groups + reader-driven corrections |
| Multi-event articles (m:n) | One canonical event per article |
| No lifecycle | Auto-archive after 7 days, revive on recluster |
| Docker-first | Docker-first (`docker compose up -d`), bare Python also supported |
| Admin baked into the same UI | No admin — single UI for everything |
