"""Event summary generation with bounded rolling prompts and durable membership checks."""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, cast

from sqlalchemy import desc, text

from .. import config as app_config
from ..database import db_session_scope
from ..database.models import Article, Event, EventSummary
from .summarizer import (
    build_incremental_summary_prompt,
    build_major_summary_prompt,
    generate_incremental_summary,
    generate_major_summary,
)
from .summary_budget import (
    MAX_RESUMMARY_ARTICLES,
    MAX_UPDATE_ARTICLES,
    MAX_ARTICLE_BYTES,
    MAX_ARTICLE_SEGMENTS,
    compact_summary_for_prompt,
    next_article_segment,
    normalize_summary_output,
    select_recent_payload,
)

logger = logging.getLogger(__name__)
_UNCHECKED_PRIOR = object()


def _articles_to_payload(articles: List[Article]) -> List[Dict[str, Any]]:
    return [
        {
            "id": a.id,
            "title": a.title,
            "publisher_name": a.publisher_name,
            "published_date": a.published_date.isoformat() if a.published_date else None,
            "url": a.url,
            "importance_score": a.importance_score,
            "word_count": a.word_count,
            "scraped_text_content": a.scraped_text_content,
            "rss_description": a.rss_description,
        }
        for a in articles
    ]


def _save_summary(
    event_id: int,
    summary_json: Dict[str, Any],
    article_ids: List[int],
    model_used: str,
    *,
    expected_article_ids: Optional[List[int]] = None,
    expected_latest_summary_id: Any = _UNCHECKED_PRIOR,
) -> Optional[EventSummary]:
    # article_ids is the membership/processed-work ledger for idempotency.
    # source_article_ids is the subset sent to the model on this generation.
    summary_json = dict(summary_json)
    summary_json["article_ids"] = list(article_ids)
    summary_json["source_article_count"] = len(summary_json.get("source_article_ids", []))
    with db_session_scope() as db:
        if db.get_bind().dialect.name == "sqlite":
            db.execute(text("BEGIN IMMEDIATE"))
        ev = db.query(Event).filter(Event.id == event_id).first()
        if not ev:
            return None
        if expected_latest_summary_id is not _UNCHECKED_PRIOR:
            latest = (
                db.query(EventSummary.id)
                .filter(EventSummary.event_id == event_id)
                .order_by(desc(EventSummary.id))
                .first()
            )
            latest_id = int(latest[0]) if latest else None
            if latest_id != expected_latest_summary_id:
                logger.warning("Summary save aborted for event %s: a newer summary was committed", event_id)
                return None
        current_articles = db.query(Article).filter(Article.event_id == event_id).all()
        current_ids = {int(a.id) for a in current_articles}
        if expected_article_ids is not None and current_ids != set(expected_article_ids):
            logger.warning(
                "Summary save aborted for event %s because its article set changed",
                event_id,
            )
            return None
        summary_json["article_count"] = len(current_articles)
        summary_json["summarized_article_count"] = len(article_ids)
        summary_json["feed_count"] = len({
            ("feed", int(a.feed_source_id)) if a.feed_source_id is not None
            else ("publisher", a.publisher_name or a.url)
            for a in current_articles
        })
        dates = sorted(
            a.published_date.date().isoformat()
            for a in current_articles if a.published_date is not None
        )
        summary_json["date_range"] = f"{dates[0]} - {dates[-1]}" if dates else "Unknown"
        es = EventSummary(
            event_id=event_id,
            summary_json=summary_json,
            article_ids=list(article_ids),
            article_count=len(article_ids),
            model_used=model_used,
        )
        db.add(es)
        ev.last_summary_at = datetime.now(timezone.utc)
        ev.summary_article_count = len(article_ids)
        ev.summary_version = (ev.summary_version or 0) + 1
        db.flush()
        return es


def _latest_prior_summary(event_id: int) -> Optional[tuple[int, Dict[str, Any]]]:
    with db_session_scope() as db:
        latest = (
            db.query(EventSummary)
            .filter(EventSummary.event_id == event_id)
            .order_by(desc(EventSummary.id))
            .first()
        )
        if not latest or not latest.summary_json:
            return None
        return int(latest.id), dict(latest.summary_json)


async def generate_initial_summary(
    event_id: int,
    llm,
    *,
    resummarize: bool = False,
    max_prompt_tokens: int = app_config.SUMMARY_MAX_PROMPT_TOKENS,
) -> bool:
    """Use the first two for a new event; latest twenty for explicit rebuilds."""
    with db_session_scope() as db:
        event = db.query(Event).filter(Event.id == event_id).first()
        if not event:
            return False
        event_name = event.name
        raw_articles = (
            db.query(Article)
            .filter(Article.event_id == event_id)
            .order_by(desc(Article.published_date), desc(Article.id))
            .all()
        )
        expected_article_ids = [cast(int, a.id) for a in raw_articles]
        latest_row = (
            db.query(EventSummary.id)
            .filter(EventSummary.event_id == event_id)
            .order_by(desc(EventSummary.id))
            .first()
        )
        expected_latest_summary_id = int(latest_row[0]) if latest_row else None
        # The founding request uses the earliest two members; an explicit
        # resummary sends no more than the newest twenty complete articles.
        candidates = raw_articles if resummarize else list(reversed(raw_articles))[:2]
        payload = _articles_to_payload(candidates)
    if not payload:
        return False
    try:
        selected = select_recent_payload(
            payload,
            max_prompt_tokens=max_prompt_tokens,
            max_articles=MAX_RESUMMARY_ARTICLES if resummarize else 2,
            skip_oversized=True,
            build_prompt=lambda items: build_major_summary_prompt(
                event_name,
                items,
                app_config.DEFAULT_MAJOR_SUMMARY_PROMPT,
            ),
        )
        if not selected:
            return False
        if len(selected) < len(payload):
            logger.info(
                "Summary for event %s uses %s recent article(s) from %s members",
                event_id, len(selected), len(payload),
            )
        summary_data = normalize_summary_output(await generate_major_summary(
            event_name=event_name,
            articles=selected,
            prompt_template=app_config.DEFAULT_MAJOR_SUMMARY_PROMPT,
            prior_summary_json=None,
            llm=llm,
        ))
        summary_data["source_article_ids"] = [int(a["id"]) for a in selected]
        summary_data["source_input_kind"] = "complete_articles"
    except Exception as e:
        logger.error("generate_initial_summary failed for event %s: %s", event_id, e, exc_info=True)
        return False
    saved = _save_summary(
        event_id, summary_data, [int(a["id"]) for a in selected],
        app_config.DEFAULT_SUMMARY_MODEL_NAME,
        expected_article_ids=expected_article_ids,
        expected_latest_summary_id=expected_latest_summary_id,
    )
    if saved is None:
        return False
    logger.info(
        "generate_initial_summary saved for event %s: %s sources, %s event members",
        event_id, len(selected), len(expected_article_ids),
    )
    return True


class UnprocessableArticle(ValueError):
    """A single article cannot be segmented within the configured request caps."""


async def _summarize_large_article(event_name: str, article: Dict[str, Any], prior: Dict[str, Any], llm) -> Dict[str, Any]:
    """Cover every character before returning a replacement summary to commit.

    Intermediate model results stay in memory; a failure leaves the durable
    article ID outstanding. Other queued articles can still be processed.
    """
    body = article.get("scraped_text_content") or article.get("rss_description") or ""
    if not body or len(body.encode("utf-8")) > MAX_ARTICLE_BYTES * MAX_ARTICLE_SEGMENTS:
        raise UnprocessableArticle("Article exceeds bounded segmentation capacity")
    working = compact_summary_for_prompt(prior)
    remaining = body
    segments = 0
    while remaining:
        if segments >= MAX_ARTICLE_SEGMENTS:
            raise UnprocessableArticle("Article exceeds bounded segmentation capacity")
        try:
            fragment, consumed = next_article_segment(
                article, remaining,
                max_prompt_tokens=app_config.SUMMARY_MAX_PROMPT_TOKENS,
                build_prompt=lambda items: build_incremental_summary_prompt(
                    event_name, items, working
                ),
            )
        except ValueError as error:
            raise UnprocessableArticle("Article metadata or segment cannot fit the prompt") from error
        working = normalize_summary_output(await generate_incremental_summary(
            event_name=event_name,
            new_articles=[fragment],
            prior_summary_json=working,
            llm=llm,
        ))
        remaining = remaining[consumed:]
        segments += 1
    return working


async def generate_summary_update(event_id: int, new_article_ids: List[int], llm) -> bool:
    if not new_article_ids:
        return await generate_initial_summary(event_id, llm, resummarize=True)
    prior_record = _latest_prior_summary(event_id)
    if prior_record is None:
        if not await generate_initial_summary(event_id, llm):
            return False
        prior_record = _latest_prior_summary(event_id)
        if prior_record is None:
            return False
    expected_latest_summary_id, prior = prior_record
    with db_session_scope() as db:
        event = db.query(Event).filter(Event.id == event_id).first()
        if not event:
            return False
        event_name = event.name
        new_articles = (
            db.query(Article)
            .filter(Article.event_id == event_id, Article.id.in_(new_article_ids))
            .order_by(desc(Article.published_date), desc(Article.id))
            .all()
        )
        all_articles = (
            db.query(Article)
            .filter(Article.event_id == event_id)
            .order_by(desc(Article.published_date), desc(Article.id))
            .all()
        )
        all_ids = [int(a.id) for a in all_articles]
    prior_ids = {
        int(article_id)
        for article_id in (prior.get("article_ids") or [])
        if isinstance(article_id, int) and not isinstance(article_id, bool)
    }
    if prior_ids - set(all_ids):
        # An article moved out. An additions-only prompt cannot repair history.
        return await generate_initial_summary(event_id, llm, resummarize=True)
    pending = [a for a in new_articles if a.id not in prior_ids]
    if not pending:
        # Retried work already reflected in the last committed membership ledger.
        return True
    remaining = _articles_to_payload(pending)
    skipped_unprocessable = False
    while remaining:
        try:
            compact_prior = compact_summary_for_prompt(prior)
            try:
                selected = select_recent_payload(
                    remaining,
                    max_prompt_tokens=app_config.SUMMARY_MAX_PROMPT_TOKENS,
                    max_articles=MAX_UPDATE_ARTICLES,
                    build_prompt=lambda items: build_incremental_summary_prompt(
                        event_name, items, compact_prior
                    ),
                )
            except ValueError:
                # A single oversized item is segmented without acknowledging
                # it until *all* fragments have succeeded. A truly impossible
                # item stays outstanding but cannot hold back later articles.
                try:
                    summary_data = await _summarize_large_article(
                        event_name, remaining[0], compact_prior, llm
                    )
                except UnprocessableArticle as error:
                    logger.warning(
                        "Summary event %s article %s deferred: %s",
                        event_id, remaining[0].get("id"), error,
                    )
                    remaining = remaining[1:]
                    skipped_unprocessable = True
                    continue
                selected = remaining[:1]
                summary_data["source_input_kind"] = "segmented_article"
            else:
                if not selected:
                    return False
                summary_data = normalize_summary_output(await generate_incremental_summary(
                    event_name=event_name,
                    new_articles=selected,
                    prior_summary_json=compact_prior,
                    llm=llm,
                ))
                summary_data["source_input_kind"] = "complete_articles"
            incorporated = [int(a["id"]) for a in selected]
            summary_data["source_article_ids"] = incorporated
        except Exception as e:
            logger.error("generate_summary_update failed for event %s: %s", event_id, e, exc_info=True)
            return False
        incorporated_ids = set(incorporated)
        processed_ids = [a for a in all_ids if a in prior_ids or a in incorporated_ids]
        saved = _save_summary(
            event_id, summary_data, processed_ids,
            app_config.DEFAULT_SUMMARY_MODEL_NAME,
            expected_article_ids=all_ids,
            expected_latest_summary_id=expected_latest_summary_id,
        )
        if saved is None:
            return False
        expected_latest_summary_id = int(saved.id)
        prior_ids.update(incorporated)
        prior = dict(saved.summary_json)
        remaining = remaining[len(selected):]
    logger.info("generate_summary_update saved for event %s: %s new article(s), skipped=%s",
                event_id, len(pending), skipped_unprocessable)
    return not skipped_unprocessable
