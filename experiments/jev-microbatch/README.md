# Jev multi-article microbatch spike

**Run date:** 2026-09-22

**Scope:** Disposable synthetic-data experiment only. No production classifier method, deployment, or database was invoked or changed. The dedup probe reproduced the request shape read from the current `JevClassifier.assess_duplicate` implementation; it did not call that method.

## Official contract checked

OpenCode Zen documents `jev-1.13` at `https://opencode.ai/zen/v1/systemone` and says requests can contain multiple typed questions, with answers keyed by question name. TypeSafe says questions are evaluated independently/in parallel against the same shared `state`. A Choice answer includes its selected choice, confidence, and option-keyed probabilities. This confirms the request structure is supported; it does **not** by itself prove separate article subobjects will be honored independently. Sources: [OpenCode Zen / Jev](https://opencode.ai/docs/zen#jev), [TypeSafe introduction](https://docs.typesafe.ai/introduction), [Choice question and response contract](https://docs.typesafe.ai/primitives/choice).

## Experiment design

`microbatch.py` uses three fictional articles and three shared fictional event candidates. Each article gets distinct `<article_id>_destination` and `<article_id>_importance` choice questions that reference that article's own field in the shared state. The test sent three singleton baselines, one 2-article request, and one 3-article request. A final, sixth call tested the current dedup gate's `duplicate_relation` payload with two synthetic events and the three choices `same` / `related` / `unrelated`.

The optional malformed-question probe was not sent; it is mutually exclusive with the sixth-call dedup probe so the harness cannot exceed six requests. The response validator checks exact answer keys, type/choice membership, exact probability keys, finite `[0,1]` values, sums within `0.02` of one, maximum-probability choice consistency, confidence range, and usage fields. It suppresses raw response bodies and credentials.

## Measured results

A mode-0600 OpenCode CLI auth-store entry was available. The key was loaded and used in-process; its value was never printed. **Six authenticated inference requests** returned HTTP 200. All 17 keyed answers passed the structural validator.

| Case | Articles/events | Questions | Request bytes | Input/output tokens | Latency | Result |
|---|---:|---:|---:|---:|---:|---|
| Single a01 | 1 article | 2 | 1,793 | 824 / 118 | 1,992 ms | destination `o001`, importance `high`; valid |
| Single a02 | 1 article | 2 | 1,797 | 825 / 118 | 1,066 ms | destination `o002`, importance `high`; valid |
| Single a03 | 1 article | 2 | 1,807 | 821 / 118 | 1,061 ms | destination `o003`, importance `medium`; valid |
| Batch 2 | 2 articles | 4 | 2,893 | 1,179 / 233 | 1,053 ms | all four choices matched singleton labels |
| Batch 3 | 3 articles | 6 | 4,003 | 1,530 / 348 | 966 ms | all destinations matched; a03 importance changed `medium` → `high` |
| Dedup pair | 2 events | 1 three-way choice | 1,078 | 591 / 43 | 1,062 ms | valid `unrelated`, confidence 0.31 |

Across batch-vs-single comparisons, 9 of 10 choices matched (five destination and five importance comparisons). All 5 destination comparisons matched. The one label difference was a03 importance in the three-article batch; with one sample per condition, this cannot distinguish model variability from cross-item influence. Total usage was 5,770 input and 978 output tokens; mean request latency was 1,200 ms. The dedup probe used a related actor but distinct development (Northwind recall vs. Northwind earnings); Jev chose `unrelated` with low confidence (0.31). This is an uncertain semantic result, not a confident match. The existing gate sends `same` or confidence below 0.95 to the full-model review, so this probe would not itself trigger an automatic merge.

Local validator self-test also passed, including rejection of a missing answer. No malformed-question provider test was made, so partial-success/failure isolation is still unknown.

## Verdict: PARTIAL — request format works; rollout not yet validated

Requests configured for Jev 1.13 returned HTTP 200 and the full response contract validated for 2–3 separate articles. The 2-article labels exactly matched singleton labels. The 3-article request kept all destination labels stable, but one importance label differed. This is promising for a small shadow-mode trial, **not enough evidence to assume semantic isolation or change production behavior**. The dedup payload shape also received a valid typed response, but its low-confidence label was not the intended `related` outcome. Malformed-question failure isolation remains untested.

## Conditional implementation plan

Only proceed after a larger shadow comparison confirms acceptable agreement:

1. Add a separate `classify_many` path for at most 2–3 articles. Keep single-article classification unchanged and emit distinct article-keyed destination/importance questions over one shared event context.
2. Validate each exact question key and probability structure independently. Mark missing/invalid article answers unresolved; retry/fallback per article rather than discarding other valid results.
3. In shadow mode compare batched outputs to singleton outputs over representative sanitized samples; measure destination agreement, importance agreement, confidence, invalid-answer rate, latency, request bytes, and input tokens. Repeat tests to separate stochastic variation from cross-item effects.
4. Preserve dedup behavior: treat `same` as a candidate for full-model review; retain the current low-confidence review fallback. Do not merge directly from a Jev response.
5. Keep assignments/merges unwritten until the shadow comparison, malformed-question behavior, fallback handling, and request limits are reviewed.

## Re-run

Dry run (no network):

```bash
python3 microbatch.py
```

Set `OPENCODE_API_KEY`, `OPENCODE_ZEN_API_KEY`, or `JEV_API_KEY` in the environment (or a supported local `.env` file), then run either sixth-call probe:

```bash
python3 microbatch.py --live --probe-dedup
python3 microbatch.py --live --probe-malformed
```

Each live command makes five baseline requests plus at most one selected probe (six total). The optional probes are mutually exclusive. Jev input usage may be billable; check current OpenCode Zen pricing before another run. Use only synthetic text and never put credentials in command-line arguments or logs.
