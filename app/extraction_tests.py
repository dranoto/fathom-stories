# app/extraction_tests.py
import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

import feedparser
from bs4 import BeautifulSoup

from . import config as app_config
from .scraper import scrape_urls, get_extension_status
from .sanitizer import sanitize_html_content
from .grouping.content_classifier import classify_article, NON_EVENT_TYPES

logger = logging.getLogger(__name__)


@dataclass
class EntryCheck:
    title: Optional[str] = None
    link: Optional[str] = None
    published_date: Optional[str] = None
    rss_description_chars: int = 0
    rss_description_words: int = 0
    scraped_text_chars: int = 0
    scraped_text_words: int = 0
    sanitized_html_chars: int = 0
    content_type: str = "unknown"
    scraper_error: Optional[str] = None
    passes: List[str] = field(default_factory=list)
    fails: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["passes"] = list(self.passes)
        d["fails"] = list(self.fails)
        return d


@dataclass
class FeedVerdict:
    feed_url: str
    feed_title: Optional[str] = None
    entries_seen: int = 0
    entries_with_link: int = 0
    entries_with_date: int = 0
    entries_scraped: int = 0
    entries_passing_full_text: int = 0
    entries_passing_all_gates: int = 0
    extension_loaded: bool = False
    extension_service_workers: int = 0
    sample: List[EntryCheck] = field(default_factory=list)
    overall_pass: bool = False
    summary: str = ""

    def as_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["sample"] = [s.as_dict() for s in self.sample]
        return d


def _word_count(text: str) -> int:
    if not text:
        return 0
    return len(re.findall(r"\b\w+\b", text))


def _extract_rss_description_plain(entry: dict) -> Optional[str]:
    raw = None
    summary_detail = entry.get("summary_detail")
    if isinstance(summary_detail, dict) and summary_detail.get("value"):
        raw = summary_detail["value"]
    if not raw:
        raw = entry.get("summary") or entry.get("description")
    if not raw:
        content = entry.get("content")
        if isinstance(content, list) and content:
            block = content[0]
            if isinstance(block, dict) and "value" in block:
                raw = block["value"]
    if not raw:
        return None
    try:
        soup = BeautifulSoup(raw, "html.parser")
        return soup.get_text(separator=" ", strip=True)
    except Exception:
        return raw


def _normalize_published(entry: dict) -> Optional[datetime]:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        v = entry.get(key)
        if v:
            try:
                return datetime(*v[:6], tzinfo=timezone.utc)
            except Exception:
                continue
    for key in ("published", "updated", "created"):
        v = entry.get(key)
        if not v:
            continue
        try:
            if isinstance(v, str):
                if v.endswith("Z"):
                    return datetime.fromisoformat(v[:-1] + "+00:00")
                return datetime.fromisoformat(v)
        except Exception:
            continue
    return None


def parse_feed(feed_url: str) -> List[dict]:
    parsed = feedparser.parse(feed_url)
    return list(parsed.entries or []), parsed.feed.get("title") if hasattr(parsed, "feed") else None


def check_entry(
    entry: dict,
    scraped_doc,
    min_words: int,
) -> EntryCheck:
    chk = EntryCheck()

    chk.title = entry.get("title")
    chk.link = entry.get("link")
    pd = _normalize_published(entry)
    chk.published_date = pd.isoformat() if pd else None

    rss_desc = _extract_rss_description_plain(entry)
    if rss_desc:
        chk.rss_description_chars = len(rss_desc)
        chk.rss_description_words = _word_count(rss_desc)
        chk.passes.append("rss_description_extracted")
    else:
        chk.fails.append("rss_description_missing")

    if chk.title:
        chk.passes.append("title_present")
    else:
        chk.fails.append("title_missing")

    if chk.link:
        chk.passes.append("link_present")
    else:
        chk.fails.append("link_missing")

    if chk.published_date:
        chk.passes.append("published_date_parsed")
    else:
        chk.fails.append("published_date_unparsed")

    if scraped_doc is not None:
        chk.scraper_error = scraped_doc.metadata.get("error")
        text = scraped_doc.page_content or ""
        chk.scraped_text_chars = len(text)
        chk.scraped_text_words = _word_count(text)
        raw_html = scraped_doc.metadata.get("full_html_content") or ""
        chk.sanitized_html_chars = len(sanitize_html_content(raw_html)) if raw_html else 0

        if chk.scraper_error:
            chk.fails.append(f"scraper_error:{chk.scraper_error[:80]}")
        elif chk.scraped_text_words >= min_words:
            chk.passes.append("full_text_extracted")
        else:
            chk.fails.append(f"text_too_short:{chk.scraped_text_words}w<{min_words}w")

        if chk.sanitized_html_chars > 0:
            chk.passes.append("html_sanitized")
        else:
            chk.fails.append("html_missing_after_sanitize")
    else:
        chk.fails.append("no_scraped_doc")

    chk.content_type = classify_article(
        title=chk.title or "",
        rss_description=rss_desc or "",
        scraped_text=scraped_doc.page_content if scraped_doc else "",
    )
    if chk.content_type in NON_EVENT_TYPES:
        chk.fails.append(f"non_event_type:{chk.content_type}")
    else:
        chk.passes.append(f"event_type:{chk.content_type}")

    return chk


def passes_all_gates(chk: EntryCheck, min_words: int) -> bool:
    if not chk.title:
        return False
    if not chk.link:
        return False
    if not chk.published_date:
        return False
    if chk.scraper_error:
        return False
    if chk.scraped_text_words < min_words:
        return False
    if chk.content_type in NON_EVENT_TYPES:
        return False
    return True


async def test_feed(
    feed_url: str,
    sample_size: int = 5,
    min_words: Optional[int] = None,
) -> FeedVerdict:
    min_words = min_words or app_config.MIN_ARTICLE_WORD_COUNT
    verdict = FeedVerdict(feed_url=feed_url)

    entries, feed_title = parse_feed(feed_url)
    verdict.feed_title = feed_title
    verdict.entries_seen = len(entries)

    candidate_entries = []
    for entry in entries:
        link = entry.get("link")
        pd = _normalize_published(entry)
        if link and pd:
            verdict.entries_with_link += 1
            verdict.entries_with_date += 1
            candidate_entries.append(entry)
        elif link:
            verdict.entries_with_link += 1
        elif pd:
            verdict.entries_with_date += 1
        if len(candidate_entries) >= sample_size:
            break

    if not candidate_entries:
        verdict.summary = f"No valid entries (need link + date). seen={verdict.entries_seen}"
        return verdict

    urls = [e["link"] for e in candidate_entries]
    scraped_docs = await scrape_urls(urls)
    docs_by_url = {d.metadata.get("source"): d for d in scraped_docs}

    ext = get_extension_status()
    verdict.extension_loaded = ext.get("loaded", False)
    verdict.extension_service_workers = ext.get("service_workers", 0)

    for entry in candidate_entries:
        url = entry.get("link")
        doc = docs_by_url.get(url)
        if doc is not None and not doc.metadata.get("error"):
            verdict.entries_scraped += 1
        chk = check_entry(entry, doc, min_words)
        verdict.sample.append(chk)
        if chk.scraped_text_words >= min_words and not chk.scraper_error:
            verdict.entries_passing_full_text += 1
        if passes_all_gates(chk, min_words):
            verdict.entries_passing_all_gates += 1

    pct_full = (
        100.0 * verdict.entries_passing_full_text / max(1, len(candidate_entries))
    )
    pct_all = (
        100.0 * verdict.entries_passing_all_gates / max(1, len(candidate_entries))
    )
    verdict.overall_pass = (
        verdict.entries_seen > 0
        and verdict.entries_passing_all_gates >= max(1, len(candidate_entries) // 2)
    )
    verdict.summary = (
        f"seen={verdict.entries_seen} sampled={len(candidate_entries)} "
        f"full_text={verdict.entries_passing_full_text} ({pct_full:.0f}%) "
        f"all_gates={verdict.entries_passing_all_gates} ({pct_all:.0f}%) "
        f"ext={'on' if verdict.extension_loaded else 'off'}"
    )
    return verdict


def print_verdict(v: FeedVerdict) -> None:
    print(f"\n=== {v.feed_url} ===")
    print(f"  Feed title : {v.feed_title}")
    print(f"  Extension  : loaded={v.extension_loaded} sw={v.extension_service_workers}")
    print(f"  Summary    : {v.summary}")
    print(f"  Verdict    : {'PASS' if v.overall_pass else 'FAIL'}")
    for i, chk in enumerate(v.sample, 1):
        verdict_line = "OK" if not chk.fails else f"FAIL ({'; '.join(chk.fails)})"
        print(
            f"  [{i}] {chk.title or '<no title>'[:80]}\n"
            f"      link={chk.link}\n"
            f"      date={chk.published_date}\n"
            f"      rss_desc={chk.rss_description_words}w "
            f"scraped={chk.scraped_text_words}w "
            f"sanitized_html={chk.sanitized_html_chars}b "
            f"ctype={chk.content_type}\n"
            f"      -> {verdict_line}"
        )
