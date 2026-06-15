# app/grouping/lifecycle.py
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from sqlalchemy import desc

from ..database import db_session_scope
from ..database.models import Event, Article, ReclusterProposal
from .. import config as app_config

logger = logging.getLogger(__name__)


def _clamp_importance(score: Optional[float]) -> float:
    if score is None:
        return 0.5
    try:
        s = float(score)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, s))


def compute_new_expiry(
    current_expires_at: Optional[datetime],
    importance_score: Optional[float],
    now: Optional[datetime] = None,
) -> datetime:
    now = now or datetime.now(timezone.utc)
    base = timedelta(hours=app_config.EVENT_TTL_BASE_HOURS)
    cap = now + timedelta(hours=app_config.EVENT_TTL_MAX_HOURS)
    if app_config.EVENT_TTL_IMPORTANCE_WEIGHTED:
        ext = base * (0.5 + _clamp_importance(importance_score))
    else:
        ext = base
    anchor = current_expires_at if current_expires_at else now
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    new_expiry = max(now, anchor) + ext
    return min(new_expiry, cap)


def initial_expiry(
    anchor: Optional[datetime] = None,
    importance_score: Optional[float] = None,
    now: Optional[datetime] = None,
) -> datetime:
    now = now or datetime.now(timezone.utc)
    base = timedelta(hours=app_config.EVENT_TTL_BASE_HOURS)
    cap = now + timedelta(hours=app_config.EVENT_TTL_MAX_HOURS)
    if app_config.EVENT_TTL_IMPORTANCE_WEIGHTED:
        ext = base * (0.5 + _clamp_importance(importance_score))
    else:
        ext = base
    if anchor is not None and anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    start = anchor or now
    return min(start + ext, cap)


def extend_expiry_on_event(
    event: Event,
    importance_score: Optional[float],
    now: Optional[datetime] = None,
) -> None:
    event.expires_at = compute_new_expiry(event.expires_at, importance_score, now=now)


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
        from ..database.models import Article
        newest = (
            db.query(Article)
            .filter(Article.event_id == event_id)
            .order_by(desc(Article.published_date), desc(Article.fetched_at))
            .first()
        )
        score = newest.importance_score if newest else None
        ev.expires_at = initial_expiry(
            anchor=ev.last_article_at or datetime.now(timezone.utc),
            importance_score=score,
        )
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
            Event.status.in_(("active", "cooling")),
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
    archive_cutoff = now - timedelta(days=app_config.AUTO_ARCHIVE_DAYS)
    cooling_cutoff = now - timedelta(days=3)
    archived_count = 0
    cooling_count = 0
    reaped_count = 0
    purged_count = 0
    purged_empty = 0

    with db_session_scope() as db:
        cooling_targets = (
            db.query(Event)
            .filter(
                Event.status == "active",
                Event.last_article_at.isnot(None),
                Event.last_article_at < cooling_cutoff,
                Event.last_article_at >= archive_cutoff,
                Event.expires_at.is_(None),
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
                Event.expires_at.is_(None),
            )
            .all()
        )
        for ev in archive_targets:
            ev.status = "archived"
            ev.archived_at = now
            archived_count += 1

        reap_targets = (
            db.query(Event)
            .filter(
                Event.status.in_(("active", "cooling")),
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
        f"LIFECYCLE: cooling={cooling_count}, archived(legacy)={archived_count}, "
        f"reaped(expires_at)={reaped_count}, purged(ancient)={purged_count}, "
        f"purged(empty)={purged_empty}"
    )
    return {
        "cooled": cooling_count,
        "archived": archived_count,
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
