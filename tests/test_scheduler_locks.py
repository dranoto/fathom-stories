import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app import tasks


class SchedulerLockTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tasks.rss_update_lock = asyncio.Lock()

    async def test_live_grouping_waits_for_rss_fetch_instead_of_dropping_run(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def hold_fetch():
            started.set()
            await release.wait()
            return 1

        with patch.object(tasks, "_do_rss_fetch", side_effect=hold_fetch), patch.object(
            tasks, "run_grouping", new=AsyncMock(return_value={"existing": 1})
        ) as run_grouping:
            fetch_task = asyncio.create_task(tasks.scheduled_rss_fetch())
            await started.wait()
            grouping_task = asyncio.create_task(tasks.scheduled_live_grouping())
            await asyncio.sleep(0)
            run_grouping.assert_not_awaited()
            release.set()
            await asyncio.gather(fetch_task, grouping_task)
            run_grouping.assert_awaited_once_with(create_new_events=False)

    async def test_regroup_waits_for_rss_fetch_instead_of_dropping_run(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def hold_fetch():
            started.set()
            await release.wait()
            return 1

        with patch.object(tasks, "_do_rss_fetch", side_effect=hold_fetch), patch.object(
            tasks, "run_regroup", new=AsyncMock(return_value={"new_events": 1})
        ) as run_regroup:
            fetch_task = asyncio.create_task(tasks.scheduled_rss_fetch())
            await started.wait()
            regroup_task = asyncio.create_task(tasks.scheduled_regroup_uncategorized())
            await asyncio.sleep(0)
            run_regroup.assert_not_awaited()
            release.set()
            await asyncio.gather(fetch_task, regroup_task)
            run_regroup.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()
