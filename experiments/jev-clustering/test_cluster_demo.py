import unittest
from pathlib import Path

from cluster_demo import (
    Article,
    PairDecision,
    Relation,
    assemble_complete_link_clusters,
    load_fixture,
    parse_jev_relationship,
    retrieve_candidates,
    run_pipeline,
)


FIXTURE = Path(__file__).parent / "fixtures" / "articles.json"


def decision(left, right, relation, confidence=0.95):
    return PairDecision(
        schema_version=1,
        left_article_id=left,
        right_article_id=right,
        relation=relation,
        confidence=confidence,
        reason="test",
        evidence=(),
        retrieval_score=0.5,
    )


class CandidateRetrievalTests(unittest.TestCase):
    def test_fixture_recovers_gold_clusters_and_reduces_pairs(self):
        articles = load_fixture(FIXTURE)
        result = run_pipeline(articles)

        self.assertEqual(result["clusters"], [[101, 102, 103], [201, 202]])
        self.assertEqual(result["singletons"], [301, 401, 402])
        self.assertEqual(result["fixture_metrics"]["candidate_recall"], 1.0)
        self.assertEqual(result["fixture_metrics"]["pair_precision"], 1.0)
        self.assertEqual(result["fixture_metrics"]["false_merge_pairs"], 0)
        self.assertEqual(result["retrieval"]["possible_pairs"], 28)
        self.assertEqual(result["retrieval"]["candidate_pairs"], 7)
        self.assertEqual(result["retrieval"]["candidate_reduction_ratio"], 0.75)

    def test_common_postings_are_skipped_and_neighbor_cap_is_respected(self):
        articles = [
            Article(
                id=index,
                title=f"Aurora sharedtoken bulletin {index}",
                source=f"Source {index}",
                published_date="2026-09-22T00:00:00Z",
                snippet="Every bulletin mentions sharedtoken.",
            )
            for index in range(1, 11)
        ]

        candidates, stats = retrieve_candidates(
            articles,
            max_posting_size=3,
            max_neighbors=1,
        )

        self.assertEqual(candidates, [])
        self.assertGreaterEqual(stats["high_frequency_tokens_skipped"], 2)
        self.assertEqual(stats["posting_pair_emissions"], 0)

    def test_candidate_output_has_no_self_or_duplicate_pairs_and_is_bounded(self):
        articles = [
            Article(
                id=index,
                title=f"Aurora mission launch {index}",
                source=f"Source {index}",
                published_date="2026-09-22T00:00:00Z",
                snippet="Aurora mission launch update.",
            )
            for index in range(1, 9)
        ]

        candidates, stats = retrieve_candidates(
            articles,
            max_posting_size=20,
            max_neighbors=2,
        )
        pairs = [(item.left_article_id, item.right_article_id) for item in candidates]

        self.assertEqual(len(pairs), len(set(pairs)))
        self.assertTrue(all(left < right for left, right in pairs))
        self.assertLessEqual(len(pairs), stats["candidate_pair_cap"])

    def test_duplicate_article_ids_are_rejected(self):
        article = Article(1, "Title", "Source", "", "")
        with self.assertRaisesRegex(ValueError, "unique"):
            retrieve_candidates([article, article])


class DecisionAndClusteringTests(unittest.TestCase):
    def test_choice_parser_normalizes_pair_and_caps_confidence(self):
        parsed = parse_jev_relationship(
            {
                "choice": "o000",
                "confidence": 0.99,
                "probabilities": {"o000": 0.94, "o001": 0.03, "o002": 0.03},
            },
            9,
            2,
        )

        self.assertEqual((parsed.left_article_id, parsed.right_article_id), (2, 9))
        self.assertEqual(parsed.relation, Relation.SAME_STORY.value)
        self.assertEqual(parsed.confidence, 0.94)

    def test_choice_parser_rejects_unverified_or_malformed_choice(self):
        with self.assertRaisesRegex(ValueError, "unknown choice"):
            parse_jev_relationship(
                {"choice": "o003", "confidence": 1, "probabilities": {}},
                1,
                2,
            )
        with self.assertRaisesRegex(ValueError, "sum to one"):
            parse_jev_relationship(
                {
                    "choice": "o000",
                    "confidence": 0.9,
                    "probabilities": {"o000": 0.4, "o001": 0.2, "o002": 0.2},
                },
                1,
                2,
            )

    def test_complete_link_rejects_ambiguous_transitive_bridge(self):
        decisions = [
            decision(1, 2, Relation.SAME_STORY.value),
            decision(2, 3, Relation.SAME_STORY.value),
            decision(1, 3, Relation.UNCERTAIN.value, confidence=0.55),
        ]

        clusters, singletons = assemble_complete_link_clusters([3, 2, 1], decisions)

        self.assertEqual(clusters, [(1, 2)])
        self.assertEqual(singletons, [3])

    def test_cluster_order_is_deterministic_and_all_members_are_accounted_for(self):
        decisions = [
            decision(3, 4, Relation.SAME_STORY.value),
            decision(1, 2, Relation.SAME_STORY.value),
        ]

        clusters, singletons = assemble_complete_link_clusters([4, 2, 3, 1, 5], decisions)

        self.assertEqual(clusters, [(1, 2), (3, 4)])
        self.assertEqual(singletons, [5])
        emitted = [article_id for group in clusters for article_id in group] + singletons
        self.assertEqual(sorted(emitted), [1, 2, 3, 4, 5])


if __name__ == "__main__":
    unittest.main()
