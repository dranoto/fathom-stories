# app/routers/visits.py
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session as SQLAlchemySession

from .. import database
from ..database.models import EventVisit
from ..dependencies import get_visitor_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/events", tags=["visits"])


@router.post("/{event_id}/visit")
async def mark_event_visited(
    event_id: int,
    visitor_id: str = Depends(get_visitor_id),
    db: SQLAlchemySession = Depends(database.get_db),
):
    now = datetime.now(timezone.utc)
    visit = (
        db.query(EventVisit)
        .filter(EventVisit.visitor_id == visitor_id, EventVisit.event_id == event_id)
        .first()
    )
    if visit:
        visit.last_visited_at = now
    else:
        visit = EventVisit(visitor_id=visitor_id, event_id=event_id, last_visited_at=now)
        db.add(visit)
    db.commit()
    return {"event_id": event_id, "last_visited_at": now}
