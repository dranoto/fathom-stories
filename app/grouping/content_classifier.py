# app/grouping/content_classifier.py
import re
from typing import List, Optional


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
    r"listen and subscribe[: ]?",
    r"apple \| spotify \| wherever you listen",
    r"^the episode:",
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
    r"^.+ review: ",
    r"^after thorough test",
    r" review:?$",
]

ADVICE_PATTERNS: List[str] = [
    r"^how to",
    r"^what to (watch|read|cook|buy|stream|do|see|know)",
    r"^a guide to",
    r"^best .+ of \d{4}",
    r"^the best .+ of",
    r"^\d+ best .+",
    r"^our editors recommend",
    r"^recommend(ed|ation)?[: ]",
    r"^leave your .+ open$",
    r"^why .+ won'?t solve",
    r"\d+ best .+ \(\d{4}\)",
    r"best .+ of \d{4}\)?$",
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
    r"^are .+ too old\?$",
    r"^here's how .+ can",
]

PRIVACY_BANNER_PATTERNS: List[str] = [
    r"this website uses essential cookies",
    r"cookies? allow us to count visits",
    r"these cookies may be set through our site by our advertising partners",
    r"we use audience measurement cookies",
    r"consent management platform",
    r"gdpr countries",
]


def _matches_any(text: str, patterns: List[str]) -> bool:
    if not text:
        return False
    for p in patterns:
        if re.search(p, text, re.IGNORECASE):
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


def classify_article(title: str, rss_description: Optional[str] = None, scraped_text: Optional[str] = None) -> str:
    from_title = classify_title(title)
    if from_title != "news":
        return from_title
    haystack = " ".join(filter(None, [rss_description or "", (scraped_text or "")[:500]]))
    if not haystack:
        return "news"
    if _matches_any(haystack, PRIVACY_BANNER_PATTERNS):
        return "cookie-banner"
    if _matches_any(haystack, PODCAST_PATTERNS):
        return "podcast"
    if _matches_any(haystack, REVIEW_PATTERNS):
        return "review"
    if _matches_any(haystack, ADVICE_PATTERNS):
        return "advice"
    if _matches_any(haystack, LIFESTYLE_PATTERNS):
        return "lifestyle"
    return "news"


NON_EVENT_TYPES = {"podcast", "opinion", "review", "advice", "newsletter", "lifestyle", "cookie-banner"}


def is_non_event(title: str) -> bool:
    return classify_title(title) in NON_EVENT_TYPES
