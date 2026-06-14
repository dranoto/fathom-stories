# app/database/__init__.py
import logging
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from .models import (
    Base, FeedSource, Article, Event, EventSummary,
    GroupingFeedback, ReclusterProposal, ArticleRead, KVSetting, ScrapeFailure,
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
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    if "articles" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("articles")}
        if "proposed_event_name" not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE articles ADD COLUMN proposed_event_name VARCHAR"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_articles_proposed_event_name ON articles (proposed_event_name)"))
            logger.info("Migrated: added articles.proposed_event_name")
    if "article_reads" in insp.get_table_names():
        read_cols = {c["name"] for c in insp.get_columns("article_reads")}
        if "visitor_id" not in read_cols:
            with engine.begin() as conn:
                conn.execute(text("DROP TABLE article_reads"))
            Base.metadata.create_all(bind=engine, tables=[ArticleRead.__table__])
            logger.info("Migrated: dropped & recreated article_reads with visitor_id")
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
    "ScrapeFailure",
    "engine",
    "SessionLocal",
    "create_db_and_tables",
    "get_db",
    "db_session_scope",
]
