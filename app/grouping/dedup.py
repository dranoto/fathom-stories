# app/grouping/dedup.py
import json
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from sqlalchemy import desc

from ..database import db_session_scope
from ..database.models import Event, Article, EventSummary, GroupingFeedback
from .. import config as app_config

logger = logging.getLogger(__name__)

DEDUP_CONFIDENCE_THRESHOLD = 0.7


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


def _event_payload(ev: Event, max_titles: int = 3) -> Dict[str, Any]:
    titles = []
    for a in (ev.articles or [])[:max_titles]:
        if a.title:
            titles.append(a.title)
    return {
        "id": ev.id,
        "name": ev.name,
        "description": ev.description,
        "last_article_at": ev.last_article_at.isoformat() if ev.last_article_at else None,
        "recent_titles": titles,
    }


def fetch_active_and_cooling_events() -> List[Event]:
    with db_session_scope() as db:
        events = (
            db.query(Event)
            .filter(Event.status.in_(("active", "cooling")))
            .order_by(desc(Event.last_article_at), desc(Event.created_at))
            .all()
        )
        for ev in events:
            _ = ev.name
            _ = ev.description
            _ = ev.articles
        return events


def merge_events(db, primary_id: int, secondary_id: int, kind: str = "dedup_merge", note: Optional[str] = None) -> bool:
    """
    Move all articles from secondary to primary, update primary's metadata, delete secondary.
    Records a GroupingFeedback row for audit. Returns True on success.
    """
    if primary_id == secondary_id:
        return False
    primary = db.query(Event).filter(Event.id == primary_id).first()
    secondary = db.query(Event).filter(Event.id == secondary_id).first()
    if not primary or not secondary:
        logger.warning(f"DEDUP: merge skipped — primary={primary_id} or secondary={secondary_id} not found")
        return False

    secondary_articles = db.query(Article).filter(Article.event_id == secondary_id).all()
    if secondary_articles:
        max_published = max(
            (a.published_date for a in secondary_articles if a.published_date),
            default=None,
        )
        if max_published and (not primary.last_article_at or max_published > primary.last_article_at):
            primary.last_article_at = max_published

    for a in secondary_articles:
        a.event_id = primary_id

    max_importance = max((a.importance_score for a in secondary_articles if a.importance_score is not None), default=0.5)
    from .lifecycle import reset_expiry_on_event
    reset_expiry_on_event(primary)

    primary.status = "active"
    primary.archived_at = None

    db.add(GroupingFeedback(
        article_id=0,
        original_event_id=secondary_id,
        corrected_event_id=primary_id,
        kind=kind,
        note=note or f"merged '{secondary.name}' into '{primary.name}'",
    ))

    db.query(EventSummary).filter(EventSummary.event_id == secondary_id).delete(synchronize_session=False)
    db.delete(secondary)
    logger.info(f"DEDUP: merged event {secondary_id} ({secondary.name!r}) into {primary_id} ({primary.name!r})")
    return True


async def dedup_events(llm: ChatOpenAI) -> Dict[str, int]:
    """
    Run a post-regroup LLM dedup pass over all active+cooling events.
    Returns counts: {"checked": N, "merged": M, "skipped_low_confidence": K, "errors": E}.
    """
    events = fetch_active_and_cooling_events()
    if len(events) < 2:
        return {"checked": len(events), "merged": 0, "skipped_low_confidence": 0, "errors": 0}

    payload = [_event_payload(e) for e in events]
    prompt = app_config.DEFAULT_DEDUP_PROMPT.format(events_json=json.dumps(payload, indent=2, default=str))

    try:
        response = await llm.agenerate([[HumanMessage(content=prompt)]])
        content = response.generations[0][0].text
    except Exception as e:
        logger.error(f"DEDUP: LLM call failed: {e}", exc_info=True)
        return {"checked": len(events), "merged": 0, "skipped_low_confidence": 0, "errors": 1}

    try:
        parsed = _parse_response(content)
    except Exception as e:
        logger.error(f"DEDUP: failed to parse LLM response: {e}\nContent: {content[:1000]}")
        return {"checked": len(events), "merged": 0, "skipped_low_confidence": 0, "errors": 1}

    pairs = parsed.get("merge_pairs", []) or []
    if not pairs:
        return {"checked": len(events), "merged": 0, "skipped_low_confidence": 0, "errors": 0}

    event_id_set = {e.id for e in events}
    merged = 0
    skipped = 0
    with db_session_scope() as db:
        for p in pairs:
            try:
                older_id = int(p.get("older_id"))
                newer_id = int(p.get("newer_id"))
                confidence = float(p.get("confidence") or 0.0)
                reason = p.get("reason") or ""
            except (TypeError, ValueError):
                skipped += 1
                continue
            if older_id not in event_id_set or newer_id not in event_id_set:
                logger.warning(f"DEDUP: skipping pair ({older_id}, {newer_id}) — not in active/cooling set")
                skipped += 1
                continue
            if confidence < DEDUP_CONFIDENCE_THRESHOLD:
                logger.info(f"DEDUP: skipping pair ({older_id}, {newer_id}) — confidence {confidence:.2f} < {DEDUP_CONFIDENCE_THRESHOLD}")
                skipped += 1
                continue
            if merge_events(db, older_id, newer_id, kind="dedup_merge", note=f"llm: {reason}"):
                event_id_set.discard(newer_id)
                merged += 1

    logger.info(f"DEDUP: checked={len(events)}, merged={merged}, skipped={skipped}")
    return {"checked": len(events), "merged": merged, "skipped_low_confidence": skipped, "errors": 0}
