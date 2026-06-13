# app/config.py
import os
import json
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# --- Database Configuration ---
SQLITE_DB_SUBDIR = "data"
SQLITE_DB_FILE = "stories.db"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///./{SQLITE_DB_SUBDIR}/{SQLITE_DB_FILE}")

# --- LLM Configuration ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
DEFAULT_SUMMARY_MODEL_NAME = os.getenv("DEFAULT_SUMMARY_MODEL_NAME", "gpt-4o-mini")
DEFAULT_GROUPING_MODEL_NAME = os.getenv("DEFAULT_GROUPING_MODEL_NAME", "gpt-4o-mini")

SUMMARY_MAX_OUTPUT_TOKENS = int(os.getenv("SUMMARY_MAX_OUTPUT_TOKENS", 8192))
GROUPING_MAX_OUTPUT_TOKENS = int(os.getenv("GROUPING_MAX_OUTPUT_TOKENS", 4096))

# --- RSS Feed Configuration ---
rss_feeds_env_str = os.getenv("RSS_FEED_URLS", "")
if rss_feeds_env_str.strip().startswith("[") and rss_feeds_env_str.strip().endswith("]"):
    try:
        RSS_FEED_URLS = json.loads(rss_feeds_env_str)
        if not isinstance(RSS_FEED_URLS, list):
            logger.warning("RSS_FEED_URLS from .env (JSON) did not parse as a list. Falling back.")
            RSS_FEED_URLS = []
    except json.JSONDecodeError:
        logger.warning("RSS_FEED_URLS in .env is not valid JSON. Falling back to empty list.")
        RSS_FEED_URLS = []
elif rss_feeds_env_str:
    RSS_FEED_URLS = [url.strip() for url in rss_feeds_env_str.split(',') if url.strip()]
else:
    RSS_FEED_URLS = []

# --- Application Behavior Defaults ---
try:
    MAX_ARTICLES_PER_INDIVIDUAL_FEED = int(os.getenv("MAX_ARTICLES_PER_INDIVIDUAL_FEED", 15))
except ValueError:
    logger.warning("Invalid MAX_ARTICLES_PER_INDIVIDUAL_FEED in .env. Using default 15.")
    MAX_ARTICLES_PER_INDIVIDUAL_FEED = 15

try:
    DEFAULT_RSS_FETCH_INTERVAL_MINUTES = int(os.getenv("DEFAULT_RSS_FETCH_INTERVAL_MINUTES", 30))
except ValueError:
    logger.warning("Invalid DEFAULT_RSS_FETCH_INTERVAL_MINUTES in .env. Using default 30.")
    DEFAULT_RSS_FETCH_INTERVAL_MINUTES = 30

try:
    RECLUSTER_HOUR_UTC = int(os.getenv("RECLUSTER_HOUR_UTC", 3))
except ValueError:
    logger.warning("Invalid RECLUSTER_HOUR_UTC in .env. Using default 3.")
    RECLUSTER_HOUR_UTC = 3

try:
    LIFECYCLE_TICK_HOURS = int(os.getenv("LIFECYCLE_TICK_HOURS", 1))
except ValueError:
    logger.warning("Invalid LIFECYCLE_TICK_HOURS in .env. Using default 1.")
    LIFECYCLE_TICK_HOURS = 1

try:
    AUTO_ARCHIVE_DAYS = int(os.getenv("AUTO_ARCHIVE_DAYS", 7))
except ValueError:
    logger.warning("Invalid AUTO_ARCHIVE_DAYS in .env. Using default 7.")
    AUTO_ARCHIVE_DAYS = 7

try:
    ARCHIVE_REVIVE_WINDOW_DAYS = int(os.getenv("ARCHIVE_REVIVE_WINDOW_DAYS", 30))
except ValueError:
    logger.warning("Invalid ARCHIVE_REVIVE_WINDOW_DAYS in .env. Using default 30.")
    ARCHIVE_REVIVE_WINDOW_DAYS = 30

# --- Scraper Configuration ---
USER_AGENT = os.getenv(
    "PLAYWRIGHT_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
)
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "10"))

PLAYWRIGHT_TIMEOUT = int(os.getenv("PLAYWRIGHT_TIMEOUT", "60000"))
PLAYWRIGHT_PAGE_WAIT_MS = int(os.getenv("PLAYWRIGHT_PAGE_WAIT_MS", "3000"))
SCRAPE_REQUEST_DELAY_SEC = int(os.getenv("SCRAPE_REQUEST_DELAY_SEC", "1"))

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_docker_extension_path = "/app/scraper_assistant"
_local_extension_path = os.path.join(PROJECT_ROOT, "scraper_assistant")

if os.getenv("PATH_TO_EXTENSION"):
    PATH_TO_EXTENSION = os.getenv("PATH_TO_EXTENSION")
elif os.path.isdir(_docker_extension_path):
    PATH_TO_EXTENSION = _docker_extension_path
else:
    PATH_TO_EXTENSION = _local_extension_path

USE_HEADLESS_BROWSER = os.getenv("USE_HEADLESS_BROWSER", "True").lower() in ('true', '1', 't')

DEFAULT_MINIMUM_WORD_COUNT = int(os.getenv("DEFAULT_MINIMUM_WORD_COUNT", "100"))
MIN_ARTICLE_WORD_COUNT = int(os.getenv("MIN_ARTICLE_WORD_COUNT", "350"))

# --- LLM Temperature Configuration ---
SUMMARY_LLM_TEMPERATURE = float(os.getenv("SUMMARY_LLM_TEMPERATURE", "0.2"))
GROUPING_LLM_TEMPERATURE = float(os.getenv("GROUPING_LLM_TEMPERATURE", "0.1"))

# --- Debug Configuration ---
DEBUG_LEVEL = os.getenv("DEBUG_LEVEL", "standard").lower()
DEBUG_LEVELS = {"minimal": 0, "standard": 1, "verbose": 2, "trace": 3}


def is_debug_level(level_name: str) -> bool:
    return DEBUG_LEVELS.get(DEBUG_LEVEL, 1) >= DEBUG_LEVELS.get(level_name, 0)


# --- Ports ---
MAIN_PORT = int(os.getenv("MAIN_PORT", "8000"))
ADMIN_PORT = int(os.getenv("ADMIN_PORT", "8001"))


# --- Default Prompts ---
DEFAULT_SUMMARY_PROMPT = os.getenv("DEFAULT_SUMMARY_PROMPT", """Task:Generate a concise, narrative summary of the following article. The output must be Markdown-formatted and meticulously optimized for scannability, readability, and minimal cognitive load. (Note: The article title will be provided separately).
Format & Content:

Key Takeaways (1-3 Labeled Bullets):
Present the most critical facts using * bullets.
Prefix each bullet with a bold semantic label (choose from: Who:, What:, Where:, When:, Why:, How:, Impact:, Context:, Next: - use the most relevant 1-3 labels).
Keep the bullet text concise (max 10-15 words).
Use bold Markdown on the most crucial 1-3 words within the bullet text itself.
Example: * **Impact:** This **challenges existing models**.
Narrative Context (3-5 Sentences):
Follow with a single, coherent paragraph.
The first sentence must immediately set the scene or state the primary significance.
This paragraph must connect the key takeaways, providing narrative flow, context, or deeper meaning.
It must build upon, not just repeat, the bullets by explaining how events unfolded or why they matter.
Use italics sparingly for emphasis if needed.
Style & Principles:

Clarity: Use active voice, strong verbs, and simple, direct language. Avoid or explain jargon.
Structure: Ensure short sentences and the defined format enhance scannability.
Markdown: Use **bold** and *italics* strategically to guide the eye, not overwhelm it.
Storytelling: Weave a clear, engaging narrative within the paragraph.
Tone: Maintain an objective, professional, yet compelling tone.

Article:{text}
Summary:""")

DEFAULT_MAJOR_SUMMARY_PROMPT = os.getenv("DEFAULT_MAJOR_SUMMARY_PROMPT", """Given a collection of articles about {event_name}, analyze them and return a JSON response with three subsections:

Return EXACTLY this JSON structure (no markdown, no extra text):
{{
  "timeline_narrative": "Chronological narrative of key developments, organized by date. Highlight the most significant moments.",
  "cross_source_synthesis": "How do different sources cover this story differently? Note any conflicting angles, unique insights from specific outlets, or notable patterns in coverage.",
  "progressive_summary": "What is NEW since the last summary (or if no previous summary, what are the most recent developments)? Focus on what the user most needs to know right now.",
  "article_count": <total number of articles analyzed>,
  "feed_count": <number of unique feeds>,
  "date_range": "<earliest date> - <latest date>",
  "key_developments": ["Milestone 1", "Milestone 2", "Milestone 3"]
}}

Articles to analyze:
{article_texts}

Return the JSON now:""")

DEFAULT_GROUP_ASSIGN_PROMPT = os.getenv("DEFAULT_GROUP_ASSIGN_PROMPT", """You are grouping incoming news articles into ongoing news events (longitudinal stories like wars, presidencies, disasters, etc.).

For each article, decide ONE of:
  a) belongs to existing event E (provide the event_id)
  b) uncategorized (true noise, no clear ongoing story)

You may NOT propose new events from a single article. New events are only created during the forced regroup pass.

Also assign an importance_score 0-1:
  - 0.8-1.0: world-historical (war escalation, major disaster, head-of-state action)
  - 0.5-0.7: significant developments in a tracked story
  - 0.2-0.4: minor updates, tangential
  - 0.0-0.1: trivial, not worth tracking

CONTENT-TYPE FILTER (HARD RULE):
Articles marked with one of these content_type values are NOT news events and MUST be assigned to "uncategorized":
  - podcast, roundup (panel discussions, roundtables, NPR/New Yorker "the X hour" segments, daily cartoon, puzzles, "books briefing", etc.)
  - opinion (editorials, op-eds, "Opinion:" tagged pieces)
  - review (movie/book/restaurant/product reviews)
  - advice (how-to, what-to-watch, best-of, shopping guides, recipes)
  - newsletter (morning/evening briefings, weekly recaps)
  - lifestyle (fashion, travel, food, celebrity, photo essays, "the women who...")

Exception: a podcast/roundtable that is EXPLICITLY about a major longitudinal story (e.g., a podcast episode called "The Russia-Ukraine War: One Year In") MAY be assigned to that event IF the event is explicitly about that story and already has 3+ existing articles.

The content_type field on each article is a strong hint. Respect it unless you have a clear reason to override.

Active events (newest at top of each):
{active_events}

Cooling events (still assignable):
{cooling_events}

{few_shot_block}

New articles to assign:
{articles_json}

Return EXACTLY this JSON (no markdown, no extra text):
{{
  "assignments": [
    {{
      "article_id": <id>,
      "decision": "existing" | "uncategorized",
      "event_id": <id> | null,
      "importance_score": <float>,
      "confidence": <float 0-1>,
      "reasoning": "<one short sentence>"
    }}
  ]
}}""")

DEFAULT_REGROUP_PROMPT = os.getenv("DEFAULT_REGROUP_PROMPT", """You are doing a forced regroup pass: reviewing ungrouped articles to either match them to existing events, or pair two-or-more of them together to form a new event.

This is the ONLY pass that creates new events. A "new" decision with only 1 article pointing to the same name is fine (it will become a pending suggestion, not an event yet). A "new" decision with 2+ articles sharing the same name WILL create an event.

For each ungrouped article, decide ONE of:
  a) existing event E (provide the event_id)
  b) new event — propose a short, specific name (3-7 words)
     - If 2+ ungrouped articles share the same proposed name, they will be paired into a new event
     - If only 1 article has the name, it will be marked as a pending suggestion
  c) uncategorized (true noise, no clear ongoing story)

Also assign an importance_score 0-1 (same scale as the live grouping pass).

CONTENT-TYPE FILTER (HARD RULE):
Articles with content_type in {{podcast, roundup, opinion, review, advice, newsletter, lifestyle}} are NOT news events. Assign them to "uncategorized" unless they're EXPLICITLY about a major longitudinal story (Russia-Ukraine, US Presidency, a specific named war/disaster) AND that event already has 3+ existing articles.

Active events (newest at top of each):
{active_events}

Cooling events (still assignable):
{cooling_events}

{few_shot_block}

Ungrouped articles to regroup:
{articles_json}

Return EXACTLY this JSON (no markdown, no extra text):
{{
  "assignments": [
    {{
      "article_id": <id>,
      "decision": "existing" | "new" | "uncategorized",
      "event_id": <id> | null,
      "event_name": "<name>" | null,
      "importance_score": <float>,
      "confidence": <float 0-1>,
      "reasoning": "<one short sentence>"
    }}
  ]
}}""")

DEFAULT_RECLUSTER_PROMPT = os.getenv("DEFAULT_RECLUSTER_PROMPT", """You are reviewing a set of active news events to suggest structural changes.

Return a JSON diff with:
  - merge_candidates: pairs of events that look like the same story
  - split_candidates: events that look like they should be two stories
  - cooling_events: active events with no fresh activity (no articles in last 3 days)
  - reviving_events: ARCHIVED events that have new evidence in recent articles
  - new_events: brand-new stories emerging in the recent articles that aren't in any event yet

Be CONSERVATIVE. Only suggest changes that are clearly correct. If unsure, omit.

Active events (with last_article_at and recent titles):
{active_events}

Cooling events:
{cooling_events}

Archived events (within last 30 days, candidates for revival):
{archived_events}

Recent unassigned articles:
{unassigned_articles}

{few_shot_block}

Return EXACTLY this JSON (no markdown, no extra text):
{{
  "merge_candidates": [
    {{"event_a_id": <id>, "event_b_id": <id>, "reason": "<short>"}}
  ],
  "split_candidates": [
    {{"event_id": <id>, "suggested_new_name": "<name>", "anchor_article_ids": [<id>, ...]}}
  ],
  "cooling_events": [<event_id>, ...],
  "reviving_events": [<archived_event_id>, ...],
  "new_events": [
    {{"name": "<name>", "anchor_article_ids": [<id>, ...]}}
  ]
}}""")


if not OPENAI_API_KEY:
    logger.warning("OPENAI_API_KEY environment variable is not set. LLM features will be impaired.")

logger.info(f"CONFIG: DATABASE_URL={DATABASE_URL}")
logger.info(f"CONFIG: OPENAI_API_KEY set={'yes' if OPENAI_API_KEY else 'no'}")
logger.info(f"CONFIG: OPENAI_BASE_URL={OPENAI_BASE_URL}")
logger.info(f"CONFIG: SUMMARY_MODEL={DEFAULT_SUMMARY_MODEL_NAME}, GROUPING_MODEL={DEFAULT_GROUPING_MODEL_NAME}")
logger.info(f"CONFIG: RSS_FEED_URLS count={len(RSS_FEED_URLS)}")
logger.info(f"CONFIG: PATH_TO_EXTENSION={PATH_TO_EXTENSION}")
logger.info(f"CONFIG: USE_HEADLESS_BROWSER={USE_HEADLESS_BROWSER}")
logger.info(f"CONFIG: AUTO_ARCHIVE_DAYS={AUTO_ARCHIVE_DAYS}, RECLUSTER_HOUR_UTC={RECLUSTER_HOUR_UTC}")
logger.info(f"CONFIG: MAIN_PORT={MAIN_PORT}, ADMIN_PORT={ADMIN_PORT}")
