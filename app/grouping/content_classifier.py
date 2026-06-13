# app/grouping/content_classifier.py
import re
from typing import List, Tuple


PODCAST_PATTERNS: List[str] = [
    r"^podcast[: ]",
    r"^the daily$",
    r"^today, explained$",
    r"^the .+ hour$",
    r"^political scene$",
    r"^the new yorker radio hour$",
    r"^books briefing[: ]",
    r"^culture week$",
    r"^on the news$",
    r"washington roundtable",
    r"^roundtable$",
    r"daily cartoon",
    r"^play shuffalo",
    r"^puzzles?[: ]",
    r"^the sports journalist",
    r"^jomboy on",
    r"^on .+ podcast$",
]

OPINION_PATTERNS: List[str] = [
    r"^opinion[: ]",
    r"^editorial[: ]",
    r"^guest essay[: ]",
    r"^op-?ed[: ]",
    r"^my .+ opinion$",
]

REVIEW_PATTERNS: List[str] = [
    r" review$",
    r"^review:",
    r" reviewed$",
    r"^a review of",
    r" rating$",
    r"^grade:",
    r"^the .+ review$",
]

ADVICE_PATTERNS: List[str] = [
    r"^how to",
    r"^what to (watch|read|cook|buy|stream|do|see|know)",
    r"^a guide to",
    r"^best .+ of \d{4}$",
    r"^the best .+ of",
    r"^our editors recommend",
    r"^recommend(ed|ation)?[: ]",
    r"^leave your .+ open$",
    r"^why .+ won'?t solve",
]

NEWSLETTER_PATTERNS: List[str] = [
    r"^the morning",
    r"^the evening",
    r"^newsletter[: ]",
    r"^weekly recap",
    r"^this week in",
    r"^week in review",
]

LIFESTYLE_PATTERNS: List[str] = [
    r"^photos of the week",
    r"^fashion ",
    r"^style ",
    r"^travel ",
    r"^food ",
    r"^recipes? ",
    r"^home ",
    r"^the women who",
    r"^the whimsy",
]


def _matches_any(title: str, patterns: List[str]) -> bool:
    for p in patterns:
        if re.search(p, title, re.IGNORECASE):
            return True
    return False


def classify_title(title: str) -> str:
    if not title:
        return "news"
    t = title.strip()
    if _matches_any(t, PODCAST_PATTERNS):
        return "podcast"
    if _matches_any(t, OPINION_PATTERNS):
        return "opinion"
    if _matches_any(t, REVIEW_PATTERNS):
        return "review"
    if _matches_any(t, ADVICE_PATTERNS):
        return "advice"
    if _matches_any(t, NEWSLETTER_PATTERNS):
        return "newsletter"
    if _matches_any(t, LIFESTYLE_PATTERNS):
        return "lifestyle"
    return "news"


NON_EVENT_TYPES = {"podcast", "opinion", "review", "advice", "newsletter", "lifestyle"}


def is_non_event(title: str) -> bool:
    return classify_title(title) in NON_EVENT_TYPES
