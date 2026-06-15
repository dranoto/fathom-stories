# app/main_api.py
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from . import config as app_config
from . import database
from .routers import events as events_router
from .routers import articles as articles_router
from .routers import grouping as grouping_router
from .routers import feeds as feeds_router
from .routers import visits as visits_router
from .middleware.visitor import VisitorCookieMiddleware
from . import tasks

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


def _init_llms(app: FastAPI) -> None:
    from .summarizer import initialize_llm
    if not app_config.OPENAI_API_KEY:
        logger.warning("MAIN_API: OPENAI_API_KEY not set; LLM features disabled.")
        app.state.llm_summary_instance = None
        app.state.llm_grouping_instance = None
        app.state.llm_chat_instance = None
        return
    app.state.llm_summary_instance = initialize_llm(
        api_key=app_config.OPENAI_API_KEY,
        base_url=app_config.OPENAI_BASE_URL,
        model_name=app_config.DEFAULT_SUMMARY_MODEL_NAME,
        temperature=app_config.SUMMARY_LLM_TEMPERATURE,
        max_tokens=app_config.SUMMARY_MAX_OUTPUT_TOKENS,
    )
    app.state.llm_grouping_instance = initialize_llm(
        api_key=app_config.OPENAI_API_KEY,
        base_url=app_config.OPENAI_BASE_URL,
        model_name=app_config.DEFAULT_GROUPING_MODEL_NAME,
        temperature=app_config.GROUPING_LLM_TEMPERATURE,
        max_tokens=app_config.GROUPING_MAX_OUTPUT_TOKENS,
    )
    app.state.llm_chat_instance = initialize_llm(
        api_key=app_config.OPENAI_API_KEY,
        base_url=app_config.OPENAI_BASE_URL,
        model_name=app_config.DEFAULT_CHAT_MODEL_NAME,
        temperature=app_config.CHAT_LLM_TEMPERATURE,
        max_tokens=app_config.CHAT_MAX_OUTPUT_TOKENS,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.create_db_and_tables()
    tasks.seed_feeds_from_env()
    _init_llms(app)

    scheduler = AsyncIOScheduler(timezone=timezone.utc)
    app.state.scheduler = scheduler

    if app_config.OPENAI_API_KEY:
        scheduler.add_job(
            tasks.scheduled_rss_fetch,
            IntervalTrigger(minutes=app_config.DEFAULT_RSS_FETCH_INTERVAL_MINUTES),
            id="rss_fetch",
            next_run_time=datetime.now(timezone.utc) + timedelta(seconds=15),
            max_instances=1, coalesce=True,
        )
        scheduler.add_job(
            tasks.scheduled_live_grouping,
            IntervalTrigger(minutes=app_config.LIVE_GROUPING_INTERVAL_MINUTES),
            id="live_grouping",
            next_run_time=datetime.now(timezone.utc) + timedelta(seconds=45),
            max_instances=1, coalesce=True,
        )
        scheduler.add_job(
            tasks.scheduled_regroup_uncategorized,
            IntervalTrigger(hours=1),
            id="regroup_uncategorized",
            next_run_time=datetime.now(timezone.utc) + timedelta(minutes=5),
            max_instances=1, coalesce=True,
        )
        scheduler.add_job(
            tasks.scheduled_daily_recluster,
            CronTrigger(hour=app_config.RECLUSTER_HOUR_UTC, minute=10),
            id="daily_recluster",
            max_instances=1, coalesce=True,
        )
        scheduler.add_job(
            tasks.scheduled_lifecycle,
            IntervalTrigger(hours=app_config.LIFECYCLE_TICK_HOURS),
            id="lifecycle",
            next_run_time=datetime.now(timezone.utc) + timedelta(minutes=2),
            max_instances=1, coalesce=True,
        )
        scheduler.start()
        logger.info("MAIN_API: scheduler started")
    else:
        logger.warning("MAIN_API: scheduler not started (no LLM key)")

    yield

    try:
        scheduler.shutdown(wait=False)
    except Exception:
        pass
    logger.info("MAIN_API: shutdown complete")


app = FastAPI(
    title="Fathom Stories",
    version="0.1.0",
    description="Event-first news tracker (fork of Fathom)",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(VisitorCookieMiddleware)

app.include_router(events_router.router)
app.include_router(articles_router.router)
app.include_router(grouping_router.router)
app.include_router(feeds_router.router)
app.include_router(visits_router.router)


frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
if frontend_dir.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")

    @app.get("/sw.js", include_in_schema=False)
    async def _serve_sw():
        return FileResponse(str(frontend_dir / "sw.js"), media_type="application/javascript")

    @app.get("/manifest.webmanifest", include_in_schema=False)
    async def _serve_manifest():
        return FileResponse(
            str(frontend_dir / "manifest.webmanifest"),
            media_type="application/manifest+json",
        )


@app.get("/")
async def root_index():
    index_path = frontend_dir / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return JSONResponse({"name": "fathom-stories", "docs": "/docs"})


@app.get("/health")
async def health():
    return {"status": "ok", "ts": datetime.now(timezone.utc).isoformat()}
