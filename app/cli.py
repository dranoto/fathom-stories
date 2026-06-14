# app/cli.py
import argparse
import asyncio
import logging
import sys
import uvicorn

from . import config as app_config
from .database import create_db_and_tables, db_session_scope, FeedSource
from .database.models import Base
from .rss_client import update_all_subscribed_feeds, add_or_update_feed_source
from .summarizer import initialize_llm
from .grouping import engine as grouping_engine
from .grouping import recluster as recluster_module
from .grouping import lifecycle as lifecycle_module

logger = logging.getLogger("fathom-stories.cli")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


def _get_grouping_llm():
    if not app_config.OPENAI_API_KEY:
        logger.error("OPENAI_API_KEY not set in .env")
        sys.exit(2)
    llm = initialize_llm(
        api_key=app_config.OPENAI_API_KEY,
        base_url=app_config.OPENAI_BASE_URL,
        model_name=app_config.DEFAULT_GROUPING_MODEL_NAME,
        temperature=app_config.GROUPING_LLM_TEMPERATURE,
        max_tokens=app_config.GROUPING_MAX_OUTPUT_TOKENS,
    )
    if not llm:
        logger.error("Failed to initialize grouping LLM")
        sys.exit(2)
    return llm


def cmd_init_db(_args):
    create_db_and_tables()
    print(f"Database initialized at {app_config.DATABASE_URL}")


def cmd_migrate_visitor_id(_args):
    from sqlalchemy import inspect, text
    from .database.models import ArticleRead
    from .database import engine

    insp = inspect(engine)
    if "article_reads" not in insp.get_table_names():
        print("article_reads table does not exist; nothing to migrate.")
        return
    cols = {c["name"] for c in insp.get_columns("article_reads")}
    if "visitor_id" in cols:
        print("article_reads already has visitor_id column; nothing to do.")
        return
    with engine.begin() as conn:
        existing_count = conn.execute(text("SELECT COUNT(*) FROM article_reads")).scalar() or 0
        conn.execute(text("DROP TABLE article_reads"))
    Base.metadata.create_all(bind=engine, tables=[ArticleRead.__table__])
    print(f"Dropped {existing_count} existing read rows and recreated article_reads with visitor_id.")


def cmd_seed_feeds(_args):
    if not app_config.RSS_FEED_URLS:
        print("No RSS_FEED_URLS in .env")
        return
    with db_session_scope() as db:
        for url in app_config.RSS_FEED_URLS:
            add_or_update_feed_source(db, url)
    print(f"Seeded {len(app_config.RSS_FEED_URLS)} feeds")


def cmd_fetch(_args):
    create_db_and_tables()
    with db_session_scope() as db:
        if not app_config.RSS_FEED_URLS:
            db_feeds = db.query(FeedSource).all()
            if not db_feeds:
                print("No feeds configured (set RSS_FEED_URLS in .env or call 'seed-feeds' first).")
                return
        else:
            for url in app_config.RSS_FEED_URLS:
                add_or_update_feed_source(db, url)
    async def runner():
        with db_session_scope() as db:
            await update_all_subscribed_feeds(db)
    asyncio.run(runner())


def cmd_cleanup_bad(_args):
    from sqlalchemy import or_, func
    from .database.models import Article
    from .grouping.content_classifier import classify_article, NON_EVENT_TYPES
    create_db_and_tables()
    cutoff = app_config.MIN_ARTICLE_WORD_COUNT
    with db_session_scope() as db:
        all_articles = db.query(Article).all()
        to_delete = []
        for a in all_articles:
            if a.scraped_text_content and a.scraped_text_content.startswith("Scraping Error:"):
                to_delete.append(a.id)
                continue
            if (a.word_count or 0) < cutoff:
                to_delete.append(a.id)
                continue
            ct = classify_article(
                title=a.title or "",
                rss_description=a.rss_description or "",
                scraped_text=a.scraped_text_content or "",
            )
            if ct in NON_EVENT_TYPES:
                to_delete.append(a.id)
        n = db.query(Article).filter(Article.id.in_(to_delete)).delete(synchronize_session=False)
    print(f"Deleted {n} bad articles (scraping errors, too short, or non-event content_type)")


def cmd_group(_args):
    create_db_and_tables()
    llm = _get_grouping_llm()
    result = asyncio.run(grouping_engine.assign_new_articles(llm))
    print(f"Grouping result: {result}")


def cmd_regroup(_args):
    create_db_and_tables()
    llm = _get_grouping_llm()
    result = asyncio.run(grouping_engine.regroup_uncategorized(llm))
    print(f"Regroup result: {result}")


def cmd_recluster(args):
    create_db_and_tables()
    llm = _get_grouping_llm()
    recluster_module.mark_events_seen_in_recluster()
    result = asyncio.run(
        recluster_module.generate_recluster_diff(llm, auto_apply=args.apply)
    )
    print(f"Recluster result: {result}")


def cmd_lifecycle(_args):
    create_db_and_tables()
    print(f"Lifecycle result: {lifecycle_module.tick()}")


def cmd_summarize(args):
    create_db_and_tables()
    from .database.models import Event
    from .database.models import EventSummary, Article
    from .grouping.summarizer import generate_major_summary
    from .summarizer import initialize_llm
    from datetime import datetime, timezone
    from sqlalchemy import desc
    from .dependencies import get_llm_summary
    from fastapi import Request

    if not app_config.OPENAI_API_KEY:
        print("OPENAI_API_KEY not set")
        sys.exit(2)
    llm = initialize_llm(
        api_key=app_config.OPENAI_API_KEY,
        base_url=app_config.OPENAI_BASE_URL,
        model_name=app_config.DEFAULT_SUMMARY_MODEL_NAME,
        temperature=app_config.SUMMARY_LLM_TEMPERATURE,
        max_tokens=app_config.SUMMARY_MAX_OUTPUT_TOKENS,
    )
    with db_session_scope() as db:
        event = db.query(Event).filter(Event.id == args.event_id).first()
        if not event:
            print(f"Event {args.event_id} not found")
            sys.exit(1)
        articles = (
            db.query(Article)
            .filter(Article.event_id == args.event_id)
            .order_by(desc(Article.published_date))
            .all()
        )
        articles_data = [
            {
                "id": a.id, "title": a.title, "publisher_name": a.publisher_name,
                "published_date": a.published_date.isoformat() if a.published_date else None,
                "url": a.url, "word_count": a.word_count,
                "scraped_text_content": a.scraped_text_content, "rss_description": a.rss_description,
            }
            for a in articles
        ]
        prior = (
            db.query(EventSummary)
            .filter(EventSummary.event_id == args.event_id)
            .order_by(desc(EventSummary.generated_at))
            .first()
        )
        prior_json = prior.summary_json if prior else None
        event_name = event.name
        eid = event.id

    async def runner():
        return await generate_major_summary(
            event_name=event_name,
            articles=articles_data,
            prompt_template=app_config.DEFAULT_MAJOR_SUMMARY_PROMPT,
            prior_summary_json=prior_json,
            llm=llm,
        )

    summary_data = asyncio.run(runner())
    with db_session_scope() as db:
        article_ids = [a["id"] for a in articles_data]
        summary_data["article_ids"] = article_ids
        es = EventSummary(
            event_id=eid,
            summary_json=summary_data,
            article_ids=article_ids,
            article_count=len(articles_data),
            model_used=app_config.DEFAULT_SUMMARY_MODEL_NAME,
        )
        db.add(es)
        ev = db.query(Event).filter(Event.id == eid).first()
        if ev:
            ev.last_summary_at = datetime.now(timezone.utc)
            ev.summary_article_count = len(articles_data)
            ev.summary_version = (ev.summary_version or 0) + 1
    print(f"Summary generated for event {eid}: {summary_data.get('progressive_summary', '')[:200]}...")


def cmd_stats(_args):
    create_db_and_tables()
    from .database.models import Event, Article, ReclusterProposal, GroupingFeedback
    from sqlalchemy import func
    with db_session_scope() as db:
        total_articles = db.query(func.count(Article.id)).scalar() or 0
        ungrouped = db.query(func.count(Article.id)).filter(Article.event_id.is_(None)).scalar() or 0
        active = db.query(func.count(Event.id)).filter(Event.status == "active").scalar() or 0
        cooling = db.query(func.count(Event.id)).filter(Event.status == "cooling").scalar() or 0
        archived = db.query(func.count(Event.id)).filter(Event.status == "archived").scalar() or 0
        pending = db.query(func.count(ReclusterProposal.id)).filter(ReclusterProposal.applied == 0).scalar() or 0
        feedback = db.query(func.count(GroupingFeedback.id)).scalar() or 0
    print(f"Articles: {total_articles} total, {ungrouped} ungrouped")
    print(f"Events: {active} active, {cooling} cooling, {archived} archived")
    print(f"Proposals pending: {pending}")
    print(f"Feedback rows: {feedback}")


def cmd_serve(args):
    create_db_and_tables()
    uvicorn.run(
        "app.main_api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )



def main():
    parser = argparse.ArgumentParser(prog="fathom-stories", description="CLI for fathom-stories")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-db", help="Create database tables").set_defaults(func=cmd_init_db)
    sub.add_parser("migrate-visitor-id", help="Drop & recreate article_reads with visitor_id column (per-browser read state)").set_defaults(func=cmd_migrate_visitor_id)
    sub.add_parser("seed-feeds", help="Add feeds from .env RSS_FEED_URLS").set_defaults(func=cmd_seed_feeds)
    sub.add_parser("cleanup-bad", help="Delete articles with scraping errors or below MIN_ARTICLE_WORD_COUNT").set_defaults(func=cmd_cleanup_bad)
    sub.add_parser("fetch", help="One-shot RSS fetch + scrape").set_defaults(func=cmd_fetch)
    sub.add_parser("group", help="One-shot live grouping").set_defaults(func=cmd_group)
    sub.add_parser("regroup", help="One-shot forced regroup of ungrouped articles (creates events when 2+ match)").set_defaults(func=cmd_regroup)
    p_recluster = sub.add_parser("recluster", help="One-shot daily recluster")
    p_recluster.add_argument("--apply", action="store_true", help="Auto-apply cooling/revive proposals")
    p_recluster.set_defaults(func=cmd_recluster)
    sub.add_parser("lifecycle", help="One-shot archive/cool tick").set_defaults(func=cmd_lifecycle)
    p_summarize = sub.add_parser("summarize", help="Generate summary for an event")
    p_summarize.add_argument("event_id", type=int)
    p_summarize.set_defaults(func=cmd_summarize)
    sub.add_parser("stats", help="Show counts").set_defaults(func=cmd_stats)

    p_serve = sub.add_parser("serve", help="Run main API (with scheduler)")
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=app_config.MAIN_PORT)
    p_serve.add_argument("--reload", action="store_true")
    p_serve.set_defaults(func=cmd_serve)


    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
