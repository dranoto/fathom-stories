# app/database/__init__.py
import logging
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from .models import (
    Base, FeedSource, Article, Event, EventSummary,
    GroupingFeedback, ReclusterProposal, ArticleRead, KVSetting,
)
from .. import config as app_config

logger = logging.getLogger(__name__)

engine = create_engine(
    app_config.DATABASE_URL,
    connect_args={"check_same_thread": False} if app_config.DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, expire_on_commit=False)


def create_db_and_tables() -> None:
    import os
    db_dir = os.path.dirname(app_config.DATABASE_URL.replace("sqlite:///", ""))
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created/verified.")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def db_session_scope():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


__all__ = [
    "Base",
    "FeedSource",
    "Article",
    "Event",
    "EventSummary",
    "GroupingFeedback",
    "ReclusterProposal",
    "ArticleRead",
    "KVSetting",
    "engine",
    "SessionLocal",
    "create_db_and_tables",
    "get_db",
    "db_session_scope",
]
