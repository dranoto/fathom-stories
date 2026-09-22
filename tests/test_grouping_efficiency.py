import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.database.models import (
    Article,
    Base,
    Event,
    GroupingFeedback,
    PendingSummaryUpdate,
)
from app.grouping import dedup, engine


class _Generation:
    text = '{"assignments": []}'


class _Response:
    generations = [[_Generation()]]


class _Llm:
    async def agenerate(self, _messages):
        return _Response()


class GroupingEfficiencyTests(unittest.TestCase):
    def _database_scope(self):
        test_engine = create_engine("sqlite:///:memory:")

        @event.listens_for(test_engine, "connect")
        def enable_foreign_keys(dbapi_connection, _connection_record):
            dbapi_connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(test_engine)
        session_factory = sessionmaker(bind=test_engine, expire_on_commit=False)

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

        return test_engine, scope

    def test_live_fetch_excludes_already_processed_inbox_articles(self):
        query = MagicMock()
        query.filter.return_value = query
        query.order_by.return_value = query
        query.limit.return_value = query
        query.all.return_value = []
        db = MagicMock()
        db.query.return_value = query

        class _Scope:
            def __enter__(self):
                return db

            def __exit__(self, *_args):
                return False

        with patch("app.grouping.engine.db_session_scope", return_value=_Scope()):
            engine.fetch_ungrouped_articles()

        filter_columns = [
            getattr(call.args[0], "left", None)
            for call in query.filter.call_args_list
            if call.args
        ]
        self.assertIn(engine.Article.grouped_at, filter_columns)

    def test_distinct_source_check_normalizes_names_and_ignores_missing_publishers(self):
        first = MagicMock()
        first.publisher_name = " Example News "
        duplicate = MagicMock()
        duplicate.publisher_name = "example news"
        missing = MagicMock()
        missing.publisher_name = None
        other = MagicMock()
        other.publisher_name = "Other News"

        self.assertFalse(engine._articles_have_distinct_sources([first, duplicate, missing]))
        self.assertTrue(engine._articles_have_distinct_sources([first, other]))

    def test_live_assignment_can_defer_new_event_creation(self):
        assignment = {
            "article_id": 1,
            "decision": "new",
            "event_name": "A new story",
            "importance_score": 0.8,
            "confidence": 0.9,
        }
        article = MagicMock()
        article.id = 1
        article.publisher_name = "Source"
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = article

        class _Scope:
            def __enter__(self):
                return db

            def __exit__(self, *_args):
                return False

        with patch("app.grouping.engine.db_session_scope", return_value=_Scope()), patch(
            "app.grouping.engine.find_or_create_event"
        ) as create:
            counts, increments = engine._apply_live([assignment], create_new_events=False)

        create.assert_not_called()
        self.assertEqual(article.proposed_event_name, "A new story")
        self.assertEqual(counts["singleton"], 1)
        self.assertEqual(increments, {})

    def test_live_assignment_commits_summary_outbox_with_assignment(self):
        test_engine, scope = self._database_scope()
        with scope() as db:
            db.add(Event(id=7, name="Existing", status="active"))
            db.add(Article(id=1, url="https://example.com/1", title="Article"))

        assignment = {
            "article_id": 1,
            "decision": "existing",
            "event_id": 7,
            "importance_score": 0.8,
            "confidence": 0.95,
        }
        with patch("app.grouping.engine.db_session_scope", new=scope):
            counts, increments = engine._apply_live([assignment])

        self.assertEqual(counts["existing"], 1)
        self.assertEqual(increments, {7: [1]})
        with scope() as db:
            self.assertEqual(db.query(Article).filter(Article.id == 1).one().event_id, 7)
            pending = db.query(PendingSummaryUpdate).one()
            self.assertEqual((pending.event_id, pending.article_id), (7, 1))
        test_engine.dispose()

    def test_regroup_new_event_commits_summary_outbox_with_articles(self):
        test_engine, scope = self._database_scope()
        with scope() as db:
            db.add_all([
                Article(id=1, url="https://example.com/1", publisher_name="One"),
                Article(id=2, url="https://example.com/2", publisher_name="Two"),
            ])

        assignments = [
            {
                "article_id": 1,
                "decision": "new",
                "event_name": "New cluster",
                "importance_score": 0.8,
                "confidence": 0.95,
            },
            {
                "article_id": 2,
                "decision": "new",
                "event_name": "New cluster",
                "importance_score": 0.7,
                "confidence": 0.94,
            },
        ]
        with patch("app.grouping.engine.db_session_scope", new=scope):
            counts, (new_event_ids, increments) = engine._apply_regroup_inner(assignments)

        self.assertEqual(counts["new_events"], 1)
        self.assertEqual(len(new_event_ids), 1)
        event_id = new_event_ids[0]
        self.assertEqual(set(increments[event_id]), {1, 2})
        with scope() as db:
            pending = {
                (row.event_id, row.article_id)
                for row in db.query(PendingSummaryUpdate).all()
            }
        self.assertEqual(pending, {(event_id, 1), (event_id, 2)})
        test_engine.dispose()

    def test_dedup_merge_moves_outbox_work_to_primary_event(self):
        test_engine, scope = self._database_scope()
        with scope() as db:
            db.add_all([
                Event(id=10, name="Primary", status="active"),
                Event(id=11, name="Secondary", status="active"),
                Article(id=12, url="https://example.com/12", event_id=11),
            ])
        with scope() as db:
            db.add(PendingSummaryUpdate(event_id=11, article_id=12))

        increments = {}
        with scope() as db:
            merged = dedup.merge_events(
                db,
                10,
                11,
                summary_increments=increments,
            )

        self.assertTrue(merged)
        self.assertEqual(increments, {10: [12]})
        with scope() as db:
            self.assertEqual(db.query(Article).filter(Article.id == 12).one().event_id, 10)
            self.assertIsNone(db.query(Event).filter(Event.id == 11).first())
            pending = db.query(PendingSummaryUpdate).one()
            self.assertEqual((pending.event_id, pending.article_id), (10, 12))
            feedback = db.query(GroupingFeedback).one()
            self.assertEqual(feedback.article_id, 12)
        test_engine.dispose()


if __name__ == "__main__":
    unittest.main()
