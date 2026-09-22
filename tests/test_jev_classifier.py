import asyncio
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import tasks
from app.database.models import Article, Base, Event
from app.grouping import engine
from app.grouping.jev_classifier import (
    JevCircuitOpenError,
    JevClassifier,
    JevProviderError,
    _serialize_payload,
)


ARTICLE = {
    "id": 42,
    "title": "Ceasefire talks resume",
    "source": "Example News",
    "published_date": "2026-09-22T10:00:00+00:00",
    "content_type": "news",
    "snippet": "Negotiators returned to talks after overnight developments.",
}
EVENTS = [
    {
        "id": 7,
        "name": "Regional ceasefire negotiations",
        "description": "Ongoing diplomacy and ceasefire negotiations.",
        "recent_titles": ["Negotiators prepare for another round"],
    }
]


def response_transport(destination_confidence=0.96, importance_confidence=0.95):
    def transport(_endpoint, _api_key, payload, _timeout):
        destination_ids = set(payload["questions"]["destination"]["criteria"])
        importance_ids = set(payload["questions"]["importance"]["criteria"])
        destination_probabilities = {key: 0.02 for key in destination_ids}
        destination_probabilities["o001"] = 0.98
        importance_probabilities = {key: 0.025 for key in importance_ids}
        importance_probabilities["high"] = 0.9
        return {
            "model": "jev-1.13",
            "answers": {
                "destination": {
                    "type": "choice",
                    "choice": "o001",
                    "confidence": destination_confidence,
                    "probabilities": destination_probabilities,
                },
                "importance": {
                    "type": "choice",
                    "choice": "high",
                    "confidence": importance_confidence,
                    "probabilities": importance_probabilities,
                },
            },
        }, 12

    return transport


def classifier(**overrides):
    settings = {
        "api_key": "key",
        "endpoint": "https://opencode.ai/zen/v1/systemone",
        "model": "jev-1.13",
        "timeout_seconds": 10,
        "min_confidence": 0.9,
        "max_concurrency": 4,
        "max_event_candidates": 80,
        "max_request_bytes": 28000,
        "failure_threshold": 8,
        "circuit_cooldown_seconds": 300,
        "transport": response_transport(),
    }
    settings.update(overrides)
    return JevClassifier(**settings)


class JevClassifierTests(unittest.IsolatedAsyncioTestCase):
    async def test_maps_high_confidence_choice_to_existing_event(self):
        assignment = await classifier().classify(ARTICLE, EVENTS)
        self.assertEqual(assignment["decision"], "existing")
        self.assertEqual(assignment["event_id"], 7)
        self.assertEqual(assignment["importance_score"], 0.7)
        self.assertEqual(assignment["confidence"], 0.96)

    async def test_low_destination_confidence_stays_ungrouped(self):
        assignment = await classifier(
            transport=response_transport(destination_confidence=0.55)
        ).classify(ARTICLE, EVENTS)
        self.assertEqual(assignment["decision"], "uncategorized")
        self.assertIsNone(assignment["event_id"])
        self.assertEqual(assignment["importance_score"], 0.7)

    async def test_low_importance_confidence_uses_neutral_score(self):
        assignment = await classifier(
            transport=response_transport(importance_confidence=0.4)
        ).classify(ARTICLE, EVENTS)
        self.assertEqual(assignment["importance_score"], 0.5)

    async def test_rejects_request_over_context_budget(self):
        with self.assertRaises(JevProviderError):
            await classifier(max_request_bytes=100).classify(ARTICLE, EVENTS)

    def test_trims_ranked_candidates_until_request_fits(self):
        events = [
            {
                "id": index,
                "name": f"Event {index}",
                "description": "description " * 100,
                "recent_titles": ["headline " * 50],
            }
            for index in range(1, 81)
        ]
        client = classifier(max_request_bytes=5000)
        payload, option_events = client._build_payload(ARTICLE, events)
        serialized = _serialize_payload(payload)
        self.assertLessEqual(len(serialized), 5000)
        self.assertLess(len(option_events), 81)

    def test_request_budget_matches_transport_serialization_for_unicode(self):
        events = [
            {
                "id": index,
                "name": f"国際ニュース {index}",
                "description": "停戦交渉と外交協議の最新状況 " * 30,
                "recent_titles": ["各国代表が協議を再開 — 合意形成を目指す" * 10],
            }
            for index in range(1, 81)
        ]
        client = classifier(max_request_bytes=5000)
        payload, _option_events = client._build_payload(ARTICLE, events)
        self.assertLessEqual(len(_serialize_payload(payload)), 5000)

    def test_relevance_ranking_can_promote_event_beyond_raw_cap(self):
        events = [
            {"id": index, "name": f"Unrelated event {index}", "description": "", "recent_titles": []}
            for index in range(1, 81)
        ]
        events.append(
            {
                "id": 999,
                "name": "Ceasefire talks resume",
                "description": "Negotiators return to ceasefire talks.",
                "recent_titles": [],
            }
        )
        client = classifier(max_event_candidates=2)
        _payload, option_events = client._build_payload(ARTICLE, events)
        self.assertIn(999, option_events.values())

    def test_relevance_ranking_penalizes_verbose_distractors(self):
        distractors = [
            {
                "id": index,
                "name": f"Daily international briefing {index}",
                "description": "ceasefire talks resume negotiators overnight developments " + "background " * 80,
                "recent_titles": ["World news roundup with many unrelated developments " * 20],
            }
            for index in range(1, 91)
        ]
        target = {
            "id": 999,
            "name": "Ceasefire talks resume",
            "description": "Negotiators returned to talks.",
            "recent_titles": ["Ceasefire negotiators resume talks"],
        }
        client = classifier(max_event_candidates=1)
        _payload, option_events = client._build_payload(ARTICLE, distractors + [target])
        self.assertIn(999, option_events.values())

    async def test_uses_selected_probability_as_confidence_ceiling(self):
        def transport(_endpoint, _api_key, _payload, _timeout):
            return {
                "answers": {
                    "destination": {
                        "type": "choice",
                        "choice": "o001",
                        "confidence": 0.99,
                        "probabilities": {"o000": 0.4, "o001": 0.6},
                    },
                    "importance": {
                        "type": "choice",
                        "choice": "medium",
                        "confidence": 0.95,
                        "probabilities": {
                            "critical": 0.01,
                            "high": 0.01,
                            "medium": 0.96,
                            "low": 0.01,
                            "trivial": 0.01,
                        },
                    },
                }
            }, 1

        assignment = await classifier(transport=transport).classify(ARTICLE, EVENTS)
        self.assertEqual(assignment["decision"], "uncategorized")
        self.assertEqual(assignment["confidence"], 0.6)

    async def test_circuit_opens_after_consecutive_provider_failures(self):
        calls = 0

        def transport(_endpoint, _api_key, _payload, _timeout):
            nonlocal calls
            calls += 1
            raise JevProviderError("provider unavailable")

        client = classifier(
            transport=transport,
            failure_threshold=2,
            max_concurrency=1,
        )
        with self.assertRaises(JevProviderError):
            await client.classify(ARTICLE, EVENTS)
        with self.assertRaises(JevProviderError):
            await client.classify(ARTICLE, EVENTS)
        with self.assertRaises(JevCircuitOpenError):
            await client.classify(ARTICLE, EVENTS)
        self.assertEqual(calls, 2)

    async def test_unexpected_transport_errors_count_toward_circuit(self):
        calls = 0

        def transport(_endpoint, _api_key, _payload, _timeout):
            nonlocal calls
            calls += 1
            raise ConnectionResetError("connection reset")

        client = classifier(transport=transport, failure_threshold=1, max_concurrency=1)
        with self.assertRaises(JevProviderError):
            await client.classify(ARTICLE, EVENTS)
        with self.assertRaises(JevCircuitOpenError):
            await client.classify(ARTICLE, EVENTS)
        self.assertEqual(calls, 1)

    async def test_engine_reports_batch_deadline_as_degraded(self):
        class SlowClassifier:
            async def classify(self, _article, _events):
                await __import__("asyncio").sleep(1)

            async def circuit_open(self):
                return False

        with patch.object(engine, "fetch_ungrouped_articles", return_value=[object()]), patch.object(
            engine, "fetch_active_events", return_value=([], [])
        ), patch.object(engine, "_article_for_prompt", return_value=ARTICLE), patch.object(
            engine.app_config, "JEV_BATCH_TIMEOUT_SECONDS", 0.01
        ):
            result = await engine.assign_new_articles_with_jev(SlowClassifier())

        self.assertEqual(result["timed_out"], 1)
        self.assertEqual(result["degraded"], 1)


class JevTaskRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tasks.grouping_lock = asyncio.Lock()
        tasks.summary_queue = None
        tasks.jev_classifier = None

    async def test_live_grouping_uses_jev_without_full_grouping_llm(self):
        classifier_instance = MagicMock()
        queue = MagicMock()
        queue.flush = AsyncMock()
        tasks.summary_queue = queue
        expected = {"existing": 1, "errors": 0}
        with patch.object(tasks.app_config, "JEV_ENABLED", True), patch.object(
            tasks, "_get_jev_classifier", return_value=classifier_instance
        ), patch.object(tasks, "_get_summary_llm", return_value="summary-llm"), patch.object(
            tasks, "_get_grouping_llm"
        ) as grouping_llm, patch.object(
            tasks.grouping_engine,
            "assign_new_articles_with_jev",
            new=AsyncMock(return_value=expected),
        ) as assign:
            result = await tasks.run_grouping()

        self.assertEqual(result, expected)
        grouping_llm.assert_not_called()
        queue.flush.assert_awaited_once_with()
        assign.assert_awaited_once_with(
            classifier_instance,
            on_event_increments=queue.enqueue,
            summary_llm=None,
        )

    async def test_live_grouping_waits_for_summary_flush_before_returning(self):
        queue = MagicMock()
        flush_started = asyncio.Event()
        release_flush = asyncio.Event()

        async def wait_for_release():
            flush_started.set()
            await release_flush.wait()

        queue.flush = AsyncMock(side_effect=wait_for_release)
        tasks.summary_queue = queue
        expected = {"existing": 2, "errors": 0}
        with patch.object(tasks.app_config, "JEV_ENABLED", True), patch.object(
            tasks, "_get_jev_classifier", return_value=MagicMock()
        ), patch.object(
            tasks.grouping_engine,
            "assign_new_articles_with_jev",
            new=AsyncMock(return_value=expected),
        ):
            grouping_task = asyncio.create_task(tasks.run_grouping())
            await flush_started.wait()
            self.assertFalse(grouping_task.done())
            release_flush.set()
            result = await grouping_task

        self.assertEqual(result, expected)
        queue.flush.assert_awaited_once_with()

    async def test_missing_jev_key_falls_back_to_full_grouping_llm(self):
        expected = {"existing": 1, "errors": 0}
        with patch.object(tasks.app_config, "JEV_ENABLED", True), patch.object(
            tasks, "_get_jev_classifier", return_value=None
        ), patch.object(tasks, "_get_grouping_llm", return_value="grouping-llm"), patch.object(
            tasks, "_get_summary_llm", return_value="summary-llm"
        ), patch.object(
            tasks.grouping_engine,
            "assign_new_articles",
            new=AsyncMock(return_value=expected),
        ) as assign:
            result = await tasks.run_grouping()

        self.assertEqual(result, expected)
        assign.assert_awaited_once_with(
            "grouping-llm",
            create_new_events=False,
            on_event_increments=None,
            summary_llm="summary-llm",
        )

    async def test_regroup_routes_new_event_summaries_through_summary_queue(self):
        queue = MagicMock()
        queue.summarize_initial = AsyncMock()
        flush_started = asyncio.Event()
        release_flush = asyncio.Event()

        async def wait_for_release():
            flush_started.set()
            await release_flush.wait()

        queue.flush = AsyncMock(side_effect=wait_for_release)
        tasks.summary_queue = queue
        expected = {"new_events": 1}
        with patch.object(tasks, "_get_grouping_llm", return_value="grouping-llm"), patch.object(
            tasks.grouping_engine,
            "regroup_uncategorized",
            new=AsyncMock(return_value=expected),
        ) as regroup:
            regroup_task = asyncio.create_task(tasks.run_regroup())
            await flush_started.wait()
            self.assertFalse(regroup_task.done())
            release_flush.set()
            result = await regroup_task

        self.assertEqual(result, expected)
        queue.flush.assert_awaited_once_with()
        kwargs = regroup.await_args.kwargs
        self.assertEqual(kwargs["summary_llm"], None)
        self.assertIs(kwargs["on_new_events"], queue.summarize_initial)
        self.assertIs(kwargs["on_event_increments"], queue.enqueue)

    async def test_grouping_uses_captured_queue_when_global_is_replaced(self):
        first_queue = MagicMock()
        first_queue.enqueue = AsyncMock(return_value=1)
        first_queue.flush = AsyncMock()
        replacement_queue = MagicMock()
        tasks.summary_queue = first_queue

        async def assign_with_callback(_classifier, *, on_event_increments, summary_llm):
            tasks.summary_queue = replacement_queue
            await on_event_increments({7: [101]})
            return {"existing": 1, "errors": 0}

        with patch.object(tasks.app_config, "JEV_ENABLED", True), patch.object(
            tasks, "_get_jev_classifier", return_value=MagicMock()
        ), patch.object(
            tasks.grouping_engine,
            "assign_new_articles_with_jev",
            new=AsyncMock(side_effect=assign_with_callback),
        ):
            result = await tasks.run_grouping()

        self.assertEqual(result["existing"], 1)
        first_queue.enqueue.assert_awaited_once_with({7: [101]})
        first_queue.flush.assert_awaited_once_with()
        replacement_queue.enqueue.assert_not_called()

    async def test_shutdown_waits_for_grouping_then_drains_captured_queue(self):
        queue = MagicMock()
        queue.shutdown = AsyncMock()
        tasks.summary_queue = queue
        lock_acquired = asyncio.Event()
        release = asyncio.Event()

        async def hold_grouping_lock():
            async with tasks.grouping_lock:
                lock_acquired.set()
                await release.wait()

        holder = asyncio.create_task(hold_grouping_lock())
        await lock_acquired.wait()
        shutdown = asyncio.create_task(tasks.shutdown_summary_queue())
        await asyncio.sleep(0)
        queue.shutdown.assert_not_awaited()

        release.set()
        await asyncio.gather(holder, shutdown)

        queue.shutdown.assert_awaited_once_with()
        self.assertIsNone(tasks.summary_queue)

    async def test_manual_enqueue_persists_when_queue_is_detached(self):
        tasks.summary_queue = None
        with patch.object(tasks, "persist_summary_updates", return_value=2) as persist:
            queued = await tasks.enqueue_summary_updates({7: [101, 102]})

        self.assertEqual(queued, 2)
        persist.assert_called_once_with({7: [101, 102]})


class RegroupSummaryRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_initial_summary_is_queued_for_retry(self):
        test_engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(test_engine)
        session_factory = sessionmaker(bind=test_engine, expire_on_commit=False)

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
            db.add(Event(id=99, name="New event", status="active"))
            db.add(Article(id=42, url="https://example.com/42", event_id=99))

        response = MagicMock()
        response.generations = [[MagicMock(text='{"assignments": []}')]]
        on_new_events = AsyncMock(return_value=[99])
        on_event_increments = AsyncMock(return_value=1)
        counts = {
            "existing": 0,
            "new_events": 1,
            "revived": 0,
            "reused": 0,
            "new_singletons": 0,
            "uncategorized": 0,
            "errors": 0,
        }

        with patch.object(engine, "fetch_ungrouped_articles", return_value=[object()]), patch.object(
            engine, "fetch_active_events", return_value=([], [])
        ), patch.object(engine, "_article_for_prompt", return_value=ARTICLE), patch.object(
            engine, "build_few_shot_examples", return_value=[]
        ), patch.object(engine, "build_few_shot_block", return_value=""), patch.object(
            engine, "build_regroup_prompt", return_value="prompt"
        ), patch.object(
            engine, "_agenerate_with_retry", new=AsyncMock(return_value=response)
        ), patch.object(engine, "parse_json_object", return_value={"assignments": []}), patch.object(
            engine,
            "_apply_regroup_inner",
            return_value=(counts, ([99], {99: [42]})),
        ), patch.object(
            engine, "db_session_scope", new=test_scope
        ), patch(
            "app.grouping.dedup.dedup_events", new=AsyncMock(return_value={})
        ):
            result = await engine.regroup_uncategorized(
                MagicMock(),
                on_new_events=on_new_events,
                on_event_increments=on_event_increments,
            )

        self.assertEqual(result["new_events"], 1)
        on_new_events.assert_awaited_once_with([99])
        on_event_increments.assert_awaited_once_with({99: [42]})
        test_engine.dispose()


class EventPromptTests(unittest.TestCase):
    def test_event_prompt_uses_newest_article_titles(self):
        now = datetime.now(timezone.utc)
        event = Event(id=7, name="Event", status="active")
        event.articles = [
            Article(id=1, title="Old", url="https://example.com/old", published_date=now - timedelta(hours=2)),
            Article(id=2, title="Newest", url="https://example.com/new", published_date=now),
            Article(id=3, title="Middle", url="https://example.com/middle", published_date=now - timedelta(hours=1)),
        ]
        payload = engine._event_summary_for_prompt(event, max_titles=2)
        self.assertEqual(payload["recent_titles"], ["Newest", "Middle"])


if __name__ == "__main__":
    unittest.main()
