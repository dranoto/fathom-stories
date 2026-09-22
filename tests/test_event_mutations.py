import unittest

from fastapi import BackgroundTasks
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.database.models import Article, Base, Event, GroupingFeedback
from app.routers import events as event_routes
from app.schemas.event import MergeRequest, SplitRequest


class EventMutationFeedbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(dbapi_connection, _connection_record):
            dbapi_connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    def tearDown(self):
        self.engine.dispose()

    async def test_merge_records_feedback_with_real_article_id(self):
        db = self.session_factory()
        try:
            db.add_all([
                Event(id=1, name="Primary", status="active"),
                Event(id=2, name="Secondary", status="active"),
                Article(id=10, url="https://example.com/10", event_id=1),
                Article(id=20, url="https://example.com/20", event_id=2),
            ])
            db.commit()

            await event_routes.merge_events(
                event_id=1,
                other_id=2,
                body=MergeRequest(other_event_id=2),
                background_tasks=BackgroundTasks(),
                db=db,
            )

            self.assertEqual(db.query(Article).filter(Article.id == 20).one().event_id, 1)
            self.assertIsNone(db.query(Event).filter(Event.id == 2).first())
            feedback = db.query(GroupingFeedback).filter(GroupingFeedback.kind == "merge").one()
            self.assertEqual(feedback.article_id, 20)
        finally:
            db.close()

    async def test_split_records_feedback_with_moved_article_id(self):
        db = self.session_factory()
        try:
            db.add(Event(id=1, name="Parent", status="active"))
            db.add_all([
                Article(id=10, url="https://example.com/10", event_id=1),
                Article(id=20, url="https://example.com/20", event_id=1),
            ])
            db.commit()

            result = await event_routes.split_event(
                event_id=1,
                body=SplitRequest(new_event_name="Child", article_ids=[20]),
                background_tasks=BackgroundTasks(),
                db=db,
            )

            self.assertNotEqual(result.id, 1)
            self.assertEqual(db.query(Article).filter(Article.id == 20).one().event_id, result.id)
            feedback = db.query(GroupingFeedback).filter(GroupingFeedback.kind == "split").one()
            self.assertEqual(feedback.article_id, 20)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
