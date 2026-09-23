# Jev-assisted clustering of ungrouped articles

**Status:** design + offline prototype only. No application code, database, deployment, provider, or production behavior was changed.

## Recommendation

Add an optional *proposal* stage to the periodic regroup flow: retrieve a bounded set of likely related ungrouped-article pairs locally; ask Jev only to judge those pairs as `same_story`, `different_story`, or `uncertain`; assemble conservative provisional clusters; then give those compact clusters (and unclustered singletons) to the existing full grouping model. **Only the full grouping model names new events or chooses existing events.** The Jev stage never invents event names, writes assignments, or changes articles. Keep final assignment and event creation behind the existing article-level validation, including the distinct-publisher rule.

This avoids sending every article pair to a model and gives the naming model more useful context than independent singleton articles. It is a shadow-first design, not a recommendation to switch production routing now.

## What the current code actually does

The following are repository observations, not assumptions about a remote service:

- `app/grouping/engine.py::assign_new_articles_with_jev` fetches the live ungrouped window, builds active/cooling event summaries and article payloads, and starts one `classifier.classify(article, event_payload)` task per article. Provider failures leave those articles unapplied; valid assignments are applied through `_apply_live(..., create_new_events=False)`.
- `app/grouping/jev_classifier.py::JevClassifier` currently handles **existing-event assignment plus importance**, not article-to-article clustering. It locally token-ranks active/cooling events, limits/trims the event choices to a byte budget, sends two choice questions (`destination`, `importance`), and strictly parses those two answers. Existing unit tests use an injected fake transport; they do not establish that the remote endpoint accepts a new question or payload.
- `app/grouping/engine.py::regroup_uncategorized` fetches up to 100 ungrouped articles with `include_processed=True`, including earlier Jev `none`/low-confidence items. It batches them using `REGROUP_BATCH_SIZE`, passes article payloads plus active/cooling event payloads to `build_regroup_prompt`, and applies the full model's article-level `assignments` through `_apply_regroup_inner`. Two or more `new` assignments sharing an event name can create an event, subject to distinct-source validation; singleton/same-source cases remain proposals. A later dedup pass follows.
- `app/grouping/prompts.py` is a JSON prompt formatter. The substantive default regroup instructions are in `app/config.py::DEFAULT_REGROUP_PROMPT`.
- `app/grouping/jev_classifier.py::_rank_events` retrieves **events for one article**. It does not retrieve likely related **articles**; that is the new local step proposed here.

## Proposed flow and boundaries

1. **Keep current live Jev behavior unchanged.** It may confidently assign articles to existing events. The cluster stage is for still-ungrouped rows encountered by regroup, including processed-but-unassigned inbox rows.
2. **Build compact local retrieval records** from each ungrouped article: ID, title, source, publication date, and bounded snippet. Never use `expected_cluster`/evaluation labels in retrieval. Treat article text as untrusted reference data.
3. **Retrieve bounded article pairs locally.** Use a token inverted index over title + snippet, ignore configured high-frequency tokens, score/rank pairs deterministically, and cap neighbors per article. The target is at most `K × N` unique candidate pairs, not `N × (N−1) / 2` provider decisions. Preserve candidate scores and shared-term evidence for audit, but do not let lexical overlap itself create a cluster.
4. **Ask Jev pairwise, not for names.** For each retrieved pair ask a single, narrow relationship question. Map the response to the internal typed decision below. A missing, malformed, timed-out, low-confidence, or circuit-open response is `uncertain` for clustering purposes; it must not become a negative or a match.
5. **Form conservative provisional clusters.** Accept only `same_story` edges above a separately configured/calibrated threshold. Use complete-link merging: two components merge only when every cross-component pair has an accepted `same_story` decision. Unretrieved and `uncertain` pairs block a merge. This avoids a single bridge article merging two otherwise unrelated stories. Leave all remaining articles as singleton items.
6. **Send compact items to the existing full grouping model.** Include each provisional cluster as a compact context item with member article IDs and bounded member evidence; include singletons too. Include the existing active/cooling event choices. Require an article-level final assignment for every input ID exactly once, so the full model can still split a provisional cluster, match its members to an existing event, name a new event, or leave them uncategorized. **The full model alone supplies canonical event names.**
7. **Apply through the existing final safeguards.** Keep the existing `_apply_regroup_inner`/event lifecycle/summary-outbox path; validate IDs and output coverage before apply; retain `_articles_have_distinct_sources`. No partial article assignment is applied from Jev. A Jev failure should fall back to the current full-model regroup path or leave the batch untouched, never silently discard work.

For clusters of size `m`, complete-link needs up to `m(m−1)/2` relationship decisions. That is bounded by retrieval caps, not avoided within a proposed cluster. A conservative cap can split a real cluster, so candidate recall is a primary gate.

## Internal typed decision contract (proposed)

This is an application-side schema, **not a claim that Jev emits JSON Schema or accepts these exact fields**. The adapter can map the service's choice answer into this normalized record:

```json
{
  "schema_version": 1,
  "left_article_id": 101,
  "right_article_id": 102,
  "relation": "same_story",
  "confidence": 0.96,
  "reason": "Both report the same Geneva ceasefire negotiation round.",
  "evidence": ["ceasefire", "Geneva", "negotiators"],
  "retrieval_score": 0.58
}
```

Validation requirements:

- IDs are distinct IDs from the retrieved batch; normalize pair ordering for deduplication.
- `relation` is exactly `same_story | different_story | uncertain`.
- `confidence` is finite and in `[0,1]`; if the provider includes a selected-choice probability, use the lower of that and its reported confidence, as the current classifier does.
- `evidence` and `reason` are bounded, optional explanatory fields; they never override the relation/confidence gate.
- Reject duplicate, unknown, omitted, or malformed answers. Treat them as `uncertain` and record a typed error metric.
- `different_story` is useful evaluation data but is not needed to form clusters; neither low confidence nor provider failure implies it.

A proposed SystemOne-style choice payload would ask one `relationship` choice among `same story`, `different stories`, and `insufficient evidence`, with both article records as bounded state. Option IDs should be mapped to the normalized enum locally. This is only a sketch based on the choice-shaped request/response handled in current code; it is **not provider-contract verified**. Do not modify `_parse_response` to accept it until a separate adapter contract test exists.

## Compact naming input example

The full grouping model sees clusters as context, but must return final assignments for member article IDs. Omit long scraped text; cap title/snippet lengths and total request bytes. Example input fragment:

```json
{
  "existing_events": [
    {"id": 7, "name": "Ceasefire negotiations", "description": "...", "recent_titles": ["..."]}
  ],
  "provisional_clusters": [
    {
      "item_id": "cluster-001",
      "member_article_ids": [101, 102, 103],
      "member_articles": [
        {"id": 101, "title": "Ceasefire talks resume in Geneva", "source": "Northstar", "published_date": "2026-09-22T08:00:00Z", "snippet": "Negotiators returned to Geneva..."},
        {"id": 102, "title": "Negotiators resume Geneva ceasefire talks", "source": "City Wire", "published_date": "2026-09-22T08:30:00Z", "snippet": "A new round of talks began..."},
        {"id": 103, "title": "Ceasefire negotiators meet again in Geneva", "source": "World Desk", "published_date": "2026-09-22T09:00:00Z", "snippet": "Officials said the meeting..."}
      ],
      "provisional_relation": "same_story",
      "retrieval_evidence": ["ceasefire", "Geneva", "negotiators"]
    }
  ],
  "singletons": [
    {"item_id": "article-104", "member_article_ids": [104], "member_articles": [{"id": 104, "title": "...", "source": "...", "published_date": "...", "snippet": "..."}]}
  ]
}
```

The required full-model output stays article-addressable, for example `{"assignments":[{"article_id":101,"decision":"new","event_name":"Geneva ceasefire talks","confidence":0.94,"importance_score":0.7}, ...]}`. Require exact once-only coverage before applying. The model may choose different destinations/names for members if the provisional cluster was wrong. Names remain subject to the current distinct-source and event-creation rules.

## Candidate retrieval design

**Prototype baseline:** stdlib-only inverted index over normalized title/snippet tokens; stopword removal; ignore postings above a configurable frequency cap; accumulate candidate pairs from retained postings; rank by deterministic token overlap/Jaccard with ID tie-breaks; retain up to `K` neighbors per article (union of either endpoint's top-K list). The prototype uses `max_posting_size=24`, `max_neighbors=8` as demo values, not production tuning.

**Production candidate options to compare offline:**

- Inverted-index BM25/TF-IDF over title + snippet: no embedding/provider requirement, easy to audit, good first baseline.
- SQLite FTS5 for persisted/local retrieval: operationally convenient but requires validating tokenizer/index lifecycle and safe DB migration/maintenance.
- Local embeddings/ANN only if the lexical baseline fails candidate-recall gates; adds model/storage/versioning complexity and should be benchmarked on the same labeled set.

Hard caps matter: skip very common terms, cap per-article candidates, deduplicate pairs, and enforce request-byte/concurrency/deadline budgets. Monitor high-frequency-token skips and candidate recall so performance controls do not silently hide true pairs. With `N` articles, a strict per-article neighbor cap bounds provider pair judgments by `O(NK)`; index work is driven by retained posting lists. Worst-case pathological dense data still needs explicit posting/candidate caps.

## Evaluation and rollout gates

Create a small manually adjudicated set from a read-only copy/export only after approval; do not use the live DB as the prototype fixture. Keep article text, labels, candidate lists, Jev decisions, and final LLM output separately so each stage can be scored.

**Retrieval:** candidate-pair recall for gold same-story pairs (primary), candidate pairs/article (provider workload), candidate reduction vs all pairs, posting-pair emissions, recall by source/event age/topic, and percentage of gold pairs suppressed by frequency/candidate caps.

**Jev pair judge / clustering:** pairwise precision, recall and F1; false-merge count/rate (higher harm than a missed cluster); `uncertain` and malformed/error rates; cluster pairwise F1 or B-cubed precision/recall/F1; cluster-size distribution; stability when article order changes.

**Final grouping:** event-name human acceptance, correct existing-event match, cross-source event rate, singleton/proposal rate, false event creation, article-ID coverage/duplicate/missing rate, and comparison with the current full-model-only baseline.

**Operations:** Jev requests per batch, p50/p95 latency, timeout/circuit-open/provider-error rate, bytes/request, deadline behavior, full-model input tokens/bytes, total cost, and fallback rate. Track by batch; do not log full article bodies by default.

Suggested gates (tune on a labeled set before any online shadow):

1. Offline retrieval recall ≥ 0.95 on adjudicated positive pairs, while keeping mean candidates/article ≤ 8 and showing a material reduction from all-pairs; disclose any segment below threshold.
2. Shadow pairwise precision ≥ 0.95 and cluster false merges = 0 on the release set; recall is measured and improved without relaxing the precision gate. Confidence threshold must be calibrated, not inherited blindly from `JEV_MIN_CONFIDENCE`.
3. Full-model-only vs clustered replay has no material decline in human-approved assignment quality; exact input coverage and all current same-source/event lifecycle tests pass.
4. Online shadow is read-only, sampled/rate-limited, has request/cost budget and a kill switch, and has no DB assignment/event/summary side effects. Start only after confirming provider payload/retention/cost expectations. Compare to baseline before enabling any application of output.
5. Apply only after explicit review and a separate implementation/change approval. Preserve instant rollback to current `regroup_uncategorized` behavior.

## Exact implementation tasks and acceptance

No tasks below were implemented in application code in this turn.

1. **Add article candidate retriever** — create `app/grouping/article_candidates.py`; add `tests/test_article_candidates.py`. Accept only article ID/title/snippet records, deterministic output/order, per-token posting and per-article neighbor caps, no DB/provider imports. Tests: repeated/common-token stress cap, deterministic ties/order, candidate recall fixture, no self-pairs/duplicate pairs, candidate count bounded by `N*K`.
2. **Add typed Jev relationship adapter** — create `app/grouping/jev_relationship.py`; add `tests/test_jev_relationship.py`. Map a verified Jev choice payload to the internal pair-decision enum; validate unknown options, probability/confidence, duplicate IDs, timeout/error behavior. Acceptance requires a documented/verified remote contract or a captured, user-approved integration test; injected fake transport tests alone are not live verification.
3. **Add conservative cluster assembly** — create `app/grouping/provisional_clusters.py`; add `tests/test_provisional_clusters.py`. Confidence-gated complete-link merge, stable IDs/order, ambiguous edges block merges, all articles emitted exactly once as cluster member or singleton. Test transitive-bridge rejection and input-order invariance.
4. **Add compact full-model prompt path** — modify `app/grouping/prompts.py` and the appropriate grouping engine call site only after tasks 1–3 pass; add `tests/test_grouping_clusters.py`. The model receives bounded cluster/singleton member details and existing events; its final output remains article-ID assignments. Validate exact coverage before `_apply_regroup_inner`; preserve distinct-source, summary-outbox, dedup, expiry, and fallback behavior. Acceptance: existing tests pass plus cluster split/existing-match/new-name/uncategorized and malformed-output no-write tests.
5. **Add shadow instrumentation/configuration** — modify `app/config.py`, `app/tasks.py` and/or a dedicated service only after explicit approval; add `tests/test_grouping_shadow.py`. Disabled by default; no article/event writes, bounded sample/request/cost/deadline, metrics and kill switch; provider/circuit failures preserve current regroup outcome.
6. **Run offline and shadow evaluation** — create `experiments/jev-clustering/evaluation.md` for an approved labeled set; do not commit private article text/credentials. Acceptance: publish stage-wise metrics and baseline comparison, meet gates above, review contract/cost/data handling, then request separate approval for any apply-mode work.

## Verification status / open questions

**Verified locally:** source code and tests show the current Jev classifier's existing-event/importance behavior, choice-style payload construction, strict two-answer parser, fake-transport test coverage, current live route, and regroup fetch/batching/article-level assignment/apply safeguards. The new no-provider prototype is a separate offline demo with deterministic fixture retrieval and rule-based pair decisions.

**Not verified for this clustering design:** the clustering prototype made no live Jev request. A separate synthetic microbatch probe (`experiments/jev-microbatch/`) confirmed that multiple keyed questions return valid answers, but it did not test an article-pair relationship question. There is no remote response yet for the proposed `relationship` contract, nor evidence of probability calibration, data-retention terms, rate limits or production latency/cost. The current `JevClassifier` parser cannot consume a relationship-only answer. The prototype does not test a real Jev clustering model, full grouping LLM, database integration, or production article retrieval.

**Open design questions:** Is one pair judgment per candidate acceptable, or does confirmed Jev support make a multi-pair/request formulation possible? Should we add an explicit local FTS index, or is the regroup cap of 100 small enough that an in-memory index is sufficient? What manually labeled sample and false-merge risk threshold should be approved before shadow evaluation?
