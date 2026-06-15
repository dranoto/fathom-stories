# app/routers/feeds.py
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import desc, func
from sqlalchemy.orm import Session as SQLAlchemySession
from sqlalchemy.exc import IntegrityError

from .. import database
from ..database.models import FeedSource, Article, ScrapeFailure
from ..dependencies import get_visitor_id
from ..rss_client import add_or_update_feed_source
from ..schemas.feed import FeedOut, FeedCreate, FeedRefreshOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/feeds", tags=["feeds"])


def _feed_to_out(db: SQLAlchemySession, feed: FeedSource) -> FeedOut:
    article_count = (
        db.query(func.count(Article.id))
        .filter(Article.feed_source_id == feed.id)
        .scalar()
        or 0
    )
    last_failure = (
        db.query(ScrapeFailure)
        .filter(ScrapeFailure.url == feed.url)
        .order_by(desc(ScrapeFailure.last_attempted_at))
        .first()
    )
    return FeedOut(
        id=feed.id,
        url=feed.url,
        name=feed.name,
        fetch_interval_minutes=feed.fetch_interval_minutes,
        last_fetched_at=feed.last_fetched_at,
        is_paused=bool(feed.is_paused),
        article_count=article_count,
        last_error=last_failure.error if last_failure else None,
        last_error_at=last_failure.last_attempted_at if last_failure else None,
    )


@router.get("", response_model=List[FeedOut])
async def list_feeds(db: SQLAlchemySession = Depends(database.get_db)):
    feeds = db.query(FeedSource).order_by(FeedSource.id).all()
    return [_feed_to_out(db, f) for f in feeds]


@router.post("", response_model=FeedOut)
async def add_feed(
    body: FeedCreate,
    db: SQLAlchemySession = Depends(database.get_db),
):
    url = body.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="url is required")
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=400, detail="url must start with http:// or https://")
    try:
        feed = add_or_update_feed_source(db, url, body.name)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Feed already exists")
    except Exception as e:
        db.rollback()
        logger.error(f"FEEDS: add_feed failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Add feed failed: {e}")
    db.commit()
    db.refresh(feed)
    if body.fetch_interval_minutes is not None and feed.fetch_interval_minutes != body.fetch_interval_minutes:
        feed.fetch_interval_minutes = body.fetch_interval_minutes
        db.commit()
        db.refresh(feed)
    return _feed_to_out(db, feed)


@router.delete("/{feed_id}")
async def remove_feed(
    feed_id: int,
    db: SQLAlchemySession = Depends(database.get_db),
):
    feed = db.query(FeedSource).filter(FeedSource.id == feed_id).first()
    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")
    db.delete(feed)
    db.commit()
    return {"deleted": feed_id}


@router.post("/{feed_id}/pause", response_model=FeedOut)
async def pause_feed(
    feed_id: int,
    db: SQLAlchemySession = Depends(database.get_db),
):
    feed = db.query(FeedSource).filter(FeedSource.id == feed_id).first()
    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")
    feed.is_paused = True
    db.commit()
    db.refresh(feed)
    return _feed_to_out(db, feed)


@router.delete("/{feed_id}/pause", response_model=FeedOut)
async def unpause_feed(
    feed_id: int,
    db: SQLAlchemySession = Depends(database.get_db),
):
    feed = db.query(FeedSource).filter(FeedSource.id == feed_id).first()
    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")
    feed.is_paused = False
    db.commit()
    db.refresh(feed)
    return _feed_to_out(db, feed)


@router.post("/{feed_id}/refresh", response_model=FeedRefreshOut)
async def refresh_feed(
    feed_id: int,
    db: SQLAlchemySession = Depends(database.get_db),
):
    from ..rss_client import update_single_feed
    feed = db.query(FeedSource).filter(FeedSource.id == feed_id).first()
    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")
    if feed.is_paused:
        raise HTTPException(status_code=409, detail="Feed is paused; unpause to refresh")
    before = (
        db.query(func.count(Article.id))
        .filter(Article.feed_source_id == feed.id)
        .scalar()
        or 0
    )
    try:
        await update_single_feed(db, feed_id)
    except Exception as e:
        logger.error(f"FEEDS: refresh_feed {feed_id} failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Refresh failed: {e}")
    after = (
        db.query(func.count(Article.id))
        .filter(Article.feed_source_id == feed.id)
        .scalar()
        or 0
    )
    return FeedRefreshOut(new_articles=after - before, total_after=after)
