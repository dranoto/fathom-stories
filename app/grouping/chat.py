# app/grouping/chat.py
import json
import logging
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Optional

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from .. import config as app_config
from ..schemas.event import ChatHistoryItem

logger = logging.getLogger(__name__)


def _format_summary_block(summary: Optional[Dict[str, Any]]) -> str:
    if not summary:
        return "(no summary has been generated for this event yet)"
    parts: List[str] = []
    kd = summary.get("key_developments")
    if isinstance(kd, list) and kd:
        parts.append("Key developments:")
        for item in kd[:5]:
            parts.append(f"- {item}")
    prog = summary.get("progressive_summary")
    if isinstance(prog, str) and prog.strip():
        parts.append(f"Progressive update: {prog.strip()}")
    tl = summary.get("timeline_narrative")
    if isinstance(tl, list) and tl:
        parts.append("Timeline:")
        for entry in tl:
            if not isinstance(entry, dict):
                continue
            date = entry.get("date") or ""
            text = entry.get("text") or ""
            if text:
                parts.append(f"  - {date}: {text}")
    css = summary.get("cross_source_synthesis")
    if isinstance(css, dict):
        synth = css.get("synthesis")
        if isinstance(synth, str) and synth.strip():
            parts.append(f"Cross-source synthesis: {synth.strip()}")
        by_source = css.get("by_source")
        if isinstance(by_source, list) and by_source:
            parts.append("By source:")
            for entry in by_source:
                if not isinstance(entry, dict):
                    continue
                src = entry.get("source") or "Unknown"
                obs = entry.get("observation") or ""
                if obs:
                    parts.append(f"  - {src}: {obs}")
    return "\n".join(parts) if parts else "(summary exists but is empty)"


def _format_articles_block(
    articles: List[Any],
    per_article_chars: int,
) -> str:
    parts: List[str] = []
    seen_urls = set()
    for article in articles:
        url = getattr(article, "url", None)
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)
        title = getattr(article, "title", None) or "Untitled"
        publisher = getattr(article, "publisher_name", None) or "Unknown Source"
        published_date = getattr(article, "published_date", None)
        if hasattr(published_date, "isoformat"):
            published_date = published_date.isoformat()
        elif published_date is None:
            published_date = "Unknown Date"
        content = getattr(article, "scraped_text_content", None) or getattr(article, "rss_description", None) or ""
        if content and per_article_chars > 0 and len(content) > per_article_chars:
            content = content[:per_article_chars].rstrip() + "..."
        if not content:
            content = "No content available."
        parts.append(
            f"--- {title} ({publisher}, {published_date}) ---\n{content}\n"
        )
    return "\n".join(parts) if parts else "(no article content available)"


def _format_history_block(history: Optional[List[ChatHistoryItem]]) -> str:
    if not history:
        return "(no prior turns)"
    lines: List[str] = []
    for item in history:
        role = "User" if item.role == "user" else "Assistant"
        content = item.content.strip()
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def build_chat_messages(
    event_name: str,
    summary: Optional[Dict[str, Any]],
    articles: List[Any],
    chat_history: Optional[List[ChatHistoryItem]],
    question: str,
    per_article_chars: int,
) -> List[BaseMessage]:
    system_prompt = app_config.DEFAULT_CHAT_PROMPT.format(
        current_datetime=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        event_name=event_name,
        summary_block=_format_summary_block(summary),
        articles_block=_format_articles_block(articles, per_article_chars),
        history_block=_format_history_block(chat_history),
        question=question.strip(),
    )
    messages: List[BaseMessage] = [SystemMessage(content=system_prompt)]
    for item in (chat_history or []):
        if item.role == "user":
            messages.append(HumanMessage(content=item.content))
        elif item.role == "assistant":
            messages.append(AIMessage(content=item.content))
    messages.append(HumanMessage(content=question.strip()))
    return messages


async def astream_chat_answer(
    llm: ChatOpenAI,
    messages: List[BaseMessage],
) -> AsyncIterator[str]:
    try:
        async for chunk in llm.astream(messages):
            content = getattr(chunk, "content", None)
            if isinstance(content, str):
                if content:
                    yield content
            elif isinstance(content, list):
                for piece in content:
                    if isinstance(piece, str) and piece:
                        yield piece
                    elif isinstance(piece, dict):
                        text = piece.get("text")
                        if isinstance(text, str) and text:
                            yield text
    except Exception as e:
        logger.error(f"Chat LLM stream failed: {e}", exc_info=True)
        raise


def serialize_sse(event: str, data: Any) -> str:
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"
