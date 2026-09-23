import os
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from app.research import telemetry


class ResearchTelemetryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "research.sqlite3"

    def tearDown(self):
        telemetry.flush(timeout=2)

    def test_parallel_initializers_all_validate_the_same_new_schema(self):
        barrier = threading.Barrier(8)

        def open_research_db(_index):
            barrier.wait(timeout=5)
            return telemetry.initialize(self.db_path)

        with ThreadPoolExecutor(max_workers=8) as pool:
            initialized = list(pool.map(open_research_db, range(8)))
        self.assertEqual(initialized, [self.db_path] * 8)
        self.assertEqual(telemetry.build_report(self.db_path)["schema_version"], telemetry.SCHEMA_VERSION)

    def test_decision_candidates_join_regroup_outcome_and_report_rate(self):
        telemetry.initialize(self.db_path)
        self.assertTrue(
            telemetry.record_decision(
                decision_id="decision-1",
                article_id=101,
                choice="none",
                confidence=0.78,
                shown_event_ids=[4, 9],
                decided_at="2026-09-22T12:00:00+00:00",
                lane="jev",
                model="jev-1.13",
                path=self.db_path,
            )
        )
        self.assertTrue(
            telemetry.record_regroup_outcome(
                decision_id="decision-1",
                regroup_run_id="regroup-1",
                article_id=101,
                assigned_event_id=9,
                regrouped_at="2026-09-22T18:00:00+00:00",
                path=self.db_path,
            )
        )
        self.assertTrue(telemetry.flush(timeout=2))

        report = telemetry.build_report(self.db_path)
        self.assertEqual(report["decisions"]["none"], 1)
        self.assertEqual(report["reunion"]["numerator"], 1)
        self.assertEqual(report["reunion"]["denominator"], 1)
        self.assertEqual(report["reunion"]["rate"], 1.0)

    def test_regroup_can_resolve_latest_prior_none_decision_by_article_id(self):
        telemetry.initialize(self.db_path)
        telemetry.record_decision(
            "decision-auto-link",
            102,
            "none",
            0.72,
            [6, 8],
            decided_at="2026-09-22T10:00:00+00:00",
            path=self.db_path,
        )
        telemetry.record_regroup_outcome(
            None,
            "regroup-auto-link-unassigned",
            102,
            None,
            regrouped_at="2026-09-22T14:00:00+00:00",
            path=self.db_path,
        )
        telemetry.record_regroup_outcome(
            None,
            "regroup-auto-link",
            102,
            6,
            regrouped_at="2026-09-22T16:00:00+00:00",
            path=self.db_path,
        )
        self.assertTrue(telemetry.flush(timeout=2))
        report = telemetry.build_report(self.db_path)
        self.assertEqual(report["reunion"]["numerator"], 1)
        self.assertEqual(report["reunion"]["denominator"], 1)

    def test_later_event_choice_prevents_regroup_attribution_to_older_none(self):
        telemetry.initialize(self.db_path)
        telemetry.record_decision(
            "older-none", 102, "none", 0.72, [6],
            decided_at="2026-09-22T10:00:00+00:00", path=self.db_path,
        )
        telemetry.record_decision(
            "newer-event", 102, "event", 0.96, [6], chosen_event_id=6,
            decided_at="2026-09-22T11:00:00+00:00", path=self.db_path,
        )
        telemetry.record_regroup_outcome(
            None, "regroup-later", 102, 6,
            regrouped_at="2026-09-22T12:00:00+00:00", path=self.db_path,
        )
        self.assertTrue(telemetry.flush(timeout=2))
        report = telemetry.build_report(self.db_path)
        self.assertEqual(report["reunion"]["denominator"], 0)
        self.assertEqual(report["reunion"]["none_decisions_without_outcome"], 1)

    def test_report_breaks_down_manual_feedback_by_kind_and_latest_jev_choice(self):
        telemetry.initialize(self.db_path)
        telemetry.record_decision("none-one", 101, "none", 0.8, [7],
                                  decided_at="2026-09-22T10:00:00Z", lane="jev", path=self.db_path)
        telemetry.record_decision("event-two", 102, "event", 0.96, [7], chosen_event_id=7,
                                  decided_at="2026-09-22T10:00:00Z", lane="jev", path=self.db_path)
        telemetry.record_manual_correction("correction-one", 101, None, 7, "move",
                                           corrected_at="2026-09-22T11:00:00Z", path=self.db_path)
        telemetry.record_manual_correction("correction-two", 102, 7, 8, "split",
                                           corrected_at="2026-09-22T11:00:00Z", path=self.db_path)
        telemetry.record_manual_correction("correction-three", 103, None, 8, "move",
                                           corrected_at="2026-09-22T11:00:00Z", path=self.db_path)
        self.assertTrue(telemetry.flush())
        report = telemetry.build_report(self.db_path)
        self.assertEqual(report["manual_corrections_by_kind"], {"move": 2, "split": 1})
        self.assertEqual(report["manual_feedback_jev_cohort"], {
            "none": 1, "event": 1, "no_prior_jev_decision": 1,
        })

    def test_duplicate_decision_id_keeps_original_choice_and_candidate_snapshot(self):
        telemetry.initialize(self.db_path)
        telemetry.record_decision(
            "same-id", 22, "none", 0.6, [3, 5], path=self.db_path
        )
        telemetry.record_decision(
            "same-id", 22, "event", 0.99, [7], chosen_event_id=7, path=self.db_path
        )
        self.assertTrue(telemetry.flush(timeout=2))

        with sqlite3.connect(self.db_path) as connection:
            decision = connection.execute(
                "SELECT choice, chosen_event_id, confidence FROM decisions WHERE decision_id = ?",
                ("same-id",),
            ).fetchone()
            candidates = connection.execute(
                "SELECT event_id FROM decision_candidates WHERE decision_id = ? ORDER BY position",
                ("same-id",),
            ).fetchall()
        self.assertEqual(decision, ("none", None, 0.6))
        self.assertEqual(candidates, [("3",), ("5",)])

    def test_provider_lane_report_uses_recorded_bytes_and_never_invents_cost(self):
        telemetry.initialize(self.db_path)
        telemetry.record_provider_call(
            call_id="call-ok",
            lane="jev",
            model="jev-1.13",
            latency_ms=120,
            request_bytes=840,
            response_bytes=220,
            prompt_tokens=180,
            completion_tokens=45,
            success=True,
            path=self.db_path,
        )
        telemetry.record_provider_call(
            call_id="call-failed",
            lane="jev",
            model="jev-1.13",
            latency_ms=300,
            request_bytes=900,
            response_bytes=None,
            success=False,
            error_type="TimeoutError",
            path=self.db_path,
        )
        self.assertTrue(telemetry.flush(timeout=2))

        report = telemetry.build_report(self.db_path)
        lane = report["provider_lanes"]["jev"]
        self.assertEqual(lane["calls"], 2)
        self.assertEqual(lane["failures"], 1)
        self.assertEqual(lane["error_rate"], 0.5)
        self.assertEqual(lane["request_bytes"], 1740)
        self.assertEqual(lane["response_bytes"], 220)
        self.assertEqual(lane["reported_tokens"], 225)
        self.assertNotIn("cost", report)

    def test_report_groups_calls_by_utc_hour_and_exposes_none_rate(self):
        telemetry.initialize(self.db_path)
        telemetry.record_decision("d1", 1, "none", 0.9, [7], path=self.db_path)
        telemetry.record_decision("d2", 2, "event", 0.99, [7], chosen_event_id=7, path=self.db_path)
        for call_id, stamp in (("a", "2026-09-22T18:00:00Z"), ("b", "2026-09-22T18:30:00Z"), ("c", "2026-09-22T19:00:00Z")):
            telemetry.record_provider_call(
                call_id, "jev", "jev-1.13", 10, 100, 20,
                success=True, called_at=stamp, path=self.db_path,
            )
        self.assertTrue(telemetry.flush())
        report = telemetry.build_report(self.db_path)
        self.assertEqual(report["provider_lanes"]["jev"]["calls_by_utc_hour"], {
            "2026-09-22T18:00Z": 2,
            "2026-09-22T19:00Z": 1,
        })
        self.assertEqual(report["decisions"]["none_rate"], 0.5)

    def test_research_databases_are_isolated_and_foreign_database_is_rejected(self):
        first = self.db_path
        second = Path(self.temp_dir.name) / "other.sqlite3"
        telemetry.initialize(first)
        telemetry.initialize(second)
        telemetry.record_decision("only-first", 1, "none", None, [], path=first)
        self.assertTrue(telemetry.flush(timeout=2))
        self.assertEqual(telemetry.build_report(first)["decisions"]["total"], 1)
        self.assertEqual(telemetry.build_report(second)["decisions"]["total"], 0)

        foreign = Path(self.temp_dir.name) / "stories.sqlite3"
        with sqlite3.connect(foreign) as connection:
            connection.execute("CREATE TABLE articles (id INTEGER PRIMARY KEY)")
        with self.assertRaises(telemetry.TelemetryDatabaseError):
            telemetry.initialize(foreign)
        with sqlite3.connect(foreign) as connection:
            tables = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        self.assertEqual(tables, [("articles",)])

    def test_schema_version_mismatch_is_rejected_without_migration(self):
        telemetry.initialize(self.db_path)
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("PRAGMA user_version = 99")
        with self.assertRaises(telemetry.TelemetryDatabaseError):
            telemetry.initialize(self.db_path)
        with self.assertRaises(telemetry.TelemetryDatabaseError):
            telemetry.build_report(self.db_path)
        with sqlite3.connect(self.db_path) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 99)

    def test_application_database_filename_is_rejected_before_creation(self):
        application_path = Path(self.temp_dir.name) / "stories.db"
        with self.assertRaises(telemetry.TelemetryDatabaseError):
            telemetry.initialize(application_path)
        self.assertFalse(application_path.exists())

    def test_records_contain_only_metadata_not_article_text_keys_or_payloads(self):
        telemetry.initialize(self.db_path)
        telemetry.record_decision(
            "safe-decision", 12, "none", 0.9, [8], path=self.db_path
        )
        telemetry.record_manual_correction(
            correction_id="correction-1",
            article_id=12,
            original_event_id=8,
            corrected_event_id=11,
            kind="move",
            path=self.db_path,
        )
        telemetry.record_provider_call(
            call_id="call-1",
            lane="grouping",
            model="example-model",
            latency_ms=12,
            request_bytes=123,
            response_bytes=45,
            success=True,
            path=self.db_path,
        )
        self.assertFalse(
            telemetry.record_provider_call(
                call_id="unsafe-error-metadata",
                lane="grouping",
                model="example-model",
                latency_ms=12,
                request_bytes=123,
                response_bytes=45,
                success=False,
                error_type="Bearer test-secret",
                path=self.db_path,
            )
        )
        self.assertTrue(telemetry.flush(timeout=2))

        with sqlite3.connect(self.db_path) as connection:
            table_names = [
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            ]
            columns = {
                table: [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
                for table in table_names
            }
            rows = []
            for table in table_names:
                rows.extend(connection.execute(f"SELECT * FROM {table}").fetchall())
        stored_text = repr((columns, rows))
        forbidden = ["article headline sentinel", "Bearer test-secret", "raw provider payload"]
        for value in forbidden:
            self.assertNotIn(value, stored_text)
        self.assertFalse(any("payload" in column or "content" in column for names in columns.values() for column in names))

    def test_cli_main_prints_aggregate_report_from_explicit_path(self):
        telemetry.initialize(self.db_path)
        output = StringIO()
        with redirect_stdout(output):
            result = telemetry.main(["--db", str(self.db_path)])
        self.assertEqual(result, 0)
        self.assertIn("Fathom research telemetry", output.getvalue())
        self.assertIn("Cost is not calculated", output.getvalue())

    def test_environment_path_is_optional_and_explicit_path_takes_precedence(self):
        explicit = Path(self.temp_dir.name) / "explicit.sqlite3"
        env_path = Path(self.temp_dir.name) / "from-env.sqlite3"
        old_value = os.environ.get("FATHOM_RESEARCH_DB_PATH")
        os.environ["FATHOM_RESEARCH_DB_PATH"] = str(env_path)
        try:
            self.assertEqual(telemetry.resolve_db_path(explicit), explicit)
            self.assertEqual(telemetry.resolve_db_path(), env_path)
        finally:
            if old_value is None:
                os.environ.pop("FATHOM_RESEARCH_DB_PATH", None)
            else:
                os.environ["FATHOM_RESEARCH_DB_PATH"] = old_value


if __name__ == "__main__":
    unittest.main()
