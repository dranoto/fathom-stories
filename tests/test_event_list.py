import unittest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.models import Article, Base, Event, EventVisit
from app.dependencies import get_visitor_id
from app.main_api import app
from app import database


class EventListTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)

        def get_test_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[database.get_db] = get_test_db
        app.dependency_overrides[get_visitor_id] = lambda: "test-visitor"
        self.client = TestClient(app)
        self.query_count = 0

        def count_queries(*_args):
            self.query_count += 1

        self._count_queries = count_queries
        event.listen(self.engine, "before_cursor_execute", self._count_queries)

    def tearDown(self):
        event.remove(self.engine, "before_cursor_execute", self._count_queries)
        self.client.close()
        app.dependency_overrides.clear()
        self.engine.dispose()

    def test_event_list_counts_new_articles_in_one_grouped_query_and_labels_publishers(self):
        now = datetime.now(timezone.utc)
        with self.Session.begin() as db:
            visited = Event(
                name="Visited",
                status="active",
                created_at=now - timedelta(days=1),
                last_article_at=now,
                expires_at=now + timedelta(days=1),
            )
            unvisited = Event(
                name="Unvisited",
                status="active",
                created_at=now - timedelta(days=1),
                last_article_at=now,
                expires_at=now + timedelta(days=1),
            )
            empty = Event(
                name="Empty",
                status="active",
                created_at=now - timedelta(days=1),
                last_article_at=now,
                expires_at=now + timedelta(days=1),
            )
            db.add_all([visited, unvisited, empty])
            db.flush()
            cutoff = now - timedelta(hours=2)
            db.add(EventVisit(visitor_id="test-visitor", event_id=visited.id, last_visited_at=cutoff))
            db.add_all([
                Article(
                    url="https://example.test/old",
                    title="old",
                    publisher_name="Source A",
                    event_id=visited.id,
                    fetched_at=now - timedelta(hours=3),
                    published_date=now - timedelta(hours=3),
                ),
                Article(
                    url="https://example.test/new-a",
                    title="new a",
                    publisher_name="Source A",
                    event_id=visited.id,
                    fetched_at=now - timedelta(hours=1),
                    published_date=now - timedelta(hours=1),
                ),
                Article(
                    url="https://example.test/new-b",
                    title="new b",
                    publisher_name="Source B",
                    event_id=visited.id,
                    fetched_at=now,
                    published_date=now,
                ),
                Article(
                    url="https://example.test/unvisited",
                    title="unvisited",
                    publisher_name="Source C",
                    event_id=unvisited.id,
                    fetched_at=now,
                    published_date=now,
                ),
            ])

        queries_before = self.query_count
        response = self.client.get("/api/events?status=active&min_articles=1&sort=score")
        list_queries = self.query_count - queries_before
        self.assertEqual(response.status_code, 200)
        payload = {event["name"]: event for event in response.json()}
        self.assertEqual(payload["Visited"]["new_since_visit"], 2)
        self.assertEqual(payload["Visited"]["publisher_label"], "Source A · Source B")
        self.assertEqual(payload["Unvisited"]["new_since_visit"], 1)
        self.assertEqual(payload["Unvisited"]["publisher_label"], "Source C")
        self.assertNotIn("Empty", payload)
        self.assertLessEqual(list_queries, 12)

    def test_event_list_returns_empty_after_minimum_article_filter(self):
        now = datetime.now(timezone.utc)
        with self.Session.begin() as db:
            db.add(Event(
                name="Empty",
                status="active",
                created_at=now,
                last_article_at=now,
                expires_at=now + timedelta(days=1),
            ))

        response = self.client.get("/api/events?status=active&min_articles=1&sort=score")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])


if __name__ == "__main__":
    unittest.main()
