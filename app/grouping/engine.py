# app/grouping/engine.py
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Tuple

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from sqlalchemy import desc, or_, func, and_

from ..database import db_session_scope
from ..database.models import Article, Event
from .. import config as app_config
from .prompts import build_group_assign_prompt, build_few_shot_block, build_regroup_prompt
from .feedback import build_few_shot_examples
from .content_classifier import classify_title
from .lifecycle import extend_expiry_on_event, initial_expiry

logger = logging.getLogger(__name__)


def _normalize_event_name(name: str) -> str:
    if not name:
        return ""
    return " ".join(name.strip().lower().split())


def find_or_create_event(
    db,
    name: str,
    now: datetime,
    importance_score: Optional[float] = None,
    anchor: Optional[datetime] = None,
) -> Tuple[Event, str]:
    """
    Reuse an existing event with the same normalized name, or create a new one.

    Match scope: active + cooling + archived within ARCHIVE_REVIVE_WINDOW_DAYS.
    Reuse order: oldest created_at first (so we never lose history).

    Returns (event, outcome) where outcome is one of:
      "created"  — a brand new Event row was created
      "reused"   — matched an existing active/cooling event
      "revived"  — matched an archived event within the revival window; it is now active
    """
    normalized = _normalize_event_name(name)
    if normalized:
        revival_cutoff = now - timedelta(days=app_config.ARCHIVE_REVIVE_WINDOW_DAYS)
        existing = (
            db.query(Event)
            .filter(func.lower(Event.name) == normalized)
            .filter(
                or_(
                    Event.status.in_(("active", "cooling")),
                    and_(
                        Event.status == "archived",
                        Event.archived_at.isnot(None),
                        Event.archived_at >= revival_cutoff,
                    ),
                )
            )
            .order_by(Event.created_at.asc(), Event.id.asc())
            .first()
        )
        if existing:
            outcome = "revived" if existing.status == "archived" else "reused"
            if existing.status == "archived":
                existing.status = "active"
                existing.archived_at = None
            if anchor:
                anchor_aware = anchor if anchor.tzinfo else anchor.replace(tzinfo=timezone.utc)
                existing_la = existing.last_article_at
                if existing_la is not None and existing_la.tzinfo is None:
                    existing_la = existing_la.replace(tzinfo=timezone.utc)
                if not existing_la or anchor_aware > existing_la:
                    existing.last_article_at = anchor
            return existing, outcome

    new_event = Event(
        name=name.strip(),
        status="active",
        last_article_at=anchor or now,
        expires_at=initial_expiry(anchor=anchor, importance_score=importance_score, now=now),
    )
    db.add(new_event)
    db.flush()
    return new_event, "created"


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
        "content_type": classify_title(article.title or ""),
    }


def fetch_ungrouped_articles(limit: Optional[int] = None) -> List[Article]:
    with db_session_scope() as db:
        q = db.query(Article).filter(Article.event_id.is_(None)).order_by(desc(Article.published_date))
        if limit:
            q = q.limit(limit)
        rows = q.all()
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
    counts, _ = _apply_live(assignments)
    return counts


def _apply_live(assignments: List[Dict[str, Any]]) -> Tuple[Dict[str, int], Dict[int, List[int]]]:
    counts = {
        "existing": 0,
        "new": 0,
        "reused": 0,
        "uncategorized": 0,
        "errors": 0,
    }
    now = datetime.now(timezone.utc)
    event_increments: Dict[int, List[int]] = defaultdict(list)

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
                    article.proposed_event_name = None
                    importance = float(a.get("importance_score") or 0.5)
                    article.importance_score = importance
                    article.grouping_confidence = float(a.get("confidence") or 0.0)
                    article.grouped_at = now
                    if not event.last_article_at or (article.published_date and article.published_date > event.last_article_at):
                        event.last_article_at = article.published_date or now
                    event.status = "active"
                    event.archived_at = None
                    extend_expiry_on_event(event, importance, now=now)
                    event_increments[event.id].append(article_id)
                    counts["existing"] += 1
                elif decision == "new":
                    name = (a.get("event_name") or "").strip()
                    if not name:
                        counts["errors"] += 1
                        continue
                    importance = float(a.get("importance_score") or 0.5)
                    new_event, outcome = find_or_create_event(
                        db,
                        name,
                        now=now,
                        importance_score=importance,
                        anchor=article.published_date or now,
                    )
                    article.event_id = new_event.id
                    article.proposed_event_name = None
                    article.importance_score = importance
                    article.grouping_confidence = float(a.get("confidence") or 0.0)
                    article.grouped_at = now
                    if outcome == "created":
                        counts["new"] += 1
                    else:
                        counts[outcome] += 1
                        extend_expiry_on_event(new_event, importance, now=now)
                elif decision == "uncategorized":
                    counts["uncategorized"] += 1
                    article.grouped_at = now
                    article.proposed_event_name = None
                    article.grouping_confidence = float(a.get("confidence") or 0.0)
                else:
                    counts["errors"] += 1
            except Exception as e:
                logger.error(f"GROUPING: error applying assignment {a}: {e}", exc_info=True)
                counts["errors"] += 1
    logger.info(f"GROUPING: applied assignments {counts}")
    return counts, dict(event_increments)


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
    counts, event_increments = _apply_live(assignments)

    from .summary_service import generate_incremental_summary_for_event
    for event_id, new_ids in event_increments.items():
        try:
            await generate_incremental_summary_for_event(event_id, new_ids, llm)
        except Exception as e:
            logger.error(f"Auto-incremental summary failed for event {event_id}: {e}", exc_info=True)

    return counts


async def regroup_uncategorized(llm: ChatOpenAI) -> Dict[str, int]:
    articles = fetch_ungrouped_articles(limit=100)
    if not articles:
        logger.info("REGROUP: no ungrouped articles")
        return {"existing": 0, "new_events": 0, "new_singletons": 0, "uncategorized": 0, "errors": 0, "skipped": 1}

    active, cooling = fetch_active_events()
    active_payload = [_event_summary_for_prompt(e) for e in active]
    cooling_payload = [_event_summary_for_prompt(e) for e in cooling]
    articles_payload = [_article_for_prompt(a) for a in articles]

    few_shot = build_few_shot_examples(limit=5)
    few_shot_block = build_few_shot_block(few_shot)

    prompt = build_regroup_prompt(
        active_events=active_payload,
        cooling_events=cooling_payload,
        articles=articles_payload,
        few_shot_block=few_shot_block,
    )

    try:
        response = await llm.agenerate([[HumanMessage(content=prompt)]])
        content = response.generations[0][0].text
    except Exception as e:
        logger.error(f"REGROUP: LLM call failed: {e}", exc_info=True)
        return {"existing": 0, "new_events": 0, "new_singletons": 0, "uncategorized": 0, "errors": 1, "skipped": 0}

    try:
        parsed = _parse_response(content)
    except Exception as e:
        logger.error(f"REGROUP: failed to parse LLM response: {e}\nContent: {content[:1000]}")
        return {"existing": 0, "new_events": 0, "new_singletons": 0, "uncategorized": 0, "errors": 1, "skipped": 0}

    assignments = parsed.get("assignments", [])
    counts, (new_event_ids, event_increments) = _apply_regroup_inner(assignments)

    from .summary_service import (
        generate_initial_summary_for_event,
        generate_incremental_summary_for_event,
    )
    for new_event_id in new_event_ids:
        try:
            await generate_initial_summary_for_event(new_event_id, llm)
        except Exception as e:
            logger.error(f"Auto-initial summary failed for event {new_event_id}: {e}", exc_info=True)
    for event_id, new_ids in event_increments.items():
        if event_id in new_event_ids:
            continue
        try:
            await generate_incremental_summary_for_event(event_id, new_ids, llm)
        except Exception as e:
            logger.error(f"Auto-incremental summary failed for event {event_id}: {e}", exc_info=True)

    dedup_counts: Dict[str, int] = {}
    try:
        from .dedup import dedup_events
        dedup_counts = await dedup_events(llm)
    except Exception as e:
        logger.error(f"REGROUP: dedup pass failed: {e}", exc_info=True)

    counts["dedup"] = dedup_counts
    return counts


def apply_regroup_assignments(assignments: List[Dict[str, Any]]) -> Dict[str, int]:
    counts, _ = _apply_regroup_inner(assignments)
    return counts


def _apply_regroup_inner(assignments: List[Dict[str, Any]]) -> Tuple[Dict[str, int], Tuple[List[int], Dict[int, List[int]]]]:
    counts = {
        "existing": 0,
        "new_events": 0,
        "revived": 0,
        "reused": 0,
        "new_singletons": 0,
        "uncategorized": 0,
        "errors": 0,
    }
    now = datetime.now(timezone.utc)
    new_event_ids: List[int] = []
    event_increments: Dict[int, List[int]] = defaultdict(list)

    new_clusters: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for a in assignments:
        try:
            decision = a.get("decision")
            if decision == "new":
                name = (a.get("event_name") or "").strip()
                if name:
                    new_clusters[name].append(a)
        except Exception:
            counts["errors"] += 1

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
                    article.proposed_event_name = None
                    importance = float(a.get("importance_score") or 0.5)
                    article.importance_score = importance
                    article.grouping_confidence = float(a.get("confidence") or 0.0)
                    article.grouped_at = now
                    if not event.last_article_at or (article.published_date and article.published_date > event.last_article_at):
                        event.last_article_at = article.published_date or now
                    event.status = "active"
                    event.archived_at = None
                    extend_expiry_on_event(event, importance, now=now)
                    event_increments[event.id].append(article_id)
                    counts["existing"] += 1
                elif decision == "uncategorized":
                    counts["uncategorized"] += 1
                    article.proposed_event_name = None
                    article.grouping_confidence = float(a.get("confidence") or 0.0)
                    article.grouped_at = now
                else:
                    counts["errors"] += 1
            except Exception as e:
                logger.error(f"REGROUP: error applying assignment {a}: {e}", exc_info=True)
                counts["errors"] += 1

        for name, items in new_clusters.items():
            if len(items) >= 2:
                first = items[0]
                first_article = db.query(Article).filter(Article.id == first.get("article_id")).first()
                if not first_article:
                    counts["errors"] += 1
                    continue
                first_importance = float(first.get("importance_score") or 0.5)
                new_event, outcome = find_or_create_event(
                    db,
                    name,
                    now=now,
                    importance_score=first_importance,
                    anchor=first_article.published_date or now,
                )
                if outcome == "created":
                    new_event_ids.append(new_event.id)
                    counts["new_events"] += 1
                else:
                    counts[outcome] += 1
                for item in items:
                    art = db.query(Article).filter(Article.id == item.get("article_id")).first()
                    if not art:
                        continue
                    art.event_id = new_event.id
                    art.proposed_event_name = None
                    importance = float(item.get("importance_score") or 0.5)
                    art.importance_score = importance
                    art.grouping_confidence = float(item.get("confidence") or 0.0)
                    art.grouped_at = now
                    if not new_event.last_article_at or (art.published_date and art.published_date > new_event.last_article_at):
                        new_event.last_article_at = art.published_date or now
                    extend_expiry_on_event(new_event, importance, now=now)
                    event_increments[new_event.id].append(art.id)
            else:
                for item in items:
                    art = db.query(Article).filter(Article.id == item.get("article_id")).first()
                    if art:
                        art.proposed_event_name = name
                        art.importance_score = float(item.get("importance_score") or 0.5)
                        art.grouping_confidence = float(item.get("confidence") or 0.0)
                        art.grouped_at = now
                counts["new_singletons"] += 1

    logger.info(f"REGROUP: applied assignments {counts}")
    return counts, (new_event_ids, dict(event_increments))
