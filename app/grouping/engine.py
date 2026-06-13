# app/grouping/engine.py
import json
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from sqlalchemy import desc, or_

from ..database import db_session_scope
from ..database.models import Article, Event
from .prompts import build_group_assign_prompt
from .feedback import build_few_shot_examples

logger = logging.getLogger(__name__)


def _parse_response(content: str) -> Dict[str, Any]:
    content = content.strip()
    if content.startswith("```json"):
        content = content[7:]
    if content.startswith("```"):
        content = content[3:]
    if content.endswith("```"):
        content = content[:-3]
    content = content.strip()
    return json.loads(content)


def _event_summary_for_prompt(event: Event, max_titles: int = 5) -> Dict[str, Any]:
    return {
        "id": event.id,
        "name": event.name,
        "description": event.description,
        "last_article_at": event.last_article_at.isoformat() if event.last_article_at else None,
        "recent_titles": [a.title for a in (event.articles or [])[:max_titles] if a.title],
    }


def _article_for_prompt(article: Article) -> Dict[str, Any]:
    snippet = (article.scraped_text_content or article.rss_description or "")[:500]
    return {
        "id": article.id,
        "title": article.title,
        "source": article.publisher_name,
        "published_date": article.published_date.isoformat() if article.published_date else None,
        "snippet": snippet,
    }


def fetch_ungrouped_articles() -> List[Article]:
    with db_session_scope() as db:
        rows = (
            db.query(Article)
            .filter(Article.event_id.is_(None))
            .order_by(desc(Article.published_date))
            .all()
        )
        for r in rows:
            _ = r.title
            _ = r.publisher_name
        return rows


def fetch_active_events() -> Tuple[List[Event], List[Event]]:
    with db_session_scope() as db:
        active = (
            db.query(Event)
            .filter(Event.status == "active")
            .order_by(desc(Event.last_article_at))
            .all()
        )
        cooling = (
            db.query(Event)
            .filter(Event.status == "cooling")
            .order_by(desc(Event.last_article_at))
            .all()
        )
        for ev in active + cooling:
            _ = ev.name
            _ = ev.description
            _ = ev.articles
        return active, cooling


def apply_assignments(assignments: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = {
        "existing": 0,
        "new": 0,
        "uncategorized": 0,
        "errors": 0,
    }
    now = datetime.now(timezone.utc)

    with db_session_scope() as db:
        for a in assignments:
            try:
                article_id = a.get("article_id")
                decision = a.get("decision")
                article = db.query(Article).filter(Article.id == article_id).first()
                if not article:
                    counts["errors"] += 1
                    continue

                if decision == "existing":
                    ev_id = a.get("event_id")
                    event = db.query(Event).filter(Event.id == ev_id).first()
                    if not event:
                        counts["errors"] += 1
                        continue
                    article.event_id = event.id
                    article.importance_score = float(a.get("importance_score") or 0.5)
                    article.grouping_confidence = float(a.get("confidence") or 0.0)
                    article.grouped_at = now
                    if not event.last_article_at or (article.published_date and article.published_date > event.last_article_at):
                        event.last_article_at = article.published_date or now
                    event.status = "active"
                    event.archived_at = None
                    counts["existing"] += 1
                elif decision == "new":
                    name = (a.get("event_name") or "").strip()
                    if not name:
                        counts["errors"] += 1
                        continue
                    new_event = Event(
                        name=name,
                        description=None,
                        status="active",
                        last_article_at=article.published_date or now,
                    )
                    db.add(new_event)
                    db.flush()
                    article.event_id = new_event.id
                    article.importance_score = float(a.get("importance_score") or 0.5)
                    article.grouping_confidence = float(a.get("confidence") or 0.0)
                    article.grouped_at = now
                    counts["new"] += 1
                elif decision == "uncategorized":
                    counts["uncategorized"] += 1
                    article.grouped_at = now
                    article.grouping_confidence = float(a.get("confidence") or 0.0)
                else:
                    counts["errors"] += 1
            except Exception as e:
                logger.error(f"GROUPING: error applying assignment {a}: {e}", exc_info=True)
                counts["errors"] += 1
    logger.info(f"GROUPING: applied assignments {counts}")
    return counts


async def assign_new_articles(llm: ChatOpenAI) -> Dict[str, int]:
    articles = fetch_ungrouped_articles()
    if not articles:
        logger.info("GROUPING: no ungrouped articles")
        return {"existing": 0, "new": 0, "uncategorized": 0, "errors": 0, "skipped": 1}

    active, cooling = fetch_active_events()
    active_payload = [_event_summary_for_prompt(e) for e in active]
    cooling_payload = [_event_summary_for_prompt(e) for e in cooling]
    articles_payload = [_article_for_prompt(a) for a in articles]

    few_shot = build_few_shot_examples(limit=5)
    from .prompts import build_few_shot_block
    few_shot_block = build_few_shot_block(few_shot)

    prompt = build_group_assign_prompt(
        active_events=active_payload,
        cooling_events=cooling_payload,
        articles=articles_payload,
        few_shot_block=few_shot_block,
    )

    try:
        response = await llm.agenerate([[HumanMessage(content=prompt)]])
        content = response.generations[0][0].text
    except Exception as e:
        logger.error(f"GROUPING: LLM call failed: {e}", exc_info=True)
        return {"existing": 0, "new": 0, "uncategorized": 0, "errors": 1, "skipped": 0}

    try:
        parsed = _parse_response(content)
    except Exception as e:
        logger.error(f"GROUPING: failed to parse LLM response: {e}\nContent: {content[:1000]}")
        return {"existing": 0, "new": 0, "uncategorized": 0, "errors": 1, "skipped": 0}

    assignments = parsed.get("assignments", [])
    counts = apply_assignments(assignments)
    return counts
