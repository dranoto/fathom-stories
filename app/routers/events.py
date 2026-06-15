# app/routers/events.py
import json
import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session as SQLAlchemySession
from sqlalchemy import desc, func

from .. import database
from ..database.models import Event, EventSummary, Article, ArticleRead, EventChatMessage
from ..schemas.event import (
    EventCreate, EventUpdate, EventResponse, EventDetailResponse,
    ArticleInEvent, EventSummaryResponse, EventSummaryData,
    MoveArticleRequest, MergeRequest, SplitRequest,
    ReclusterProposalOut, GroupingFeedbackOut, StatsOut,
    ChatHistoryItem, EventChatRequest, EventChatPersistRequest, EventChatResponse,
)
from ..dependencies import get_llm_summary, get_llm_chat, get_visitor_id
from ..security import verify_event_exists
from ..grouping.feedback import record_correction
from ..grouping import lifecycle as lifecycle_module
from ..grouping import chat as chat_module
from .. import config as app_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/events", tags=["events"])


async def _regen_summary_after_move(event_id: int, article_id: int, llm) -> None:
    try:
        from ..grouping.summary_service import generate_incremental_summary_for_event
        await generate_incremental_summary_for_event(event_id, [article_id], llm)
    except Exception as e:
        logger.error(f"Background summary regen after move failed for event {event_id}: {e}", exc_info=True)


async def _regen_summary_after_remove(event_id: int, llm) -> None:
    try:
        from ..grouping.summary_service import regenerate_summary_for_event
        await regenerate_summary_for_event(event_id, llm)
    except Exception as e:
        logger.error(f"Background summary regen after remove failed for event {event_id}: {e}", exc_info=True)


@router.get("", response_model=List[EventResponse])
async def list_events(
    status: Optional[str] = Query(None, description="Filter by status: active|cooling|archived"),
    min_articles: int = Query(1, ge=1, description="Only include events with at least this many articles"),
    visitor_id: str = Depends(get_visitor_id),
    db: SQLAlchemySession = Depends(database.get_db),
):
    q = db.query(Event)
    if status:
        q = q.filter(Event.status == status)
    events = q.order_by(desc(Event.last_article_at), desc(Event.created_at)).all()
    if events:
        try:
            from ..grouping.lifecycle import reap_expired_in_list
            reap_expired_in_list(db, [e.id for e in events])
            db.commit()
            if status:
                events = [e for e in events if e.status == status]
        except Exception as e:
            logger.warning(f"Lazy reap failed in list_events: {e}", exc_info=True)
            db.rollback()
    if not events:
        return []

    event_ids = [e.id for e in events]
    article_counts = dict(
        db.query(Article.event_id, func.count(Article.id))
        .filter(Article.event_id.in_(event_ids))
        .group_by(Article.event_id)
        .all()
    )

    if min_articles > 0:
        events = [e for e in events if article_counts.get(e.id, 0) >= min_articles]
        if not events:
            return []
    feed_counts = dict(
        db.query(Article.event_id, func.count(func.distinct(Article.feed_source_id)))
        .filter(Article.event_id.in_(event_ids), Article.feed_source_id.isnot(None))
        .group_by(Article.event_id)
        .all()
    )
    importance_avgs = dict(
        db.query(Article.event_id, func.avg(Article.importance_score))
        .filter(Article.event_id.in_(event_ids), Article.importance_score.isnot(None))
        .group_by(Article.event_id)
        .all()
    )
    read_article_ids_subq = (
        db.query(ArticleRead.article_id)
        .filter(ArticleRead.visitor_id == visitor_id)
        .subquery()
    )
    unread_counts = dict(
        db.query(Article.event_id, func.count(Article.id))
        .filter(
            Article.event_id.in_(event_ids),
            Article.id.notin_(read_article_ids_subq),
        )
        .group_by(Article.event_id)
        .all()
    )

    from ..database.models import EventVisit
    from datetime import datetime, timezone
    visit_rows = (
        db.query(EventVisit.event_id, EventVisit.last_visited_at)
        .filter(EventVisit.visitor_id == visitor_id, EventVisit.event_id.in_(event_ids))
        .all()
    )
    visits_by_event = {row[0]: row[1] for row in visit_rows}
    new_since_visit: dict = {}
    for ev in events:
        last_visit = visits_by_event.get(ev.id)
        if last_visit is None:
            new_since_visit[ev.id] = article_counts.get(ev.id, 0)
        else:
            compare_at = last_visit
            if compare_at.tzinfo is not None:
                compare_at = compare_at.replace(tzinfo=None)
            n = (
                db.query(func.count(Article.id))
                .filter(
                    Article.event_id == ev.id,
                    Article.fetched_at > compare_at,
                )
                .scalar()
                or 0
            )
            new_since_visit[ev.id] = int(n)

    result = [
        EventResponse(
            id=ev.id,
            name=ev.name,
            description=ev.description,
            status=ev.status,
            created_at=ev.created_at,
            last_article_at=ev.last_article_at,
            archived_at=ev.archived_at,
            expires_at=ev.expires_at,
            summary_version=ev.summary_version or 0,
            article_count=article_counts.get(ev.id, 0),
            unread_count=unread_counts.get(ev.id, 0),
            read_count=max(0, article_counts.get(ev.id, 0) - unread_counts.get(ev.id, 0)),
            feed_count=feed_counts.get(ev.id, 0),
            importance_avg=float(importance_avgs.get(ev.id) or 0.0),
            new_since_visit=new_since_visit.get(ev.id, 0),
        )
        for ev in events
    ]
    result.sort(
        key=lambda r: (
            -(r.article_count or 0),
            -(r.last_article_at.timestamp() if r.last_article_at else 0),
            -(r.created_at.timestamp() if r.created_at else 0),
        )
    )
    return result


@router.post("", response_model=EventResponse)
async def create_event(
    event_data: EventCreate,
    db: SQLAlchemySession = Depends(database.get_db),
):
    event = Event(name=event_data.name.strip(), description=event_data.description, status="active")
    from ..grouping.lifecycle import reset_expiry
    event.expires_at = reset_expiry()
    db.add(event)
    try:
        db.commit()
        db.refresh(event)
    except Exception as e:
        db.rollback()
        logger.error(f"Error creating event: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create event")
    return EventResponse(
        id=event.id, name=event.name, description=event.description, status=event.status,
        created_at=event.created_at, last_article_at=event.last_article_at,
        archived_at=event.archived_at, expires_at=event.expires_at,
        summary_version=event.summary_version or 0,
        article_count=0, feed_count=0, importance_avg=0.0,
    )


@router.get("/search/articles")
async def search_articles(
    keyword: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
    visitor_id: str = Depends(get_visitor_id),
    db: SQLAlchemySession = Depends(database.get_db),
):
    search_term = f"%{keyword}%"
    articles = (
        db.query(Article)
        .outerjoin(Event, Article.event_id == Event.id)
        .filter(
            (Article.title.ilike(search_term)) | (Article.rss_description.ilike(search_term))
        )
        .order_by(desc(Article.published_date))
        .limit(limit)
        .all()
    )
    read_ids = {
        r.article_id
        for r in db.query(ArticleRead.article_id)
        .filter(ArticleRead.visitor_id == visitor_id)
        .all()
    }
    return [
        ArticleInEvent(
            id=a.id, title=a.title, publisher_name=a.publisher_name,
            published_date=a.published_date, url=a.url, word_count=a.word_count,
            importance_score=a.importance_score, grouping_confidence=a.grouping_confidence,
            is_read=(a.id in read_ids),
            event_id=a.event_id,
            event_name=a.event.name if a.event else None,
        )
        for a in articles
    ]


@router.get("/{event_id}", response_model=EventDetailResponse)
async def get_event(
    event_id: int,
    visitor_id: str = Depends(get_visitor_id),
    db: SQLAlchemySession = Depends(database.get_db),
):
    event = verify_event_exists(db, event_id)
    try:
        from ..grouping.lifecycle import reap_expired_in_list
        reap_expired_in_list(db, [event_id])
        db.commit()
        db.refresh(event)
    except Exception as e:
        logger.warning(f"Lazy reap failed in get_event {event_id}: {e}", exc_info=True)
        db.rollback()
    articles = (
        db.query(Article)
        .filter(Article.event_id == event_id)
        .order_by(desc(Article.published_date))
        .all()
    )
    read_ids = {
        r.article_id
        for r in db.query(ArticleRead.article_id)
        .filter(
            ArticleRead.visitor_id == visitor_id,
            ArticleRead.article_id.in_([a.id for a in articles]),
        )
        .all()
    }
    article_payloads = [
        ArticleInEvent(
            id=a.id, title=a.title, publisher_name=a.publisher_name,
            published_date=a.published_date, url=a.url, word_count=a.word_count,
            importance_score=a.importance_score, grouping_confidence=a.grouping_confidence,
            is_read=(a.id in read_ids),
        )
        for a in articles
    ]
    latest_summary = (
        db.query(EventSummary)
        .filter(EventSummary.event_id == event_id)
        .order_by(desc(EventSummary.generated_at))
        .first()
    )
    summary_data = None
    if latest_summary and latest_summary.summary_json:
        sj = dict(latest_summary.summary_json)
        sj["article_ids"] = latest_summary.article_ids or []
        try:
            summary_data = EventSummaryData(**sj)
        except Exception:
            summary_data = None
    return EventDetailResponse(
        id=event.id, name=event.name, description=event.description, status=event.status,
        created_at=event.created_at, last_article_at=event.last_article_at,
        archived_at=event.archived_at, expires_at=event.expires_at,
        articles=article_payloads,
        latest_summary=summary_data, summary_version=event.summary_version or 0,
    )


@router.put("/{event_id}", response_model=EventResponse)
async def update_event(
    event_id: int,
    event_data: EventUpdate,
    db: SQLAlchemySession = Depends(database.get_db),
):
    event = verify_event_exists(db, event_id)
    if event_data.name is not None:
        event.name = event_data.name.strip()
    if event_data.description is not None:
        event.description = event_data.description
    if event_data.status is not None:
        if event_data.status not in ("active", "cooling", "archived"):
            raise HTTPException(status_code=400, detail="status must be active|cooling|archived")
        event.status = event_data.status
        if event_data.status == "archived":
            event.archived_at = datetime.now(timezone.utc)
        elif event_data.status == "active":
            event.archived_at = None
    try:
        db.commit()
        db.refresh(event)
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating event: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update event")
    article_count = db.query(func.count(Article.id)).filter(Article.event_id == event_id).scalar() or 0
    return EventResponse(
        id=event.id, name=event.name, description=event.description, status=event.status,
        created_at=event.created_at, last_article_at=event.last_article_at,
        archived_at=event.archived_at, expires_at=event.expires_at,
        summary_version=event.summary_version or 0,
        article_count=article_count, feed_count=0, importance_avg=0.0,
    )


@router.delete("/{event_id}")
async def delete_event(
    event_id: int,
    db: SQLAlchemySession = Depends(database.get_db),
):
    event = verify_event_exists(db, event_id)
    try:
        db.query(Article).filter(Article.event_id == event_id).update(
            {Article.event_id: None, Article.grouped_at: None}, synchronize_session=False
        )
        db.delete(event)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting event: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete event")
    return {"message": "Event deleted, articles returned to uncategorized"}


@router.post("/{event_id}/articles/{article_id}")
async def add_article_to_event(
    event_id: int,
    article_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    db: SQLAlchemySession = Depends(database.get_db),
):
    event = verify_event_exists(db, event_id)
    article = db.query(Article).filter(Article.id == article_id).first()
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    already_in = (article.event_id == event_id)
    original = article.event_id
    article.event_id = event_id
    article.grouped_at = datetime.now(timezone.utc)
    if not event.last_article_at or (article.published_date and article.published_date > event.last_article_at):
        event.last_article_at = article.published_date or datetime.now(timezone.utc)
    event.status = "active"
    event.archived_at = None
    from ..grouping.lifecycle import reset_expiry_on_event
    reset_expiry_on_event(event)
    record_correction(
        article_id=article_id,
        kind="move",
        original_event_id=original,
        corrected_event_id=event_id,
    )
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Error adding article to event: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to add article to event")

    if not already_in:
        try:
            llm = get_llm_summary(request)
            background_tasks.add_task(_regen_summary_after_move, event_id, article_id, llm)
        except HTTPException:
            pass

    return {
        "message": "Article added to event" if not already_in else "Article already in event",
        "article_id": article_id,
        "event_id": event_id,
        "already_in": already_in,
        "summary_regenerated": False,
    }


@router.delete("/{event_id}/articles/{article_id}")
async def remove_article_from_event(
    event_id: int,
    article_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    db: SQLAlchemySession = Depends(database.get_db),
):
    event = verify_event_exists(db, event_id)
    article = db.query(Article).filter(Article.id == article_id, Article.event_id == event_id).first()
    if not article:
        raise HTTPException(status_code=404, detail="Article not in event")
    record_correction(
        article_id=article_id,
        kind="move",
        original_event_id=event_id,
        corrected_event_id=None,
    )
    remaining_articles = db.query(Article).filter(
        Article.event_id == event_id, Article.id != article_id
    ).all()
    disbanded = False
    if len(remaining_articles) < 2:
        disbanded = True
        for a in remaining_articles:
            a.event_id = None
            a.grouped_at = datetime.now(timezone.utc)
            a.proposed_event_name = None
        db.query(EventSummary).filter(EventSummary.event_id == event_id).delete(synchronize_session=False)
        db.delete(event)
    article.event_id = None
    article.grouped_at = datetime.now(timezone.utc)
    article.proposed_event_name = None
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Error removing article from event: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to remove article")

    if not disbanded:
        try:
            llm = get_llm_summary(request)
            background_tasks.add_task(_regen_summary_after_remove, event_id, llm)
        except HTTPException:
            pass

    return {
        "message": "Article removed" + (" and event disbanded" if disbanded else ""),
        "disbanded": disbanded,
        "article_id": article_id,
        "event_id": event_id,
        "summary_regenerated": False,
    }


@router.post("/{event_id}/summary", response_model=EventSummaryResponse)
async def generate_event_summary(
    event_id: int,
    request: Request,
    db: SQLAlchemySession = Depends(database.get_db),
):
    verify_event_exists(db, event_id)
    try:
        llm = get_llm_summary(request)
    except HTTPException:
        raise
    from ..grouping.summary_service import generate_initial_summary_for_event
    ok = await generate_initial_summary_for_event(event_id, llm)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to generate summary")
    return await get_event_summary(event_id, db)


@router.get("/{event_id}/summary")
async def get_event_summary(
    event_id: int,
    db: SQLAlchemySession = Depends(database.get_db),
):
    verify_event_exists(db, event_id)
    latest = (
        db.query(EventSummary)
        .filter(EventSummary.event_id == event_id)
        .order_by(desc(EventSummary.generated_at))
        .first()
    )
    if not latest:
        return None
    return EventSummaryResponse(
        id=latest.id, event_id=latest.event_id,
        summary_json=EventSummaryData(**latest.summary_json),
        article_ids=latest.article_ids or [],
        generated_at=latest.generated_at, article_count=latest.article_count,
        model_used=latest.model_used,
    )


@router.post("/{event_id}/articles/{article_id}/move", response_model=EventResponse)
async def move_article(
    event_id: int,
    article_id: int,
    body: MoveArticleRequest,
    db: SQLAlchemySession = Depends(database.get_db),
):
    source = verify_event_exists(db, event_id)
    article = db.query(Article).filter(Article.id == article_id).first()
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    if article.event_id != event_id:
        raise HTTPException(status_code=400, detail="Article is not in this event")
    target_id = body.target_event_id
    if body.new_event_name and not target_id:
        from ..grouping.lifecycle import reset_expiry
        new_ev = Event(
            name=body.new_event_name.strip(),
            status="active",
            expires_at=reset_expiry(),
        )
        db.add(new_ev)
        db.flush()
        target_id = new_ev.id
    elif target_id:
        target = verify_event_exists(db, target_id)
        _ = target.id
    else:
        raise HTTPException(status_code=400, detail="Provide target_event_id or new_event_name")

    record_correction(
        article_id=article_id,
        kind="move",
        original_event_id=event_id,
        corrected_event_id=target_id,
        note=body.note,
    )
    article.event_id = target_id
    article.grouped_at = datetime.now(timezone.utc)
    if target_id:
        from ..grouping.lifecycle import reset_expiry_on_event
        target_ev = db.query(Event).filter(Event.id == target_id).first()
        if target_ev is not None:
            reset_expiry_on_event(target_ev)
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Error moving article: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to move article")
    target_ev = verify_event_exists(db, target_id)
    article_count = db.query(func.count(Article.id)).filter(Article.event_id == target_id).scalar() or 0
    return EventResponse(
        id=target_ev.id, name=target_ev.name, description=target_ev.description, status=target_ev.status,
        created_at=target_ev.created_at, last_article_at=target_ev.last_article_at,
        archived_at=target_ev.archived_at, expires_at=target_ev.expires_at,
        summary_version=target_ev.summary_version or 0,
        article_count=article_count, feed_count=0, importance_avg=0.0,
    )


@router.post("/{event_id}/merge/{other_id}")
async def merge_events(
    event_id: int,
    other_id: int,
    body: MergeRequest,
    db: SQLAlchemySession = Depends(database.get_db),
):
    if event_id == other_id:
        raise HTTPException(status_code=400, detail="Cannot merge event into itself")
    primary = verify_event_exists(db, event_id)
    secondary = verify_event_exists(db, other_id)
    db.query(Article).filter(Article.event_id == other_id).update(
        {Article.event_id: event_id}, synchronize_session=False
    )
    record_correction(
        article_id=0, kind="merge",
        original_event_id=other_id, corrected_event_id=event_id,
        note=body.note or f"merged '{secondary.name}' into '{primary.name}'",
    )
    try:
        db.delete(secondary)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Error merging events: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to merge events")
    return {"message": f"Merged {other_id} into {event_id}", "primary_event_id": event_id}


@router.post("/{event_id}/split", response_model=EventResponse)
async def split_event(
    event_id: int,
    body: SplitRequest,
    db: SQLAlchemySession = Depends(database.get_db),
):
    parent = verify_event_exists(db, event_id)
    if not body.article_ids:
        raise HTTPException(status_code=400, detail="article_ids required")
    if not body.new_event_name.strip():
        raise HTTPException(status_code=400, detail="new_event_name required")
    new_ev = Event(name=body.new_event_name.strip(), status="active")
    db.add(new_ev)
    db.flush()
    from ..grouping.lifecycle import reset_expiry, reset_expiry_on_event
    split_articles = (
        db.query(Article)
        .filter(Article.id.in_(body.article_ids), Article.event_id == event_id)
        .all()
    )
    for art in split_articles:
        reset_expiry_on_event(new_ev)
    if split_articles:
        new_ev.last_article_at = max(
            (a.published_date for a in split_articles if a.published_date),
            default=None,
        ) or datetime.now(timezone.utc)
    if new_ev.expires_at is None:
        new_ev.expires_at = reset_expiry()
    db.query(Article).filter(Article.id.in_(body.article_ids), Article.event_id == event_id).update(
        {Article.event_id: new_ev.id}, synchronize_session=False
    )
    record_correction(
        article_id=0, kind="split",
        original_event_id=event_id, corrected_event_id=new_ev.id,
        note=body.note or f"split out '{new_ev.name}' from '{parent.name}'",
    )
    try:
        db.commit()
        db.refresh(new_ev)
    except Exception as e:
        db.rollback()
        logger.error(f"Error splitting event: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to split event")
    article_count = db.query(func.count(Article.id)).filter(Article.event_id == new_ev.id).scalar() or 0
    return EventResponse(
        id=new_ev.id, name=new_ev.name, description=new_ev.description, status=new_ev.status,
        created_at=new_ev.created_at, last_article_at=new_ev.last_article_at,
        archived_at=new_ev.archived_at, expires_at=new_ev.expires_at,
        summary_version=new_ev.summary_version or 0,
        article_count=article_count, feed_count=0, importance_avg=0.0,
    )


@router.post("/{event_id}/revive")
async def revive_event_endpoint(
    event_id: int,
    db: SQLAlchemySession = Depends(database.get_db),
):
    ev = verify_event_exists(db, event_id)
    if ev.status != "archived":
        raise HTTPException(status_code=400, detail="Event is not archived")
    success = lifecycle_module.revive_event(event_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to revive event")
    return {"message": "Event revived", "event_id": event_id}


@router.get("/_stats/all", response_model=StatsOut)
async def stats(
    db: SQLAlchemySession = Depends(database.get_db),
):
    from ..database.models import GroupingFeedback, ReclusterProposal
    total_articles = db.query(func.count(Article.id)).scalar() or 0
    ungrouped = db.query(func.count(Article.id)).filter(Article.event_id.is_(None)).scalar() or 0
    active = db.query(func.count(Event.id)).filter(Event.status == "active").scalar() or 0
    cooling = db.query(func.count(Event.id)).filter(Event.status == "cooling").scalar() or 0
    archived = db.query(func.count(Event.id)).filter(Event.status == "archived").scalar() or 0
    pending = db.query(func.count(ReclusterProposal.id)).filter(ReclusterProposal.applied == 0).scalar() or 0
    feedback = db.query(func.count(GroupingFeedback.id)).scalar() or 0
    return StatsOut(
        articles_total=total_articles, articles_ungrouped=ungrouped,
        events_active=active, events_cooling=cooling, events_archived=archived,
        proposals_pending=pending, feedback_count=feedback,
    )


@router.get("/{event_id}/chat-history", response_model=List[ChatHistoryItem])
async def get_event_chat_history(
    event_id: int,
    visitor_id: str = Depends(get_visitor_id),
    db: SQLAlchemySession = Depends(database.get_db),
):
    verify_event_exists(db, event_id)
    rows = (
        db.query(EventChatMessage)
        .filter(
            EventChatMessage.event_id == event_id,
            EventChatMessage.visitor_id == visitor_id,
        )
        .order_by(EventChatMessage.created_at.asc(), EventChatMessage.id.asc())
        .all()
    )
    return [
        ChatHistoryItem(role=row.role, content=row.content)
        for row in rows
        if row.role in ("user", "assistant")
    ]


@router.post("/{event_id}/chat/persist")
async def persist_event_chat_turn(
    event_id: int,
    body: EventChatPersistRequest,
    visitor_id: str = Depends(get_visitor_id),
    db: SQLAlchemySession = Depends(database.get_db),
):
    verify_event_exists(db, event_id)
    if not body.question.strip() or not body.answer.strip():
        raise HTTPException(status_code=400, detail="question and answer required")
    try:
        user_row = EventChatMessage(
            event_id=event_id, visitor_id=visitor_id,
            role="user", content=body.question.strip(),
        )
        assistant_row = EventChatMessage(
            event_id=event_id, visitor_id=visitor_id,
            role="assistant", content=body.answer.strip(),
            model_used=body.model_used,
        )
        db.add(user_row)
        db.add(assistant_row)
        db.commit()
        return {
            "ok": True,
            "message_ids": [user_row.id, assistant_row.id],
        }
    except Exception as e:
        db.rollback()
        logger.error(f"Error persisting chat turn for event {event_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to persist chat turn")


@router.post("/{event_id}/chat")
async def chat_about_event(
    event_id: int,
    body: EventChatRequest,
    request: Request,
    visitor_id: str = Depends(get_visitor_id),
    db: SQLAlchemySession = Depends(database.get_db),
):
    event = verify_event_exists(db, event_id)
    if not body.question.strip():
        raise HTTPException(status_code=400, detail="question required")
    try:
        llm = get_llm_chat(request)
    except HTTPException:
        raise

    articles = (
        db.query(Article)
        .filter(Article.event_id == event_id)
        .order_by(desc(Article.published_date), desc(Article.id))
        .limit(app_config.CHAT_CONTEXT_MAX_ARTICLES)
        .all()
    )
    if not articles:
        raise HTTPException(status_code=400, detail="Event has no articles to chat about")

    latest_summary = (
        db.query(EventSummary)
        .filter(EventSummary.event_id == event_id)
        .order_by(desc(EventSummary.generated_at))
        .first()
    )
    summary_payload = latest_summary.summary_json if latest_summary else None

    sources = [
        ArticleInEvent(
            id=a.id, title=a.title, publisher_name=a.publisher_name,
            published_date=a.published_date, url=a.url, word_count=a.word_count,
            importance_score=a.importance_score, grouping_confidence=a.grouping_confidence,
            is_read=False, event_id=a.event_id, event_name=event.name,
        )
        for a in articles[:5]
    ]

    try:
        messages = chat_module.build_chat_messages(
            event_name=event.name,
            summary=summary_payload,
            articles=articles,
            chat_history=body.chat_history,
            question=body.question,
            per_article_chars=app_config.CHAT_CONTEXT_PER_ARTICLE_CHARS,
        )
    except Exception as e:
        logger.error(f"Error building chat messages for event {event_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to build chat context")

    model_name = app_config.DEFAULT_CHAT_MODEL_NAME
    sources_payload = [s.model_dump(mode="json") for s in sources]

    async def event_stream():
        yield chat_module.serialize_sse("meta", {
            "event_id": event_id,
            "model_used": model_name,
            "sources": sources_payload,
        })
        try:
            async for delta in chat_module.astream_chat_answer(llm, messages):
                if delta:
                    yield chat_module.serialize_sse("delta", {"text": delta})
        except Exception as e:
            logger.error(f"Chat stream error for event {event_id}: {e}", exc_info=True)
            yield chat_module.serialize_sse("error", {"message": str(e)})
            return
        yield chat_module.serialize_sse("done", {"event_id": event_id})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
