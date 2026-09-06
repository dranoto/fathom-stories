import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, inspect

from app.database.models import Base


class DatabaseSchemaTests(unittest.TestCase):
    def test_expected_query_indexes_are_created(self):
        engine = create_engine("sqlite:///:memory:")
        try:
            Base.metadata.create_all(engine)
            inspector = inspect(engine)
            article_indexes = {index["name"] for index in inspector.get_indexes("articles")}
            read_indexes = {index["name"] for index in inspector.get_indexes("article_reads")}
        finally:
            engine.dispose()

        self.assertIn("ix_articles_event_published", article_indexes)
        self.assertIn("ix_article_reads_visitor_article", read_indexes)

    def test_create_db_and_tables_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "stories.db"
            url = f"sqlite:///{db_path}"
            engine = create_engine(url)
            try:
                with patch("app.database.engine", engine), patch(
                    "app.database.app_config.DATABASE_URL", url
                ):
                    from app.database import create_db_and_tables
                    create_db_and_tables()
                    create_db_and_tables()
                inspector = inspect(engine)
                self.assertIn("articles", inspector.get_table_names())
                article_indexes = {index["name"] for index in inspector.get_indexes("articles")}
                self.assertIn("ix_articles_event_published", article_indexes)
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
