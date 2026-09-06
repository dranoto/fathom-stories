import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.grouping.summary_queue import SummaryQueue


class SummaryQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_coalesces_articles_for_one_event(self):
        update = AsyncMock(return_value=True)
        queue = SummaryQueue(llm=object(), debounce_seconds=0.02, guard_timeout=30)
        with patch("app.grouping.summary_queue.generate_summary_update", update):
            await queue.enqueue({7: [1, 2]})
            await asyncio.sleep(0.005)
            await queue.enqueue({7: [2, 3]})
            current_task = queue._tasks[7]
            await asyncio.wait_for(current_task, timeout=1)

        update.assert_awaited_once_with(7, [1, 2, 3], queue.llm)

    async def test_uses_independent_tasks_for_different_events(self):
        update = AsyncMock(return_value=True)
        queue = SummaryQueue(llm=object(), debounce_seconds=0, guard_timeout=30)
        with patch("app.grouping.summary_queue.generate_summary_update", update):
            await queue.enqueue({7: [1], 8: [2]})
            await asyncio.gather(*list(queue._tasks.values()))

        calls = {(call.args[0], tuple(call.args[1])) for call in update.await_args_list}
        self.assertEqual(calls, {(7, (1,)), (8, (2,))})

    async def test_shutdown_flushes_pending_updates(self):
        update = AsyncMock(return_value=True)
        queue = SummaryQueue(llm=object(), debounce_seconds=60, guard_timeout=30)
        with patch("app.grouping.summary_queue.generate_summary_update", update):
            await queue.enqueue({9: [4, 5]})
            await queue.shutdown()

        update.assert_awaited_once_with(9, [4, 5], queue.llm)


if __name__ == "__main__":
    unittest.main()
