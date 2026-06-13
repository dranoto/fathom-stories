# app/security.py
import logging
from typing import Set
from fastapi import HTTPException
from sqlalchemy.orm import Session as SQLAlchemySession

from . import database

logger = logging.getLogger(__name__)


def verify_article_exists(db: SQLAlchemySession, article_id: int) -> database.Article:
    article = db.query(database.Article).filter(database.Article.id == article_id).first()
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    return article


def verify_event_exists(db: SQLAlchemySession, event_id: int) -> database.Event:
    event = db.query(database.Event).filter(database.Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event
