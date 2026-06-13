# app/tasks.py
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from . import config as app_config
from .database import db_session_scope, FeedSource
from .rss_client import update_all_subscribed_feeds, add_or_update_feed_source
from .grouping import engine as grouping_engine
from .grouping import recluster as recluster_module
from .grouping import lifecycle as lifecycle_module
from .summarizer import initialize_llm

logger = logging.getLogger(__name__)

rss_update_lock = asyncio.Lock()


def seed_feeds_from_env() -> None:
    if not app_config.RSS_FEED_URLS:
        logger.info("TASKS: no RSS_FEED_URLS in .env; skipping seed")
        return
    with db_session_scope() as db:
        for url in app_config.RSS_FEED_URLS:
            add_or_update_feed_source(db, url)
    logger.info(f"TASKS: seeded {len(app_config.RSS_FEED_URLS)} feeds from .env")


def _get_grouping_llm():
    return initialize_llm(
        api_key=app_config.OPENAI_API_KEY,
        base_url=app_config.OPENAI_BASE_URL,
        model_name=app_config.DEFAULT_GROUPING_MODEL_NAME,
        temperature=app_config.GROUPING_LLM_TEMPERATURE,
        max_tokens=app_config.GROUPING_MAX_OUTPUT_TOKENS,
    )


async def scheduled_rss_fetch() -> None:
    if rss_update_lock.locked():
        logger.info("TASKS: rss_fetch already running; skipping")
        return
    async with rss_update_lock:
        logger.info("TASKS: scheduled_rss_fetch starting")
        try:
            with db_session_scope() as db:
                await update_all_subscribed_feeds(db)
        except Exception as e:
            logger.error(f"TASKS: scheduled_rss_fetch failed: {e}", exc_info=True)


async def scheduled_live_grouping() -> None:
    if not app_config.OPENAI_API_KEY:
        return
    if rss_update_lock.locked():
        logger.info("TASKS: rss_fetch in progress; deferring live_grouping")
        return
    logger.info("TASKS: scheduled_live_grouping starting")
    try:
        llm = _get_grouping_llm()
        if not llm:
            logger.warning("TASKS: grouping LLM init failed")
            return
        result = await grouping_engine.assign_new_articles(llm)
        logger.info(f"TASKS: live_grouping result: {result}")
    except Exception as e:
        logger.error(f"TASKS: scheduled_live_grouping failed: {e}", exc_info=True)


async def scheduled_daily_recluster() -> None:
    if not app_config.OPENAI_API_KEY:
        return
    logger.info("TASKS: scheduled_daily_recluster starting")
    try:
        llm = _get_grouping_llm()
        if not llm:
            return
        recluster_module.mark_events_seen_in_recluster()
        result = await recluster_module.generate_recluster_diff(llm, auto_apply=False)
        logger.info(f"TASKS: daily_recluster result: {result}")
    except Exception as e:
        logger.error(f"TASKS: scheduled_daily_recluster failed: {e}", exc_info=True)


async def scheduled_lifecycle() -> None:
    logger.info("TASKS: scheduled_lifecycle starting")
    try:
        result = lifecycle_module.tick()
        logger.info(f"TASKS: lifecycle result: {result}")
    except Exception as e:
        logger.error(f"TASKS: scheduled_lifecycle failed: {e}", exc_info=True)
