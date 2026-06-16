# app/cli.py
import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone, timedelta
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


def cmd_backfill_expiry(_args):
    from sqlalchemy import inspect, text
    from .database.models import Event
    from .database import engine
    from datetime import datetime, timezone

    insp = inspect(engine)
    if "events" not in insp.get_table_names():
        print("events table does not exist; run init-db first.")
        return
    cols = {c["name"] for c in insp.get_columns("events")}
    if "expires_at" not in cols:
        print("events.expires_at column does not exist; run init-db first.")
        return
    with db_session_scope() as db:
        targets = db.query(Event).filter(Event.expires_at.is_(None)).all()
        if not targets:
            print("No events with NULL expires_at; nothing to do.")
            return
        from .grouping.lifecycle import reset_expiry
        n = 0
        for ev in targets:
            ev.expires_at = reset_expiry()
            n += 1
    print(f"Backfilled expires_at on {n} events.")


def cmd_backfill_expiry_48h(args):
    """
    One-shot: reset every active event's expires_at to now + 48h.

    Use this after the initial migration to clean up stale 24h values
    that the original backfill set before the rule changed to 48h.
    """
    from .database.models import Event
    from .grouping.lifecycle import reset_expiry
    with db_session_scope() as db:
        targets = db.query(Event).filter(Event.status == "active").all()
        if not targets:
            print("No active events.")
            return
        new_expiry = reset_expiry()
        for ev in targets:
            old = ev.expires_at
            if not args.apply:
                print(f"  RESET: id={ev.id} name={ev.name!r} {old} -> {new_expiry}")
            else:
                ev.expires_at = new_expiry
        if not args.apply:
            print(f"\n{len(targets)} event(s) would be reset. Re-run with --apply to commit.")
            return
    print(f"\nReset {len(targets)} event(s) to expires_at = {new_expiry}.")


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


def cmd_purge_percent_off(args):
    import re
    from .database.models import Article
    from .rss_client import PERCENT_OFF_PATTERN

    create_db_and_tables()

    with db_session_scope() as db:
        matches = []
        for a in db.query(Article).all():
            if a.title and PERCENT_OFF_PATTERN.search(a.title):
                matches.append(a)

    if not matches:
        print("No articles with '% off' in the title. Nothing to do.")
        return

    print(f"Found {len(matches)} article(s) with '% off' in the title:")
    for a in matches[:20]:
        pub = a.publisher_name or "(unknown)"
        print(f"  - id={a.id} {pub}: {a.title[:90]!r}")
    if len(matches) > 20:
        print(f"  ... and {len(matches) - 20} more.")

    if not args.apply:
        print("\nDry-run only. Re-run with --apply to commit.")
        return

    ids = [a.id for a in matches]
    with db_session_scope() as db:
        n = db.query(Article).filter(Article.id.in_(ids)).delete(synchronize_session=False)
    print(f"\nDeleted {n} article(s).")


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


def cmd_disband_same_source(args):
    from sqlalchemy import func
    from .database.models import Event, Article, EventSummary

    create_db_and_tables()
    now = datetime.now(timezone.utc)

    with db_session_scope() as db:
        candidates = (
            db.query(Event)
            .join(Article, Article.event_id == Event.id)
            .group_by(Event.id)
            .having(func.count(Article.id) <= 2)
            .having(
                func.count(func.distinct(func.coalesce(Article.publisher_name, ""))) <= 1
            )
            .order_by(Event.id)
            .all()
        )

    if not candidates:
        print("No candidates found (events with <=2 articles from a single source).")
        return

    print(f"Found {len(candidates)} candidate event(s):")
    for ev in candidates:
        with db_session_scope() as db:
            arts = db.query(Article).filter(Article.event_id == ev.id).all()
        pubs = sorted({a.publisher_name for a in arts})
        titles = [a.title for a in arts if a.title]
        print(
            f"  event {ev.id} status={ev.status} name={ev.name!r} "
            f"articles={len(arts)} publishers={pubs} last_article_at={ev.last_article_at}"
        )
        for t in titles:
            print(f"      - {t}")

    if not args.apply:
        print("\nDry-run only. Re-run with --apply to commit.")
        return

    disbanded = 0
    displaced = 0
    with db_session_scope() as db:
        ev_ids = [ev.id for ev in candidates]
        target_events = db.query(Event).filter(Event.id.in_(ev_ids)).all()
        for ev in target_events:
            arts = db.query(Article).filter(Article.event_id == ev.id).all()
            for a in arts:
                a.event_id = None
                a.proposed_event_name = ev.name
                a.grouped_at = now
                displaced += 1
            db.query(EventSummary).filter(
                EventSummary.event_id == ev.id
            ).delete(synchronize_session=False)
            db.query(Event).filter(Event.id == ev.id).delete(synchronize_session=False)
            logger.info(
                f"DISBAND: event {ev.id} {ev.name!r} "
                f"({len(arts)} article(s), status={ev.status}) — articles back to inbox"
            )
            disbanded += 1

    print(f"\nDisbanded {disbanded} event(s); {displaced} article(s) back to inbox (proposed_event_name set).")


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
    from datetime import datetime, timezone, timedelta
    with db_session_scope() as db:
        total_articles = db.query(func.count(Article.id)).scalar() or 0
        ungrouped = db.query(func.count(Article.id)).filter(Article.event_id.is_(None)).scalar() or 0
        active = db.query(func.count(Event.id)).filter(Event.status == "active").scalar() or 0
        cooling = db.query(func.count(Event.id)).filter(Event.status == "cooling").scalar() or 0
        archived = db.query(func.count(Event.id)).filter(Event.status == "archived").scalar() or 0
        recent_archived = db.query(func.count(Event.id)).filter(
            Event.status == "archived",
            Event.archived_at >= datetime.now(timezone.utc) - timedelta(hours=24),
        ).scalar() or 0
        pending = db.query(func.count(ReclusterProposal.id)).filter(ReclusterProposal.applied == 0).scalar() or 0
        feedback = db.query(func.count(GroupingFeedback.id)).scalar() or 0
    print(f"Articles: {total_articles} total, {ungrouped} ungrouped")
    print(f"Events: {active} active, {cooling} cooling, {archived} archived ({recent_archived} archived in last 24h)")
    print(f"Proposals pending: {pending}")
    print(f"Feedback rows: {feedback}")


def cmd_test_extraction(args):
    import asyncio
    from .extraction_tests import test_feed, print_verdict

    urls = list(args.url) if args.url else []
    if not urls and app_config.RSS_FEED_URLS:
        urls = list(app_config.RSS_FEED_URLS)
    if not urls:
        print("No feed URLs provided. Pass --url one or more times, or set RSS_FEED_URLS in .env.")
        sys.exit(2)
    if args.url_file:
        try:
            with open(args.url_file, "r", encoding="utf-8") as fh:
                extra = [line.strip() for line in fh if line.strip() and not line.startswith("#")]
                urls.extend(extra)
        except OSError as e:
            print(f"Failed to read URL file {args.url_file}: {e}")
            sys.exit(2)

    async def runner():
        results = []
        for u in urls:
            print(f"Testing feed: {u}")
            verdict = await test_feed(u, sample_size=args.sample, min_words=args.min_words)
            results.append(verdict)
        return results

    results = asyncio.run(runner())
    for v in results:
        print_verdict(v)
    fails = [v for v in results if not v.overall_pass]
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump([v.as_dict() for v in results], fh, indent=2, default=str)
        print(f"\nWrote JSON verdict to {args.json_out}")
    if fails:
        print(f"\n{fails} feed(s) failed overall gates.")
        sys.exit(1)
    print(f"\nAll {len(results)} feed(s) passed overall.")


def cmd_dedup_events(args):
    from sqlalchemy import func
    from .database.models import Event
    from .grouping.engine import _normalize_event_name

    with db_session_scope() as db:
        events = (
            db.query(Event)
            .filter(Event.status.in_(("active", "cooling", "archived")))
            .all()
        )
        groups: dict = {}
        for ev in events:
            norm = _normalize_event_name(ev.name)
            if not norm:
                continue
            groups.setdefault(norm, []).append(ev)

        actions = []
        for norm, evs in groups.items():
            if len(evs) < 2:
                continue
            evs_sorted = sorted(
                evs,
                key=lambda e: (
                    -sum(1 for a in (e.articles or []) if a.event_id == e.id),
                    e.created_at,
                ),
            )
            primary = evs_sorted[0]
            for secondary in evs_sorted[1:]:
                n_articles = sum(1 for a in (secondary.articles or []) if a.event_id == secondary.id)
                if n_articles == 0:
                    actions.append(("delete_empty_phantom", primary, secondary, 0))
                else:
                    actions.append(("merge", primary, secondary, n_articles))

    if not actions:
        print("No duplicate-name groups found.")
        return

    print(f"Found {len(actions)} action(s):")
    for kind, primary, secondary, n in actions:
        if kind == "delete_empty_phantom":
            print(f"  DELETE empty phantom: id={secondary.id} name={secondary.name!r} (0 articles)")
        else:
            print(f"  MERGE: id={secondary.id} ({n} articles) -> id={primary.id} ({primary.name!r})")

    if not args.apply:
        print("\nDry-run only. Re-run with --apply to commit.")
        return

    from .grouping.dedup import merge_events
    applied = 0
    with db_session_scope() as db:
        for kind, primary, secondary, n in actions:
            if kind == "delete_empty_phantom":
                db.query(Event).filter(Event.id == secondary.id).delete(synchronize_session=False)
                applied += 1
            else:
                if merge_events(db, primary.id, secondary.id, kind="cli_dedup"):
                    applied += 1
    print(f"\nApplied {applied} action(s).")


def cmd_revive_recent(args):
    from .database.models import Event
    from .grouping.lifecycle import reset_expiry

    with db_session_scope() as db:
        targets = (
            db.query(Event)
            .filter(
                Event.status == "archived",
                Event.last_article_at.isnot(None),
                Event.last_article_at >= datetime.now(timezone.utc) - timedelta(days=7),
            )
            .order_by(Event.id)
            .all()
        )
        if not targets:
            print("No archived events with articles in the last 7 days.")
            return

        for ev in targets:
            new_expiry = reset_expiry()
            if not args.apply:
                print(f"  REVIVE: id={ev.id} name={ev.name!r} last_article_at={ev.last_article_at} -> expires_at={new_expiry}")
            else:
                ev.status = "active"
                ev.archived_at = None
                ev.expires_at = new_expiry

        if not args.apply:
            print(f"\n{len(targets)} event(s) would be revived. Re-run with --apply to commit.")
            return
    print(f"\nRevived {len(targets)} event(s).")


def cmd_purge_archive(args):
    create_db_and_tables()
    n = lifecycle_module.purge_ancient_archives(limit=args.limit)
    print(f"Purge result: {n} archived events deleted (older than {app_config.PURGE_ARCHIVE_AFTER_DAYS}d)")


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
    sub.add_parser("backfill-expiry", help="One-shot: set expires_at on events with NULL expires_at").set_defaults(func=cmd_backfill_expiry)
    p_bf48 = sub.add_parser("backfill-expiry-48h", help="One-shot: reset every active event's expires_at to now + 48h. Dry-run by default.")
    p_bf48.add_argument("--apply", action="store_true", help="Commit the reset (default: dry-run)")
    p_bf48.set_defaults(func=cmd_backfill_expiry_48h)
    sub.add_parser("seed-feeds", help="Add feeds from .env RSS_FEED_URLS").set_defaults(func=cmd_seed_feeds)
    sub.add_parser("cleanup-bad", help="Delete articles with scraping errors or below MIN_ARTICLE_WORD_COUNT").set_defaults(func=cmd_cleanup_bad)

    p_pctoff = sub.add_parser(
        "purge-percent-off",
        help="One-shot: delete articles whose title contains '% off' (promo/deal spam). Dry-run by default.",
    )
    p_pctoff.add_argument("--apply", action="store_true", help="Commit the deletion (default: dry-run)")
    p_pctoff.set_defaults(func=cmd_purge_percent_off)
    sub.add_parser("fetch", help="One-shot RSS fetch + scrape").set_defaults(func=cmd_fetch)
    sub.add_parser("group", help="One-shot live grouping").set_defaults(func=cmd_group)
    sub.add_parser("regroup", help="One-shot forced regroup of ungrouped articles (creates events when 2+ match)").set_defaults(func=cmd_regroup)
    p_recluster = sub.add_parser("recluster", help="One-shot daily recluster")
    p_recluster.add_argument("--apply", action="store_true", help="Auto-apply cooling/revive proposals")
    p_recluster.set_defaults(func=cmd_recluster)

    p_disband = sub.add_parser(
        "disband-same-source",
        help="One-shot: disband events with <=2 articles from a single publisher_name. Dry-run by default.",
    )
    p_disband.add_argument("--apply", action="store_true", help="Commit the disband (default: dry-run)")
    p_disband.set_defaults(func=cmd_disband_same_source)
    sub.add_parser("lifecycle", help="One-shot archive/cool tick").set_defaults(func=cmd_lifecycle)
    p_summarize = sub.add_parser("summarize", help="Generate summary for an event")
    p_summarize.add_argument("event_id", type=int)
    p_summarize.set_defaults(func=cmd_summarize)
    sub.add_parser("stats", help="Show counts").set_defaults(func=cmd_stats)
    p_purge = sub.add_parser("purge-archive", help="One-shot: delete archived events older than PURGE_ARCHIVE_AFTER_DAYS")
    p_purge.add_argument("--limit", type=int, default=app_config.PURGE_BATCH_LIMIT, help="Max events to delete in this run (default: PURGE_BATCH_LIMIT)")
    p_purge.set_defaults(func=cmd_purge_archive)

    p_dedup = sub.add_parser("dedup-events", help="One-shot: find duplicate-name event groups and merge/delete. Dry-run by default.")
    p_dedup.add_argument("--apply", action="store_true", help="Commit the proposed dedup actions (default: dry-run)")
    p_dedup.set_defaults(func=cmd_dedup_events)

    p_revive = sub.add_parser("revive-recent", help="One-shot: revive archived events whose last article is < 7d old. Dry-run by default.")
    p_revive.add_argument("--apply", action="store_true", help="Commit the proposed revivals (default: dry-run)")
    p_revive.set_defaults(func=cmd_revive_recent)

    p_test = sub.add_parser("test-extraction", help="Run extraction tests on candidate RSS feed URL(s)")
    p_test.add_argument("--url", action="append", help="Feed URL to test (repeatable). Defaults to RSS_FEED_URLS from .env.")
    p_test.add_argument("--url-file", help="Path to a file with one feed URL per line")
    p_test.add_argument("--sample", type=int, default=5, help="Number of entries to sample per feed (default: 5)")
    p_test.add_argument("--min-words", type=int, default=app_config.MIN_ARTICLE_WORD_COUNT, help="Minimum scraped word count to count as 'full text extracted' (default: MIN_ARTICLE_WORD_COUNT)")
    p_test.add_argument("--json-out", help="If set, write the full verdict list to this file as JSON")
    p_test.set_defaults(func=cmd_test_extraction)

    p_serve = sub.add_parser("serve", help="Run main API (with scheduler)")
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=app_config.MAIN_PORT)
    p_serve.add_argument("--reload", action="store_true")
    p_serve.set_defaults(func=cmd_serve)


    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
