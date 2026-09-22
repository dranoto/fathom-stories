# app/grouping/engine.py
import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Tuple, Awaitable, Callable, Set

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from sqlalchemy import desc, or_, func, and_

from ..database import db_session_scope
from ..database.models import Article, Event
from .. import config as app_config
from .prompts import build_group_assign_prompt, build_few_shot_block, build_regroup_prompt
from .feedback import build_few_shot_examples
from .content_classifier import classify_title
from .lifecycle import reset_expiry, reset_expiry_on_event
from .response_parser import parse_json_object
from .summary_queue import (
    delete_persisted_summary_updates,
    persist_summary_updates_in_session,
)

logger = logging.getLogger(__name__)


SUMMARY_GUARD_TIMEOUT = app_config.SUMMARY_REQUEST_TIMEOUT + 30
GROUPING_GUARD_TIMEOUT = app_config.GROUPING_REQUEST_TIMEOUT + 30


def _normalize_event_name(name: str) -> str:
    if not name:
        return ""
    return " ".join(name.strip().lower().split())


def _articles_have_distinct_sources(articles: List[Article]) -> bool:
    if not app_config.REQUIRE_DISTINCT_SOURCES:
        return True
    sources = {
        (a.publisher_name or "").strip().casefold()
        for a in articles
        if (a.publisher_name or "").strip()
    }
    return len(sources) >= 2


def find_or_create_event(
    db,
    name: str,
    now: datetime,
    anchor: Optional[datetime] = None,
) -> Tuple[Event, str]:
    """
    Reuse an existing event with the same normalized name, or create a new one.

    Match scope: active + archived within ARCHIVE_REVIVE_WINDOW_DAYS.
    Reuse order: oldest created_at first (so we never lose history).

    Returns (event, outcome) where outcome is one of:
      "created"  — a brand new Event row was created
      "reused"   — matched an existing active event
      "revived"  — matched an archived event within the revival window; it is now active

    Note: there is no "cooling" state — events are either active or archived.
    """
    normalized = _normalize_event_name(name)
    if normalized:
        revival_cutoff = now - timedelta(days=app_config.ARCHIVE_REVIVE_WINDOW_DAYS)
        existing = (
            db.query(Event)
            .filter(func.lower(Event.name) == normalized)
            .filter(
                or_(
                    Event.status == "active",
                    and_(
                        Event.status == "archived",
                        Event.archived_at.isnot(None),
                        Event.archived_at >= revival_cutoff,
                    ),
                )
            )
            .order_by(Event.created_at.asc(), Event.id.asc())
            .first()
        )
        if existing:
            outcome = "revived" if existing.status == "archived" else "reused"
            if existing.status == "archived":
                existing.status = "active"
                existing.archived_at = None
            if anchor:
                anchor_aware = anchor if anchor.tzinfo else anchor.replace(tzinfo=timezone.utc)
                existing_la = existing.last_article_at
                if existing_la is not None and existing_la.tzinfo is None:
                    existing_la = existing_la.replace(tzinfo=timezone.utc)
                if not existing_la or anchor_aware > existing_la:
                    existing.last_article_at = anchor
            return existing, outcome

    new_event = Event(
        name=name.strip(),
        status="active",
        last_article_at=anchor or now,
        expires_at=reset_expiry(),
    )
    db.add(new_event)
    db.flush()
    return new_event, "created"


def _event_summary_for_prompt(event: Event, max_titles: int = 5) -> Dict[str, Any]:
    def article_sort_key(article: Article) -> Tuple[float, int]:
        published = article.published_date
        article_id = getattr(article, "id", 0)
        numeric_id = article_id if isinstance(article_id, int) else 0
        if published is None:
            return (float("-inf"), numeric_id)
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        return (published.timestamp(), numeric_id)

    recent_articles = sorted(event.articles or [], key=article_sort_key, reverse=True)
    return {
        "id": event.id,
        "name": event.name,
        "description": event.description,
        "last_article_at": event.last_article_at.isoformat() if event.last_article_at else None,
        "recent_titles": [a.title for a in recent_articles[:max_titles] if a.title],
    }


def _article_for_prompt(article: Article, snippet_chars: int = 500) -> Dict[str, Any]:
    snippet = (article.scraped_text_content or article.rss_description or "")[:snippet_chars]
    return {
        "id": article.id,
        "title": article.title,
        "source": article.publisher_name,
        "published_date": article.published_date.isoformat() if article.published_date else None,
        "snippet": snippet,
        "content_type": classify_title(article.title or ""),
    }


def _chunked(seq: List[Any], size: int):
    if size <= 0:
        yield seq
        return
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def fetch_ungrouped_articles(
    limit: Optional[int] = None,
    window_hours: Optional[int] = None,
    *,
    include_processed: bool = False,
) -> List[Article]:
    window_hours = window_hours if window_hours is not None else app_config.LIVE_GROUP_WINDOW_HOURS
    with db_session_scope() as db:
        q = db.query(Article).filter(Article.event_id.is_(None))
        if not include_processed:
            q = q.filter(Article.grouped_at.is_(None))
        if window_hours and window_hours > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
            q = q.filter(or_(Article.published_date >= cutoff, Article.published_date.is_(None)))
        q = q.order_by(desc(Article.published_date))
        if limit:
            q = q.limit(limit)
        rows = q.all()
        for r in rows:
            _ = r.title
            _ = r.publisher_name
        return rows


def fetch_active_events() -> Tuple[List[Event], List[Event]]:
    with db_session_scope() as db:
        active = (
            db.query(Event)
            .filter(Event.status == "active")
            .order_by(desc(Event.last_article_at))
            .all()
        )
        cooling = (
            db.query(Event)
            .filter(Event.status == "cooling")
            .order_by(desc(Event.last_article_at))
            .all()
        )
        for ev in active + cooling:
            _ = ev.name
            _ = ev.description
            _ = ev.articles
        return active, cooling


def apply_assignments(assignments: List[Dict[str, Any]]) -> Dict[str, int]:
    counts, _ = _apply_live(assignments)
    return counts


def _apply_live(
    assignments: List[Dict[str, Any]],
    *,
    create_new_events: bool = True,
) -> Tuple[Dict[str, int], Dict[int, List[int]]]:
    counts = {
        "existing": 0,
        "new": 0,
        "reused": 0,
        "singleton": 0,
        "uncategorized": 0,
        "errors": 0,
    }
    now = datetime.now(timezone.utc)
    event_increments: Dict[int, List[int]] = defaultdict(list)

    with db_session_scope() as db:
        for a in assignments:
            try:
                article_id = a.get("article_id")
                decision = a.get("decision")
                article = db.query(Article).filter(Article.id == article_id).first()
                if not article:
                    counts["errors"] += 1
                    continue

                if decision == "existing":
                    ev_id = a.get("event_id")
                    event = db.query(Event).filter(Event.id == ev_id).first()
                    if not event:
                        counts["errors"] += 1
                        continue
                    article.event_id = event.id
                    article.proposed_event_name = None
                    importance = float(a.get("importance_score") or 0.5)
                    article.importance_score = importance
                    article.grouping_confidence = float(a.get("confidence") or 0.0)
                    article.grouped_at = now
                    if not event.last_article_at or (article.published_date and article.published_date > event.last_article_at):
                        event.last_article_at = article.published_date or now
                    event.status = "active"
                    event.archived_at = None
                    reset_expiry_on_event(event)
                    event_increments[event.id].append(article_id)
                    counts["existing"] += 1
                elif decision == "new":
                    name = (a.get("event_name") or "").strip()
                    if not name:
                        counts["errors"] += 1
                        continue
                    importance = float(a.get("importance_score") or 0.5)
                    confidence = float(a.get("confidence") or 0.0)
                    if not create_new_events or not _articles_have_distinct_sources([article]):
                        article.proposed_event_name = name
                        article.importance_score = importance
                        article.grouping_confidence = confidence
                        article.grouped_at = now
                        counts["singleton"] += 1
                        continue
                    new_event, outcome = find_or_create_event(
                        db,
                        name,
                        now=now,
                        anchor=article.published_date or now,
                    )
                    article.event_id = new_event.id
                    article.proposed_event_name = None
                    article.importance_score = importance
                    article.grouping_confidence = confidence
                    article.grouped_at = now
                    if outcome == "created":
                        counts["new"] += 1
                    else:
                        counts[outcome] += 1
                        reset_expiry_on_event(new_event)
                    event_increments[new_event.id].append(article_id)
                elif decision == "uncategorized":
                    counts["uncategorized"] += 1
                    article.grouped_at = now
                    article.proposed_event_name = None
                    article.importance_score = float(a.get("importance_score") or 0.5)
                    article.grouping_confidence = float(a.get("confidence") or 0.0)
                else:
                    counts["errors"] += 1
            except Exception as e:
                logger.error(f"GROUPING: error applying assignment {a}: {e}", exc_info=True)
                counts["errors"] += 1
        persist_summary_updates_in_session(db, event_increments)
    logger.info(f"GROUPING: applied assignments {counts}")
    return counts, dict(event_increments)


async def assign_new_articles(
    llm: ChatOpenAI,
    *,
    create_new_events: bool = False,
    on_event_increments: Optional[Callable[[Dict[int, List[int]]], Awaitable[int]]] = None,
    summary_llm: Optional[ChatOpenAI] = None,
) -> Dict[str, int]:
    articles = fetch_ungrouped_articles(
        limit=app_config.LIVE_GROUP_MAX_ARTICLES,
        window_hours=app_config.LIVE_GROUP_WINDOW_HOURS,
    )
    if not articles:
        logger.info(f"GROUPING: no ungrouped articles in last {app_config.LIVE_GROUP_WINDOW_HOURS}h window")
        return {"existing": 0, "new": 0, "uncategorized": 0, "errors": 0, "skipped": 1}

    active, cooling = fetch_active_events()
    active_payload = [_event_summary_for_prompt(e) for e in active]
    cooling_payload = [_event_summary_for_prompt(e) for e in cooling]

    few_shot = build_few_shot_examples(limit=5)
    few_shot_block = build_few_shot_block(few_shot)

    batch_size = max(1, app_config.LIVE_GROUP_BATCH_SIZE)
    n_batches = (len(articles) + batch_size - 1) // batch_size
    logger.info(f"GROUPING: {len(articles)} articles in {n_batches} batch(es) of {batch_size}")

    all_assignments: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {
        "existing": 0, "new": 0, "uncategorized": 0, "errors": 0, "skipped": 0,
    }
    event_increments: Dict[int, List[int]] = defaultdict(list)

    for idx, chunk in enumerate(_chunked(articles, batch_size), start=1):
        try:
            chunk_increments = await asyncio.wait_for(
                _assign_chunk(
                    llm=llm,
                    chunk=chunk,
                    idx=idx,
                    n_batches=n_batches,
                    active_payload=active_payload,
                    cooling_payload=cooling_payload,
                    few_shot_block=few_shot_block,
                    create_new_events=create_new_events,
                ),
                timeout=GROUPING_GUARD_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.error(f"GROUPING: batch {idx}/{n_batches} timed out after {GROUPING_GUARD_TIMEOUT}s; marking chunk as errors")
            counts["errors"] += len(chunk)
            continue
        if chunk_increments is None:
            counts["errors"] += len(chunk)
            continue
        all_assignments.extend(chunk_increments[0])
        for ev_id, ids in chunk_increments[1].items():
            event_increments[ev_id].extend(ids)

    if all_assignments:
        applied_counts, applied_increments = _apply_live(
            all_assignments,
            create_new_events=create_new_events,
        )
        counts["existing"] += applied_counts.get("existing", 0)
        counts["new"] += applied_counts.get("new", 0)
        counts["uncategorized"] += applied_counts.get("uncategorized", 0)
        counts["errors"] += applied_counts.get("errors", 0)
        for ev_id, ids in applied_increments.items():
            event_increments[ev_id].extend(ids)

    await _process_event_increments(
        dict(event_increments),
        on_event_increments=on_event_increments,
        summary_llm=summary_llm,
    )
    return counts


async def assign_new_articles_with_jev(
    classifier: Any,
    *,
    on_event_increments: Optional[Callable[[Dict[int, List[int]]], Awaitable[int]]] = None,
    summary_llm: Optional[ChatOpenAI] = None,
) -> Dict[str, int]:
    articles = fetch_ungrouped_articles(
        limit=app_config.LIVE_GROUP_MAX_ARTICLES,
        window_hours=app_config.LIVE_GROUP_WINDOW_HOURS,
    )
    if not articles:
        logger.info(f"JEV GROUPING: no ungrouped articles in last {app_config.LIVE_GROUP_WINDOW_HOURS}h window")
        return {
            "existing": 0,
            "new": 0,
            "uncategorized": 0,
            "errors": 0,
            "provider_errors": 0,
            "timed_out": 0,
            "circuit_open": 0,
            "degraded": 0,
            "skipped": 1,
        }

    active, cooling = fetch_active_events()
    event_payload = [_event_summary_for_prompt(event) for event in active + cooling]
    article_payload = [_article_for_prompt(article) for article in articles]
    classification_tasks = [
        asyncio.create_task(classifier.classify(article, event_payload))
        for article in article_payload
    ]
    pending = set(classification_tasks)
    try:
        _done, pending = await asyncio.wait(
            classification_tasks,
            timeout=app_config.JEV_BATCH_TIMEOUT_SECONDS,
        )
    except BaseException:
        for task in classification_tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*classification_tasks, return_exceptions=True)
        raise
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    results: List[Any] = []
    for task in classification_tasks:
        if task in pending:
            results.append(asyncio.TimeoutError("Jev live grouping batch deadline exceeded"))
            continue
        try:
            results.append(task.result())
        except BaseException as exc:
            results.append(exc)

    assignments: List[Dict[str, Any]] = []
    provider_errors = 0
    timed_out = 0
    circuit_rejections = 0
    logged_errors = 0
    for article, result in zip(article_payload, results):
        if isinstance(result, BaseException):
            provider_errors += 1
            if isinstance(result, asyncio.TimeoutError):
                timed_out += 1
            if type(result).__name__ == "JevCircuitOpenError":
                circuit_rejections += 1
            elif logged_errors < 5:
                logged_errors += 1
                logger.error(
                    f"JEV GROUPING: article {article['id']} classification failed: {result}",
                    exc_info=(type(result), result, result.__traceback__),
                )
            continue
        assignments.append(result)

    circuit_open = int(
        bool(circuit_rejections)
        or (hasattr(classifier, "circuit_open") and await classifier.circuit_open())
    )
    counts: Dict[str, int] = {
        "existing": 0,
        "new": 0,
        "uncategorized": 0,
        "errors": provider_errors,
        "provider_errors": provider_errors,
        "timed_out": timed_out,
        "circuit_open": circuit_open,
        "degraded": int(provider_errors > 0),
        "skipped": 0,
    }
    event_increments: Dict[int, List[int]] = {}
    if assignments:
        applied_counts, event_increments = _apply_live(assignments, create_new_events=False)
        counts["existing"] += applied_counts.get("existing", 0)
        counts["uncategorized"] += applied_counts.get("uncategorized", 0)
        counts["errors"] += applied_counts.get("errors", 0)

    await _process_event_increments(
        event_increments,
        on_event_increments=on_event_increments,
        summary_llm=summary_llm,
    )
    logger.info(f"JEV GROUPING: classified {len(articles)} articles with result {counts}")
    return counts


async def _process_event_increments(
    event_increments: Dict[int, List[int]],
    *,
    on_event_increments: Optional[Callable[[Dict[int, List[int]]], Awaitable[int]]],
    summary_llm: Optional[ChatOpenAI],
) -> None:
    if not event_increments:
        return
    if on_event_increments is not None:
        await on_event_increments(event_increments)
        return
    if summary_llm is None:
        logger.warning("GROUPING: summary updates skipped because no summary LLM is available")
        return

    from .summary_service import generate_summary_update
    for event_id, new_ids in event_increments.items():
        try:
            succeeded = bool(
                await asyncio.wait_for(
                    generate_summary_update(event_id, new_ids, summary_llm),
                    timeout=SUMMARY_GUARD_TIMEOUT,
                )
            )
            if succeeded:
                delete_persisted_summary_updates(event_id, new_ids)
            else:
                logger.error(
                    "Auto-summary-update returned failure for event %s; durable "
                    "work remains queued",
                    event_id,
                )
        except asyncio.TimeoutError:
            logger.error(f"Auto-summary-update timed out for event {event_id} after {SUMMARY_GUARD_TIMEOUT}s; durable work remains queued")
        except Exception as e:
            logger.error(f"Auto-summary-update failed for event {event_id}: {e}", exc_info=True)


async def _agenerate_with_retry(llm: ChatOpenAI, messages: List[HumanMessage]) -> Any:
    last_err: Optional[BaseException] = None
    for attempt in range(1, 3):
        try:
            return await llm.agenerate(messages)
        except Exception as e:
            last_err = e
            if attempt < 2:
                logger.warning(
                    f"GROUPING: agenerate attempt {attempt}/2 failed: "
                    f"{type(e).__name__}: {e}; retrying once"
                )
    assert last_err is not None
    raise last_err


def _is_context_overflow(exc: BaseException) -> bool:
    name = type(exc).__name__
    if name in ("OpenAIContextOverflowError", "ContextOverflowError"):
        return True
    msg = str(exc)
    return "context_length_exceeded" in msg or "context length" in msg.lower()


async def _assign_chunk(
    llm: ChatOpenAI,
    chunk: List[Article],
    idx: int,
    n_batches: int,
    active_payload: List[Dict[str, Any]],
    cooling_payload: List[Dict[str, Any]],
    few_shot_block: str,
    create_new_events: bool,
) -> Optional[Tuple[List[Dict[str, Any]], Dict[int, List[int]]]]:
    payload = [_article_for_prompt(a) for a in chunk]
    prompt = build_group_assign_prompt(
        active_events=active_payload,
        cooling_events=cooling_payload,
        articles=payload,
        few_shot_block=few_shot_block,
    )

    content: Optional[str] = None
    for attempt, snippet_chars in enumerate((500, 200), start=1):
        try:
            response = await _agenerate_with_retry(llm, [[HumanMessage(content=prompt)]])
            content = response.generations[0][0].text
            break
        except Exception as e:
            if attempt == 1 and _is_context_overflow(e):
                logger.warning(
                    f"GROUPING: batch {idx}/{n_batches} overflow ({e}); retrying with snippet_chars=200"
                )
                payload = [_article_for_prompt(a, snippet_chars=snippet_chars) for a in chunk]
                prompt = build_group_assign_prompt(
                    active_events=active_payload,
                    cooling_events=cooling_payload,
                    articles=payload,
                    few_shot_block=few_shot_block,
                )
                continue
            logger.error(f"GROUPING: batch {idx}/{n_batches} LLM call failed: {e}", exc_info=True)
            return None

    if content is None:
        return None

    try:
        parsed = parse_json_object(content)
    except Exception as e:
        logger.error(f"GROUPING: batch {idx}/{n_batches} parse failed: {e}\n{content[:1000]}")
        return None

    return parsed.get("assignments", []), {}


async def regroup_uncategorized(
    llm: ChatOpenAI,
    *,
    summary_llm: Optional[ChatOpenAI] = None,
    on_event_increments: Optional[Callable[[Dict[int, List[int]]], Awaitable[int]]] = None,
    on_new_events: Optional[Callable[[List[int]], Awaitable[Optional[List[int]]]]] = None,
) -> Dict[str, int]:
    articles = fetch_ungrouped_articles(
        limit=100,
        window_hours=0,
        include_processed=True,
    )
    if not articles:
        logger.info("REGROUP: no ungrouped articles")
        return {"existing": 0, "new_events": 0, "new_singletons": 0, "uncategorized": 0, "errors": 0, "skipped": 1, "batches": 0, "batches_failed": 0}

    active, cooling = fetch_active_events()
    active_payload = [_event_summary_for_prompt(e) for e in active]
    cooling_payload = [_event_summary_for_prompt(e) for e in cooling]

    few_shot = build_few_shot_examples(limit=5)
    few_shot_block = build_few_shot_block(few_shot)

    batch_size = max(1, app_config.REGROUP_BATCH_SIZE)
    total_counts = {
        "existing": 0, "new_events": 0, "revived": 0, "reused": 0,
        "new_singletons": 0, "uncategorized": 0, "errors": 0,
        "batches": 0, "batches_failed": 0,
    }
    all_new_event_ids: List[int] = []
    event_increments: Dict[int, List[int]] = defaultdict(list)
    n_batches = (len(articles) + batch_size - 1) // batch_size
    logger.info(f"REGROUP: {len(articles)} articles in {n_batches} batch(es) of {batch_size}")

    for idx, chunk in enumerate(_chunked(articles, batch_size), start=1):
        payload = [_article_for_prompt(a) for a in chunk]
        prompt = build_regroup_prompt(
            active_events=active_payload,
            cooling_events=cooling_payload,
            articles=payload,
            few_shot_block=few_shot_block,
        )
        try:
            response = await asyncio.wait_for(
                _agenerate_with_retry(llm, [[HumanMessage(content=prompt)]]),
                timeout=GROUPING_GUARD_TIMEOUT,
            )
            content = response.generations[0][0].text
        except asyncio.TimeoutError:
            logger.error(f"REGROUP: batch {idx}/{n_batches} timed out after {GROUPING_GUARD_TIMEOUT}s; marking chunk as errors")
            total_counts["batches_failed"] += 1
            total_counts["errors"] += len(chunk)
            continue
        except Exception as e:
            logger.error(f"REGROUP: batch {idx}/{n_batches} LLM call failed: {e}")
            total_counts["batches_failed"] += 1
            total_counts["errors"] += len(chunk)
            continue

        try:
            parsed = parse_json_object(content)
        except Exception as e:
            logger.error(f"REGROUP: batch {idx}/{n_batches} parse failed: {e}\n{content[:1000]}")
            total_counts["batches_failed"] += 1
            total_counts["errors"] += len(chunk)
            continue

        assignments = parsed.get("assignments", [])
        chunk_counts, (new_event_ids, chunk_increments) = _apply_regroup_inner(assignments)
        for k, v in chunk_counts.items():
            if k in total_counts:
                total_counts[k] += v
        total_counts["batches"] += 1
        all_new_event_ids.extend(new_event_ids)
        for ev_id, new_ids in chunk_increments.items():
            event_increments[ev_id].extend(new_ids)

    dedup_counts: Dict[str, int] = {}
    dedup_increments: Dict[int, List[int]] = defaultdict(list)
    try:
        from .dedup import dedup_events
        dedup_counts = await dedup_events(
            llm,
            summary_increments=dedup_increments,
        )
    except Exception as e:
        logger.error(f"REGROUP: dedup pass failed: {e}", exc_info=True)

    for event_id, article_ids in dedup_increments.items():
        event_increments[event_id].extend(article_ids)

    # Dedup can move articles and delete newly-created secondary events. Reconcile
    # the summary work against the final committed mapping before generating it.
    current_increments: Dict[int, List[int]] = {}
    with db_session_scope() as db:
        requested_new_event_ids = set(all_new_event_ids)
        surviving_new_event_ids = {
            row[0]
            for row in db.query(Event.id)
            .filter(Event.id.in_(requested_new_event_ids))
            .all()
        } if requested_new_event_ids else set()
        for event_id, article_ids in event_increments.items():
            requested_ids = set(article_ids)
            if not requested_ids:
                continue
            valid_ids = [
                row[0]
                for row in db.query(Article.id)
                .filter(
                    Article.event_id == event_id,
                    Article.id.in_(requested_ids),
                )
                .all()
            ]
            if valid_ids:
                current_increments[event_id] = valid_ids

    event_increments = defaultdict(list, current_increments)
    new_event_ids_set = surviving_new_event_ids
    failed_initial_event_ids: Set[int] = set()
    if surviving_new_event_ids:
        initial_event_ids = sorted(surviving_new_event_ids)
        if on_new_events is not None:
            try:
                failed_ids = await on_new_events(initial_event_ids)
                if failed_ids:
                    failed_initial_event_ids.update(int(event_id) for event_id in failed_ids)
            except Exception as e:
                failed_initial_event_ids.update(new_event_ids_set)
                logger.error(
                    "Auto-initial summary callback failed; routing all new events "
                    "through the retryable incremental path: %s",
                    e,
                    exc_info=True,
                )
        else:
            from .summary_service import generate_initial_summary
            initial_summary_llm = summary_llm or llm
            for new_event_id in initial_event_ids:
                try:
                    succeeded = bool(
                        await asyncio.wait_for(
                            generate_initial_summary(new_event_id, initial_summary_llm),
                            timeout=SUMMARY_GUARD_TIMEOUT,
                        )
                    )
                    if not succeeded:
                        failed_initial_event_ids.add(new_event_id)
                    else:
                        delete_persisted_summary_updates(
                            new_event_id,
                            event_increments.get(new_event_id, []),
                        )
                except asyncio.TimeoutError:
                    failed_initial_event_ids.add(new_event_id)
                    logger.error(f"Auto-initial summary timed out for event {new_event_id} after {SUMMARY_GUARD_TIMEOUT}s; retrying through incremental path")
                except Exception as e:
                    failed_initial_event_ids.add(new_event_id)
                    logger.error(f"Auto-initial summary failed for event {new_event_id}: {e}", exc_info=True)
    queued_increments = {
        event_id: new_ids
        for event_id, new_ids in event_increments.items()
        if event_id not in new_event_ids_set or event_id in failed_initial_event_ids
    }
    await _process_event_increments(
        queued_increments,
        on_event_increments=on_event_increments,
        summary_llm=summary_llm or llm,
    )

    total_counts["dedup"] = dedup_counts
    logger.info(f"REGROUP: applied assignments {total_counts}")
    return total_counts


def apply_regroup_assignments(assignments: List[Dict[str, Any]]) -> Dict[str, int]:
    counts, _ = _apply_regroup_inner(assignments)
    return counts


def _apply_regroup_inner(assignments: List[Dict[str, Any]]) -> Tuple[Dict[str, int], Tuple[List[int], Dict[int, List[int]]]]:
    counts = {
        "existing": 0,
        "new_events": 0,
        "revived": 0,
        "reused": 0,
        "new_singletons": 0,
        "uncategorized": 0,
        "errors": 0,
    }
    now = datetime.now(timezone.utc)
    new_event_ids: List[int] = []
    event_increments: Dict[int, List[int]] = defaultdict(list)

    new_clusters: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for a in assignments:
        try:
            decision = a.get("decision")
            if decision == "new":
                name = (a.get("event_name") or "").strip()
                if name:
                    new_clusters[name].append(a)
        except Exception:
            counts["errors"] += 1

    with db_session_scope() as db:
        for a in assignments:
            try:
                article_id = a.get("article_id")
                decision = a.get("decision")
                article = db.query(Article).filter(Article.id == article_id).first()
                if not article:
                    counts["errors"] += 1
                    continue

                if decision == "existing":
                    ev_id = a.get("event_id")
                    event = db.query(Event).filter(Event.id == ev_id).first()
                    if not event:
                        counts["errors"] += 1
                        continue
                    article.event_id = event.id
                    article.proposed_event_name = None
                    importance = float(a.get("importance_score") or 0.5)
                    article.importance_score = importance
                    article.grouping_confidence = float(a.get("confidence") or 0.0)
                    article.grouped_at = now
                    if not event.last_article_at or (article.published_date and article.published_date > event.last_article_at):
                        event.last_article_at = article.published_date or now
                    event.status = "active"
                    event.archived_at = None
                    reset_expiry_on_event(event)
                    event_increments[event.id].append(article_id)
                    counts["existing"] += 1
                elif decision == "new":
                    # New clusters are applied together in the second pass below.
                    continue
                elif decision == "uncategorized":
                    counts["uncategorized"] += 1
                    article.proposed_event_name = None
                    article.importance_score = float(a.get("importance_score") or 0.5)
                    article.grouping_confidence = float(a.get("confidence") or 0.0)
                    article.grouped_at = now
                else:
                    counts["errors"] += 1
            except Exception as e:
                logger.error(f"REGROUP: error applying assignment {a}: {e}", exc_info=True)
                counts["errors"] += 1

        for name, items in new_clusters.items():
            if len(items) >= 2:
                first = items[0]
                first_article = db.query(Article).filter(Article.id == first.get("article_id")).first()
                if not first_article:
                    counts["errors"] += 1
                    continue
                first_importance = float(first.get("importance_score") or 0.5)
                cluster_rows: List[Article] = []
                for item in items:
                    art = db.query(Article).filter(Article.id == item.get("article_id")).first()
                    if art:
                        cluster_rows.append(art)
                if not _articles_have_distinct_sources(cluster_rows):
                    for item in items:
                        art = db.query(Article).filter(Article.id == item.get("article_id")).first()
                        if art:
                            art.proposed_event_name = name
                            art.importance_score = float(item.get("importance_score") or 0.5)
                            art.grouping_confidence = float(item.get("confidence") or 0.0)
                            art.grouped_at = now
                    counts["new_singletons"] += 1
                    continue
                new_event, outcome = find_or_create_event(
                    db,
                    name,
                    now=now,
                    anchor=first_article.published_date or now,
                )
                if outcome == "created":
                    new_event_ids.append(new_event.id)
                    counts["new_events"] += 1
                else:
                    counts[outcome] += 1
                for item in items:
                    art = db.query(Article).filter(Article.id == item.get("article_id")).first()
                    if not art:
                        continue
                    art.event_id = new_event.id
                    art.proposed_event_name = None
                    importance = float(item.get("importance_score") or 0.5)
                    art.importance_score = importance
                    art.grouping_confidence = float(item.get("confidence") or 0.0)
                    art.grouped_at = now
                    if not new_event.last_article_at or (art.published_date and art.published_date > new_event.last_article_at):
                        new_event.last_article_at = art.published_date or now
                    reset_expiry_on_event(new_event)
                    event_increments[new_event.id].append(art.id)
            else:
                for item in items:
                    art = db.query(Article).filter(Article.id == item.get("article_id")).first()
                    if art:
                        art.proposed_event_name = name
                        art.importance_score = float(item.get("importance_score") or 0.5)
                        art.grouping_confidence = float(item.get("confidence") or 0.0)
                        art.grouped_at = now
                counts["new_singletons"] += 1

        persist_summary_updates_in_session(db, event_increments)

    logger.info(f"REGROUP: applied assignments {counts}")
    return counts, (new_event_ids, dict(event_increments))
