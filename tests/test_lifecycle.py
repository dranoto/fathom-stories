import os
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.models import Article, Base, Event, ReclusterProposal
from app.grouping import lifecycle


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)

        @contextmanager
        def scope():
            db = self.Session()
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

    def test_reap_expired_archives_only_due_active_events(self):
        now = datetime.now(timezone.utc)
        with self.scope() as db:
            due = Event(name="due", status="active", expires_at=now - timedelta(minutes=1))
            future = Event(name="future", status="active", expires_at=now + timedelta(hours=1))
            archived = Event(
                name="archived",
                status="archived",
                expires_at=now - timedelta(minutes=1),
                archived_at=now - timedelta(days=1),
            )
            db.add_all([due, future, archived])
            db.flush()
            due_id, future_id, archived_id = due.id, future.id, archived.id

        with self.scope() as db:
            count = lifecycle.reap_expired_in_list(db, [due_id, future_id, archived_id])

        self.assertEqual(count, 1)
        with self.scope() as db:
            self.assertEqual(db.get(Event, due_id).status, "archived")
            self.assertIsNotNone(db.get(Event, due_id).archived_at)
            self.assertEqual(db.get(Event, future_id).status, "active")
            self.assertEqual(db.get(Event, archived_id).status, "archived")

    def test_tick_reaps_due_event_and_deletes_only_old_empty_event(self):
        now = datetime.now(timezone.utc)
        with self.scope() as db:
            due = Event(name="due", status="active", expires_at=now - timedelta(minutes=1))
            old_empty = Event(name="old empty", status="active", created_at=now - timedelta(hours=1))
            young_empty = Event(name="young empty", status="active", created_at=now)
            populated = Event(name="populated", status="active", created_at=now - timedelta(hours=1))
            db.add_all([due, old_empty, young_empty, populated])
            db.flush()
            db.add(Article(url="https://example.test/article", title="article", event_id=populated.id))
            ids = due.id, old_empty.id, young_empty.id, populated.id

        with patch.object(lifecycle, "db_session_scope", self.scope), patch.object(
            lifecycle, "purge_ancient_archives", return_value=0
        ):
            result = lifecycle.tick()

        self.assertEqual(result, {"reaped": 1, "purged": 0, "purged_empty": 1})
        due_id, old_empty_id, young_empty_id, populated_id = ids
        with self.scope() as db:
            self.assertEqual(db.get(Event, due_id).status, "archived")
            self.assertIsNone(db.get(Event, old_empty_id))
            self.assertIsNotNone(db.get(Event, young_empty_id))
            self.assertIsNotNone(db.get(Event, populated_id))

    def test_purge_ancient_archives_respects_limit_and_cleans_old_proposals(self):
        now = datetime.now(timezone.utc)
        ancient = now - timedelta(days=lifecycle.app_config.PURGE_ARCHIVE_AFTER_DAYS + 1)
        recent = now - timedelta(days=1)
        with self.scope() as db:
            db.add_all([
                Event(name="ancient one", status="archived", archived_at=ancient),
                Event(name="ancient two", status="archived", archived_at=ancient),
                Event(name="recent", status="archived", archived_at=recent),
                ReclusterProposal(kind="merge", payload={}, created_at=ancient),
                ReclusterProposal(kind="merge", payload={}, created_at=recent),
            ])

        with patch.object(lifecycle, "db_session_scope", self.scope):
            purged = lifecycle.purge_ancient_archives(limit=1)

        self.assertEqual(purged, 1)
        with self.scope() as db:
            self.assertEqual(db.query(Event).filter(Event.status == "archived").count(), 2)
            self.assertEqual(db.query(ReclusterProposal).count(), 1)

    def test_reset_expiry_is_anchored_to_now(self):
        before = datetime.now(timezone.utc) + timedelta(hours=lifecycle.app_config.EVENT_TTL_RESET_HOURS)
        expires_at = lifecycle.reset_expiry()
        after = datetime.now(timezone.utc) + timedelta(hours=lifecycle.app_config.EVENT_TTL_RESET_HOURS)
        self.assertGreaterEqual(expires_at, before)
        self.assertLessEqual(expires_at, after)


if __name__ == "__main__":
    unittest.main()
