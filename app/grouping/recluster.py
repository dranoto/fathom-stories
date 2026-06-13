# app/grouping/recluster.py
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Tuple, Optional
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from sqlalchemy import desc

from ..database import db_session_scope
from ..database.models import Article, Event, ReclusterProposal
from .. import config as app_config
from .prompts import build_recluster_prompt, build_few_shot_block
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


def fetch_recluster_window() -> Tuple[List[Event], List[Event], List[Event], List[Article]]:
    cutoff_for_archived = datetime.now(timezone.utc) - timedelta(days=app_config.ARCHIVE_REVIVE_WINDOW_DAYS)
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
        archived = (
            db.query(Event)
            .filter(
                Event.status == "archived",
                Event.archived_at >= cutoff_for_archived,
            )
            .order_by(desc(Event.archived_at))
            .all()
        )
        recent_cutoff = datetime.now(timezone.utc) - timedelta(days=14)
        unassigned = (
            db.query(Article)
            .filter(Article.event_id.is_(None), Article.published_date >= recent_cutoff)
            .order_by(desc(Article.published_date))
            .limit(50)
            .all()
        )
        for ev in active + cooling + archived:
            _ = ev.name
            _ = ev.description
            _ = ev.articles
        for a in unassigned:
            _ = a.title
            _ = a.publisher_name
        return active, cooling, archived, unassigned


def _event_payload(ev: Event) -> Dict[str, Any]:
    return {
        "id": ev.id,
        "name": ev.name,
        "description": ev.description,
        "last_article_at": ev.last_article_at.isoformat() if ev.last_article_at else None,
        "recent_titles": [a.title for a in (ev.articles or [])[:5] if a.title],
    }


def _article_payload(a: Article) -> Dict[str, Any]:
    return {
        "id": a.id,
        "title": a.title,
        "source": a.publisher_name,
        "published_date": a.published_date.isoformat() if a.published_date else None,
        "snippet": (a.scraped_text_content or a.rss_description or "")[:300],
    }


def write_proposals(proposal_payload: Dict[str, Any]) -> List[ReclusterProposal]:
    written: List[ReclusterProposal] = []
    now = datetime.now(timezone.utc)
    with db_session_scope() as db:
        for mc in proposal_payload.get("merge_candidates", []):
            p = ReclusterProposal(
                kind="merge",
                payload=mc,
                created_at=now,
                rationale=mc.get("reason"),
            )
            db.add(p)
            written.append(p)
        for sc in proposal_payload.get("split_candidates", []):
            p = ReclusterProposal(
                kind="split",
                payload=sc,
                created_at=now,
                rationale=f"split: {sc.get('suggested_new_name')}",
            )
            db.add(p)
            written.append(p)
        for cid in proposal_payload.get("cooling_events", []):
            p = ReclusterProposal(
                kind="cool",
                payload={"event_id": cid},
                created_at=now,
            )
            db.add(p)
            written.append(p)
        for rid in proposal_payload.get("reviving_events", []):
            p = ReclusterProposal(
                kind="revive",
                payload={"event_id": rid},
                created_at=now,
            )
            db.add(p)
            written.append(p)
        for ne in proposal_payload.get("new_events", []):
            p = ReclusterProposal(
                kind="new",
                payload=ne,
                created_at=now,
                rationale=ne.get("name"),
            )
            db.add(p)
            written.append(p)
    logger.info(f"RECLUSTER: wrote {len(written)} proposals")
    return written


async def generate_recluster_diff(llm: ChatOpenAI, auto_apply: bool = False) -> Dict[str, Any]:
    active, cooling, archived, unassigned = fetch_recluster_window()
    if not active and not cooling and not archived:
        return {"status": "no_events", "proposals": 0}

    active_p = [_event_payload(e) for e in active]
    cooling_p = [_event_payload(e) for e in cooling]
    archived_p = [_event_payload(e) for e in archived]
    unassigned_p = [_article_payload(a) for a in unassigned]

    few_shot = build_few_shot_examples(limit=5)
    few_shot_block = build_few_shot_block(few_shot)

    prompt = build_recluster_prompt(
        active_events=active_p,
        cooling_events=cooling_p,
        archived_events=archived_p,
        unassigned_articles=unassigned_p,
        few_shot_block=few_shot_block,
    )

    try:
        response = await llm.agenerate([[HumanMessage(content=prompt)]])
        content = response.generations[0][0].text
    except Exception as e:
        logger.error(f"RECLUSTER: LLM call failed: {e}", exc_info=True)
        return {"status": "llm_error", "proposals": 0}

    try:
        parsed = _parse_response(content)
    except Exception as e:
        logger.error(f"RECLUSTER: parse failed: {e}\nContent: {content[:1000]}")
        return {"status": "parse_error", "proposals": 0}

    proposals = write_proposals(parsed)

    if auto_apply:
        from . import lifecycle
        for p in proposals:
            if p.kind in ("cool", "revive"):
                try:
                    payload = p.payload or {}
                    if p.kind == "cool":
                        lifecycle.set_event_status(payload.get("event_id"), "cooling")
                    elif p.kind == "revive":
                        lifecycle.revive_event(payload.get("event_id"))
                    p.applied = 1
                    p.applied_at = datetime.now(timezone.utc)
                except Exception as e:
                    logger.error(f"RECLUSTER: auto-apply failed for {p.id}: {e}", exc_info=True)

    return {"status": "ok", "proposals": len(proposals), "auto_applied": auto_apply}


def mark_events_seen_in_recluster() -> int:
    with db_session_scope() as db:
        now = datetime.now(timezone.utc)
        count = (
            db.query(Event)
            .filter(Event.status.in_(("active", "cooling")))
            .update({Event.last_seen_in_recluster_at: now}, synchronize_session=False)
        )
    return count
