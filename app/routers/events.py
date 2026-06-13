# app/routers/events.py
import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session as SQLAlchemySession
from sqlalchemy import desc, func

from .. import database
from ..database.models import Event, EventSummary, Article
from ..schemas.event import (
    EventCreate, EventUpdate, EventResponse, EventDetailResponse,
    ArticleInEvent, EventSummaryResponse, EventSummaryData,
    MoveArticleRequest, MergeRequest, SplitRequest,
    ReclusterProposalOut, GroupingFeedbackOut, StatsOut,
)
from ..dependencies import get_llm_summary
from ..security import verify_event_exists
from ..grouping.summarizer import generate_major_summary
from ..grouping.feedback import record_correction
from ..grouping import lifecycle as lifecycle_module
from .. import config as app_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("", response_model=List[EventResponse])
async def list_events(
    status: Optional[str] = Query(None, description="Filter by status: active|cooling|archived"),
    db: SQLAlchemySession = Depends(database.get_db),
):
    q = db.query(Event)
    if status:
        q = q.filter(Event.status == status)
    events = q.order_by(desc(Event.last_article_at), desc(Event.created_at)).all()
    if not events:
        return []

    event_ids = [e.id for e in events]
    article_counts = dict(
        db.query(Article.event_id, func.count(Article.id))
        .filter(Article.event_id.in_(event_ids))
        .group_by(Article.event_id)
        .all()
    )
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

    return [
        EventResponse(
            id=ev.id,
            name=ev.name,
            description=ev.description,
            status=ev.status,
            created_at=ev.created_at,
            last_article_at=ev.last_article_at,
            archived_at=ev.archived_at,
            summary_version=ev.summary_version or 0,
            article_count=article_counts.get(ev.id, 0),
            feed_count=feed_counts.get(ev.id, 0),
            importance_avg=float(importance_avgs.get(ev.id) or 0.0),
        )
        for ev in events
    ]


@router.post("", response_model=EventResponse)
async def create_event(
    event_data: EventCreate,
    db: SQLAlchemySession = Depends(database.get_db),
):
    event = Event(name=event_data.name.strip(), description=event_data.description, status="active")
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
        archived_at=event.archived_at, summary_version=event.summary_version or 0,
        article_count=0, feed_count=0, importance_avg=0.0,
    )


@router.get("/search/articles")
async def search_articles(
    keyword: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
    db: SQLAlchemySession = Depends(database.get_db),
):
    search_term = f"%{keyword}%"
    articles = (
        db.query(Article)
        .filter(
            (Article.title.ilike(search_term)) | (Article.rss_description.ilike(search_term))
        )
        .order_by(desc(Article.published_date))
        .limit(limit)
        .all()
    )
    return [
        ArticleInEvent(
            id=a.id, title=a.title, publisher_name=a.publisher_name,
            published_date=a.published_date, url=a.url, word_count=a.word_count,
            importance_score=a.importance_score, grouping_confidence=a.grouping_confidence,
        )
        for a in articles
    ]


@router.get("/{event_id}", response_model=EventDetailResponse)
async def get_event(
    event_id: int,
    db: SQLAlchemySession = Depends(database.get_db),
):
    event = verify_event_exists(db, event_id)
    articles = (
        db.query(Article)
        .filter(Article.event_id == event_id)
        .order_by(desc(Article.published_date))
        .all()
    )
    article_payloads = [
        ArticleInEvent(
            id=a.id, title=a.title, publisher_name=a.publisher_name,
            published_date=a.published_date, url=a.url, word_count=a.word_count,
            importance_score=a.importance_score, grouping_confidence=a.grouping_confidence,
            is_read=False,
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
        archived_at=event.archived_at, articles=article_payloads,
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
        archived_at=event.archived_at, summary_version=event.summary_version or 0,
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
    db: SQLAlchemySession = Depends(database.get_db),
):
    event = verify_event_exists(db, event_id)
    article = db.query(Article).filter(Article.id == article_id).first()
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    if article.event_id == event_id:
        return {"message": "Article already in event", "article_id": article_id, "event_id": event_id}
    original = article.event_id
    article.event_id = event_id
    article.grouped_at = datetime.now(timezone.utc)
    if not event.last_article_at or (article.published_date and article.published_date > event.last_article_at):
        event.last_article_at = article.published_date or datetime.now(timezone.utc)
    event.status = "active"
    event.archived_at = None
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
    return {"message": "Article added to event", "article_id": article_id, "event_id": event_id}


@router.delete("/{event_id}/articles/{article_id}")
async def remove_article_from_event(
    event_id: int,
    article_id: int,
    db: SQLAlchemySession = Depends(database.get_db),
):
    verify_event_exists(db, event_id)
    article = db.query(Article).filter(Article.id == article_id, Article.event_id == event_id).first()
    if not article:
        raise HTTPException(status_code=404, detail="Article not in event")
    record_correction(
        article_id=article_id,
        kind="move",
        original_event_id=event_id,
        corrected_event_id=None,
    )
    article.event_id = None
    article.grouped_at = datetime.now(timezone.utc)
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Error removing article from event: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to remove article")
    return {"message": "Article removed from event"}


@router.post("/{event_id}/summary", response_model=EventSummaryResponse)
async def generate_event_summary(
    event_id: int,
    request: Request,
    db: SQLAlchemySession = Depends(database.get_db),
):
    event = verify_event_exists(db, event_id)
    articles = (
        db.query(Article)
        .filter(Article.event_id == event_id)
        .order_by(desc(Article.published_date))
        .all()
    )
    if not articles:
        raise HTTPException(status_code=400, detail="No articles in event")
    articles_data = [
        {
            "id": a.id, "title": a.title, "publisher_name": a.publisher_name,
            "published_date": a.published_date.isoformat() if a.published_date else None,
            "url": a.url, "word_count": a.word_count,
            "scraped_text_content": a.scraped_text_content, "rss_description": a.rss_description,
        }
        for a in articles
    ]
    prior_summary = (
        db.query(EventSummary)
        .filter(EventSummary.event_id == event_id)
        .order_by(desc(EventSummary.generated_at))
        .first()
    )
    prior_json = prior_summary.summary_json if prior_summary else None
    try:
        llm = get_llm_summary(request)
        summary_json = await generate_major_summary(
            event_name=event.name,
            articles=articles_data,
            prompt_template=app_config.DEFAULT_MAJOR_SUMMARY_PROMPT,
            prior_summary_json=prior_json,
            llm=llm,
        )
    except Exception as e:
        logger.error(f"Error generating major summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to generate summary: {e}")
    article_ids_used = [a["id"] for a in articles_data]
    summary_json["article_ids"] = article_ids_used
    new_summary = EventSummary(
        event_id=event_id,
        summary_json=summary_json,
        article_ids=article_ids_used,
        article_count=len(articles_data),
        model_used=app_config.DEFAULT_SUMMARY_MODEL_NAME,
    )
    db.add(new_summary)
    event.last_summary_at = datetime.now(timezone.utc)
    event.summary_article_count = len(articles_data)
    event.summary_version = (event.summary_version or 0) + 1
    try:
        db.commit()
        db.refresh(new_summary)
    except Exception as e:
        db.rollback()
        logger.error(f"Error saving event summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to save summary")
    return EventSummaryResponse(
        id=new_summary.id, event_id=new_summary.event_id,
        summary_json=EventSummaryData(**new_summary.summary_json),
        article_ids=new_summary.article_ids or [],
        generated_at=new_summary.generated_at,
        article_count=new_summary.article_count,
        model_used=new_summary.model_used,
    )


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
        new_ev = Event(name=body.new_event_name.strip(), status="active")
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
        archived_at=target_ev.archived_at, summary_version=target_ev.summary_version or 0,
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
        archived_at=new_ev.archived_at, summary_version=new_ev.summary_version or 0,
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
