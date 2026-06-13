# app/grouping/prompts.py
import logging
from typing import List, Dict, Any, Optional

from .. import config as app_config

logger = logging.getLogger(__name__)


def build_few_shot_block(feedback_examples: List[Dict[str, Any]]) -> str:
    if not feedback_examples:
        return ""
    lines = ["Recent editor corrections (treat as ground truth):"]
    for ex in feedback_examples:
        kind = ex.get("kind", "move")
        original_event = ex.get("original_event_name") or "(none)"
        corrected_event = ex.get("corrected_event_name") or "(none)"
        note = ex.get("note") or ""
        lines.append(
            f"  - [{kind}] article_id={ex.get('article_id')}: "
            f"from '{original_event}' to '{corrected_event}'"
            + (f" — {note}" if note else "")
        )
    return "\n".join(lines)


def build_group_assign_prompt(
    active_events: List[Dict[str, Any]],
    cooling_events: List[Dict[str, Any]],
    articles: List[Dict[str, Any]],
    few_shot_block: str,
    prompt_template: Optional[str] = None,
) -> str:
    template = prompt_template or app_config.DEFAULT_GROUP_ASSIGN_PROMPT
    import json
    return template.format(
        active_events=json.dumps(active_events, indent=2, default=str),
        cooling_events=json.dumps(cooling_events, indent=2, default=str),
        few_shot_block=few_shot_block,
        articles_json=json.dumps(articles, indent=2, default=str),
    )


def build_recluster_prompt(
    active_events: List[Dict[str, Any]],
    cooling_events: List[Dict[str, Any]],
    archived_events: List[Dict[str, Any]],
    unassigned_articles: List[Dict[str, Any]],
    few_shot_block: str,
    prompt_template: Optional[str] = None,
) -> str:
    template = prompt_template or app_config.DEFAULT_RECLUSTER_PROMPT
    import json
    return template.format(
        active_events=json.dumps(active_events, indent=2, default=str),
        cooling_events=json.dumps(cooling_events, indent=2, default=str),
        archived_events=json.dumps(archived_events, indent=2, default=str),
        unassigned_articles=json.dumps(unassigned_articles, indent=2, default=str),
        few_shot_block=few_shot_block,
    )
