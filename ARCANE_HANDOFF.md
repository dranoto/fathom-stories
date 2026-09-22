# Arcane handoff — grouping outage fix (2026-09-22)

## Symptom
The reader showed only the inbox: `/api/events?status=active` returned `[]`.
RSS fetching was unaffected. The live database had no active events and more than 500 archived events.

## Root cause
The `FreeOnly` combo on `OPENAI_BASE_URL=https://router.latour.live/v1` routed grouping to a reasoning model. On the large grouping prompt it spent the `GROUPING_MAX_OUTPUT_TOKENS=8192` budget on hidden reasoning and never emitted the requested JSON. Scheduled live grouping and regrouping failed until event expiry archived the remaining active events.

Raw endpoint tests with the real prompt showed:
- normal reasoning: timeout or `finish_reason=length` without JSON
- `reasoning_effort: "none"`: fast, valid grouping JSON

## Changes in this commit
- LLM reasoning is configured per lane rather than globally:
  - `GROUPING_REASONING_EFFORT=none`
  - `SUMMARY_REASONING_EFFORT=medium`
  - `CHAT_REASONING_EFFORT=` by default
  - `LLM_REASONING_EFFORT=` remains an optional compatibility fallback
- Summary output headroom is raised to `SUMMARY_MAX_OUTPUT_TOKENS=16384` and the default summary timeout to 300 seconds.
- `initialize_llm()` omits `reasoning_effort` entirely when the lane setting is blank, avoiding provider compatibility failures.
- Grouping, regrouping, deduplication, and reclustering share a defensive response parser that removes complete thinking blocks, rejects unclosed thinking, ignores surrounding prose, and requires a JSON object root.
- Tests cover parser recovery and per-lane reasoning forwarding.
- The deployment bind mounts remain fixed to the authoritative live data paths.

## Authoritative database and mounts
The current production database is:

`/home/thankfulcarp/fathom-stories-local/data/stories.db`

It was verified as the newest healthy copy, with current article and event changes. Do not switch the Arcane project to either of these stale copies:
- `/home/thankfulcarp/fathom-stories/data/stories.db`
- this repository's `data/stories.db`

The canonical compose mounts are therefore:
- `/home/thankfulcarp/fathom-stories-local/data:/app/data`
- `/home/thankfulcarp/fathom-stories-local/logs:/app/logs`

## Jev live-classifier workflow
The live article pass now supports OpenCode Zen SystemOne as a bounded classifier:
- one request per new article
- active/cooling events plus `none` as destination choices
- destination and importance answered in the same Jev request
- confidence below `JEV_MIN_CONFIDENCE` stays ungrouped
- successful `none`/low-confidence decisions are marked processed for the full-model regroup pool
- transport/parse failures remain unprocessed and retry on a later live pass
- the full grouping LLM still performs periodic new-event creation, revival, and deduplication

The production settings are intended to be:
- `JEV_ENABLED=true`
- `JEV_ENDPOINT=https://opencode.ai/zen/v1/systemone`
- `JEV_MODEL=jev-1.13`
- `JEV_MIN_CONFIDENCE=0.90`
- `JEV_MAX_EVENT_CANDIDATES=80`
- `JEV_MAX_REQUEST_BYTES=28000`, a UTF-8 byte ceiling conservatively below Jev's 32k-token context window
- `JEV_FAILURE_THRESHOLD=8` and `JEV_CIRCUIT_COOLDOWN_SECONDS=300`
- `JEV_BATCH_TIMEOUT_SECONDS=120`, the classification-phase deadline before assignment writes and queued summary work

A live read-only probe against the authoritative database classified the newest ungrouped article against six active events and returned event 720 at 0.92 confidence. The project environment must receive `OPENCODE_ZEN_API_KEY` without logging or committing the value.

## Recovered state
A manual regroup against the live database recovered five new events and revived one existing event. The old code remains unsafe until the image and Arcane environment are recreated with the grouping reasoning setting.

## Verification
Run the unit suite before deployment, then verify after recreation:

```bash
python3 -m unittest discover -s tests -v
curl -s localhost:8800/api/events/_stats/all
docker inspect fathom-stories-app-1 --format '{{json .Mounts}}'
docker exec fathom-stories-app-1 printenv GROUPING_REASONING_EFFORT
docker exec fathom-stories-app-1 printenv SUMMARY_REASONING_EFFORT
docker exec fathom-stories-app-1 printenv SUMMARY_MAX_OUTPUT_TOKENS
```

Expected configuration:
- `GROUPING_REASONING_EFFORT=none`
- `SUMMARY_REASONING_EFFORT=medium`
- `SUMMARY_MAX_OUTPUT_TOKENS=16384`
- live database mounted from `/home/thankfulcarp/fathom-stories-local/data`
