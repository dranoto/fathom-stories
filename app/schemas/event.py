# app/schemas/event.py
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class EventSummaryData(BaseModel):
    timeline_narrative: str
    cross_source_synthesis: str
    progressive_summary: str
    article_ids: Optional[List[int]] = []
    article_count: Optional[int] = None
    feed_count: Optional[int] = None
    date_range: Optional[str] = None
    key_developments: Optional[List[str]] = None

    class Config:
        from_attributes = True


class EventCreate(BaseModel):
    name: str
    description: Optional[str] = None


class EventUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None


class EventResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    status: str
    created_at: datetime
    last_article_at: Optional[datetime] = None
    archived_at: Optional[datetime] = None
    summary_version: Optional[int] = 0
    article_count: Optional[int] = 0
    unread_count: Optional[int] = 0
    read_count: Optional[int] = 0
    feed_count: Optional[int] = 0
    importance_avg: Optional[float] = 0.0

    class Config:
        from_attributes = True


class ArticleInEvent(BaseModel):
    id: int
    title: Optional[str]
    publisher_name: Optional[str]
    published_date: Optional[datetime]
    url: str
    word_count: Optional[int]
    importance_score: Optional[float] = None
    grouping_confidence: Optional[float] = None
    is_read: Optional[bool] = False
    added_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class EventDetailResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    status: str
    created_at: datetime
    last_article_at: Optional[datetime] = None
    archived_at: Optional[datetime] = None
    articles: List[ArticleInEvent]
    latest_summary: Optional[EventSummaryData] = None
    summary_version: Optional[int] = 0

    class Config:
        from_attributes = True


class EventSummaryResponse(BaseModel):
    id: int
    event_id: int
    summary_json: EventSummaryData
    article_ids: List[int] = []
    generated_at: datetime
    article_count: int
    model_used: Optional[str] = None

    class Config:
        from_attributes = True


class MoveArticleRequest(BaseModel):
    target_event_id: Optional[int] = None
    new_event_name: Optional[str] = None
    note: Optional[str] = None


class MergeRequest(BaseModel):
    other_event_id: int
    note: Optional[str] = None


class SplitRequest(BaseModel):
    new_event_name: str
    article_ids: List[int]
    note: Optional[str] = None


class ReclusterProposalOut(BaseModel):
    id: int
    kind: str
    payload: dict
    rationale: Optional[str] = None
    created_at: datetime
    applied: int
    applied_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class GroupingFeedbackOut(BaseModel):
    id: int
    article_id: int
    original_event_id: Optional[int]
    corrected_event_id: Optional[int]
    kind: str
    note: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class ArticleOut(BaseModel):
    id: int
    title: Optional[str]
    publisher_name: Optional[str]
    published_date: Optional[datetime]
    url: str
    word_count: Optional[int]
    importance_score: Optional[float] = None
    grouping_confidence: Optional[float] = None
    event_id: Optional[int] = None
    fetched_at: Optional[datetime] = None
    is_read: Optional[bool] = False

    class Config:
        from_attributes = True


class ArticleDetailOut(ArticleOut):
    scraped_text_content: Optional[str] = None
    full_html_content: Optional[str] = None
    rss_description: Optional[str] = None


class StatsOut(BaseModel):
    articles_total: int
    articles_ungrouped: int
    events_active: int
    events_cooling: int
    events_archived: int
    proposals_pending: int
    feedback_count: int
