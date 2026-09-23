"""Input/output bounds for rolling event summaries.

The route behind a combo may use a different tokenizer. cl100k is an input
estimate, not a provider-reported count; a separate UTF-8 byte cap and a 10%
headroom keep the configured 100k budget conservative.
"""
from __future__ import annotations

import json
from typing import Any, Callable

import tiktoken

MAX_TIMELINE_ENTRIES = 12
MAX_SOURCE_ENTRIES = 12
MAX_KEY_DEVELOPMENTS = 5
MAX_RESUMMARY_ARTICLES = 20
MAX_UPDATE_ARTICLES = 2
MAX_PROMPT_BYTES = 300_000
MAX_ARTICLE_BYTES = 75_000
MAX_ARTICLE_SEGMENTS = 32
_HEADROOM = 0.9


def _trim_text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        raise ValueError("Summary field must be a string")
    if len(value) <= limit:
        return value
    return value[:limit].rsplit(" ", 1)[0] or value[:limit]


def _keep_milestones(items: list, limit: int) -> list:
    if len(items) <= limit:
        return items
    return items[:2] + items[-(limit - 2):]


def normalize_summary_output(summary: dict[str, Any]) -> dict[str, Any]:
    """Validate the reader-facing schema and bound every future input field.

    Counts/ID ledgers are deliberately excluded: only the database supplies
    those values after successful generation.
    """
    if not isinstance(summary, dict):
        raise ValueError("Summary must be an object")
    key_developments = summary.get("key_developments")
    timeline = summary.get("timeline_narrative")
    cross = summary.get("cross_source_synthesis")
    progressive = summary.get("progressive_summary")
    if (not isinstance(key_developments, list) or not isinstance(timeline, list)
            or not isinstance(cross, dict) or not isinstance(progressive, str)):
        raise ValueError("Summary is missing required reader-facing fields")
    by_source = cross.get("by_source")
    if not isinstance(by_source, list) or not isinstance(cross.get("synthesis"), str):
        raise ValueError("Source synthesis has invalid structure")
    if any(not isinstance(item, str) for item in key_developments):
        raise ValueError("Key developments must be strings")
    if any(not isinstance(item, dict) or not isinstance(item.get("date"), str)
           or not isinstance(item.get("text"), str) for item in timeline):
        raise ValueError("Timeline entries must have dates and text")
    if any(not isinstance(item, dict) or not isinstance(item.get("source"), str)
           or not isinstance(item.get("observation"), str) for item in by_source):
        raise ValueError("Source entries must have names and observations")
    return {
        "key_developments": [_trim_text(x, 350) for x in key_developments[:MAX_KEY_DEVELOPMENTS]],
        "timeline_narrative": [
            {"date": _trim_text(x["date"], 40), "text": _trim_text(x["text"], 900)}
            for x in _keep_milestones(timeline, MAX_TIMELINE_ENTRIES)
        ],
        "cross_source_synthesis": {
            "by_source": [
                {"source": _trim_text(x["source"], 120),
                 "observation": _trim_text(x["observation"], 450)}
                for x in _keep_milestones(by_source, MAX_SOURCE_ENTRIES)
            ],
            "synthesis": _trim_text(cross["synthesis"], 1400),
        },
        "progressive_summary": _trim_text(progressive, 1800),
    }


def compact_summary_for_prompt(summary: dict[str, Any]) -> dict[str, Any]:
    """Discard metadata and normalize legacy append-only reader summaries."""
    return normalize_summary_output(summary)


def estimated_tokens(prompt: str) -> int:
    """OpenAI cl100k estimate only; not the routed provider's tokenizer."""
    return len(tiktoken.get_encoding("cl100k_base").encode_ordinary(prompt))


def within_budget(prompt: str, max_prompt_tokens: int) -> bool:
    return (max_prompt_tokens > 0
            and len(prompt.encode("utf-8")) <= min(MAX_PROMPT_BYTES, max_prompt_tokens * 3)
            and estimated_tokens(prompt) <= int(max_prompt_tokens * _HEADROOM))


def select_recent_payload(
    articles: list[dict[str, Any]],
    *,
    max_prompt_tokens: int,
    max_articles: int,
    build_prompt: Callable[[list[dict[str, Any]]], str],
    skip_oversized: bool = False,
) -> list[dict[str, Any]]:
    """Select recent complete articles under both byte and token estimates.

    `skip_oversized` is for independent source snapshots only: omitted IDs must
    not be marked incorporated. Incremental work uses segments instead.
    """
    selected: list[dict[str, Any]] = []
    if not within_budget(build_prompt([]), max_prompt_tokens):
        raise ValueError("Summary instructions/prior state exceed prompt budget")
    for article in articles[:max_articles]:
        body = article.get("scraped_text_content") or article.get("rss_description") or ""
        if len(body.encode("utf-8")) > MAX_ARTICLE_BYTES:
            if skip_oversized:
                continue
            if not selected:
                raise ValueError("First article exceeds per-article summary input limit")
            break
        next_selected = [*selected, article]
        if not within_budget(build_prompt(next_selected), max_prompt_tokens):
            if skip_oversized:
                continue
            if not selected:
                raise ValueError("First article exceeds total summary prompt budget")
            break
        selected = next_selected
    return selected


def next_article_segment(
    article: dict[str, Any],
    body: str,
    *,
    max_prompt_tokens: int,
    build_prompt: Callable[[list[dict[str, Any]]], str],
) -> tuple[dict[str, Any], int]:
    """Return the largest nonempty Unicode prefix fitting the actual prompt.

    This is used only for an oversized *incremental* article. The caller must
    commit its ID only after every character of its body has been processed.
    """
    if not body:
        raise ValueError("Cannot split an article without content")
    low, high = 1, min(len(body), MAX_ARTICLE_BYTES)
    best = 0
    while low <= high:
        middle = (low + high) // 2
        piece = body[:middle]
        segment = {**article, "scraped_text_content": piece, "rss_description": ""}
        if len(piece.encode("utf-8")) <= MAX_ARTICLE_BYTES and within_budget(
            build_prompt([segment]), max_prompt_tokens
        ):
            best = middle
            low = middle + 1
        else:
            high = middle - 1
    if not best:
        raise ValueError("One article segment cannot fit the summary prompt")
    return {**article, "scraped_text_content": body[:best], "rss_description": ""}, best
