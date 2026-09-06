# app/database/__init__.py
import logging
from contextlib import contextmanager
from pathlib import Path
from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker, Session

from .models import (
    Base, FeedSource, Article, Event, EventSummary,
    GroupingFeedback, ReclusterProposal, ArticleRead, KVSetting, ScrapeFailure,
    EventChatMessage,
)
from .. import config as app_config

logger = logging.getLogger(__name__)

if app_config.DATABASE_URL.startswith("sqlite"):
    sqlite_connect_args = {
        "check_same_thread": False,
        "timeout": 30,
    }
else:
    sqlite_connect_args = {}

engine = create_engine(
    app_config.DATABASE_URL,
    connect_args=sqlite_connect_args,
)

if app_config.DATABASE_URL.startswith("sqlite"):
    sqlite_url = make_url(app_config.DATABASE_URL)
    sqlite_path = sqlite_url.database
    sqlite_absolute_path = str(Path(sqlite_path).resolve()) if sqlite_path else ""
    sqlite_on_network_fs = sqlite_absolute_path.startswith(("/media/", "/mnt/"))

    @event.listens_for(engine, "connect")
    def _configure_sqlite(dbapi_connection, connection_record) -> None:
        cursor = dbapi_connection.cursor()
        if not sqlite_on_network_fs:
            cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

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
    if "events" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("events")}
        if "expires_at" not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE events ADD COLUMN expires_at DATETIME"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_events_expires_at ON events (expires_at)"))
                conn.execute(text(
                    "UPDATE events SET expires_at = "
                    "CASE WHEN last_article_at IS NOT NULL THEN datetime(last_article_at, '+48 hours') "
                    "ELSE datetime(created_at, '+48 hours') END "
                    "WHERE expires_at IS NULL"
                ))
            logger.info("Migrated: added events.expires_at with backfill (last_article_at or created_at + 48h)")
    if "feed_sources" in insp.get_table_names():
        feed_cols = {c["name"] for c in insp.get_columns("feed_sources")}
        if "is_paused" not in feed_cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE feed_sources ADD COLUMN is_paused BOOLEAN DEFAULT 0"))
            logger.info("Migrated: added feed_sources.is_paused")
    if "article_reads" in insp.get_table_names():
        read_cols = {c["name"] for c in insp.get_columns("article_reads")}
        if "visitor_id" not in read_cols:
            with engine.begin() as conn:
                conn.execute(text("DROP TABLE article_reads"))
            Base.metadata.create_all(bind=engine, tables=[ArticleRead.__table__])
            insp.clear_cache()
            logger.info("Migrated: dropped & recreated article_reads with visitor_id")
        current_indexes = {index["name"] for index in insp.get_indexes("article_reads")}
        for index in ArticleRead.__table__.indexes:
            index.create(bind=engine, checkfirst=True)
            if index.name not in current_indexes:
                logger.info(f"Migrated: added article_reads index {index.name}")
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
    "EventChatMessage",
    "engine",
    "SessionLocal",
    "create_db_and_tables",
    "get_db",
    "db_session_scope",
]
