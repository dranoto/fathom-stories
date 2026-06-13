# app/database/models.py
from sqlalchemy import (
    Column, Integer, String, DateTime, ForeignKey, JSON, Float, Text, Index
)
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime, timezone

Base = declarative_base()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FeedSource(Base):
    __tablename__ = "feed_sources"

    id = Column(Integer, primary_key=True, index=True)
    url = Column(String, nullable=False, unique=True, index=True)
    name = Column(String, nullable=True)
    fetch_interval_minutes = Column(Integer, nullable=False, default=30)
    last_fetched_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    articles = relationship("Article", back_populates="feed_source")

    def __repr__(self) -> str:
        return f"<FeedSource(id={self.id}, name='{self.name}', url='{self.url}')>"


class Article(Base):
    __tablename__ = "articles"

    id = Column(Integer, primary_key=True, index=True)
    feed_source_id = Column(Integer, ForeignKey("feed_sources.id", ondelete='SET NULL'), nullable=True, index=True)
    url = Column(String, nullable=False, unique=True, index=True)
    title = Column(String, nullable=True)
    publisher_name = Column(String, nullable=True)
    published_date = Column(DateTime(timezone=True), nullable=True, index=True)
    rss_description = Column(Text, nullable=True)
    raw_rss_item = Column(JSON, nullable=True)
    scraped_text_content = Column(Text, nullable=True)
    full_html_content = Column(Text, nullable=True)
    word_count = Column(Integer, nullable=True, default=0)
    fetched_at = Column(DateTime(timezone=True), default=_utcnow, index=True)

    event_id = Column(Integer, ForeignKey("events.id", ondelete='SET NULL'), nullable=True, index=True)
    importance_score = Column(Float, nullable=True, default=0.5)
    grouping_confidence = Column(Float, nullable=True, default=0.0)
    grouped_at = Column(DateTime(timezone=True), nullable=True)
    proposed_event_name = Column(String, nullable=True, index=True)

    feed_source = relationship("FeedSource", back_populates="articles")
    event = relationship("Event", back_populates="articles")

    def __repr__(self) -> str:
        return f"<Article(id={self.id}, title='{(self.title or '')[:40]}', event_id={self.event_id})>"


class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, index=True)
    description = Column(Text, nullable=True)
    status = Column(String, nullable=False, default="active", index=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, index=True)
    last_article_at = Column(DateTime(timezone=True), nullable=True, index=True)
    archived_at = Column(DateTime(timezone=True), nullable=True)
    summary_article_count = Column(Integer, nullable=True, default=0)
    last_summary_at = Column(DateTime(timezone=True), nullable=True)
    summary_version = Column(Integer, nullable=True, default=0)
    last_seen_in_recluster_at = Column(DateTime(timezone=True), nullable=True)

    articles = relationship("Article", back_populates="event")
    summaries = relationship(
        "EventSummary", back_populates="event",
        cascade="all, delete-orphan", order_by="desc(EventSummary.generated_at)",
    )

    def __repr__(self) -> str:
        return f"<Event(id={self.id}, name='{self.name}', status='{self.status}')>"


class EventSummary(Base):
    __tablename__ = "event_summaries"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("events.id", ondelete='CASCADE'), nullable=False, index=True)
    summary_json = Column(JSON, nullable=False)
    article_ids = Column(JSON, nullable=True)
    generated_at = Column(DateTime(timezone=True), default=_utcnow, index=True)
    article_count = Column(Integer, nullable=False, default=0)
    model_used = Column(String, nullable=True)

    event = relationship("Event", back_populates="summaries")

    def __repr__(self) -> str:
        return f"<EventSummary(id={self.id}, event_id={self.event_id}, article_count={self.article_count})>"


class GroupingFeedback(Base):
    __tablename__ = "grouping_feedback"

    id = Column(Integer, primary_key=True, index=True)
    article_id = Column(Integer, ForeignKey("articles.id", ondelete='CASCADE'), nullable=False, index=True)
    original_event_id = Column(Integer, ForeignKey("events.id", ondelete='SET NULL'), nullable=True)
    corrected_event_id = Column(Integer, ForeignKey("events.id", ondelete='SET NULL'), nullable=True)
    kind = Column(String, nullable=False)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, index=True)

    def __repr__(self) -> str:
        return (
            f"<GroupingFeedback(id={self.id}, article_id={self.article_id}, "
            f"kind='{self.kind}', orig={self.original_event_id}, corr={self.corrected_event_id})>"
        )


class ReclusterProposal(Base):
    __tablename__ = "recluster_proposals"

    id = Column(Integer, primary_key=True, index=True)
    kind = Column(String, nullable=False, index=True)
    payload = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, index=True)
    applied = Column(Integer, nullable=False, default=0)
    applied_at = Column(DateTime(timezone=True), nullable=True)
    rationale = Column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<ReclusterProposal(id={self.id}, kind='{self.kind}', applied={self.applied})>"


class ArticleRead(Base):
    __tablename__ = "article_reads"

    article_id = Column(Integer, ForeignKey("articles.id", ondelete='CASCADE'), primary_key=True)
    read_at = Column(DateTime(timezone=True), default=_utcnow, index=True)

    def __repr__(self) -> str:
        return f"<ArticleRead(article_id={self.article_id}, read_at={self.read_at})>"


class KVSetting(Base):
    __tablename__ = "kv_settings"

    key = Column(String, primary_key=True)
    value = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    def __repr__(self) -> str:
        return f"<KVSetting(key='{self.key}', value='{(self.value or '')[:40]}')>"


Index("ix_articles_event_published", Article.event_id, Article.published_date)
Index("ix_events_status_last_article", Event.status, Event.last_article_at)
