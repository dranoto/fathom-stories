import unittest
from unittest.mock import MagicMock, patch

from app.grouping import engine


class _Generation:
    text = '{"assignments": []}'


class _Response:
    generations = [[_Generation()]]


class _Llm:
    async def agenerate(self, _messages):
        return _Response()


class GroupingEfficiencyTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
