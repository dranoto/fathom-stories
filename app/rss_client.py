# app/rss_client.py
import feedparser
import asyncio
import logging # Added logging
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from typing import Any, Optional, List # Added List
from bs4 import BeautifulSoup

from . import config as app_config
from .database import FeedSource, Article, ScrapeFailure
from .scraper import scrape_urls # Import the updated scraper function
from langchain_core.documents import Document as LangchainDocument # For type hinting

logger = logging.getLogger(__name__) # Added logger


def now_dt():
    return datetime.now(timezone.utc)


async def _parse_feed_in_thread(feed_url: str):
    loop = asyncio.get_event_loop()
    try:
        # feedparser.parse is I/O blocking, so run in executor
        parsed_data = await loop.run_in_executor(None, feedparser.parse, feed_url)
        return parsed_data
    except Exception as e:
        logger.error(f"RSS_CLIENT: Exception during feedparser.parse for {feed_url}: {e}", exc_info=True)
        return None

def _normalize_datetime(dt_input: Any) -> Optional[datetime]:
    if not dt_input:
        return None
    
    parsed_date = None
    if isinstance(dt_input, datetime): 
        if dt_input.tzinfo is None: 
            return dt_input.replace(tzinfo=timezone.utc) 
        return dt_input 

    if isinstance(dt_input, tuple): 
        try:
            # Ensure all parts of the tuple are integers and valid for datetime
            dt_tuple_list = [int(x) if isinstance(x, (int, float)) else 0 for x in dt_input[:6]]
            while len(dt_tuple_list) < 6: dt_tuple_list.append(0) 
            
            # Basic validation for month and day before creating datetime
            if not (1 <= dt_tuple_list[1] <= 12 and 1 <= dt_tuple_list[2] <= 31):
                 logger.warning(f"RSS_CLIENT: Invalid month/day in date tuple: {dt_input}, using None.")
                 return None
            parsed_date = datetime(*dt_tuple_list, tzinfo=timezone.utc) 
        except (TypeError, ValueError) as e: 
            logger.warning(f"RSS_CLIENT: Could not parse date tuple {dt_input}: {e}")
            pass # Fall through to other parsing methods or return None
            
    if not parsed_date and isinstance(dt_input, str):
        # Try common date formats, including ISO 8601 with and without 'Z'
        # Note: feedparser often returns strings that datetime.fromisoformat can handle,
        # but robust parsing might require dateutil.parser if formats vary wildly.
        try:
            if dt_input.endswith('Z'):
                parsed_date = datetime.fromisoformat(dt_input[:-1] + '+00:00')
            else:
                # Attempt direct ISO format, then try with common timezone abbreviations (less reliable)
                try:
                    parsed_date = datetime.fromisoformat(dt_input)
                except ValueError:
                    # This is a very basic attempt; for robust parsing of varied string dates,
                    # a library like dateutil.parser.parse(dt_input) would be better.
                    # For now, we rely on feedparser's parsed tuple or a strict ISO string.
                    logger.warning(f"RSS_CLIENT: String date '{dt_input}' not in strict ISO format, parsing might fail or be naive.")
                    # Example: from dateutil import parser; parsed_date = parser.parse(dt_input)
                    pass # Fall through if simple fromisoformat fails

            if parsed_date and parsed_date.tzinfo is None: 
                # If parsed but naive, assume UTC as a common practice for feeds
                parsed_date = parsed_date.replace(tzinfo=timezone.utc)
            return parsed_date
        except ValueError:
            logger.warning(f"RSS_CLIENT: Could not parse date string '{dt_input}' with fromisoformat.")
            pass 

    if not parsed_date:
        logger.warning(f"RSS_CLIENT: Failed to normalize date input: {dt_input}")
        return None
    return parsed_date


def _make_entry_serializable(entry: dict) -> dict:
    """
    Converts a feedparser entry into a JSON-serializable dictionary.
    Specifically handles time.struct_time objects by converting them to ISO 8601 strings.
    """
    serializable_entry = dict(entry)
    for key, value in serializable_entry.items():
        if key.endswith('_parsed') and value:
            # _normalize_datetime returns a timezone-aware datetime object
            dt_obj = _normalize_datetime(value)
            if dt_obj:
                # Convert to ISO 8601 string format
                serializable_entry[key] = dt_obj.isoformat()
    return serializable_entry


async def fetch_and_store_articles_from_feed(db: Session, feed_source: FeedSource) -> int:
    """
    Fetches articles from a single FeedSource, scrapes their content,
    stores new ones in the database (with text and HTML content),
    and updates the feed_source's last_fetched_at timestamp.
    """
    logger.info(f"RSS_CLIENT: Fetching articles for: {feed_source.url} (Name: {feed_source.name})")
    feed_data = await _parse_feed_in_thread(feed_source.url)  # type: ignore - SQLAlchemy Column
    current_time_utc = datetime.now(timezone.utc) 

    if feed_data is None or not hasattr(feed_data, 'feed') or not hasattr(feed_data, 'entries'):
        logger.warning(f"RSS_CLIENT: Failed to parse or invalid feed structure for {feed_source.url}")
        feed_source.last_fetched_at = current_time_utc  # type: ignore - SQLAlchemy descriptor
        db.add(feed_source)
        return 0

    feed_title_from_rss = feed_data.feed.get('title', feed_source.url.split('/')[2] if len(feed_source.url.split('/')) > 2 else feed_source.url)  # type: ignore - FeedParserDict type
    if not feed_source.name and feed_title_from_rss:  # type: ignore - SQLAlchemy descriptor
        feed_source.name = feed_title_from_rss  # type: ignore - SQLAlchemy descriptor
        db.add(feed_source)

    new_articles_count = 0
    processed_in_batch = 0

    urls_to_scrape = []
    feed_entries_map = {} # To map URL back to its feed entry data

    for entry in feed_data.entries:
        if processed_in_batch >= app_config.MAX_ARTICLES_PER_INDIVIDUAL_FEED:
            logger.info(f"RSS_CLIENT: Reached MAX_ARTICLES_PER_INDIVIDUAL_FEED ({app_config.MAX_ARTICLES_PER_INDIVIDUAL_FEED}) for {feed_source.name}. Skipping remaining entries.")
            break

        article_url = entry.get("link")
        if not article_url:
            logger.warning(f"RSS_CLIENT: Feed entry for {feed_source.name} missing 'link'. Entry: {entry.get('title', 'N/A')}")
            continue

        recent_failure = (
            db.query(ScrapeFailure)
            .filter(ScrapeFailure.url == article_url)
            .order_by(ScrapeFailure.last_attempted_at.desc())
            .first()
        )
        if recent_failure:
            retry_cutoff = now_dt() - timedelta(days=app_config.SCRAPE_FAILURE_RETRY_DAYS)
            if recent_failure.last_attempted_at and recent_failure.last_attempted_at > retry_cutoff:
                logger.debug(
                    f"RSS_CLIENT: skipping {article_url} — scrape failed "
                    f"{recent_failure.attempt_count}x, last at {recent_failure.last_attempted_at}"
                )
                continue

        processed_in_batch +=1

        existing_article = db.query(Article).filter(Article.url == article_url).first()
        if existing_article:
            logger.debug(f"RSS_CLIENT: Article URL already exists, skipping: {article_url}")
            continue 

        title = entry.get("title")
        published_date_raw = entry.get("published_parsed", entry.get("published", entry.get("updated")))
        published_date_dt = _normalize_datetime(published_date_raw)

        if not title: logger.warning(f"RSS_CLIENT: Feed entry for {article_url} missing 'title'. Skipping."); continue
        if not published_date_dt: logger.warning(f"RSS_CLIENT: Feed entry for {article_url} (Title: {title}) missing valid 'published_date'. Skipping."); continue
            
        urls_to_scrape.append(article_url)
        feed_entries_map[article_url] = entry # Store entry for later use

    if not urls_to_scrape:
        logger.info(f"RSS_CLIENT: No new, valid article URLs to scrape from feed {feed_source.name}.")
        feed_source.last_fetched_at = current_time_utc  # type: ignore - SQLAlchemy descriptor
        db.add(feed_source)
        return 0

    logger.info(f"RSS_CLIENT: Found {len(urls_to_scrape)} new article URLs to scrape from {feed_source.name}.")
    
    # Scrape all new URLs in a batch
    # The scraper.py is already configured via app_config for extension path and headless mode
    scraped_docs: List[LangchainDocument] = await scrape_urls(urls_to_scrape)

    for scraped_doc in scraped_docs:
        article_url = scraped_doc.metadata.get("source")
        if not article_url:
            logger.error("RSS_CLIENT: Scraped document missing 'source' (URL) in metadata. Skipping.")
            continue

        feed_entry_data = feed_entries_map.get(article_url)
        if not feed_entry_data:
            logger.error(f"RSS_CLIENT: Could not find original feed entry data for scraped URL: {article_url}. Skipping.")
            continue

        # Use title from feed entry as primary, fallback to scraped title if feed entry title was poor/missing (already checked)
        article_title = feed_entry_data.get("title") 
        scraped_title = scraped_doc.metadata.get("title")
        if not article_title and scraped_title: # Only use scraped_title if feed title was truly missing
            logger.info(f"RSS_CLIENT: Using title from scraper for {article_url} as feed entry title was missing.")
            article_title = scraped_title
        elif scraped_title and article_title and len(scraped_title) > len(article_title) + 10: # Arbitrary: if scraped title is much longer
             logger.info(f"RSS_CLIENT: Scraped title for {article_url} ('{scraped_title[:30]}...') is significantly different from feed title ('{article_title[:30]}...'). Prioritizing feed title for now.")


        # Get published_date from the stored feed_entry_data to ensure consistency
        published_date_dt = _normalize_datetime(feed_entry_data.get("published_parsed", feed_entry_data.get("published", feed_entry_data.get("updated"))))
        if not published_date_dt: # Should have been caught earlier, but double check
            logger.warning(f"RSS_CLIENT: Critical - published_date became invalid for {article_url} before saving. Skipping.")
            continue

        # NEW: Extract and clean RSS description to plain text
        raw_description = None
        # First, try to get the full content from 'summary_detail'.
        if 'summary_detail' in feed_entry_data and feed_entry_data['summary_detail'].get('value'):
            raw_description = feed_entry_data['summary_detail']['value']

        # If not found, fall back to 'summary', then 'description'.
        if not raw_description:
            raw_description = feed_entry_data.get('summary', feed_entry_data.get('description'))

        # As a final fallback, check the 'content' field.
        if not raw_description and 'content' in feed_entry_data:
            if isinstance(feed_entry_data['content'], list) and feed_entry_data['content']:
                content_block = feed_entry_data['content'][0]
                if isinstance(content_block, dict) and 'value' in content_block:
                    raw_description = content_block['value']

        plain_text_description = None
        if raw_description:
            try:
                # Use BeautifulSoup to strip any HTML tags and get plain text.
                soup = BeautifulSoup(raw_description, "html.parser")
                plain_text_description = soup.get_text(separator=' ', strip=True)
            except Exception as e:
                logger.warning(f"RSS_CLIENT: Could not parse description HTML for {article_url}. Storing raw. Error: {e}")
                # As a fallback, use the raw description if parsing fails.
                plain_text_description = raw_description

        # Prepare content for DB
        text_content_to_save = scraped_doc.page_content
        html_content_to_save = scraped_doc.metadata.get('full_html_content')

        scraper_error = scraped_doc.metadata.get('error')
        word_count_to_save = scraped_doc.metadata.get('word_count', 0)

        if scraper_error:
            logger.warning(
                f"RSS_CLIENT: dropping {article_url} — scraper error: {scraper_error}"
            )
            existing_failure = (
                db.query(ScrapeFailure)
                .filter(ScrapeFailure.url == article_url)
                .first()
            )
            if existing_failure:
                existing_failure.attempt_count = (existing_failure.attempt_count or 1) + 1
                existing_failure.last_attempted_at = now_dt()
                existing_failure.error = str(scraper_error)[:1000]
            else:
                db.add(ScrapeFailure(
                    url=article_url,
                    error=str(scraper_error)[:1000],
                    attempt_count=1,
                    first_attempted_at=now_dt(),
                    last_attempted_at=now_dt(),
                ))
            continue
        if (word_count_to_save or 0) < app_config.MIN_ARTICLE_WORD_COUNT:
            logger.info(
                f"RSS_CLIENT: dropping {article_url} — {word_count_to_save} words "
                f"(< {app_config.MIN_ARTICLE_WORD_COUNT} threshold)"
            )
            continue

        serializable_entry = _make_entry_serializable(feed_entry_data)

        new_article = Article(
            feed_source_id=feed_source.id,
            url=article_url,
            title=article_title,
            publisher_name=feed_source.name or feed_title_from_rss,
            published_date=published_date_dt,
            rss_description=plain_text_description,
            raw_rss_item=serializable_entry,
            scraped_text_content=text_content_to_save,
            full_html_content=html_content_to_save,
            word_count=word_count_to_save
        )
        db.add(new_article)
        new_articles_count += 1
        logger.debug(f"RSS_CLIENT: Staged new article for DB: {article_url} (Title: {article_title[:30]}...)")


    feed_source.last_fetched_at = current_time_utc  # type: ignore - SQLAlchemy descriptor
    db.add(feed_source)
    logger.info(f"RSS_CLIENT: Finished processing feed {feed_source.name}. Staged {new_articles_count} new articles with scraped content for commit.")
    return new_articles_count


async def _process_single_feed(feed_id: int) -> tuple[int, Optional[str]]:
    """Process a single feed with its own DB session. Returns (count, error_msg)."""
    from .database import SessionLocal
    
    db = SessionLocal()
    try:
        feed_source = db.query(FeedSource).filter(FeedSource.id == feed_id).first()
        if not feed_source:
            return 0, f"Feed ID {feed_id} not found"
        
        newly_added = await fetch_and_store_articles_from_feed(db, feed_source)
        db.commit()
        return newly_added, None
    except Exception as e:
        db.rollback()
        try:
            feed_source = db.query(FeedSource).filter(FeedSource.id == feed_id).first()
            if feed_source:
                feed_source.last_fetched_at = datetime.now(timezone.utc)
                db.add(feed_source)
                db.commit()
        except Exception:
            db.rollback()
        return 0, str(e)
    finally:
        db.close()


async def update_all_subscribed_feeds(db: Session):
    logger.info("RSS_CLIENT_SCHEDULER: Starting update for all subscribed feeds...")
    now_aware = datetime.now(timezone.utc) 
    
    feeds_to_update = []
    all_feeds = db.query(FeedSource).all()
    for feed in all_feeds:
        should_fetch = False
        if feed.last_fetched_at is None:
            should_fetch = True
            logger.info(f"RSS_CLIENT_SCHEDULER: Feed '{feed.name}' (ID: {feed.id}) never fetched. Adding to update queue.")
        else:
            last_fetched_aware = feed.last_fetched_at
            if last_fetched_aware.tzinfo is None or last_fetched_aware.tzinfo.utcoffset(last_fetched_aware) is None:
                logger.warning(f"RSS_CLIENT_SCHEDULER: Warning - Feed '{feed.name}' (ID: {feed.id}) has an offset-naive last_fetched_at ('{last_fetched_aware}'). Assuming UTC.")
                last_fetched_aware = last_fetched_aware.replace(tzinfo=timezone.utc)
            
            fetch_time_cutoff = now_aware - timedelta(minutes=feed.fetch_interval_minutes)
            if last_fetched_aware < fetch_time_cutoff:
                should_fetch = True
                logger.info(f"RSS_CLIENT_SCHEDULER: Feed '{feed.name}' (ID: {feed.id}) due for update. Last fetched: {last_fetched_aware}, Cutoff: {fetch_time_cutoff}. Adding to queue.")
        
        if should_fetch:
            feeds_to_update.append(feed.id)

    if not feeds_to_update:
        logger.info("RSS_CLIENT_SCHEDULER: No feeds currently due for update.")
        return 0

    logger.info(f"RSS_CLIENT_SCHEDULER: Found {len(feeds_to_update)} feeds to update.")
    
    semaphore = asyncio.Semaphore(5)
    
    async def sem_process(feed_id: int) -> tuple[int, Optional[str]]:
        async with semaphore:
            return await _process_single_feed(feed_id)
    
    results = await asyncio.gather(*[sem_process(fid) for fid in feeds_to_update])
    
    total_new_articles_overall = 0
    for (newly_added, error_msg) in results:
        total_new_articles_overall += newly_added
        if error_msg:
            logger.error(f"RSS_CLIENT_SCHEDULER: Error processing feed: {error_msg}")
    
    logger.info(f"RSS_CLIENT_SCHEDULER: Finished feed update cycle. Total new articles committed across all feeds: {total_new_articles_overall}.")
    return total_new_articles_overall

async def update_single_feed(db: Session, feed_id: int):
    """
    Updates a single feed by its ID, regardless of whether it's due for update.
    Used for manual refresh of a specific feed.
    """
    logger.info(f"RSS_CLIENT: Updating single feed with ID: {feed_id}")
    feed_source = db.query(FeedSource).filter(FeedSource.id == feed_id).first()
    if not feed_source:
        logger.warning(f"RSS_CLIENT: Feed with ID {feed_id} not found.")
        return
    
    try:
        newly_added = await fetch_and_store_articles_from_feed(db, feed_source)
        db.commit()
        logger.info(f"RSS_CLIENT: Successfully processed single feed '{feed_source.name}'. Added {newly_added} articles.")
    except Exception as e:
        db.rollback()
        logger.error(f"RSS_CLIENT: Error processing single feed {feed_source.url}: {e}", exc_info=True)
        try:
            feed_source.last_fetched_at = datetime.now(timezone.utc)  # type: ignore - SQLAlchemy descriptor
            db.add(feed_source)
            db.commit()
        except Exception:
            db.rollback()

def add_initial_feeds_to_db(db: Session, feed_urls: list[str]):
    logger.info(f"RSS_CLIENT: Attempting to add/verify initial feeds: {feed_urls}")
    added_count = 0
    for url in feed_urls:
        existing_feed = db.query(FeedSource).filter(FeedSource.url == url).first()
        if not existing_feed:
            try:
                feed_name_guess = url.split('/')[2].replace("www.", "") if len(url.split('/')) > 2 else url
                new_feed = FeedSource(
                    url=url, 
                    name=feed_name_guess, 
                    fetch_interval_minutes=app_config.DEFAULT_RSS_FETCH_INTERVAL_MINUTES 
                )
                db.add(new_feed)
                db.commit() 
                added_count += 1
                logger.info(f"RSS_CLIENT: Added new feed source to DB and committed: {url}")
            except IntegrityError:
                db.rollback()
                logger.warning(f"RSS_CLIENT: Feed already exists (IntegrityError on add): {url}")
            except Exception as e:
                db.rollback()
                logger.error(f"RSS_CLIENT: Error adding feed {url} to DB: {e}", exc_info=True)
    if added_count > 0:
        logger.info(f"RSS_CLIENT: Added {added_count} new feed sources to the database.")
    else:
        logger.info("RSS_CLIENT: No new feed sources added (all provided URLs likely exist or list was empty).")

def add_or_update_feed_source(db: Session, url: str, name: Optional[str] = None) -> FeedSource:
    feed = db.query(FeedSource).filter(FeedSource.url == url).first()
    if feed is None:
        guessed_name = name or (url.split('/')[2].replace("www.", "") if len(url.split('/')) > 2 else url)
        feed = FeedSource(
            url=url,
            name=guessed_name,
            fetch_interval_minutes=app_config.DEFAULT_RSS_FETCH_INTERVAL_MINUTES,
        )
        db.add(feed)
        db.commit()
        db.refresh(feed)
        logger.info(f"RSS_CLIENT: Added feed source: {url}")
    return feed
