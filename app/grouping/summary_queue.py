import asyncio
import logging
from collections import defaultdict
from typing import Dict, List, Set, Tuple

from sqlalchemy.exc import IntegrityError

from .. import config as app_config
from ..database import db_session_scope
from ..database.models import Article, PendingSummaryUpdate
from .summary_service import generate_initial_summary, generate_summary_update

logger = logging.getLogger(__name__)


def persist_summary_updates_in_session(
    db,
    event_increments: Dict[int, List[int]],
) -> Tuple[Dict[int, List[int]], int]:
    """Persist valid pairs in the caller's transaction.

    This is the transactional outbox boundary: callers that change an article's
    event assignment use the same session to record the summary work, so a crash
    cannot commit one without the other.
    """
    normalized: Dict[int, List[int]] = {}
    inserted = 0
    for raw_event_id, raw_article_ids in event_increments.items():
        event_id = int(raw_event_id)
        requested = {
            int(article_id)
            for article_id in raw_article_ids
            if article_id is not None
        }
        if not requested:
            continue

        # Flush assignment changes before validating the current mapping.
        db.flush()
        valid_ids = {
            row[0]
            for row in db.query(Article.id)
            .filter(Article.event_id == event_id, Article.id.in_(requested))
            .all()
        }
        if not valid_ids:
            continue

        # One article can only belong to one event. Remove any stale outbox row
        # for its previous event in the same transaction as the move.
        db.query(PendingSummaryUpdate).filter(
            PendingSummaryUpdate.article_id.in_(valid_ids),
            PendingSummaryUpdate.event_id != event_id,
        ).delete(synchronize_session=False)

        for article_id in valid_ids:
            try:
                with db.begin_nested():
                    db.add(
                        PendingSummaryUpdate(
                            event_id=event_id,
                            article_id=article_id,
                        )
                    )
                    db.flush()
                inserted += 1
            except IntegrityError:
                # Another concurrent enqueue already persisted this pair.
                pass
        normalized[event_id] = sorted(valid_ids)
    return normalized, inserted


def _persist_summary_updates(
    event_increments: Dict[int, List[int]],
) -> Tuple[Dict[int, List[int]], int]:
    """Persist valid event/article pairs and return normalized IDs plus insert count."""
    with db_session_scope() as db:
        return persist_summary_updates_in_session(db, event_increments)


def persist_summary_updates(event_increments: Dict[int, List[int]]) -> int:
    """Persist updates when the in-memory queue is unavailable during shutdown."""
    _normalized, inserted = _persist_summary_updates(event_increments)
    return inserted


def _load_persisted_updates() -> Dict[int, Set[int]]:
    """Load retry work and remove rows already summarized or no longer valid."""
    pending: Dict[int, Set[int]] = defaultdict(set)
    with db_session_scope() as db:
        rows = db.query(PendingSummaryUpdate).all()
        by_event: Dict[int, List[PendingSummaryUpdate]] = defaultdict(list)
        for row in rows:
            by_event[int(row.event_id)].append(row)

        for event_id, event_rows in by_event.items():
            article_ids = [int(row.article_id) for row in event_rows]
            valid_ids = {
                row[0]
                for row in db.query(Article.id)
                .filter(Article.event_id == event_id, Article.id.in_(article_ids))
                .all()
            }
            for row in event_rows:
                article_id = int(row.article_id)
                if article_id not in valid_ids:
                    db.delete(row)
                else:
                    pending[event_id].add(article_id)
    return pending


def _delete_persisted_updates(event_id: int, article_ids: Set[int]) -> None:
    if not article_ids:
        return
    with db_session_scope() as db:
        db.query(PendingSummaryUpdate).filter(
            PendingSummaryUpdate.event_id == event_id,
            PendingSummaryUpdate.article_id.in_(article_ids),
        ).delete(synchronize_session=False)


def delete_persisted_summary_updates(event_id: int, article_ids: List[int]) -> None:
    """Clear durable outbox rows after a synchronous summary succeeds."""
    _delete_persisted_updates(event_id, {int(article_id) for article_id in article_ids})


def _prune_invalid_persisted_updates(event_id: int, article_ids: Set[int]) -> Set[int]:
    """Delete queued IDs whose event/article mapping no longer exists."""
    if not article_ids:
        return set()
    with db_session_scope() as db:
        valid_ids = {
            row[0]
            for row in db.query(Article.id)
            .filter(Article.event_id == event_id, Article.id.in_(article_ids))
            .all()
        }
        invalid_ids = article_ids - valid_ids
        if invalid_ids:
            db.query(PendingSummaryUpdate).filter(
                PendingSummaryUpdate.event_id == event_id,
                PendingSummaryUpdate.article_id.in_(invalid_ids),
            ).delete(synchronize_session=False)
    return invalid_ids


def _event_article_ids(event_id: int) -> List[int]:
    with db_session_scope() as db:
        return [
            row[0]
            for row in db.query(Article.id)
            .filter(Article.event_id == event_id)
            .all()
        ]


class SummaryQueue:
    def __init__(
        self,
        llm,
        debounce_seconds: float,
        guard_timeout: float,
        *,
        durable: bool = False,
    ):
        self.llm = llm
        self.debounce_seconds = max(0.0, debounce_seconds)
        self.guard_timeout = guard_timeout
        self.durable = durable
        self._pending: Dict[int, Set[int]] = defaultdict(set)
        if durable:
            for event_id, article_ids in _load_persisted_updates().items():
                self._pending[event_id].update(article_ids)
        self._deadlines: Dict[int, float] = {}
        self._tasks: Dict[int, asyncio.Task] = {}
        self._wakeups: Dict[int, asyncio.Event] = {}
        self._lock = asyncio.Lock()
        self._flush_lock = asyncio.Lock()

    def _start_event_locked(self, event_id: int, *, force: bool = False) -> asyncio.Task:
        task = self._tasks.get(event_id)
        if task is not None and not task.done():
            if force:
                self._wakeups[event_id].set()
            return task

        wakeup = asyncio.Event()
        if force:
            wakeup.set()
        self._wakeups[event_id] = wakeup
        self._deadlines[event_id] = (
            asyncio.get_running_loop().time() + self.debounce_seconds
        )
        task = asyncio.create_task(self._run_event(event_id))
        self._tasks[event_id] = task
        return task

    async def enqueue(self, event_increments: Dict[int, List[int]]) -> int:
        if self.durable:
            normalized, _inserted = _persist_summary_updates(event_increments)
        else:
            normalized = event_increments

        queued = 0
        async with self._lock:
            for event_id, article_ids in normalized.items():
                valid_ids = {
                    int(article_id)
                    for article_id in article_ids
                    if article_id is not None
                }
                if not valid_ids:
                    continue
                event_id = int(event_id)
                pending_ids = self._pending[event_id]
                new_ids = valid_ids - pending_ids
                pending_ids.update(valid_ids)
                queued += len(new_ids)
                self._start_event_locked(event_id)
        return queued

    async def _run_event(self, event_id: int) -> Tuple[bool, Set[int]]:
        current_task = asyncio.current_task()
        article_ids: List[int] = []
        succeeded = False
        discarded_ids: Set[int] = set()
        try:
            async with self._lock:
                pending_ids = self._pending.get(event_id)
                if not pending_ids:
                    return True, set()
                deadline = self._deadlines[event_id]
                wakeup = self._wakeups[event_id]

            delay = deadline - asyncio.get_running_loop().time()
            if delay > 0 and not wakeup.is_set():
                try:
                    await asyncio.wait_for(wakeup.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass

            async with self._lock:
                article_ids = sorted(self._pending.get(event_id, set()))
            if not article_ids:
                return True, set()

            if self.durable:
                discarded_ids = _prune_invalid_persisted_updates(
                    event_id,
                    set(article_ids),
                )
                if discarded_ids:
                    article_ids = [
                        article_id
                        for article_id in article_ids
                        if article_id not in discarded_ids
                    ]
                if not article_ids:
                    succeeded = True
                    return True, set(discarded_ids)

            try:
                succeeded = bool(
                    await asyncio.wait_for(
                        generate_summary_update(event_id, article_ids, self.llm),
                        timeout=self.guard_timeout,
                    )
                )
                if not succeeded:
                    logger.error(
                        "Queued summary update returned failure for event %s; "
                        "keeping %s article(s) pending",
                        event_id,
                        len(article_ids),
                    )
            except asyncio.TimeoutError:
                logger.error(
                    "Queued summary update timed out for event %s after %ss; "
                    "keeping %s article(s) pending",
                    event_id,
                    self.guard_timeout,
                    len(article_ids),
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(
                    "Queued summary update failed for event %s; keeping %s article(s) "
                    "pending: %s",
                    event_id,
                    len(article_ids),
                    e,
                    exc_info=True,
                )

            attempted_ids = set(article_ids)
            if self.durable:
                try:
                    if succeeded:
                        _delete_persisted_updates(event_id, attempted_ids)
                    else:
                        discarded_ids = _prune_invalid_persisted_updates(
                            event_id,
                            attempted_ids,
                        )
                except Exception as e:
                    # A successful summary without a successful durable dequeue is
                    # retried safely after restart rather than losing the work.
                    if succeeded:
                        succeeded = False
                    logger.error(
                        "Durable summary queue update failed for event %s: %s",
                        event_id,
                        e,
                        exc_info=True,
                    )
            return succeeded, attempted_ids
        finally:
            async with self._lock:
                pending_ids = self._pending.get(event_id)
                if pending_ids:
                    if succeeded:
                        pending_ids.difference_update(article_ids)
                    if discarded_ids:
                        pending_ids.difference_update(discarded_ids)

                if self._tasks.get(event_id) is current_task:
                    self._tasks.pop(event_id, None)
                self._wakeups.pop(event_id, None)
                self._deadlines.pop(event_id, None)

                pending_ids = self._pending.get(event_id)
                if not pending_ids:
                    self._pending.pop(event_id, None)
                elif succeeded:
                    # IDs arriving after this request took its snapshot receive a
                    # fresh bounded debounce window. Failed IDs remain pending for
                    # the next explicit flush or enqueue, avoiding a hot retry loop.
                    self._start_event_locked(event_id)

    async def _flush_event_to_cutoff(self, event_id: int, cutoff_ids: Set[int]) -> None:
        remaining = set(cutoff_ids)
        while remaining:
            async with self._lock:
                remaining.intersection_update(self._pending.get(event_id, set()))
                if not remaining:
                    return
                task = self._start_event_locked(event_id, force=True)

            succeeded, attempted_ids = await task

            async with self._lock:
                remaining.intersection_update(self._pending.get(event_id, set()))
            if not remaining:
                return
            if not attempted_ids:
                return
            if not succeeded and remaining.intersection(attempted_ids):
                # Preserve failed work for the next hourly pass rather than retrying
                # indefinitely inside this bounded flush.
                return
            # The task was already in flight and did not include every ID that was
            # pending at flush start. Force one more worker for those cutoff IDs.

    async def _refresh_durable_pending(self) -> None:
        if not self.durable:
            return
        restored = _load_persisted_updates()
        async with self._lock:
            for event_id, article_ids in restored.items():
                self._pending[event_id].update(article_ids)

    async def flush(self) -> None:
        """Flush work that is pending when this call starts.

        Later enqueues are deliberately outside this flush's cutoff. This keeps an
        hourly grouping pass and application shutdown bounded even if manual edits
        continue arriving concurrently.
        """
        async with self._flush_lock:
            await self._refresh_durable_pending()
            async with self._lock:
                cutoff = {
                    event_id: set(article_ids)
                    for event_id, article_ids in self._pending.items()
                    if article_ids
                }
            if cutoff:
                await asyncio.gather(
                    *(
                        self._flush_event_to_cutoff(event_id, article_ids)
                        for event_id, article_ids in cutoff.items()
                    )
                )

    async def summarize_initial(self, event_ids: List[int]) -> List[int]:
        """Generate initial summaries and return event IDs that still need work."""
        failed: List[int] = []
        for event_id in dict.fromkeys(event_ids):
            persisted_ids: Set[int] = set()
            article_ids: List[int] = []
            try:
                if self.durable:
                    article_ids = _event_article_ids(event_id)
                    normalized, _inserted = _persist_summary_updates(
                        {event_id: article_ids}
                    )
                    persisted_ids.update(normalized.get(event_id, []))
                succeeded = bool(
                    await asyncio.wait_for(
                        (generate_summary_update(event_id, article_ids, self.llm)
                         if self.durable else generate_initial_summary(event_id, self.llm)),
                        timeout=self.guard_timeout,
                    )
                )
                if not succeeded:
                    failed.append(event_id)
                    logger.error(
                        "Initial summary returned failure for event %s; "
                        "routing it through the retryable incremental queue",
                        event_id,
                    )
                elif self.durable:
                    _delete_persisted_updates(event_id, persisted_ids)
            except asyncio.TimeoutError:
                failed.append(event_id)
                logger.error(
                    "Initial summary timed out for event %s after %ss; "
                    "routing it through the retryable incremental queue",
                    event_id,
                    self.guard_timeout,
                )
            except Exception as e:
                failed.append(event_id)
                logger.error(
                    "Initial summary failed for event %s; routing it through the "
                    "retryable incremental queue: %s",
                    event_id,
                    e,
                    exc_info=True,
                )
        return failed

    async def shutdown(self) -> None:
        await self.flush()
        async with self._lock:
            remaining = sum(len(article_ids) for article_ids in self._pending.values())
            affected_events = len(self._pending)
        if remaining:
            logger.error(
                "Summary queue shutdown with %s article(s) still pending across %s "
                "event(s); durable retry state will be restored on next startup",
                remaining,
                affected_events,
            )


def build_summary_queue(llm) -> SummaryQueue:
    return SummaryQueue(
        llm=llm,
        debounce_seconds=app_config.SUMMARY_DEBOUNCE_MINUTES * 60,
        guard_timeout=app_config.SUMMARY_REQUEST_TIMEOUT + 30,
        durable=True,
    )
