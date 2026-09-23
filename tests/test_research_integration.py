import asyncio
import os
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.orm import sessionmaker

from app.database.models import Article, Base, Event
from app.grouping.jev_classifier import JevClassifier
from app.grouping import engine as grouping_engine, summarizer as grouping_summarizer
from app.grouping import dedup as grouping_dedup
from app.routers import events as event_routes
from app.research import telemetry


class ResearchIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_telemetry_failure_cannot_change_jev_classification(self):
        from tests.test_jev_classifier import ARTICLE, EVENTS, classifier

        with patch.object(telemetry, "record_provider_call", side_effect=RuntimeError("analytics down")), patch.object(
            telemetry, "record_decision", side_effect=RuntimeError("analytics down")
        ):
            result = await classifier().classify(ARTICLE, EVENTS)
        self.assertEqual(result["decision"], "existing")
        self.assertEqual(result["event_id"], 7)

    async def test_full_model_dedup_call_has_its_own_lane(self):
        with tempfile.TemporaryDirectory() as temp:
            research_path = Path(temp) / "research.sqlite3"

            class Llm:
                model_name = "group-model"

                async def agenerate(self, _messages):
                    return type("Response", (), {"generations": [[type("Generation", (), {"text": '{"merge_pairs": []}'})()]]})()

            events = [Event(id=1, name="Mars mission", status="active"), Event(id=2, name="Venus mission", status="active")]
            with patch.dict(os.environ, {"FATHOM_RESEARCH_DB_PATH": str(research_path)}), patch.object(
                grouping_dedup, "fetch_active_and_cooling_events", return_value=events
            ), patch.object(grouping_dedup.app_config, "JEV_DEDUP_ENABLED", False):
                result = await grouping_dedup.dedup_events(Llm())
                self.assertTrue(telemetry.flush())
            self.assertEqual(result["merged"], 0)
            with sqlite3.connect(research_path) as db:
                lane = db.execute("SELECT lane, success FROM provider_calls").fetchone()
            self.assertEqual(lane, ("regroup_dedup", 1))

    async def test_manual_add_records_committed_feedback_without_note_or_article_text(self):
        from fastapi import BackgroundTasks

        with tempfile.TemporaryDirectory() as temp:
            research_path = Path(temp) / "research.sqlite3"
            main_engine = create_engine(f"sqlite:///{Path(temp) / 'main.sqlite3'}")
            Base.metadata.create_all(main_engine)
            factory = sessionmaker(bind=main_engine, expire_on_commit=False)
            with factory() as db:
                db.add_all([
                    Event(id=7, name="Mars update", status="active"),
                    Article(id=42, title="Sensitive example headline", url="https://example.org/42"),
                ])
                db.commit()
                with patch.dict(os.environ, {"FATHOM_RESEARCH_DB_PATH": str(research_path)}):
                    result = await event_routes.add_article_to_event(
                        event_id=7, article_id=42, request=None,
                        background_tasks=BackgroundTasks(), db=db,
                    )
                    self.assertTrue(telemetry.flush())
            self.assertFalse(result["already_in"])
            with sqlite3.connect(research_path) as con:
                rows = con.execute("SELECT correction_id, article_id, original_event_id, corrected_event_id, kind FROM manual_corrections").fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][1:], ("42", None, "7", "move"))
            self.assertNotIn("Sensitive example headline", repr(rows))
            main_engine.dispose()

    async def test_regroup_records_final_committed_assignment_for_jev_none(self):
        with tempfile.TemporaryDirectory() as temp:
            research_path = Path(temp) / "research.sqlite3"
            main_engine = create_engine(f"sqlite:///{Path(temp) / 'main.sqlite3'}")

            @sa_event.listens_for(main_engine, "connect")
            def foreign_keys(connection, _record):
                connection.execute("PRAGMA foreign_keys=ON")

            Base.metadata.create_all(main_engine)
            factory = sessionmaker(bind=main_engine, expire_on_commit=False)

            @contextmanager
            def scope():
                db = factory()
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
                    Event(id=7, name="Mars sample return", status="active"),
                    Article(id=42, title="Mars mission update", url="https://example.org/42"),
                ])
            with scope() as db:
                input_article = db.query(Article).filter(Article.id == 42).one()
                event = db.query(Event).filter(Event.id == 7).one()
                _ = event.articles

            class GroupLlm:
                async def agenerate(self, _messages):
                    return type("Response", (), {"generations": [[type("Generation", (), {"text":
                        '{"assignments": [{"article_id": 42, "decision": "existing", "event_id": 7, "importance_score": 0.5, "confidence": 0.96}]}'
                    })()]]})()

            with patch.dict(os.environ, {"FATHOM_RESEARCH_DB_PATH": str(research_path)}):
                telemetry.record_decision("decision-42", 42, "none", 0.9, [7])
                with patch.object(grouping_engine, "db_session_scope", new=scope), patch.object(
                    grouping_engine, "fetch_ungrouped_articles", return_value=[input_article]
                ), patch.object(grouping_engine, "fetch_active_events", return_value=([event], [])), patch.object(
                    grouping_engine, "build_few_shot_examples", return_value=[]
                ), patch("app.grouping.dedup.dedup_events", new=AsyncMock(return_value={})):
                    result = await grouping_engine.regroup_uncategorized(
                        GroupLlm(), on_event_increments=AsyncMock(return_value=1)
                    )
                    with patch.object(telemetry, "record_regroup_outcome", side_effect=RuntimeError("analytics down")):
                        without_telemetry = await grouping_engine.regroup_uncategorized(
                            GroupLlm(), on_event_increments=AsyncMock(return_value=1)
                        )
                self.assertTrue(telemetry.flush())
            self.assertEqual(result["existing"], 1)
            self.assertEqual(without_telemetry["existing"], 1)
            self.assertEqual(telemetry.build_report(research_path)["reunion"]["numerator"], 1)
            main_engine.dispose()

    async def test_unapplied_regroup_assignment_is_not_reported_as_observed(self):
        with tempfile.TemporaryDirectory() as temp:
            research_path = Path(temp) / "research.sqlite3"
            main_engine = create_engine(f"sqlite:///{Path(temp) / 'main.sqlite3'}")
            Base.metadata.create_all(main_engine)
            factory = sessionmaker(bind=main_engine, expire_on_commit=False)

            @contextmanager
            def scope():
                with factory() as db:
                    try:
                        yield db
                        db.commit()
                    except Exception:
                        db.rollback()
                        raise

            with scope() as db:
                db.add(Article(id=42, title="Unassigned news", url="https://example.org/42"))
            with scope() as db:
                article = db.query(Article).filter(Article.id == 42).one()

            class GroupLlm:
                async def agenerate(self, _messages):
                    return type("Response", (), {"generations": [[type("Generation", (), {"text":
                        '{"assignments": [{"article_id": 42, "decision": "new", "event_name": ""}]}'
                    })()]]})()

            with patch.dict(os.environ, {"FATHOM_RESEARCH_DB_PATH": str(research_path)}):
                telemetry.record_decision("decision-42-invalid-regroup", 42, "none", 0.9, [7])
                with patch.object(grouping_engine, "db_session_scope", new=scope), patch.object(
                    grouping_engine, "fetch_ungrouped_articles", return_value=[article]
                ), patch.object(grouping_engine, "fetch_active_events", return_value=([], [])), patch.object(
                    grouping_engine, "build_few_shot_examples", return_value=[]
                ), patch("app.grouping.dedup.dedup_events", new=AsyncMock(return_value={})):
                    await grouping_engine.regroup_uncategorized(GroupLlm())
                self.assertTrue(telemetry.flush())
            self.assertEqual(telemetry.build_report(research_path)["reunion"]["denominator"], 0)
            main_engine.dispose()

    async def test_grouping_retries_and_summary_stream_have_separate_provider_lanes(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "research.sqlite3"

            class GroupLlm:
                model_name = "group-model"
                attempts = 0

                async def agenerate(self, _messages):
                    self.attempts += 1
                    if self.attempts == 1:
                        raise TimeoutError("test outage")
                    return type("Response", (), {
                        "generations": [[type("Generation", (), {"text": '{"assignments": []}'})()]],
                        "llm_output": {"token_usage": {"prompt_tokens": 10, "completion_tokens": 4}},
                    })()

            class SummaryLlm:
                model_name = "summary-model"

                async def astream(self, _messages):
                    yield type("Chunk", (), {"content": "Short text", "response_metadata": {"finish_reason": "stop"}})()

            from langchain_core.messages import HumanMessage
            with patch.dict(os.environ, {"FATHOM_RESEARCH_DB_PATH": str(path)}):
                result = await grouping_engine._agenerate_with_retry(GroupLlm(), [[HumanMessage(content="group prompt")]], lane="regroup")
                summary = await grouping_summarizer._stream_full_text(SummaryLlm(), "summary prompt")
                self.assertTrue(telemetry.flush())
            self.assertIn("assignments", result.generations[0][0].text)
            self.assertEqual(summary, "Short text")
            with sqlite3.connect(path) as db:
                rows = db.execute("SELECT lane, success, prompt_tokens, completion_tokens FROM provider_calls ORDER BY called_at, rowid").fetchall()
            self.assertEqual([row[:2] for row in rows], [("regroup", 0), ("regroup", 1), ("summary", 1)])
            self.assertEqual(rows[1][2:4], (10, 4))

    async def test_jev_records_actual_shown_candidates_choice_and_provider_call(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "research.sqlite3"
            articles = {"id": 42, "title": "Mars mission update", "source": "Example", "snippet": "Mars sample return"}
            events = [
                {"id": 7, "name": "Mars sample return", "description": "", "recent_titles": []},
                {"id": 9, "name": "Weather forecast", "description": "", "recent_titles": []},
            ]

            def transport(_endpoint, _key, payload, _timeout):
                options = list(payload["questions"]["destination"]["criteria"])
                destination = {option: (0.96 if option == "o001" else 0.04 / (len(options) - 1)) for option in options}
                importance = {label: (0.96 if label == "high" else 0.01) for label in ("critical", "high", "medium", "low", "trivial")}
                return {"answers": {
                    "destination": {"type": "choice", "choice": "o001", "confidence": 0.96, "probabilities": destination},
                    "importance": {"type": "choice", "choice": "high", "confidence": 0.96, "probabilities": importance},
                }}, 12

            classifier = JevClassifier(
                api_key="test", endpoint="https://example.org", model="jev-1.13",
                timeout_seconds=1, min_confidence=0.9, max_concurrency=1,
                max_event_candidates=1, max_request_bytes=4096,
                failure_threshold=2, circuit_cooldown_seconds=1, transport=transport,
            )
            with patch.dict(os.environ, {"FATHOM_RESEARCH_DB_PATH": str(path)}):
                result = await classifier.classify(articles, events)
                self.assertTrue(telemetry.flush())
            self.assertEqual(result["decision"], "existing")
            with sqlite3.connect(path) as db:
                row = db.execute("SELECT article_id, choice, chosen_event_id, confidence FROM decisions").fetchone()
                candidates = db.execute("SELECT event_id FROM decision_candidates ORDER BY position").fetchall()
                provider = db.execute("SELECT lane, success, request_bytes FROM provider_calls").fetchone()
            self.assertEqual(row, ("42", "event", "7", 0.96))
            self.assertEqual(candidates, [("7",)])
            self.assertEqual(provider[0:2], ("jev", 1))
            self.assertGreater(provider[2], 0)

    async def test_none_decision_reunites_with_shown_event_after_regroup(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "research.sqlite3"
            events = [{"id": 7, "name": "Mars mission", "description": "", "recent_titles": []}]
            article = {"id": 12, "title": "New mission", "source": "Example", "snippet": "mission"}

            def transport(_endpoint, _key, payload, _timeout):
                importance = {name: (0.96 if name == "medium" else 0.01) for name in ("critical", "high", "medium", "low", "trivial")}
                return {"answers": {
                    "destination": {"type": "choice", "choice": "o000", "confidence": 0.95, "probabilities": {"o000": 0.96, "o001": 0.04}},
                    "importance": {"type": "choice", "choice": "medium", "confidence": 0.96, "probabilities": importance},
                }}, 9

            classifier = JevClassifier(
                api_key="test", endpoint="https://example.org", model="jev-1.13",
                timeout_seconds=1, min_confidence=0.9, max_concurrency=1,
                max_event_candidates=1, max_request_bytes=4096,
                failure_threshold=2, circuit_cooldown_seconds=1, transport=transport,
            )
            with patch.dict(os.environ, {"FATHOM_RESEARCH_DB_PATH": str(path)}):
                result = await classifier.classify(article, events)
                telemetry.record_regroup_outcome(None, "regroup-1", 12, 7)
                self.assertTrue(telemetry.flush())
            self.assertEqual(result["decision"], "uncategorized")
            self.assertEqual(telemetry.build_report(path)["reunion"]["rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
