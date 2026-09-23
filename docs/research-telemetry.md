# Research telemetry (optional)

`app.research.telemetry` writes metadata-only research observations to a SQLite file that is independent of the application database. It uses only the Python standard library. The telemetry path is opt-in: set `FATHOM_RESEARCH_DB_PATH` to an absolute path, or pass an explicit path to each API call. With no configured path, record functions return `False` without writing; report/initialize calls fail clearly. Never point it at `stories.db`.

Example local setup and report:

```bash
export FATHOM_RESEARCH_DB_PATH=/path/to/fathom-research.sqlite3
python3 -m app.research.telemetry
```

Or initialize/report without the environment variable:

```bash
python3 -c 'from app.research.telemetry import initialize; initialize("/path/to/fathom-research.sqlite3")'
python3 -m app.research.telemetry --db /path/to/fathom-research.sqlite3
```

## What is stored

- Completed Jev choices: idempotent decision ID, article ID, timestamp, `none`/`event` choice, chosen event ID where applicable, destination confidence, lane/model metadata, and the ordered event IDs shown in that exact request.
- Regroup outcomes: article ID, regroup-run ID, timestamp, and final assigned event ID (or null). `(article_id, regroup_run_id)` is idempotent. If `decision_id=None`, the writer checks the latest earlier decision of *either* choice for that article and associates the outcome only when it was `none`; a later `event` decision must not make an old `none` appear reunited. The grouping thread does not query telemetry SQLite.
- Manual corrections: correction ID, article ID, original/corrected event IDs, correction kind, timestamp, and optional decision ID.
- Provider-call metadata per lane: call ID, model, latency, measured/requested request and response byte counts, optional token counts, success, and exception class name only.

No API accepts article text, titles, URLs, prompt/response bodies, API keys, arbitrary provider error messages, or free-text feedback notes. Do not add such fields. The telemetry stores only identifiers and aggregates/measurements, not provider payloads.

## Write behavior and database safety

Record functions validate scalar metadata and enqueue to a bounded daemon-worker queue; the grouping/event loop does not open or write SQLite. A `True` return means the item was queued, not durably committed. Disabled configuration, queue saturation, SQLite contention/I/O errors, invalid metadata, or process exit before queued writes drain can lose telemetry; none of these conditions should fail grouping. `flush(timeout=...)` is intended for tests or deliberate shutdown/report handoff, not the grouping hot path. `dropped_write_count()` exposes losses in the current process only.

`initialize(path)` creates a new empty SQLite file and sets `application_id` plus `user_version=1`. Concurrent first-time initializers recheck an empty file under a SQLite write lock so only one creates the schema. It refuses a path whose resolved filename is `stories.db`, non-empty/foreign databases, validates the exact telemetry table set and columns, and does not migrate unknown schema versions. `build_report(path)` opens SQLite read-only and validates the same identifiers/version. The CLI never initializes or creates a database. This is separate-file protection, not a substitute for choosing a dedicated path.

Request/response byte values are supplied by the integration caller. Record exact serialized/request-body byte counts where the provider transport exposes them; otherwise pass only a clearly measured application-level size or `None`. The report labels the sum estimated bytes. It reports available tokens but does not infer or calculate cost.

## Integration status and remaining hooks

**Wired and opt-in:** `JevClassifier.classify()` records destination choices, exact shown-event IDs after trimming, destination confidence and Jev request metadata; `assess_duplicate()` uses a separate `jev_dedup` provider lane. The retry helper records individual grouping/regroup attempts, the summary stream records each stream attempt, and full-model dedup has a `regroup_dedup` lane. Regroup records committed article/event outcomes after dedup, and add/remove/move/merge/split record manual feedback after successful commit. All writes are opt-in through `FATHOM_RESEARCH_DB_PATH`; deployed environments without that path collect nothing. The Ania deployment uses a separate research path while keeping Jev dedup disabled.

**Still to add:** exact Jev HTTP response-byte measurement, chat/recluster lane hooks, calibrated provider prices and dollar cost, summary coalescing counters, and controlled shutdown flushing. Grouping/summary response byte values currently represent the UTF-8 bytes of generated application text (not the complete HTTP response); Jev response bytes remain unknown. Token counts are recorded only when supplied by the provider. This report does **not** estimate money.

The positions below document the intended integration boundaries and remaining work; the already-wired hooks can be used as references.

All positions below refer to the current functions/anchors; retain these hooks when surrounding code moves. Import the helpers from `app.research.telemetry`. Generate stable UUID call/decision/correction IDs at the integration boundary; do not include them in provider payloads.

### Jev decision snapshot and provider call

1. In `app/grouping/jev_classifier.py`, `JevClassifier.classify()`, immediately after `payload, option_events = self._build_payload(article, events)`, snapshot candidate IDs from the returned `option_events` values, excluding the `None` value for the `none` choice. This is the final candidate set after request-size trimming; do not use the untrimmed `events` argument.
2. In the same `classify()` method, immediately after `_parse_response(raw, set(option_events))` succeeds and **before** the minimum-confidence/ungrouped return branches, record the actual model decision:
   - `option_events[destination["choice"]] is None` → `choice="none"`.
   - Otherwise → `choice="event"` and pass that ID as `chosen_event_id`, even if confidence later keeps the article unassigned. The destination confidence is `destination["confidence"]`, not the separate importance confidence.
   - This distinction matters because `_ungrouped_assignment()` also handles a low-confidence event choice; its returned `decision="uncategorized"` alone cannot reconstruct Jev's actual choice.
   - Example: `record_decision(uuid, article["id"], choice, destination["confidence"], shown_event_ids, chosen_event_id=..., lane="jev", model=self.model)`.
3. In `classify()`, bracket the actual `self.transport(...)` call inside the existing `try`/`except` to record one provider-call row on both success and transport failure. Use a fresh call ID per attempt, elapsed milliseconds, `error_type=type(exc).__name__` (never `str(exc)`), and safe scalar usage counts only when the response reports them. Count the encoded request at `_serialize_payload(payload)`. For exact response bytes, measure `len(body)` in `_post_json()` immediately after `response.read(...)` and return/propagate that integer to `classify()`; do not store `raw` or re-serialize it as if it were the HTTP body. The current transport contract returns `(raw, latency_ms)`, so carry the additional byte count as scalar metadata without persisting payload data. Circuit-open rejections before transport are not provider calls.
4. Apply the same provider-call hook around `JevClassifier.assess_duplicate()`'s transport call if duplicate-check traffic should be included; give it a distinct lane such as `jev_dedup` so it does not inflate classification counts.

### Full grouping, regroup, and summary provider lanes

- In `app/grouping/engine.py`, instrument each actual attempt of `_agenerate_with_retry()` at the `llm.agenerate(messages)` call. The helper is used by live grouping (`_assign_chunk`) and `regroup_uncategorized()`; pass a lane argument from those call sites (`grouping` versus `regroup`) so retry attempts and failures are counted accurately. Do not store `messages`, prompts, or response text. Use token usage metadata only if present.
- In `app/grouping/summarizer.py`, instrument each actual `llm.astream([HumanMessage(content=prompt)])` call inside `_stream_full_text()`, including retries. Use lane `summary`; count prompt UTF-8 bytes locally only as an application-level size if no transport byte metric is available. Accumulate output byte counts as measurements only if their meaning is documented; otherwise store `None`. Never submit streamed chunks or summaries to telemetry.
- If tracking dedup provider traffic, instrument the `llm.agenerate(...)` call inside `app/grouping/dedup.py::dedup_events()` as a distinct `regroup_dedup` lane. The telemetry API is lane-agnostic.

### Regroup outcomes

In `app/grouping/engine.py::regroup_uncategorized()`:

1. Generate one `regroup_run_id` at function entry.
2. Track the article IDs whose regroup batch returned valid, parsed assignments. Do not report failed/timed-out/unparsed batches as completed observations.
3. After the dedup pass and the final article/event reconciliation (the block ending where `event_increments` is replaced with `defaultdict(list, current_increments)`), read the final `Article.event_id` values for those observed IDs. Enqueue one `record_regroup_outcome(None, regroup_run_id, article_id, final_event_id, regrouped_at=...)` per article. `None` lets the telemetry writer join it to the latest prior Jev `none` decision by article ID/time; if an exact decision ID has been carried safely by the integration, pass it instead. A null `final_event_id` is a recorded completed regroup result, not a missing outcome.

### Manual corrections

In `app/routers/events.py`, the two correction write paths are `add_article_to_event()` (the `record_correction_in_session(...)` call followed by `db.commit()`) and `remove_article_from_event()` (same pattern). Capture the returned `GroupingFeedback` row; only after `db.commit()` succeeds, enqueue `record_manual_correction(correction_id=str(feedback.id), article_id=..., original_event_id=..., corrected_event_id=..., kind=...)`. This avoids counting a correction that the application transaction rolled back. Do not send `note` or any other user text.

## Report interpretation

The CLI reports per-lane call/success/failure counts, error rate, mean/median/p95 latency, measured request/response byte sums and how many calls supplied each, and provider-reported token totals. It breaks down manual corrections by kind and by the latest earlier recorded Jev choice (`none`, `event`, or no prior decision). Manual edits are a **selected sample**, not a denominator for an automated-classification error rate; timestamps and missing telemetry may also limit attribution. It explicitly does not estimate money/cost.

`None-to-shown-event reunion` is computed over unique Jev `none` decisions with at least one linked/temporally associated completed regroup outcome. The numerator is the subset with any recorded outcome assigned to an event ID in that decision's saved candidate snapshot. Decisions with no recorded regroup outcome are reported separately and excluded as unobserved/censored (for example, not yet regrouped, a failed batch, or telemetry loss). Thus the displayed rate is not a rate over every `none` decision and can be biased by incomplete observation. It also measures event-ID reunion, not semantic equivalence after a later event merge.

## Verification

```bash
python3 -m unittest tests.test_research_telemetry -q
```
