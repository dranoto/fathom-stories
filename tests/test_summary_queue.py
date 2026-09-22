import asyncio
import unittest
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import Article, Base, Event, EventSummary, PendingSummaryUpdate
from app.grouping.summary_queue import SummaryQueue


class SummaryQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_coalesces_articles_for_one_event(self):
        update = AsyncMock(return_value=True)
        queue = SummaryQueue(llm=object(), debounce_seconds=0.1, guard_timeout=30)
        with patch("app.grouping.summary_queue.generate_summary_update", update):
            await queue.enqueue({7: [1, 2]})
            await queue.enqueue({7: [2, 3]})
            current_task = queue._tasks[7]
            await asyncio.wait_for(current_task, timeout=1)

        update.assert_awaited_once_with(7, [1, 2, 3], queue.llm)

    async def test_repeated_enqueue_does_not_postpone_first_deadline(self):
        started = asyncio.Event()

        async def record_update(event_id, article_ids, llm):
            started.set()
            return True

        update = AsyncMock(side_effect=record_update)
        queue = SummaryQueue(llm=object(), debounce_seconds=0.2, guard_timeout=30)
        with patch("app.grouping.summary_queue.generate_summary_update", update):
            await queue.enqueue({7: [1]})
            first_task = queue._tasks[7]
            await asyncio.sleep(0.15)
            await queue.enqueue({7: [2]})
            self.assertIs(queue._tasks[7], first_task)
            await asyncio.wait_for(started.wait(), timeout=0.1)
            await first_task

        update.assert_awaited_once_with(7, [1, 2], queue.llm)

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

    async def test_concurrent_flush_calls_share_one_drain(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def block_update(event_id, article_ids, llm):
            started.set()
            await release.wait()
            return True

        update = AsyncMock(side_effect=block_update)
        queue = SummaryQueue(llm=object(), debounce_seconds=60, guard_timeout=30)
        with patch("app.grouping.summary_queue.generate_summary_update", update):
            await queue.enqueue({9: [4, 5]})
            first_flush = asyncio.create_task(queue.flush())
            await started.wait()
            second_flush = asyncio.create_task(queue.flush())
            await asyncio.sleep(0)
            self.assertEqual(update.await_count, 1)
            release.set()
            await asyncio.gather(first_flush, second_flush)

        update.assert_awaited_once_with(9, [4, 5], queue.llm)

    async def test_failed_update_remains_pending_for_next_flush(self):
        update = AsyncMock(side_effect=[False, True])
        queue = SummaryQueue(llm=object(), debounce_seconds=60, guard_timeout=30)
        with patch("app.grouping.summary_queue.generate_summary_update", update):
            await queue.enqueue({11: [6, 7]})
            await queue.flush()
            self.assertEqual(queue._pending[11], {6, 7})
            self.assertNotIn(11, queue._tasks)

            await queue.flush()

        self.assertNotIn(11, queue._pending)
        self.assertEqual(update.await_count, 2)
        self.assertEqual(update.await_args_list[0].args[1], [6, 7])
        self.assertEqual(update.await_args_list[1].args[1], [6, 7])

    async def test_timeout_remains_pending_for_next_flush(self):
        attempts = 0

        async def timeout_then_succeed(event_id, article_ids, llm):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                await asyncio.sleep(0.1)
            return True

        queue = SummaryQueue(llm=object(), debounce_seconds=60, guard_timeout=0.01)
        with patch(
            "app.grouping.summary_queue.generate_summary_update",
            new=AsyncMock(side_effect=timeout_then_succeed),
        ):
            await queue.enqueue({12: [8]})
            await queue.flush()
            self.assertEqual(queue._pending[12], {8})

            queue.guard_timeout = 1
            await queue.flush()

        self.assertNotIn(12, queue._pending)
        self.assertEqual(attempts, 2)

    async def test_flush_cutoff_does_not_wait_for_later_enqueue(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def block_first(event_id, article_ids, llm):
            started.set()
            await release.wait()
            return True

        update = AsyncMock(side_effect=block_first)
        queue = SummaryQueue(llm=object(), debounce_seconds=60, guard_timeout=30)
        with patch("app.grouping.summary_queue.generate_summary_update", update):
            await queue.enqueue({13: [9]})
            flush_task = asyncio.create_task(queue.flush())
            await started.wait()
            await queue.enqueue({13: [10]})
            release.set()
            await asyncio.wait_for(flush_task, timeout=1)

            self.assertEqual(queue._pending[13], {10})
            self.assertIn(13, queue._tasks)
            queue._tasks[13].cancel()
            await asyncio.gather(queue._tasks[13], return_exceptions=True)

        update.assert_awaited_once_with(13, [9], queue.llm)

    async def test_initial_summary_reports_false_timeout_and_exception(self):
        initial = AsyncMock(side_effect=[True, False, asyncio.TimeoutError(), RuntimeError("boom")])
        queue = SummaryQueue(llm=object(), debounce_seconds=0, guard_timeout=30)
        with patch("app.grouping.summary_queue.generate_initial_summary", initial):
            failed = await queue.summarize_initial([20, 21, 22, 23])

        self.assertEqual(failed, [21, 22, 23])

    async def test_failed_work_is_restored_after_restart_and_cleared_on_success(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine, expire_on_commit=False)

        @contextmanager
        def test_scope():
            db = session_factory()
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

        with test_scope() as db:
            db.add(Event(id=30, name="Durable event", status="active"))
            db.add(
                Article(
                    id=31,
                    url="https://example.com/durable",
                    title="Durable article",
                    event_id=30,
                )
            )

        with patch("app.grouping.summary_queue.db_session_scope", new=test_scope), patch(
            "app.grouping.summary_queue.generate_summary_update",
            new=AsyncMock(return_value=False),
        ):
            first_queue = SummaryQueue(
                llm=object(),
                debounce_seconds=60,
                guard_timeout=30,
                durable=True,
            )
            await first_queue.enqueue({30: [31]})
            await first_queue.flush()

        with test_scope() as db:
            self.assertEqual(db.query(PendingSummaryUpdate).count(), 1)

        with patch("app.grouping.summary_queue.db_session_scope", new=test_scope), patch(
            "app.grouping.summary_queue.generate_summary_update",
            new=AsyncMock(return_value=True),
        ):
            restored_queue = SummaryQueue(
                llm=object(),
                debounce_seconds=60,
                guard_timeout=30,
                durable=True,
            )
            self.assertEqual(restored_queue._pending[30], {31})
            await restored_queue.flush()

        with test_scope() as db:
            self.assertEqual(db.query(PendingSummaryUpdate).count(), 0)
        engine.dispose()

    async def test_flush_refreshes_outbox_rows_committed_after_queue_startup(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine, expire_on_commit=False)

        @contextmanager
        def test_scope():
            db = session_factory()
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

        with test_scope() as db:
            db.add(Event(id=32, name="Atomic outbox event", status="active"))
            db.add(Article(id=33, url="https://example.com/atomic", event_id=32))

        with patch("app.grouping.summary_queue.db_session_scope", new=test_scope):
            queue = SummaryQueue(
                llm=object(),
                debounce_seconds=60,
                guard_timeout=30,
                durable=True,
            )

        self.assertNotIn(32, queue._pending)
        with test_scope() as db:
            db.add(PendingSummaryUpdate(event_id=32, article_id=33))

        update = AsyncMock(return_value=True)
        with patch("app.grouping.summary_queue.db_session_scope", new=test_scope), patch(
            "app.grouping.summary_queue.generate_summary_update",
            new=update,
        ):
            await queue.flush()

        update.assert_awaited_once_with(32, [33], queue.llm)
        with test_scope() as db:
            self.assertEqual(db.query(PendingSummaryUpdate).count(), 0)
        engine.dispose()

    async def test_restart_retains_regeneration_work_for_already_summarized_articles(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine, expire_on_commit=False)

        @contextmanager
        def test_scope():
            db = session_factory()
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

        with test_scope() as db:
            db.add(Event(id=34, name="Removal retry event", status="active"))
            db.add(Article(id=35, url="https://example.com/removal-retry", event_id=34))
            db.add(
                EventSummary(
                    event_id=34,
                    summary_json={"headline": "Prior", "article_ids": [35, 36]},
                    article_ids=[35, 36],
                    article_count=2,
                )
            )
            db.add(PendingSummaryUpdate(event_id=34, article_id=35))

        with patch("app.grouping.summary_queue.db_session_scope", new=test_scope):
            restored_queue = SummaryQueue(
                llm=object(),
                debounce_seconds=60,
                guard_timeout=30,
                durable=True,
            )

        self.assertEqual(restored_queue._pending[34], {35})
        with test_scope() as db:
            self.assertEqual(db.query(PendingSummaryUpdate).count(), 1)
        engine.dispose()

    async def test_failed_initial_summary_is_durable_before_retry_routing(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine, expire_on_commit=False)

        @contextmanager
        def test_scope():
            db = session_factory()
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

        with test_scope() as db:
            db.add(Event(id=35, name="Initial durable event", status="active"))
            db.add(
                Article(
                    id=36,
                    url="https://example.com/initial-durable",
                    title="Initial durable article",
                    event_id=35,
                )
            )

        with patch("app.grouping.summary_queue.db_session_scope", new=test_scope), patch(
            "app.grouping.summary_queue.generate_initial_summary",
            new=AsyncMock(return_value=False),
        ):
            queue = SummaryQueue(
                llm=object(),
                debounce_seconds=60,
                guard_timeout=30,
                durable=True,
            )
            failed = await queue.summarize_initial([35])

        self.assertEqual(failed, [35])
        with test_scope() as db:
            self.assertEqual(db.query(PendingSummaryUpdate).count(), 1)

        with patch("app.grouping.summary_queue.db_session_scope", new=test_scope):
            restored_queue = SummaryQueue(
                llm=object(),
                debounce_seconds=60,
                guard_timeout=30,
                durable=True,
            )
        self.assertEqual(restored_queue._pending[35], {36})
        engine.dispose()

    async def test_moved_article_is_pruned_from_durable_retry_state(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine, expire_on_commit=False)

        @contextmanager
        def test_scope():
            db = session_factory()
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

        with test_scope() as db:
            db.add(Event(id=40, name="Old event", status="active"))
            db.add(Event(id=41, name="New event", status="active"))
            db.add(
                Article(
                    id=42,
                    url="https://example.com/moved",
                    title="Moved article",
                    event_id=40,
                )
            )

        with patch("app.grouping.summary_queue.db_session_scope", new=test_scope), patch(
            "app.grouping.summary_queue.generate_summary_update",
            new=AsyncMock(return_value=False),
        ):
            queue = SummaryQueue(
                llm=object(),
                debounce_seconds=60,
                guard_timeout=30,
                durable=True,
            )
            await queue.enqueue({40: [42]})
            with test_scope() as db:
                article = db.query(Article).filter(Article.id == 42).one()
                article.event_id = 41
            await queue.flush()

        self.assertNotIn(40, queue._pending)
        with test_scope() as db:
            self.assertEqual(db.query(PendingSummaryUpdate).count(), 0)
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
