import unittest
from contextlib import contextmanager
import tempfile
import threading
import time
from unittest.mock import ANY, AsyncMock, patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.database.models import Article, Base, Event, EventSummary
from app.grouping import summary_service


def _summary(label: str) -> dict:
    return {
        "key_developments": [label],
        "timeline_narrative": [{"date": "2026-09-22", "text": label}],
        "cross_source_synthesis": {
            "by_source": [{"source": "Synthetic outlet", "observation": label}],
            "synthesis": label,
        },
        "progressive_summary": label,
    }


class SummaryServiceConsistencyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

        @contextmanager
        def scope():
            db = self.session_factory()
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

        self.scope = scope

    def tearDown(self):
        self.engine.dispose()

    def _seed_prior_summary(self):
        with self.scope() as db:
            db.add_all([
                Event(id=1, name="Source event", status="active"),
                Event(id=2, name="Target event", status="active"),
                Article(id=1, url="https://example.com/1", title="One", event_id=1),
                Article(id=2, url="https://example.com/2", title="Two", event_id=1),
                EventSummary(
                    event_id=1,
                    summary_json={**_summary("Prior"), "article_ids": [1, 2]},
                    article_ids=[1, 2],
                    article_count=2,
                ),
            ])

    async def test_removal_forces_full_summary_regeneration(self):
        self._seed_prior_summary()
        with self.scope() as db:
            db.query(Article).filter(Article.id == 2).one().event_id = 2

        regenerate = AsyncMock(return_value=True)
        with patch.object(summary_service, "db_session_scope", new=self.scope), patch.object(
            summary_service,
            "generate_initial_summary",
            new=regenerate,
        ), patch.object(
            summary_service,
            "generate_incremental_summary",
            new=AsyncMock(),
        ) as incremental:
            result = await summary_service.generate_summary_update(1, [1], object())

        self.assertTrue(result)
        regenerate.assert_awaited_once_with(1, ANY, resummarize=True)
        incremental.assert_not_awaited()

    async def test_moved_article_is_not_sent_to_old_event_incremental_prompt(self):
        self._seed_prior_summary()
        with self.scope() as db:
            article = db.query(Article).filter(Article.id == 2).one()
            article.event_id = 2
            latest = db.query(EventSummary).filter(EventSummary.event_id == 1).one()
            latest.article_ids = [1]
            latest.summary_json = {"headline": "Current", "article_ids": [1]}
            latest.article_count = 1

        incremental = AsyncMock()
        with patch.object(summary_service, "db_session_scope", new=self.scope), patch.object(
            summary_service,
            "generate_incremental_summary",
            new=incremental,
        ):
            result = await summary_service.generate_summary_update(1, [2], object())

        self.assertTrue(result)
        incremental.assert_not_awaited()

    async def test_article_move_during_generation_aborts_stale_save(self):
        self._seed_prior_summary()
        with self.scope() as db:
            latest = db.query(EventSummary).filter(EventSummary.event_id == 1).one()
            latest.article_ids = [1]
            latest.summary_json = {**_summary("Prior"), "article_ids": [1]}
            latest.article_count = 1

        async def move_during_generation(**_kwargs):
            with self.scope() as db:
                db.query(Article).filter(Article.id == 2).one().event_id = 2
            return {"headline": "Would be stale"}

        with patch.object(summary_service, "db_session_scope", new=self.scope), patch.object(
            summary_service,
            "generate_incremental_summary",
            new=AsyncMock(side_effect=move_during_generation),
        ):
            result = await summary_service.generate_summary_update(1, [2], object())

        self.assertFalse(result)
        with self.scope() as db:
            self.assertEqual(db.query(EventSummary).filter(EventSummary.event_id == 1).count(), 1)

    async def test_too_small_initial_budget_keeps_summary_unsaved(self):
        with self.scope() as db:
            db.add(Event(id=3, name="Large event", status="active"))
            db.add_all([
                Article(
                    id=3,
                    url="https://example.com/3",
                    title="Newest",
                    event_id=3,
                    scraped_text_content="x" * 100,
                ),
                Article(
                    id=4,
                    url="https://example.com/4",
                    title="Older",
                    event_id=3,
                    scraped_text_content="y" * 100,
                ),
            ])

        with patch.object(summary_service, "db_session_scope", new=self.scope), patch.object(
            summary_service,
            "generate_major_summary",
            new=AsyncMock(return_value={"headline": "Truncated"}),
        ):
            result = await summary_service.generate_initial_summary(
                3,
                object(),
                max_prompt_tokens=30,
            )

        self.assertFalse(result)
        with self.scope() as db:
            self.assertEqual(db.query(EventSummary).filter(EventSummary.event_id == 3).count(), 0)

    def test_sqlite_summary_save_holds_writer_lock_through_membership_validation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            engine = create_engine(
                f"sqlite:///{temp_dir}/summary-lock.db",
                connect_args={"check_same_thread": False, "timeout": 2},
            )

            @event.listens_for(engine, "connect")
            def configure_sqlite(dbapi_connection, _connection_record):
                dbapi_connection.execute("PRAGMA foreign_keys=ON")
                dbapi_connection.execute("PRAGMA busy_timeout=2000")

            Base.metadata.create_all(engine)
            session_factory = sessionmaker(bind=engine, expire_on_commit=False)

            @contextmanager
            def scope():
                db = session_factory()
                try:
                    yield db
                    db.commit()
                except Exception:
                    db.rollback()
                    raise
                finally:
                    db.close()

            with scope() as db:
                db.add_all([
                    Event(id=1, name="Source", status="active"),
                    Event(id=2, name="Target", status="active"),
                    Article(id=1, url="https://example.com/1", event_id=1),
                    Article(id=2, url="https://example.com/2", event_id=1),
                ])

            summary_insert_started = threading.Event()
            release_summary_insert = threading.Event()

            @event.listens_for(engine, "before_cursor_execute")
            def pause_summary_insert(
                _connection,
                _cursor,
                statement,
                _parameters,
                _context,
                _executemany,
            ):
                if (
                    threading.current_thread().name == "summary-writer"
                    and statement.lstrip().upper().startswith("INSERT INTO EVENT_SUMMARIES")
                ):
                    summary_insert_started.set()
                    release_summary_insert.wait(timeout=2)

            save_result = {}
            move_finished = threading.Event()

            def save_summary():
                with patch.object(summary_service, "db_session_scope", new=scope):
                    save_result["summary"] = summary_service._save_summary(
                        1,
                        {"headline": "Current"},
                        [1, 2],
                        "test-model",
                        expected_article_ids=[1, 2],
                    )

            def move_article():
                with scope() as db:
                    db.query(Article).filter(Article.id == 2).one().event_id = 2
                move_finished.set()

            summary_thread = threading.Thread(target=save_summary, name="summary-writer")
            summary_thread.start()
            self.assertTrue(summary_insert_started.wait(timeout=2))

            move_thread = threading.Thread(target=move_article, name="article-mover")
            move_thread.start()
            time.sleep(0.1)
            self.assertFalse(move_finished.is_set())

            release_summary_insert.set()
            summary_thread.join(timeout=2)
            move_thread.join(timeout=2)

            self.assertFalse(summary_thread.is_alive())
            self.assertFalse(move_thread.is_alive())
            self.assertIsNotNone(save_result.get("summary"))
            self.assertTrue(move_finished.is_set())
            with scope() as db:
                self.assertEqual(db.query(Article).filter(Article.id == 2).one().event_id, 2)
                self.assertEqual(db.query(EventSummary).filter(EventSummary.event_id == 1).count(), 1)
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
