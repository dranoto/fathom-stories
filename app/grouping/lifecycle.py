# app/grouping/lifecycle.py
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from sqlalchemy import desc

from ..database import db_session_scope
from ..database.models import Event, Article, ReclusterProposal
from .. import config as app_config

logger = logging.getLogger(__name__)


def reset_expiry() -> datetime:
    """
    Returns the event's new expiry timestamp: now + EVENT_TTL_RESET_HOURS.

    Every article reset is anchored to now. The timer never exceeds 48h from
    the moment of assignment, regardless of the article's published_date.
    A freshly-arrived article gives a full 48h window. A stale article also
    gives a full 48h window, anchored to now. A future-dated article does
    not push the cap forward.
    """
    return datetime.now(timezone.utc) + timedelta(hours=app_config.EVENT_TTL_RESET_HOURS)


def reset_expiry_on_event(event: Event) -> None:
    event.expires_at = reset_expiry()


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
        newest = (
            db.query(Article)
            .filter(Article.event_id == event_id)
            .order_by(desc(Article.published_date), desc(Article.fetched_at))
            .first()
        )
        ev.expires_at = reset_expiry()
    logger.info(f"LIFECYCLE: revived event {event_id} (expires_at={ev.expires_at})")
    return True


def _reap_expired(db, event_ids: list) -> int:
    if not event_ids:
        return 0
    now = datetime.now(timezone.utc)
    rows = (
        db.query(Event)
        .filter(
            Event.id.in_(event_ids),
            Event.status == "active",
            Event.expires_at.isnot(None),
            Event.expires_at < now,
        )
        .all()
    )
    for ev in rows:
        ev.status = "archived"
        ev.archived_at = now
    return len(rows)


def reap_expired_in_list(db, event_ids: list) -> int:
    return _reap_expired(db, event_ids)


def tick() -> dict:
    now = datetime.now(timezone.utc)
    reaped_count = 0
    purged_count = 0
    purged_empty = 0

    with db_session_scope() as db:
        reap_targets = (
            db.query(Event)
            .filter(
                Event.status == "active",
                Event.expires_at.isnot(None),
                Event.expires_at < now,
            )
            .all()
        )
        for ev in reap_targets:
            ev.status = "archived"
            ev.archived_at = now
            reaped_count += 1

        empty_cutoff = now - timedelta(seconds=app_config.PURGE_EMPTY_FLOOR_SECONDS)
        empty_id_rows = (
            db.query(Event.id)
            .outerjoin(Article, Article.event_id == Event.id)
            .filter(Article.id.is_(None))
            .filter(Event.created_at < empty_cutoff)
            .limit(app_config.PURGE_EMPTY_BATCH_LIMIT)
            .all()
        )
        empty_ids = [row[0] for row in empty_id_rows]
        if empty_ids:
            (
                db.query(Event)
                .filter(Event.id.in_(empty_ids))
                .delete(synchronize_session="fetch")
            )
            purged_empty = len(empty_ids)

    purged_count = purge_ancient_archives(limit=app_config.PURGE_BATCH_LIMIT)

    logger.info(
        f"LIFECYCLE: reaped(expires_at)={reaped_count}, purged(ancient)={purged_count}, "
        f"purged(empty)={purged_empty}"
    )
    return {
        "reaped": reaped_count,
        "purged": purged_count,
        "purged_empty": purged_empty,
    }


def purge_ancient_archives(limit: int = 200) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=app_config.PURGE_ARCHIVE_AFTER_DAYS)
    purged_events = 0
    purged_proposals = 0
    with db_session_scope() as db:
        event_ids = [
            row[0]
            for row in db.query(Event.id)
            .filter(Event.status == "archived", Event.archived_at < cutoff)
            .limit(limit)
            .all()
        ]
        if event_ids:
            purged_events = (
                db.query(Event)
                .filter(Event.id.in_(event_ids))
                .delete(synchronize_session=False)
            )
        purged_proposals = (
            db.query(ReclusterProposal)
            .filter(ReclusterProposal.created_at < cutoff)
            .delete(synchronize_session=False)
        )
    if purged_events or purged_proposals:
        logger.info(
            f"LIFECYCLE: purged {purged_events} archived events older than "
            f"{app_config.PURGE_ARCHIVE_AFTER_DAYS}d and {purged_proposals} old recluster_proposals"
        )
    return purged_events
