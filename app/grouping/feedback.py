# app/grouping/feedback.py
import logging
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import desc

from ..database.models import GroupingFeedback, Article, Event
from ..database import db_session_scope

logger = logging.getLogger(__name__)


def record_correction(
    article_id: int,
    kind: str,
    original_event_id: Optional[int] = None,
    corrected_event_id: Optional[int] = None,
    note: Optional[str] = None,
) -> None:
    with db_session_scope() as db:
        fb = GroupingFeedback(
            article_id=article_id,
            original_event_id=original_event_id,
            corrected_event_id=corrected_event_id,
            kind=kind,
            note=note,
        )
        db.add(fb)
    logger.info(
        f"GROUPING_FEEDBACK: recorded {kind} for article_id={article_id} "
        f"({original_event_id} -> {corrected_event_id})"
    )


def build_few_shot_examples(limit: int = 5) -> List[Dict[str, Any]]:
    with db_session_scope() as db:
        rows = (
            db.query(GroupingFeedback)
            .order_by(desc(GroupingFeedback.created_at))
            .limit(limit)
            .all()
        )
        out: List[Dict[str, Any]] = []
        for r in rows:
            original_name = None
            corrected_name = None
            if r.original_event_id:
                ev = db.query(Event).filter(Event.id == r.original_event_id).first()
                original_name = ev.name if ev else None
            if r.corrected_event_id:
                ev = db.query(Event).filter(Event.id == r.corrected_event_id).first()
                corrected_name = ev.name if ev else None
            out.append(
                {
                    "id": r.id,
                    "article_id": r.article_id,
                    "kind": r.kind,
                    "original_event_id": r.original_event_id,
                    "corrected_event_id": r.corrected_event_id,
                    "original_event_name": original_name,
                    "corrected_event_name": corrected_name,
                    "note": r.note,
                }
            )
        return out
