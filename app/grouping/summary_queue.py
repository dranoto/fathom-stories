import asyncio
import logging
from collections import defaultdict
from typing import Dict, List, Set

from .. import config as app_config
from .summary_service import generate_initial_summary, generate_summary_update

logger = logging.getLogger(__name__)


class SummaryQueue:
    def __init__(self, llm, debounce_seconds: float, guard_timeout: float):
        self.llm = llm
        self.debounce_seconds = max(0.0, debounce_seconds)
        self.guard_timeout = guard_timeout
        self._pending: Dict[int, Set[int]] = defaultdict(set)
        self._tasks: Dict[int, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    async def enqueue(self, event_increments: Dict[int, List[int]]) -> int:
        queued = 0
        async with self._lock:
            for event_id, article_ids in event_increments.items():
                valid_ids = {int(article_id) for article_id in article_ids if article_id is not None}
                if not valid_ids:
                    continue
                existing_task = self._tasks.get(event_id)
                if existing_task is not None and not existing_task.done():
                    existing_task.cancel()
                self._pending[event_id].update(valid_ids)
                queued += len(valid_ids)
                self._tasks[event_id] = asyncio.create_task(self._run_event(event_id))
        return queued

    async def _run_event(self, event_id: int) -> None:
        current_task = asyncio.current_task()
        try:
            await asyncio.sleep(self.debounce_seconds)
            while True:
                async with self._lock:
                    article_ids = sorted(self._pending.pop(event_id, set()))
                if not article_ids:
                    return
                try:
                    await asyncio.wait_for(
                        generate_summary_update(event_id, article_ids, self.llm),
                        timeout=self.guard_timeout,
                    )
                except asyncio.TimeoutError:
                    logger.error(
                        f"Queued summary update timed out for event {event_id} "
                        f"after {self.guard_timeout}s"
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(
                        f"Queued summary update failed for event {event_id}: {e}",
                        exc_info=True,
                    )
                async with self._lock:
                    if not self._pending.get(event_id):
                        return
        finally:
            async with self._lock:
                if self._tasks.get(event_id) is current_task:
                    self._tasks.pop(event_id, None)

    async def summarize_initial(self, event_ids: List[int]) -> None:
        for event_id in dict.fromkeys(event_ids):
            try:
                await asyncio.wait_for(
                    generate_initial_summary(event_id, self.llm),
                    timeout=self.guard_timeout,
                )
            except asyncio.TimeoutError:
                logger.error(
                    f"Initial summary timed out for event {event_id} after {self.guard_timeout}s"
                )
            except Exception as e:
                logger.error(
                    f"Initial summary failed for event {event_id}: {e}",
                    exc_info=True,
                )

    async def shutdown(self) -> None:
        async with self._lock:
            pending = dict(self._pending)
            self._pending.clear()
            tasks = [task for task in self._tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for event_id, article_ids in pending.items():
            try:
                await asyncio.wait_for(
                    generate_summary_update(event_id, sorted(article_ids), self.llm),
                    timeout=self.guard_timeout,
                )
            except Exception as e:
                logger.error(
                    f"Final queued summary update failed for event {event_id}: {e}",
                    exc_info=True,
                )


def build_summary_queue(llm) -> SummaryQueue:
    return SummaryQueue(
        llm=llm,
        debounce_seconds=app_config.SUMMARY_DEBOUNCE_MINUTES * 60,
        guard_timeout=app_config.SUMMARY_REQUEST_TIMEOUT + 30,
    )
