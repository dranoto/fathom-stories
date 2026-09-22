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

## Recovered state
A manual regroup against the live database recovered five new events and revived one existing event. The old code remains unsafe until the image and Arcane environment are recreated with the grouping reasoning setting.

## Verification
Run the unit suite before deployment, then verify after recreation:

```bash
python3 -m unittest discover -s tests -v
curl -s localhost:8800/api/events/_stats/all
docker inspect fathom-stories --format '{{json .Mounts}}'
docker exec fathom-stories printenv GROUPING_REASONING_EFFORT
docker exec fathom-stories printenv SUMMARY_REASONING_EFFORT
docker exec fathom-stories printenv SUMMARY_MAX_OUTPUT_TOKENS
```

Expected configuration:
- `GROUPING_REASONING_EFFORT=none`
- `SUMMARY_REASONING_EFFORT=medium`
- `SUMMARY_MAX_OUTPUT_TOKENS=16384`
- live database mounted from `/home/thankfulcarp/fathom-stories-local/data`
