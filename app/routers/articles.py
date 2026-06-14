# app/routers/articles.py
import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session as SQLAlchemySession
from sqlalchemy import desc

from .. import database
from ..database.models import Article, ArticleRead
from ..dependencies import get_visitor_id
from ..sanitizer import sanitize_html_content
from ..schemas.event import ArticleOut, ArticleDetailOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/articles", tags=["articles"])


@router.get("/reads/ids", response_model=List[int])
async def list_read_article_ids(
    visitor_id: str = Depends(get_visitor_id),
    db: SQLAlchemySession = Depends(database.get_db),
):
    rows = (
        db.query(ArticleRead.article_id)
        .filter(ArticleRead.visitor_id == visitor_id)
        .all()
    )
    return [r[0] for r in rows]


@router.get("", response_model=List[ArticleOut])
async def list_articles(
    ungrouped: bool = Query(False, description="Show only uncategorized articles"),
    event_id: Optional[int] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    visitor_id: str = Depends(get_visitor_id),
    db: SQLAlchemySession = Depends(database.get_db),
):
    q = db.query(Article)
    if ungrouped:
        q = q.filter(Article.event_id.is_(None))
    if event_id is not None:
        q = q.filter(Article.event_id == event_id)
    articles = q.order_by(desc(Article.published_date)).offset(offset).limit(limit).all()
    read_ids = {
        r.article_id
        for r in db.query(ArticleRead.article_id)
        .filter(
            ArticleRead.visitor_id == visitor_id,
            ArticleRead.article_id.in_([a.id for a in articles]),
        )
        .all()
    }
    return [
        ArticleOut(
            id=a.id, title=a.title, publisher_name=a.publisher_name,
            published_date=a.published_date, url=a.url, word_count=a.word_count,
            importance_score=a.importance_score, grouping_confidence=a.grouping_confidence,
            event_id=a.event_id, fetched_at=a.fetched_at,
            is_read=(a.id in read_ids),
        )
        for a in articles
    ]


@router.get("/{article_id}", response_model=ArticleDetailOut)
async def get_article(
    article_id: int,
    visitor_id: str = Depends(get_visitor_id),
    db: SQLAlchemySession = Depends(database.get_db),
):
    article = db.query(Article).filter(Article.id == article_id).first()
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    is_read = (
        db.query(ArticleRead)
        .filter(
            ArticleRead.article_id == article_id,
            ArticleRead.visitor_id == visitor_id,
        )
        .first()
        is not None
    )
    return ArticleDetailOut(
        id=article.id, title=article.title, publisher_name=article.publisher_name,
        published_date=article.published_date, url=article.url, word_count=article.word_count,
        importance_score=article.importance_score, grouping_confidence=article.grouping_confidence,
        event_id=article.event_id, fetched_at=article.fetched_at, is_read=is_read,
        scraped_text_content=article.scraped_text_content,
        full_html_content=sanitize_html_content(article.full_html_content or ""),
        rss_description=article.rss_description,
    )


@router.post("/{article_id}/read")
async def mark_read(
    article_id: int,
    visitor_id: str = Depends(get_visitor_id),
    db: SQLAlchemySession = Depends(database.get_db),
):
    article = db.query(Article).filter(Article.id == article_id).first()
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    existing = (
        db.query(ArticleRead)
        .filter(
            ArticleRead.article_id == article_id,
            ArticleRead.visitor_id == visitor_id,
        )
        .first()
    )
    if not existing:
        db.add(ArticleRead(
            article_id=article_id,
            visitor_id=visitor_id,
            read_at=datetime.now(timezone.utc),
        ))
        db.commit()
    return {"message": "marked read", "article_id": article_id}


@router.delete("/{article_id}/read")
async def mark_unread(
    article_id: int,
    visitor_id: str = Depends(get_visitor_id),
    db: SQLAlchemySession = Depends(database.get_db),
):
    db.query(ArticleRead).filter(
        ArticleRead.article_id == article_id,
        ArticleRead.visitor_id == visitor_id,
    ).delete()
    db.commit()
    return {"message": "marked unread", "article_id": article_id}
