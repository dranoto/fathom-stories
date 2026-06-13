# app/grouping/lifecycle.py
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from sqlalchemy import desc

from ..database import db_session_scope
from ..database.models import Event
from .. import config as app_config

logger = logging.getLogger(__name__)


def set_event_status(event_id: int, status: str) -> bool:
    with db_session_scope() as db:
        ev = db.query(Event).filter(Event.id == event_id).first()
        if not ev:
            return False
        ev.status = status
        if status == "archived":
            ev.archived_at = datetime.now(timezone.utc)
        elif status == "active":
            ev.archived_at = None
    return True


def revive_event(event_id: int) -> bool:
    with db_session_scope() as db:
        ev = db.query(Event).filter(Event.id == event_id).first()
        if not ev:
            return False
        ev.status = "active"
        ev.archived_at = None
    logger.info(f"LIFECYCLE: revived event {event_id}")
    return True


def tick() -> dict:
    now = datetime.now(timezone.utc)
    archive_cutoff = now - timedelta(days=app_config.AUTO_ARCHIVE_DAYS)
    cooling_cutoff = now - timedelta(days=3)
    archived_count = 0
    cooling_count = 0
    revived_count = 0

    with db_session_scope() as db:
        cooling_targets = (
            db.query(Event)
            .filter(
                Event.status == "active",
                Event.last_article_at.isnot(None),
                Event.last_article_at < cooling_cutoff,
                Event.last_article_at >= archive_cutoff,
            )
            .all()
        )
        for ev in cooling_targets:
            ev.status = "cooling"
            cooling_count += 1

        archive_targets = (
            db.query(Event)
            .filter(
                Event.status.in_(("active", "cooling")),
                Event.last_article_at.isnot(None),
                Event.last_article_at < archive_cutoff,
            )
            .all()
        )
        for ev in archive_targets:
            ev.status = "archived"
            ev.archived_at = now
            archived_count += 1

    logger.info(
        f"LIFECYCLE: cooling={cooling_count}, archived={archived_count}, revived={revived_count}"
    )
    return {
        "cooled": cooling_count,
        "archived": archived_count,
        "revived": revived_count,
    }
