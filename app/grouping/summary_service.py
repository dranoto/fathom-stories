# app/grouping/summary_service.py
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple
from sqlalchemy import desc, func

from ..database import db_session_scope
from ..database.models import Event, EventSummary, Article
from .. import config as app_config
from .summarizer import generate_major_summary, generate_incremental_summary

logger = logging.getLogger(__name__)


def _articles_to_payload(articles: List[Article]) -> List[Dict[str, Any]]:
    out = []
    for a in articles:
        out.append({
            "id": a.id,
            "title": a.title,
            "publisher_name": a.publisher_name,
            "published_date": a.published_date.isoformat() if a.published_date else None,
            "url": a.url,
            "word_count": a.word_count,
            "scraped_text_content": a.scraped_text_content,
            "rss_description": a.rss_description,
        })
    return out


def _save_summary(event_id: int, summary_json: Dict[str, Any], article_ids: List[int], model_used: str) -> Optional[EventSummary]:
    summary_json["article_ids"] = article_ids
    with db_session_scope() as db:
        es = EventSummary(
            event_id=event_id,
            summary_json=summary_json,
            article_ids=article_ids,
            article_count=len(article_ids),
            model_used=model_used,
        )
        db.add(es)
        ev = db.query(Event).filter(Event.id == event_id).first()
        if ev:
            ev.last_summary_at = datetime.now(timezone.utc)
            ev.summary_article_count = len(article_ids)
            ev.summary_version = (ev.summary_version or 0) + 1
        db.flush()
        return es


def _latest_prior_summary(event_id: int) -> Optional[Dict[str, Any]]:
    with db_session_scope() as db:
        latest = (
            db.query(EventSummary)
            .filter(EventSummary.event_id == event_id)
            .order_by(desc(EventSummary.generated_at))
            .first()
        )
        if not latest:
            return None
        return dict(latest.summary_json) if latest.summary_json else None


async def generate_initial_summary_for_event(event_id: int, llm) -> bool:
    with db_session_scope() as db:
        event = db.query(Event).filter(Event.id == event_id).first()
        if not event:
            return False
        event_name = event.name
        articles = (
            db.query(Article)
            .filter(Article.event_id == event_id)
            .order_by(desc(Article.published_date))
            .all()
        )
        article_ids = [a.id for a in articles]
        payload = _articles_to_payload(articles)
    if not articles:
        return False
    try:
        summary_data = await generate_major_summary(
            event_name=event_name,
            articles=payload,
            prompt_template=app_config.DEFAULT_MAJOR_SUMMARY_PROMPT,
            prior_summary_json=None,
            llm=llm,
        )
    except Exception as e:
        logger.error(f"Initial summary failed for event {event_id}: {e}", exc_info=True)
        return False
    _save_summary(event_id, summary_data, article_ids, app_config.DEFAULT_SUMMARY_MODEL_NAME)
    logger.info(f"Initial summary saved for event {event_id} ({event_name})")
    return True


async def generate_incremental_summary_for_event(
    event_id: int, new_article_ids: List[int], llm
) -> bool:
    if not new_article_ids:
        return False
    prior = _latest_prior_summary(event_id)
    if prior is None:
        return await generate_initial_summary_for_event(event_id, llm)
    with db_session_scope() as db:
        event = db.query(Event).filter(Event.id == event_id).first()
        if not event:
            return False
        event_name = event.name
        new_articles = (
            db.query(Article)
            .filter(Article.id.in_(new_article_ids))
            .order_by(desc(Article.published_date))
            .all()
        )
        new_payload = _articles_to_payload(new_articles)
        all_articles = (
            db.query(Article)
            .filter(Article.event_id == event_id)
            .order_by(desc(Article.published_date))
            .all()
        )
        all_ids = [a.id for a in all_articles]
    try:
        summary_data = await generate_incremental_summary(
            event_name=event_name,
            new_articles=new_payload,
            prior_summary_json=prior,
            llm=llm,
        )
    except Exception as e:
        logger.error(f"Incremental summary failed for event {event_id}: {e}", exc_info=True)
        return False
    _save_summary(event_id, summary_data, all_ids, app_config.DEFAULT_SUMMARY_MODEL_NAME)
    logger.info(
        f"Incremental summary saved for event {event_id} ({event_name}) — {len(new_article_ids)} new article(s)"
    )
    return True
