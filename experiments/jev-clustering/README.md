# Offline Jev-clustering prototype

A stdlib-only fixture demo of the proposed pipeline. It makes **no provider calls**, reads no app database, and does not import or change application code. The pair judge is a deterministic title-overlap rule—not Jev and not a quality claim.

From this directory:

```bash
python3 cluster_demo.py
python3 -m unittest -v test_cluster_demo.py
```

Or from the repository root:

```bash
python3 experiments/jev-clustering/cluster_demo.py
python3 -m unittest discover -s experiments/jev-clustering -p 'test_*.py' -v
```

The JSON output contains candidate-retrieval counts, normalized typed pair decisions, provisional clusters/singletons, fixture-only metrics, and a bounded `compact_naming_input` example for a later full grouping model. The fixture labels are only read by the evaluator; retrieval, judging, and cluster assembly do not use them.

Current demo fixture: 8 articles, 28 possible pairs, 7 retrieved candidates (75% fewer pair judgments), 2 provisional clusters, 3 singletons, candidate recall 1.0, pair precision/recall/F1 1.0, and zero false-merge pairs. These are tiny fixture results, not representative live performance.

For the design, current-code observations, proposed decision contract, rollout gates, and exact implementation tasks, see [`../../docs/jev-clustering-plan.md`](../../docs/jev-clustering-plan.md).
