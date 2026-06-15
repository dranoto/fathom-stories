# app/schemas/event.py
from pydantic import BaseModel, ConfigDict
from typing import Optional, List, Any
from datetime import datetime

from ._serializers import UtcDateTime


class EventSummaryData(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    timeline_narrative: Optional[Any] = None
    cross_source_synthesis: Optional[Any] = None
    progressive_summary: Optional[str] = None
    article_ids: Optional[List[int]] = []
    article_count: Optional[int] = None
    feed_count: Optional[int] = None
    date_range: Optional[str] = None
    key_developments: Optional[List[str]] = None


class EventCreate(BaseModel):
    name: str
    description: Optional[str] = None


class EventUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None


class EventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: Optional[str]
    status: str
    created_at: UtcDateTime
    last_article_at: UtcDateTime = None
    archived_at: UtcDateTime = None
    expires_at: UtcDateTime = None
    summary_version: Optional[int] = 0
    article_count: Optional[int] = 0
    unread_count: Optional[int] = 0
    read_count: Optional[int] = 0
    feed_count: Optional[int] = 0
    importance_avg: Optional[float] = 0.0


class ArticleInEvent(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: Optional[str]
    publisher_name: Optional[str]
    published_date: UtcDateTime
    url: str
    word_count: Optional[int]
    importance_score: Optional[float] = None
    grouping_confidence: Optional[float] = None
    is_read: Optional[bool] = False
    added_at: UtcDateTime = None


class EventDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: Optional[str]
    status: str
    created_at: UtcDateTime
    last_article_at: UtcDateTime = None
    archived_at: UtcDateTime = None
    expires_at: UtcDateTime = None
    articles: List[ArticleInEvent]
    latest_summary: Optional[EventSummaryData] = None
    summary_version: Optional[int] = 0


class EventSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: int
    summary_json: EventSummaryData
    article_ids: List[int] = []
    generated_at: UtcDateTime
    article_count: int
    model_used: Optional[str] = None


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
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    payload: dict
    rationale: Optional[str] = None
    created_at: UtcDateTime
    applied: int
    applied_at: UtcDateTime = None


class GroupingFeedbackOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    article_id: int
    original_event_id: Optional[int]
    corrected_event_id: Optional[int]
    kind: str
    note: Optional[str]
    created_at: UtcDateTime


class ArticleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: Optional[str]
    publisher_name: Optional[str]
    published_date: UtcDateTime
    url: str
    word_count: Optional[int]
    importance_score: Optional[float] = None
    grouping_confidence: Optional[float] = None
    event_id: Optional[int] = None
    fetched_at: UtcDateTime = None
    is_read: Optional[bool] = False


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
