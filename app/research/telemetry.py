"""Optional, metadata-only research telemetry in an isolated SQLite database."""

import argparse
import math
import os
import queue
import re
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable, Optional


SCHEMA_VERSION = 1
_APPLICATION_ID = 0x4652544D
_ENV_PATH = "FATHOM_RESEARCH_DB_PATH"
_WRITE_TIMEOUT_SECONDS = 0.05
_QUEUE_CAPACITY = 2048
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,255}$")
_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}$")
_ERROR_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.]{0,127}$")


class TelemetryDatabaseError(RuntimeError):
    """The selected file is not a compatible research telemetry database."""


class TelemetryPathNotConfigured(RuntimeError):
    """No independent telemetry database path has been configured."""


_SCHEMA = {
    "decisions": (
        "decision_id",
        "article_id",
        "decided_at",
        "choice",
        "chosen_event_id",
        "confidence",
        "lane",
        "model",
    ),
    "decision_candidates": (
        "decision_id",
        "event_id",
        "position",
    ),
    "regroup_outcomes": (
        "decision_id",
        "regroup_run_id",
        "article_id",
        "assigned_event_id",
        "regrouped_at",
    ),
    "manual_corrections": (
        "correction_id",
        "decision_id",
        "article_id",
        "original_event_id",
        "corrected_event_id",
        "kind",
        "corrected_at",
    ),
    "provider_calls": (
        "call_id",
        "lane",
        "model",
        "called_at",
        "latency_ms",
        "request_bytes",
        "response_bytes",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "success",
        "error_type",
    ),
}

_SCHEMA_SQL = """
CREATE TABLE decisions (
    decision_id TEXT PRIMARY KEY,
    article_id TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    choice TEXT NOT NULL CHECK (choice IN ('none', 'event')),
    chosen_event_id TEXT,
    confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    lane TEXT,
    model TEXT,
    CHECK ((choice = 'none' AND chosen_event_id IS NULL) OR
           (choice = 'event' AND chosen_event_id IS NOT NULL))
);
CREATE TABLE decision_candidates (
    decision_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK (position >= 0),
    PRIMARY KEY (decision_id, event_id),
    UNIQUE (decision_id, position)
);
CREATE TABLE regroup_outcomes (
    decision_id TEXT,
    regroup_run_id TEXT NOT NULL,
    article_id TEXT NOT NULL,
    assigned_event_id TEXT,
    regrouped_at TEXT NOT NULL,
    PRIMARY KEY (article_id, regroup_run_id)
);
CREATE TABLE manual_corrections (
    correction_id TEXT PRIMARY KEY,
    decision_id TEXT,
    article_id TEXT NOT NULL,
    original_event_id TEXT,
    corrected_event_id TEXT,
    kind TEXT NOT NULL,
    corrected_at TEXT NOT NULL
);
CREATE TABLE provider_calls (
    call_id TEXT PRIMARY KEY,
    lane TEXT NOT NULL,
    model TEXT NOT NULL,
    called_at TEXT NOT NULL,
    latency_ms REAL NOT NULL CHECK (latency_ms >= 0),
    request_bytes INTEGER CHECK (request_bytes IS NULL OR request_bytes >= 0),
    response_bytes INTEGER CHECK (response_bytes IS NULL OR response_bytes >= 0),
    prompt_tokens INTEGER CHECK (prompt_tokens IS NULL OR prompt_tokens >= 0),
    completion_tokens INTEGER CHECK (completion_tokens IS NULL OR completion_tokens >= 0),
    total_tokens INTEGER CHECK (total_tokens IS NULL OR total_tokens >= 0),
    success INTEGER NOT NULL CHECK (success IN (0, 1)),
    error_type TEXT
);
CREATE INDEX decision_candidates_event_idx ON decision_candidates(event_id);
CREATE INDEX decisions_article_idx ON decisions(article_id);
CREATE INDEX regroup_outcomes_article_idx ON regroup_outcomes(article_id);
CREATE INDEX provider_calls_lane_time_idx ON provider_calls(lane, called_at);
"""


class _Barrier:
    def __init__(self) -> None:
        self.event = threading.Event()


_write_queue: queue.Queue[Any] = queue.Queue(maxsize=_QUEUE_CAPACITY)
_worker_started = False
_worker_start_lock = threading.Lock()
_worker: Optional[threading.Thread] = None
_known_databases: set[str] = set()
_known_databases_lock = threading.Lock()
_dropped_writes = 0
_dropped_lock = threading.Lock()


def resolve_db_path(path: Optional[os.PathLike[str] | str] = None) -> Optional[Path]:
    """Use an explicit path first, otherwise the dedicated environment variable."""
    configured: Optional[os.PathLike[str] | str] = path
    if configured is None:
        configured = os.environ.get(_ENV_PATH)
    if configured is None or not os.fspath(configured).strip():
        return None
    return Path(configured).expanduser().absolute()


def _require_path(path: Optional[os.PathLike[str] | str] = None) -> Path:
    result = resolve_db_path(path)
    if result is None:
        raise TelemetryPathNotConfigured(
            f"Set {_ENV_PATH} or pass an explicit research database path"
        )
    return result


def _validate_schema(connection: sqlite3.Connection) -> None:
    app_id = connection.execute("PRAGMA application_id").fetchone()[0]
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if app_id != _APPLICATION_ID:
        raise TelemetryDatabaseError("Selected SQLite file is not a research telemetry database")
    if version != SCHEMA_VERSION:
        raise TelemetryDatabaseError(
            f"Unsupported research telemetry schema version {version}; expected {SCHEMA_VERSION}"
        )
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    if tables != set(_SCHEMA):
        raise TelemetryDatabaseError("Invalid research telemetry table set")
    for table, expected_columns in _SCHEMA.items():
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
        actual_columns = tuple(row[1] for row in rows)
        if actual_columns != expected_columns:
            raise TelemetryDatabaseError(
                f"Invalid research telemetry schema: table {table!r} does not match version {SCHEMA_VERSION}"
            )


def initialize(path: Optional[os.PathLike[str] | str] = None) -> Path:
    """Create a new telemetry database or validate an existing one; never adopt another DB."""
    db_path = _require_path(path)
    try:
        resolved_name = db_path.resolve(strict=False).name.casefold()
    except OSError as exc:
        raise TelemetryDatabaseError("Could not validate the selected research database path") from exc
    if resolved_name == "stories.db":
        raise TelemetryDatabaseError("Refusing to use the application stories.db as research telemetry")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Initialization happens in the optional writer; allow concurrent first-use DDL.
        connection = sqlite3.connect(db_path, timeout=2.0)
    except sqlite3.Error as exc:
        raise TelemetryDatabaseError("Could not open the selected research database") from exc
    try:
        connection.execute("PRAGMA busy_timeout = 2000")
        app_id = connection.execute("PRAGMA application_id").fetchone()[0]
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if app_id == _APPLICATION_ID:
            if version != SCHEMA_VERSION:
                raise TelemetryDatabaseError(
                    f"Unsupported research telemetry schema version {version}; expected {SCHEMA_VERSION}"
                )
            _validate_schema(connection)
            return db_path
        try:
            connection.execute("BEGIN IMMEDIATE")
            # Another process may have initialized the file after our first
            # empty-file check; recheck under the SQLite write lock.
            locked_app_id = connection.execute("PRAGMA application_id").fetchone()[0]
            locked_version = connection.execute("PRAGMA user_version").fetchone()[0]
            locked_tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            if locked_app_id == _APPLICATION_ID:
                _validate_schema(connection)
                connection.commit()
                return db_path
            if locked_app_id != 0 or locked_version != 0 or locked_tables:
                raise TelemetryDatabaseError("Refusing to initialize a foreign SQLite database")
            # executescript() commits an existing transaction first, losing the
            # lock. Execute each static DDL statement in this transaction.
            for statement in _SCHEMA_SQL.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(f"PRAGMA application_id = {_APPLICATION_ID}")
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            _validate_schema(connection)
            connection.commit()
        except sqlite3.Error as exc:
            if connection.in_transaction:
                connection.rollback()
            raise TelemetryDatabaseError("Could not initialize research telemetry schema") from exc
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        return db_path
    except TelemetryDatabaseError:
        raise
    except sqlite3.Error as exc:
        raise TelemetryDatabaseError("Selected file is not a valid research telemetry database") from exc
    finally:
        connection.close()


def _safe_id(value: Any, field: str) -> str:
    text = str(value)
    if not _ID_PATTERN.fullmatch(text):
        raise ValueError(f"Invalid {field}")
    return text


def _safe_label(value: Any, field: str) -> str:
    text = str(value)
    if not _LABEL_PATTERN.fullmatch(text):
        raise ValueError(f"Invalid {field}")
    return text


def _timestamp(value: Optional[str | datetime]) -> str:
    if value is None:
        instant = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        instant = value
    elif isinstance(value, str):
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("Invalid timestamp")
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _optional_nonnegative_int(value: Optional[int], field: str) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"Invalid {field}")
    return value


def _optional_confidence(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise ValueError("Invalid confidence")
    return number


def _enqueue(operation: str, values: tuple[Any, ...], path: Optional[os.PathLike[str] | str]) -> bool:
    global _dropped_writes
    try:
        db_path = resolve_db_path(path)
        if db_path is None:
            return False
        _ensure_worker()
        _write_queue.put_nowait((operation, str(db_path), values))
        return True
    except Exception:
        with _dropped_lock:
            _dropped_writes += 1
        return False


def _ensure_worker() -> None:
    global _worker_started, _worker
    if _worker_started:
        return
    with _worker_start_lock:
        if _worker_started:
            return
        _worker = threading.Thread(target=_write_loop, name="fathom-research-telemetry", daemon=True)
        _worker.start()
        _worker_started = True


def _write_loop() -> None:
    while True:
        job = _write_queue.get()
        try:
            if isinstance(job, _Barrier):
                job.event.set()
                continue
            operation, path, values = job
            _write_one(operation, path, values)
        except Exception:
            with _dropped_lock:
                global _dropped_writes
                _dropped_writes += 1
        finally:
            _write_queue.task_done()


def _write_one(operation: str, path: str, values: tuple[Any, ...]) -> None:
    with _known_databases_lock:
        initialized = path in _known_databases
    if not initialized:
        initialize(path)
        with _known_databases_lock:
            _known_databases.add(path)
    connection = sqlite3.connect(path, timeout=_WRITE_TIMEOUT_SECONDS)
    try:
        connection.execute(f"PRAGMA busy_timeout = {int(_WRITE_TIMEOUT_SECONDS * 1000)}")
        connection.execute("BEGIN IMMEDIATE")
        if operation == "decision":
            connection.execute(
                "INSERT OR IGNORE INTO decisions "
                "(decision_id, article_id, decided_at, choice, chosen_event_id, confidence, lane, model) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                values[:8],
            )
            if connection.execute("SELECT changes()").fetchone()[0]:
                decision_id, _article_id, _at, _choice, _chosen, _confidence, _lane, _model, candidates = values
                connection.executemany(
                    "INSERT INTO decision_candidates (decision_id, event_id, position) VALUES (?, ?, ?)",
                    ((decision_id, event_id, position) for position, event_id in enumerate(candidates)),
                )
        elif operation == "regroup":
            decision_id, regroup_run_id, article_id, assigned_event_id, regrouped_at = values
            if decision_id is None:
                matched = connection.execute(
                    "SELECT decision_id, choice FROM decisions "
                    "WHERE article_id = ? AND decided_at <= ? "
                    "ORDER BY decided_at DESC, decision_id DESC LIMIT 1",
                    (article_id, regrouped_at),
                ).fetchone()
                decision_id = matched[0] if matched and matched[1] == "none" else None
            connection.execute(
                "INSERT OR IGNORE INTO regroup_outcomes "
                "(decision_id, regroup_run_id, article_id, assigned_event_id, regrouped_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (decision_id, regroup_run_id, article_id, assigned_event_id, regrouped_at),
            )
        elif operation == "correction":
            connection.execute(
                "INSERT OR IGNORE INTO manual_corrections "
                "(correction_id, decision_id, article_id, original_event_id, corrected_event_id, kind, corrected_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                values,
            )
        elif operation == "provider":
            connection.execute(
                "INSERT OR IGNORE INTO provider_calls "
                "(call_id, lane, model, called_at, latency_ms, request_bytes, response_bytes, "
                "prompt_tokens, completion_tokens, total_tokens, success, error_type) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                values,
            )
        else:
            raise ValueError("Unknown telemetry operation")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def record_decision(
    decision_id: Any,
    article_id: Any,
    choice: str,
    confidence: Optional[float],
    shown_event_ids: Iterable[Any],
    *,
    chosen_event_id: Any = None,
    decided_at: Optional[str | datetime] = None,
    lane: Optional[str] = None,
    model: Optional[str] = None,
    path: Optional[os.PathLike[str] | str] = None,
) -> bool:
    """Queue a decision and its ordered, decision-time candidate identifiers."""
    try:
        normalized_choice = str(choice).casefold()
        if normalized_choice not in {"none", "event"}:
            return False
        selected_id = _safe_id(chosen_event_id, "chosen event id") if chosen_event_id is not None else None
        if (normalized_choice == "event") != (selected_id is not None):
            return False
        candidates = tuple(dict.fromkeys(_safe_id(value, "candidate event id") for value in shown_event_ids))
        safe_lane = _safe_label(lane, "lane") if lane is not None else None
        safe_model = _safe_label(model, "model") if model is not None else None
        values = (
            _safe_id(decision_id, "decision id"),
            _safe_id(article_id, "article id"),
            _timestamp(decided_at),
            normalized_choice,
            selected_id,
            _optional_confidence(confidence),
            safe_lane,
            safe_model,
            candidates,
        )
        return _enqueue("decision", values, path)
    except Exception:
        return False


def record_regroup_outcome(
    decision_id: Any,
    regroup_run_id: Any,
    article_id: Any,
    assigned_event_id: Any,
    *,
    regrouped_at: Optional[str | datetime] = None,
    path: Optional[os.PathLike[str] | str] = None,
) -> bool:
    """Queue one article's regroup outcome; None resolves the latest prior none decision."""
    try:
        values = (
            _safe_id(decision_id, "decision id") if decision_id is not None else None,
            _safe_id(regroup_run_id, "regroup run id"),
            _safe_id(article_id, "article id"),
            _safe_id(assigned_event_id, "assigned event id") if assigned_event_id is not None else None,
            _timestamp(regrouped_at),
        )
        return _enqueue("regroup", values, path)
    except Exception:
        return False


def record_manual_correction(
    correction_id: Any,
    article_id: Any,
    original_event_id: Any,
    corrected_event_id: Any,
    kind: str,
    *,
    decision_id: Any = None,
    corrected_at: Optional[str | datetime] = None,
    path: Optional[os.PathLike[str] | str] = None,
) -> bool:
    """Queue correction identifiers only; free-text notes are intentionally unsupported."""
    try:
        values = (
            _safe_id(correction_id, "correction id"),
            _safe_id(decision_id, "decision id") if decision_id is not None else None,
            _safe_id(article_id, "article id"),
            _safe_id(original_event_id, "original event id") if original_event_id is not None else None,
            _safe_id(corrected_event_id, "corrected event id") if corrected_event_id is not None else None,
            _safe_label(kind, "correction kind"),
            _timestamp(corrected_at),
        )
        return _enqueue("correction", values, path)
    except Exception:
        return False


def record_provider_call(
    call_id: Any,
    lane: str,
    model: str,
    latency_ms: float,
    request_bytes: Optional[int],
    response_bytes: Optional[int],
    *,
    prompt_tokens: Optional[int] = None,
    completion_tokens: Optional[int] = None,
    total_tokens: Optional[int] = None,
    success: bool,
    error_type: Optional[str] = None,
    called_at: Optional[str | datetime] = None,
    path: Optional[os.PathLike[str] | str] = None,
) -> bool:
    """Queue call metadata. Never accept or persist credentials or request/response bodies."""
    try:
        latency = float(latency_ms)
        if not math.isfinite(latency) or latency < 0 or not isinstance(success, bool):
            return False
        if error_type is not None and not _ERROR_PATTERN.fullmatch(str(error_type)):
            return False
        values = (
            _safe_id(call_id, "provider call id"),
            _safe_label(lane, "lane"),
            _safe_label(model, "model"),
            _timestamp(called_at),
            latency,
            _optional_nonnegative_int(request_bytes, "request bytes"),
            _optional_nonnegative_int(response_bytes, "response bytes"),
            _optional_nonnegative_int(prompt_tokens, "prompt tokens"),
            _optional_nonnegative_int(completion_tokens, "completion tokens"),
            _optional_nonnegative_int(total_tokens, "total tokens"),
            int(success),
            str(error_type) if error_type is not None else None,
        )
        return _enqueue("provider", values, path)
    except Exception:
        return False


def flush(timeout: float = 2.0) -> bool:
    """Wait for already-queued writes, primarily for tests and controlled shutdowns."""
    try:
        _ensure_worker()
        barrier = _Barrier()
        _write_queue.put_nowait(barrier)
    except Exception:
        return False
    return barrier.event.wait(max(0.0, float(timeout)))


def dropped_write_count() -> int:
    """Return the in-process count of telemetry jobs rejected or lost by the worker."""
    with _dropped_lock:
        return _dropped_writes


def _readonly_connection(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise TelemetryDatabaseError("Research telemetry database does not exist")
    connection: Optional[sqlite3.Connection] = None
    try:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.1)
        connection.row_factory = sqlite3.Row
        _validate_schema(connection)
        return connection
    except TelemetryDatabaseError:
        if connection is not None:
            connection.close()
        raise
    except sqlite3.Error as exc:
        if connection is not None:
            connection.close()
        raise TelemetryDatabaseError("Could not read the selected research telemetry database") from exc


def _percentile(values: list[float], percentile: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def build_report(path: Optional[os.PathLike[str] | str] = None) -> dict[str, Any]:
    """Return aggregate analysis from a read-only database connection."""
    db_path = _require_path(path)
    connection = _readonly_connection(db_path)
    try:
        decisions_by_choice = {
            row["choice"]: row["count"]
            for row in connection.execute("SELECT choice, COUNT(*) AS count FROM decisions GROUP BY choice")
        }
        decisions_total = sum(decisions_by_choice.values())
        regroup_count = connection.execute("SELECT COUNT(*) FROM regroup_outcomes").fetchone()[0]
        correction_count = connection.execute("SELECT COUNT(*) FROM manual_corrections").fetchone()[0]

        provider_lanes: dict[str, dict[str, Any]] = {}
        lane_names = [row[0] for row in connection.execute("SELECT DISTINCT lane FROM provider_calls ORDER BY lane")]
        for lane_name in lane_names:
            rows = connection.execute(
                "SELECT model, called_at, latency_ms, request_bytes, response_bytes, prompt_tokens, "
                "completion_tokens, total_tokens, success FROM provider_calls WHERE lane = ?",
                (lane_name,),
            ).fetchall()
            latencies = [float(row["latency_ms"]) for row in rows if row["latency_ms"] is not None]
            successes = sum(int(row["success"]) for row in rows)
            failures = len(rows) - successes
            request_bytes_known = [int(row["request_bytes"]) for row in rows if row["request_bytes"] is not None]
            response_bytes_known = [int(row["response_bytes"]) for row in rows if row["response_bytes"] is not None]
            tokens = sum(
                int(row["total_tokens"])
                if row["total_tokens"] is not None
                else sum(int(row[key]) for key in ("prompt_tokens", "completion_tokens") if row[key] is not None)
                for row in rows
            )
            calls_by_hour: dict[str, int] = {}
            for row in rows:
                hour = datetime.fromisoformat(row["called_at"]).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:00Z")
                calls_by_hour[hour] = calls_by_hour.get(hour, 0) + 1
            provider_lanes[lane_name] = {
                "calls": len(rows),
                "successes": successes,
                "failures": failures,
                "error_rate": failures / len(rows) if rows else None,
                "latency_ms": {
                    "mean": mean(latencies) if latencies else None,
                    "median": median(latencies) if latencies else None,
                    "p95": _percentile(latencies, 0.95),
                },
                "request_bytes": sum(request_bytes_known),
                "request_bytes_calls_measured": len(request_bytes_known),
                "response_bytes": sum(response_bytes_known),
                "response_bytes_calls_measured": len(response_bytes_known),
                "estimated_bytes": sum(request_bytes_known) + sum(response_bytes_known),
                "reported_tokens": tokens,
                "calls_by_utc_hour": dict(sorted(calls_by_hour.items())),
                "models": sorted({str(row["model"]) for row in rows}),
            }

        all_decisions = connection.execute(
            "SELECT decision_id, article_id, decided_at, choice, lane FROM decisions "
            "ORDER BY decided_at, decision_id"
        ).fetchall()
        none_decisions = [row for row in all_decisions if row["choice"] == "none"]
        outcome_rows = connection.execute(
            "SELECT decision_id, regroup_run_id, article_id, assigned_event_id, regrouped_at "
            "FROM regroup_outcomes ORDER BY decision_id, regrouped_at, regroup_run_id"
        ).fetchall()
        outcomes_by_decision: dict[str, list[sqlite3.Row]] = {}
        unlinked_outcomes = []
        for row in outcome_rows:
            if row["decision_id"] is None:
                unlinked_outcomes.append(row)
            else:
                outcomes_by_decision.setdefault(row["decision_id"], []).append(row)
        decisions_by_article: dict[str, list[sqlite3.Row]] = {}
        for decision in all_decisions:
            decisions_by_article.setdefault(decision["article_id"], []).append(decision)
        manual_corrections_by_kind: dict[str, int] = {}
        manual_feedback_jev_cohort = {"none": 0, "event": 0, "no_prior_jev_decision": 0}
        for correction in connection.execute(
            "SELECT article_id, corrected_at, kind FROM manual_corrections"
        ):
            kind = correction["kind"]
            manual_corrections_by_kind[kind] = manual_corrections_by_kind.get(kind, 0) + 1
            prior_jev = [
                decision for decision in decisions_by_article.get(correction["article_id"], [])
                if decision["lane"] == "jev" and decision["decided_at"] <= correction["corrected_at"]
            ]
            cohort = prior_jev[-1]["choice"] if prior_jev else "no_prior_jev_decision"
            manual_feedback_jev_cohort[cohort] += 1
        for outcome in unlinked_outcomes:
            prior_decisions = [
                decision
                for decision in decisions_by_article.get(outcome["article_id"], [])
                if decision["decided_at"] <= outcome["regrouped_at"]
            ]
            if prior_decisions and prior_decisions[-1]["choice"] == "none":
                latest = prior_decisions[-1]
                outcomes_by_decision.setdefault(latest["decision_id"], []).append(outcome)
        candidate_rows = connection.execute(
            "SELECT decision_id, event_id FROM decision_candidates"
        ).fetchall()
        candidates_by_decision: dict[str, set[str]] = {}
        for row in candidate_rows:
            candidates_by_decision.setdefault(row["decision_id"], set()).add(row["event_id"])
        observed_none = 0
        reunited_none = 0
        for decision in none_decisions:
            decision_id = decision["decision_id"]
            decision_outcomes = outcomes_by_decision.get(decision_id, [])
            if not decision_outcomes:
                continue
            observed_none += 1
            shown_event_ids = candidates_by_decision.get(decision_id, set())
            if any(
                outcome["assigned_event_id"] is not None
                and outcome["assigned_event_id"] in shown_event_ids
                for outcome in decision_outcomes
            ):
                reunited_none += 1
        return {
            "schema_version": SCHEMA_VERSION,
            "database_path": str(db_path),
            "decisions": {
                "total": decisions_total,
                "none": decisions_by_choice.get("none", 0),
                "event": decisions_by_choice.get("event", 0),
                "none_rate": decisions_by_choice.get("none", 0) / decisions_total if decisions_total else None,
            },
            "regroup_outcomes": regroup_count,
            "manual_corrections": correction_count,
            "manual_corrections_by_kind": dict(sorted(manual_corrections_by_kind.items())),
            "manual_feedback_jev_cohort": manual_feedback_jev_cohort,
            "provider_lanes": provider_lanes,
            "reunion": {
                "numerator": reunited_none,
                "denominator": observed_none,
                "rate": reunited_none / observed_none if observed_none else None,
                "none_decisions_without_outcome": decisions_by_choice.get("none", 0) - observed_none,
            },
        }
    finally:
        connection.close()


def format_report(report: dict[str, Any]) -> str:
    lines = [f"Fathom research telemetry (schema v{report['schema_version']})"]
    lines.append("Provider calls by lane:")
    if not report["provider_lanes"]:
        lines.append("  (no provider calls recorded)")
    for lane_name, lane in report["provider_lanes"].items():
        error_rate = "n/a" if lane["error_rate"] is None else f"{lane['error_rate']:.1%}"
        latency = lane["latency_ms"]
        latency_text = "n/a" if latency["mean"] is None else (
            f"mean={latency['mean']:.1f}, median={latency['median']:.1f}, p95={latency['p95']:.1f}"
        )
        lines.append(
            f"  {lane_name}: calls={lane['calls']} success={lane['successes']} "
            f"failures={lane['failures']} error_rate={error_rate} latency_ms[{latency_text}]"
        )
        lines.append(
            f"    estimated_bytes={lane['estimated_bytes']} "
            f"(request={lane['request_bytes']} across {lane['request_bytes_calls_measured']} measured calls; "
            f"response={lane['response_bytes']} across {lane['response_bytes_calls_measured']} measured calls)"
        )
        lines.append(f"    reported_tokens={lane['reported_tokens']} models={', '.join(lane['models']) or 'n/a'}")
        hours = list(lane["calls_by_utc_hour"].items())
        if hours:
            lines.append("    calls_by_utc_hour (latest 24): " + ", ".join(f"{hour}={count}" for hour, count in hours[-24:]))
    reunion = report["reunion"]
    reunion_rate = "n/a" if reunion["rate"] is None else f"{reunion['rate']:.1%}"
    lines.extend(
        [
            f"Decisions: total={report['decisions']['total']} none={report['decisions']['none']} "
            f"event={report['decisions']['event']} "
            f"none_rate={report['decisions']['none_rate']:.1%}" if report["decisions"]["none_rate"] is not None else
            f"Decisions: total=0 none=0 event=0 none_rate=n/a",
            f"Regroup outcomes={report['regroup_outcomes']} manual corrections={report['manual_corrections']}",
            "  Manual kinds: " + (
                ", ".join(f"{kind}={count}" for kind, count in report["manual_corrections_by_kind"].items())
                or "none"
            ),
            "  Manual feedback by latest prior Jev choice (selected sample, not error rate): "
            + ", ".join(f"{choice}={count}" for choice, count in report["manual_feedback_jev_cohort"].items()),
            f"None-to-shown-event reunion (any observed regroup outcome): "
            f"{reunion['numerator']}/{reunion['denominator']} = {reunion_rate}",
            f"  None decisions with no recorded regroup outcome are unobserved/censored and excluded "
            f"({reunion['none_decisions_without_outcome']}); a decision counts as reunited if any recorded "
            f"outcome assigns it to an event shown at decision time.",
            "Cost is not calculated; byte/token counts are telemetry, not provider billing estimates.",
        ]
    )
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Fathom research telemetry report")
    parser.add_argument("--db", dest="db_path", help=f"research SQLite path (or set {_ENV_PATH})")
    args = parser.parse_args(argv)
    try:
        print(format_report(build_report(args.db_path)))
    except (TelemetryDatabaseError, TelemetryPathNotConfigured, OSError) as exc:
        print(f"Research telemetry report unavailable: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
