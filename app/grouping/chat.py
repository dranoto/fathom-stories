# app/grouping/chat.py
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional

from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI

from .. import config as app_config
from ..schemas.event import ChatHistoryItem

logger = logging.getLogger(__name__)


OnTextDelta = Callable[[str], None]
OnToolUse = Callable[[str, Dict[str, Any]], None]


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


def _extract_text_delta(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out: List[str] = []
        for piece in content:
            if isinstance(piece, str):
                if piece:
                    out.append(piece)
            elif isinstance(piece, dict):
                t = piece.get("text")
                if isinstance(t, str) and t:
                    out.append(t)
                elif piece.get("type") == "text":
                    t2 = piece.get("text")
                    if isinstance(t2, str):
                        out.append(t2)
        return "".join(out)
    return ""


async def _ainvoke_tool(tool, args: Dict[str, Any], timeout_seconds: int) -> Any:
    try:
        return await asyncio.wait_for(tool.ainvoke(args), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        return f"Error: tool '{getattr(tool, 'name', '?')}' timed out after {timeout_seconds}s"
    except Exception as e:
        return f"Error: tool '{getattr(tool, 'name', '?')}' failed: {e}"


def _stringify_tool_result(result: Any) -> str:
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception:
        return str(result)


async def astream_chat_with_tools(
    llm: ChatOpenAI,
    messages: List[BaseMessage],
    tools: Optional[List[Any]] = None,
    max_iterations: int = 4,
    tool_timeout_seconds: int = 30,
    on_text_delta: Optional[OnTextDelta] = None,
    on_tool_use: Optional[OnToolUse] = None,
) -> str:
    """
    Stream a chat answer with optional tool-calling. Returns the full final text.

    - If `tools` is empty/None: just streams and returns (no tool loop).
    - If `tools`: runs the agent loop up to `max_iterations`. After each LLM
      stream completes, if the assistant message contains tool_calls, executes
      them and feeds results back as ToolMessages, then loops. If no tool_calls,
      returns the final text.
    """
    tools = tools or []
    full_text_parts: List[str] = []

    if not tools:
        async for delta in _astream_plain(llm, messages):
            if delta:
                full_text_parts.append(delta)
                if on_text_delta:
                    on_text_delta(delta)
        return "".join(full_text_parts)

    tools_by_name: Dict[str, Any] = {}
    for t in tools:
        name = getattr(t, "name", None)
        if name:
            tools_by_name[name] = t

    bound_llm = llm.bind_tools(tools)
    working_messages: List[BaseMessage] = list(messages)

    for iteration in range(max_iterations):
        accumulated: Optional[AIMessageChunk] = None
        async for chunk in bound_llm.astream(working_messages):
            if accumulated is None:
                accumulated = chunk
            else:
                accumulated = accumulated + chunk
            delta = _extract_text_delta(getattr(chunk, "content", None))
            if delta:
                full_text_parts.append(delta)
                if on_text_delta:
                    on_text_delta(delta)

        if accumulated is None:
            logger.warning("Chat LLM produced no chunks; ending tool loop")
            break

        ai_message: AIMessage = accumulated
        tool_calls = list(getattr(ai_message, "tool_calls", []) or [])
        working_messages.append(ai_message)

        if not tool_calls:
            break

        for tool_call in tool_calls:
            name = tool_call.get("name") if isinstance(tool_call, dict) else getattr(tool_call, "name", None)
            args = tool_call.get("args") if isinstance(tool_call, dict) else getattr(tool_call, "args", {}) or {}
            tc_id = tool_call.get("id") if isinstance(tool_call, dict) else getattr(tool_call, "id", None)
            tool = tools_by_name.get(name) if name else None
            if tool is None:
                tool_msg = ToolMessage(
                    content=f"Error: tool '{name}' is not available.",
                    tool_call_id=tc_id or "",
                )
                if on_tool_use and name:
                    on_tool_use(name, args if isinstance(args, dict) else {})
                working_messages.append(tool_msg)
                continue

            if on_tool_use:
                try:
                    on_tool_use(name, args if isinstance(args, dict) else {})
                except Exception:
                    pass

            result = await _ainvoke_tool(tool, args if isinstance(args, dict) else {}, tool_timeout_seconds)
            tool_msg = ToolMessage(
                content=_stringify_tool_result(result),
                tool_call_id=tc_id or "",
            )
            working_messages.append(tool_msg)

    return "".join(full_text_parts)


async def _astream_plain(
    llm: ChatOpenAI,
    messages: List[BaseMessage],
) -> AsyncIterator[str]:
    try:
        async for chunk in llm.astream(messages):
            content = getattr(chunk, "content", None)
            delta = _extract_text_delta(content)
            if delta:
                yield delta
    except Exception as e:
        logger.error(f"Chat LLM stream failed: {e}", exc_info=True)
        raise


def serialize_sse(event: str, data: Any) -> str:
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"
