# app/schemas/feed.py
from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime

from ._serializers import UtcDateTime


class FeedOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    url: str
    name: Optional[str] = None
    fetch_interval_minutes: int
    last_fetched_at: Optional[UtcDateTime] = None
    is_paused: bool = False
    article_count: int = 0
    last_error: Optional[str] = None
    last_error_at: Optional[UtcDateTime] = None


class FeedCreate(BaseModel):
    url: str
    name: Optional[str] = None
    fetch_interval_minutes: Optional[int] = None


class FeedRefreshOut(BaseModel):
    new_articles: int
    total_after: int
