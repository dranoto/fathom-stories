from __future__ import annotations

import argparse
import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable


_STOP_WORDS = frozenset(
    "a an and are as at be been by for from has have in into is it its of on or that the their this to was were with after before again another around says said according Tuesday Wednesday Thursday Friday Monday today latest new news report reports update updates reaches reached begins began focus focused opens opened officials residents leaders lawmakers storm morning early coast round also amid top".split()
)
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_CHOICE_TO_RELATION = {
    "o000": "same_story",
    "o001": "different_story",
    "o002": "uncertain",
}


@dataclass(frozen=True)
class Article:
    id: int
    title: str
    source: str
    published_date: str
    snippet: str
    expected_cluster: str | None = None


@dataclass(frozen=True)
class Candidate:
    left_article_id: int
    right_article_id: int
    shared_terms: tuple[str, ...]
    retrieval_score: float


@dataclass(frozen=True)
class PairDecision:
    schema_version: int
    left_article_id: int
    right_article_id: int
    relation: str
    confidence: float
    reason: str
    evidence: tuple[str, ...]
    retrieval_score: float


class Relation(str, Enum):
    SAME_STORY = "same_story"
    DIFFERENT_STORY = "different_story"
    UNCERTAIN = "uncertain"


def tokenize(text: str) -> frozenset[str]:
    return frozenset(
        token
        for token in _TOKEN_RE.findall(text.casefold())
        if len(token) > 1 and token not in _STOP_WORDS
    )


def _article_tokens(article: Article) -> frozenset[str]:
    return tokenize(f"{article.title} {article.snippet}")


def _jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    left_set = set(left)
    right_set = set(right)
    union = left_set | right_set
    return len(left_set & right_set) / len(union) if union else 0.0


def retrieve_candidates(
    articles: list[Article],
    *,
    max_posting_size: int = 24,
    max_neighbors: int = 8,
) -> tuple[list[Candidate], dict[str, int | float]]:
    """Generate a bounded candidate-pair set from a deterministic inverted index."""
    if max_posting_size < 2:
        raise ValueError("max_posting_size must be at least 2")
    if max_neighbors < 1:
        raise ValueError("max_neighbors must be at least 1")

    by_id = {article.id: article for article in articles}
    if len(by_id) != len(articles):
        raise ValueError("article IDs must be unique")

    postings: dict[str, list[int]] = defaultdict(list)
    tokens_by_id = {article.id: _article_tokens(article) for article in articles}
    for article_id in sorted(by_id):
        for token in sorted(tokens_by_id[article_id]):
            postings[token].append(article_id)

    shared_by_pair: dict[tuple[int, int], set[str]] = defaultdict(set)
    posting_pair_emissions = 0
    high_frequency_tokens_skipped = 0
    for token, article_ids in sorted(postings.items()):
        if len(article_ids) > max_posting_size:
            high_frequency_tokens_skipped += 1
            continue
        for left_id, right_id in combinations(article_ids, 2):
            shared_by_pair[(left_id, right_id)].add(token)
            posting_pair_emissions += 1

    ranked_by_article: dict[int, list[tuple[float, int, Candidate]]] = defaultdict(list)
    for (left_id, right_id), shared in shared_by_pair.items():
        union = tokens_by_id[left_id] | tokens_by_id[right_id]
        score = len(shared) / len(union) if union else 0.0
        candidate = Candidate(
            left_article_id=left_id,
            right_article_id=right_id,
            shared_terms=tuple(sorted(shared)),
            retrieval_score=score,
        )
        ranked_by_article[left_id].append((-score, right_id, candidate))
        ranked_by_article[right_id].append((-score, left_id, candidate))

    selected_pairs: set[tuple[int, int]] = set()
    for article_id, ranked in ranked_by_article.items():
        ranked.sort(key=lambda item: (item[0], item[1]))
        selected_pairs.update(
            (item[2].left_article_id, item[2].right_article_id)
            for item in ranked[:max_neighbors]
        )

    candidates = [
        Candidate(
            left_article_id=left_id,
            right_article_id=right_id,
            shared_terms=tuple(sorted(shared_by_pair[(left_id, right_id)])),
            retrieval_score=(
                len(shared_by_pair[(left_id, right_id)])
                / len(tokens_by_id[left_id] | tokens_by_id[right_id])
                if tokens_by_id[left_id] | tokens_by_id[right_id]
                else 0.0
            ),
        )
        for left_id, right_id in sorted(selected_pairs)
    ]
    possible_pairs = len(articles) * (len(articles) - 1) // 2
    max_unique_pairs = len(articles) * max_neighbors
    stats: dict[str, int | float] = {
        "article_count": len(articles),
        "possible_pairs": possible_pairs,
        "candidate_pairs": len(candidates),
        "candidate_pair_cap": max_unique_pairs,
        "candidate_reduction_ratio": (
            1.0 - len(candidates) / possible_pairs if possible_pairs else 0.0
        ),
        "posting_pair_emissions": posting_pair_emissions,
        "indexed_tokens": len(postings),
        "high_frequency_tokens_skipped": high_frequency_tokens_skipped,
        "max_posting_size": max_posting_size,
        "max_neighbors_per_article": max_neighbors,
    }
    return candidates, stats


def deterministic_pair_judge(
    candidates: list[Candidate], articles: list[Article]
) -> list[PairDecision]:
    """Offline stand-in only; this is not a Jev model or a production classifier."""
    by_id = {article.id: article for article in articles}
    decisions: list[PairDecision] = []
    for candidate in candidates:
        left = by_id[candidate.left_article_id]
        right = by_id[candidate.right_article_id]
        left_title = tokenize(left.title)
        right_title = tokenize(right.title)
        shared_title = tuple(sorted(left_title & right_title))
        title_score = _jaccard(left_title, right_title)
        if len(shared_title) >= 2 and title_score >= 0.30:
            relation = Relation.SAME_STORY.value
            confidence = 0.95
            reason = "Deterministic fixture rule: strong title overlap."
        elif shared_title and title_score >= 0.12:
            relation = Relation.UNCERTAIN.value
            confidence = 0.55
            reason = "Deterministic fixture rule: partial title overlap; defer."
        else:
            relation = Relation.DIFFERENT_STORY.value
            confidence = 0.90
            reason = "Deterministic fixture rule: insufficient title overlap."
        decisions.append(
            PairDecision(
                schema_version=1,
                left_article_id=candidate.left_article_id,
                right_article_id=candidate.right_article_id,
                relation=relation,
                confidence=confidence,
                reason=reason,
                evidence=shared_title,
                retrieval_score=candidate.retrieval_score,
            )
        )
    return decisions


def parse_jev_relationship(
    answer: dict[str, Any], left_article_id: int, right_article_id: int
) -> PairDecision:
    """Validate one normalized choice answer and map it to the internal schema.

    The caller must first extract this answer from a verified provider response.
    This helper does not make a provider request or validate a live endpoint contract.
    """
    if left_article_id == right_article_id:
        raise ValueError("pair decision requires two distinct article IDs")
    if left_article_id > right_article_id:
        left_article_id, right_article_id = right_article_id, left_article_id
    if not isinstance(answer, dict):
        raise ValueError("relationship answer must be an object")
    choice = answer.get("choice")
    if choice not in _CHOICE_TO_RELATION:
        raise ValueError("relationship answer has an unknown choice")
    probabilities = answer.get("probabilities")
    if not isinstance(probabilities, dict) or set(probabilities) != set(_CHOICE_TO_RELATION):
        raise ValueError("relationship probabilities must match all supplied choices")

    parsed_probabilities: dict[str, float] = {}
    for key, value in probabilities.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("relationship probabilities must be numbers")
        number = float(value)
        if not math.isfinite(number) or not 0.0 <= number <= 1.0:
            raise ValueError("relationship probabilities must be finite and in [0, 1]")
        parsed_probabilities[key] = number
    if not math.isclose(sum(parsed_probabilities.values()), 1.0, abs_tol=0.02):
        raise ValueError("relationship probabilities must sum to one")
    if parsed_probabilities[choice] < max(parsed_probabilities.values()):
        raise ValueError("selected relationship choice is not the highest probability")

    reported = answer.get("confidence")
    if isinstance(reported, bool) or not isinstance(reported, (int, float)):
        raise ValueError("relationship confidence must be a number")
    reported_confidence = float(reported)
    if not math.isfinite(reported_confidence) or not 0.0 <= reported_confidence <= 1.0:
        raise ValueError("relationship confidence must be finite and in [0, 1]")

    return PairDecision(
        schema_version=1,
        left_article_id=left_article_id,
        right_article_id=right_article_id,
        relation=_CHOICE_TO_RELATION[choice],
        confidence=min(reported_confidence, parsed_probabilities[choice]),
        reason="Mapped from a validated relationship choice.",
        evidence=(),
        retrieval_score=0.0,
    )


def assemble_complete_link_clusters(
    article_ids: Iterable[int],
    decisions: list[PairDecision],
    *,
    min_confidence: float = 0.90,
) -> tuple[list[tuple[int, ...]], list[int]]:
    """Merge only when all cross-component pairs are accepted same-story pairs."""
    members = sorted(set(article_ids))
    if not 0.0 <= min_confidence <= 1.0:
        raise ValueError("min_confidence must be in [0, 1]")
    decision_by_pair = {
        (min(item.left_article_id, item.right_article_id), max(item.left_article_id, item.right_article_id)): item
        for item in decisions
    }
    groups: dict[int, set[int]] = {article_id: {article_id} for article_id in members}
    owner = {article_id: article_id for article_id in members}
    accepted = sorted(
        (
            decision
            for decision in decisions
            if decision.relation == Relation.SAME_STORY.value
            and decision.confidence >= min_confidence
        ),
        key=lambda item: (
            -item.confidence,
            min(item.left_article_id, item.right_article_id),
            max(item.left_article_id, item.right_article_id),
        ),
    )

    for decision in accepted:
        left_id, right_id = decision.left_article_id, decision.right_article_id
        if left_id not in owner or right_id not in owner:
            continue
        left_root = owner[left_id]
        right_root = owner[right_id]
        if left_root == right_root:
            continue
        left_group = groups[left_root]
        right_group = groups[right_root]
        every_cross_pair_matches = True
        for left_member in left_group:
            for right_member in right_group:
                pair = (min(left_member, right_member), max(left_member, right_member))
                cross_decision = decision_by_pair.get(pair)
                if (
                    cross_decision is None
                    or cross_decision.relation != Relation.SAME_STORY.value
                    or cross_decision.confidence < min_confidence
                ):
                    every_cross_pair_matches = False
                    break
            if not every_cross_pair_matches:
                break
        if not every_cross_pair_matches:
            continue
        new_root = min(left_root, right_root)
        old_root = max(left_root, right_root)
        merged = groups.pop(left_root) | groups.pop(right_root)
        groups[new_root] = merged
        for member in merged:
            owner[member] = new_root
        if old_root == new_root:
            raise AssertionError("cluster roots must be distinct")

    components = sorted((tuple(sorted(group)) for group in groups.values()), key=lambda group: group[0])
    clusters = [component for component in components if len(component) > 1]
    singletons = [component[0] for component in components if len(component) == 1]
    return clusters, singletons


def build_naming_input(
    articles: list[Article],
    clusters: list[tuple[int, ...]],
    singletons: list[int],
    decisions: list[PairDecision],
) -> dict[str, Any]:
    by_id = {article.id: article for article in articles}
    items: list[dict[str, Any]] = []
    ordered_groups = sorted(
        [(tuple(cluster), True) for cluster in clusters]
        + [((article_id,), False) for article_id in singletons],
        key=lambda item: item[0][0],
    )
    cluster_number = 0
    for member_ids, is_cluster in ordered_groups:
        if is_cluster:
            cluster_number += 1
            item_id = f"cluster-{cluster_number:03d}"
        else:
            item_id = f"article-{member_ids[0]}"
        evidence = [
            {
                "left_article_id": decision.left_article_id,
                "right_article_id": decision.right_article_id,
                "relation": decision.relation,
                "confidence": decision.confidence,
                "shared_title_terms": list(decision.evidence),
            }
            for decision in decisions
            if decision.left_article_id in member_ids
            and decision.right_article_id in member_ids
            and decision.relation == Relation.SAME_STORY.value
        ]
        items.append(
            {
                "item_id": item_id,
                "member_article_ids": list(member_ids),
                "member_articles": [
                    {
                        "id": article_id,
                        "title": by_id[article_id].title[:240],
                        "source": by_id[article_id].source[:120],
                        "published_date": by_id[article_id].published_date,
                        "snippet": by_id[article_id].snippet[:240],
                    }
                    for article_id in member_ids
                ],
                "provisional_relation": "same_story" if is_cluster else None,
                "retrieval_evidence": evidence,
            }
        )
    return {"existing_events": [], "items": items}


def evaluate_fixture(
    articles: list[Article], candidates: list[Candidate], clusters: list[tuple[int, ...]]
) -> dict[str, float | int]:
    gold_pairs: set[tuple[int, int]] = set()
    for left, right in combinations(articles, 2):
        if left.expected_cluster and left.expected_cluster == right.expected_cluster:
            gold_pairs.add((min(left.id, right.id), max(left.id, right.id)))
    candidate_pairs = {
        (candidate.left_article_id, candidate.right_article_id) for candidate in candidates
    }
    predicted_pairs = {
        (left_id, right_id)
        for cluster in clusters
        for left_id, right_id in combinations(cluster, 2)
    }
    candidate_true_positives = len(candidate_pairs & gold_pairs)
    true_positives = len(predicted_pairs & gold_pairs)
    candidate_precision = candidate_true_positives / len(candidate_pairs) if candidate_pairs else 0.0
    candidate_recall = candidate_true_positives / len(gold_pairs) if gold_pairs else 0.0
    pair_precision = true_positives / len(predicted_pairs) if predicted_pairs else 0.0
    pair_recall = true_positives / len(gold_pairs) if gold_pairs else 0.0
    pair_f1 = (
        2 * pair_precision * pair_recall / (pair_precision + pair_recall)
        if pair_precision + pair_recall
        else 0.0
    )
    return {
        "gold_positive_pairs": len(gold_pairs),
        "candidate_precision": round(candidate_precision, 4),
        "candidate_recall": round(candidate_recall, 4),
        "pair_precision": round(pair_precision, 4),
        "pair_recall": round(pair_recall, 4),
        "pair_f1": round(pair_f1, 4),
        "false_merge_pairs": len(predicted_pairs - gold_pairs),
    }


def run_pipeline(
    articles: list[Article], *, max_posting_size: int = 24, max_neighbors: int = 8
) -> dict[str, Any]:
    candidates, retrieval_stats = retrieve_candidates(
        articles,
        max_posting_size=max_posting_size,
        max_neighbors=max_neighbors,
    )
    decisions = deterministic_pair_judge(candidates, articles)
    clusters, singletons = assemble_complete_link_clusters(
        (article.id for article in articles), decisions
    )
    return {
        "mode": "offline fixture; deterministic retriever and rule-based judge; no provider",
        "retrieval": retrieval_stats,
        "decisions": [
            {
                "schema_version": item.schema_version,
                "left_article_id": item.left_article_id,
                "right_article_id": item.right_article_id,
                "relation": item.relation,
                "confidence": item.confidence,
                "reason": item.reason,
                "evidence": list(item.evidence),
                "retrieval_score": round(item.retrieval_score, 4),
            }
            for item in decisions
        ],
        "clusters": [list(cluster) for cluster in clusters],
        "singletons": singletons,
        "compact_naming_input": build_naming_input(articles, clusters, singletons, decisions),
        "fixture_metrics": evaluate_fixture(articles, candidates, clusters),
    }


def load_fixture(path: Path) -> list[Article]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [Article(**row) for row in payload["articles"]]


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline Jev-assisted clustering design prototype")
    parser.add_argument(
        "--fixture",
        type=Path,
        default=Path(__file__).parent / "fixtures" / "articles.json",
        help="JSON fixture file (default: bundled articles.json)",
    )
    parser.add_argument("--max-posting-size", type=int, default=24)
    parser.add_argument("--max-neighbors", type=int, default=8)
    args = parser.parse_args()
    articles = load_fixture(args.fixture)
    print(
        json.dumps(
            run_pipeline(
                articles,
                max_posting_size=args.max_posting_size,
                max_neighbors=args.max_neighbors,
            ),
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
