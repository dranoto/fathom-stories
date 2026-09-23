import json
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from app.database.models import Event
from app.grouping import dedup
from app.grouping.jev_classifier import JevClassifier, JevProviderError


def make_events():
    return [
        Event(id=1, name="Mars sample return mission", status="active", created_at=datetime(2025, 1, 1, tzinfo=timezone.utc)),
        Event(id=2, name="Mars mission sample return", status="active", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        Event(id=3, name="Local weather forecast", status="active", created_at=datetime(2026, 1, 2, tzinfo=timezone.utc)),
    ]


def typed_answer(choice, confidence=0.97):
    labels = ("same", "related", "unrelated")
    probabilities = {label: (0.98 if label == choice else 0.01) for label in labels}
    return {"answers": {"duplicate_relation": {
        "type": "choice", "choice": choice, "confidence": confidence,
        "probabilities": probabilities,
    }}}


class DedupGateTests(unittest.IsolatedAsyncioTestCase):
    def test_retriever_nominates_close_names_without_unrelated_events(self):
        self.assertEqual(dedup.nominate_duplicate_pairs(make_events()), [(1, 2)])

    async def test_typed_three_way_decision_is_strict(self):
        payloads = []

        def transport(_endpoint, _key, payload, _timeout):
            payloads.append(payload)
            return typed_answer("related"), 12

        gate = JevClassifier(
            api_key="test", endpoint="https://example.org", model="jev-test",
            timeout_seconds=1, min_confidence=0.9, max_concurrency=1,
            max_event_candidates=3, max_request_bytes=4096,
            failure_threshold=2, circuit_cooldown_seconds=1, transport=transport,
        )
        result = await gate.assess_duplicate({"id": 1, "name": "Mars mission"}, {"id": 2, "name": "Mars sample mission"})
        self.assertEqual(result["choice"], "related")
        self.assertEqual(set(payloads[0]["questions"]), {"duplicate_relation"})
        self.assertLessEqual(len(json.dumps(payloads[0]).encode()), 4096)
        with patch.object(gate, "transport", return_value=({"answers": {}}, 1)):
            with self.assertRaises(JevProviderError):
                await gate.assess_duplicate({"id": 1}, {"id": 2})

    async def test_gate_sends_only_same_pair_to_full_model_and_never_merges_by_itself(self):
        class Llm:
            prompts = []

            async def agenerate(self, batches):
                self.prompts.append(batches[0][-1].content)
                response = type("Response", (), {})()
                response.generations = [[type("Generation", (), {"text": '{"merge_pairs": []}'})()]]
                return response

        gate = type("Gate", (), {})()
        gate.assess_duplicate = AsyncMock(return_value={"choice": "same", "confidence": 0.98})
        llm = Llm()
        with patch.object(dedup, "fetch_active_and_cooling_events", return_value=make_events()), patch.object(
            dedup.app_config, "JEV_DEDUP_ENABLED", True
        ), patch.object(dedup.app_config, "JEV_DEDUP_APPLY", True), patch.object(dedup.app_config, "JEV_API_KEY", "test"), patch.object(
            dedup, "_make_dedup_gate", return_value=gate
        ), patch.object(dedup, "merge_events") as merge:
            result = await dedup.dedup_events(llm)

        self.assertEqual(result["merged"], 0)
        merge.assert_not_called()
        self.assertIn('"id": 1', llm.prompts[0])
        self.assertIn('"id": 2', llm.prompts[0])
        self.assertNotIn('"id": 3', llm.prompts[0])

    async def test_gate_negative_decision_skips_full_model_and_ambiguous_one_keeps_it(self):
        llm = AsyncMock()
        gate = type("Gate", (), {})()
        gate.assess_duplicate = AsyncMock(return_value={"choice": "unrelated", "confidence": 0.99})
        with patch.object(dedup, "fetch_active_and_cooling_events", return_value=make_events()), patch.object(
            dedup.app_config, "JEV_DEDUP_ENABLED", True
        ), patch.object(dedup.app_config, "JEV_DEDUP_APPLY", True), patch.object(dedup.app_config, "JEV_API_KEY", "test"), patch.object(
            dedup, "_make_dedup_gate", return_value=gate
        ):
            result = await dedup.dedup_events(llm)
        self.assertEqual(result["merged"], 0)
        llm.agenerate.assert_not_called()

        gate.assess_duplicate.return_value = {"choice": "related", "confidence": 0.65}
        response = type("Response", (), {})()
        response.generations = [[type("Generation", (), {"text": '{"merge_pairs": []}'})()]]
        llm.agenerate.return_value = response
        with patch.object(dedup, "fetch_active_and_cooling_events", return_value=make_events()), patch.object(
            dedup.app_config, "JEV_DEDUP_ENABLED", True
        ), patch.object(dedup.app_config, "JEV_DEDUP_APPLY", True), patch.object(dedup.app_config, "JEV_API_KEY", "test"), patch.object(
            dedup, "_make_dedup_gate", return_value=gate
        ):
            await dedup.dedup_events(llm)
        llm.agenerate.assert_awaited_once()

    async def test_provider_failure_falls_back_to_complete_original_event_set(self):
        class Llm:
            prompts = []

            async def agenerate(self, batches):
                self.prompts.append(batches[0][-1].content)
                response = type("Response", (), {})()
                response.generations = [[type("Generation", (), {"text": '{"merge_pairs": []}'})()]]
                return response

        gate = type("Gate", (), {})()
        gate.assess_duplicate = AsyncMock(side_effect=JevProviderError("offline"))
        llm = Llm()
        with patch.object(dedup, "fetch_active_and_cooling_events", return_value=make_events()), patch.object(
            dedup.app_config, "JEV_DEDUP_ENABLED", True
        ), patch.object(dedup.app_config, "JEV_DEDUP_APPLY", True), patch.object(dedup.app_config, "JEV_API_KEY", "test"), patch.object(
            dedup, "_make_dedup_gate", return_value=gate
        ):
            result = await dedup.dedup_events(llm)
        self.assertEqual(result["errors"], 0)
        self.assertIn('"id": 3', llm.prompts[0])


if __name__ == "__main__":
    unittest.main()
