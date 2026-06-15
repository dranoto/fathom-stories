# tests/test_reader_highlight.py
"""End-to-end tests for the reader behavior added in the move/remove + highlight commit.

Run with the server already running on http://localhost:8800:

    python3 -m tests.test_reader_highlight

Tests:
  1. test_highlight_opened_article — opening an article adds the current-article
     class to its bubble in the timeline pane, with a blue box-shadow.
     [FAST, no LLM]
  2. test_highlight_switches_when_opening_another_article — opening a second
     article moves the highlight to it and clears the first.
     [FAST, no LLM]
  3. test_move_flow_navigates_immediately — clicking Move on an inbox article
     navigates to the destination event tab and shows the article in the reader
     with the Remove button (no Move picker), and highlights it in the timeline.
     [SLOW: requires the LLM to complete the move-and-regenerate-summary call.
     May take 10-30s; in CI environments with no LLM key this will hang.]
  4. test_remove_flow_navigates_immediately — clicking Remove on an event
     article navigates to the event summary (no picker) and the article is
     gone from the timeline.
     [SLOW: same as test 3.]

Each test creates a fresh browser context, so they don't share state. The
server is expected to be running on $BASE_URL (default http://localhost:8800)
with $LLM_API_KEY set so the move/remove endpoints (which call the LLM to
regenerate the summary) can complete.

Run only the fast tests with:

    python3 -m tests.test_reader_highlight --fast
"""
import asyncio
import os
import sys
import time

from playwright.async_api import async_playwright

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8800")
HEADLESS = os.environ.get("HEADLESS", "1") == "1"
TIMEOUT_MS = int(os.environ.get("TIMEOUT_MS", "15000"))


def log(msg):
    print(f"  {msg}", flush=True)


async def goto_app(page):
    await page.goto(f"{BASE_URL}/", wait_until="networkidle", timeout=TIMEOUT_MS)
    await page.wait_for_timeout(2500)


async def open_inbox_tab(page):
    await page.evaluate("""() => {
        const t = document.querySelector('#event-tabs [data-inbox]');
        if (t) t.click();
    }""")
    await page.wait_for_timeout(1500)


async def open_first_bubble_in_pane(page):
    article_id = await page.evaluate("""() => {
        const b = document.querySelector('#event-pane .bubble[data-article-id]');
        if (b) { b.click(); return b.dataset.articleId; }
        return null;
    }""")
    await page.wait_for_timeout(1500)
    return article_id


async def get_picker_visible(page):
    return await page.evaluate("""() => {
        const p = document.getElementById('reader-event-picker');
        return !!(p && !p.hidden);
    }""")


async def get_event_options(page):
    return await page.evaluate("""() => {
        const s = document.getElementById('reader-event-select');
        return [...s.options].map(o => ({ value: o.value, text: o.textContent.trim() }));
    }""")


async def get_active_tab_event_id(page):
    return await page.evaluate("""() => {
        const a = document.querySelector('#event-tabs .event-tab.active');
        return a?.dataset?.eventId || null;
    }""")


async def get_reader_state(page):
    return await page.evaluate("""() => {
        const activeTab = document.querySelector('#event-tabs .event-tab.active');
        const removeBtn = document.getElementById('btn-remove-from-event');
        const picker = document.getElementById('reader-event-picker');
        const moveBtn = document.getElementById('btn-confirm-move');
        return {
            activeTabId: activeTab?.dataset?.eventId || null,
            removeBtnVisible: !!(removeBtn && !removeBtn.hidden),
            removeBtnText: removeBtn?.textContent || null,
            pickerVisible: !!(picker && !picker.hidden),
            moveBtnText: moveBtn?.textContent || null,
            bodyH1: document.querySelector('.reader-body h1')?.textContent || null,
        };
    }""")


async def get_bubble_highlight(page, article_id):
    return await page.evaluate(f"""() => {{
        const b = document.querySelector('.bubble[data-article-id="{article_id}"]');
        if (!b) return {{ found: false }};
        const cs = getComputedStyle(b);
        return {{
            found: true,
            hasClass: b.classList.contains('current-article'),
            boxShadow: cs.boxShadow,
        }};
    }}""")


async def wait_for_state(page, predicate, timeout_s=60, interval_s=1):
    """Polls the reader state until predicate(state) is truthy or timeout."""
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        last = await get_reader_state(page)
        if predicate(last):
            return last
        await page.wait_for_timeout(int(interval_s * 1000))
    return last


async def test_highlight_opened_article(browser):
    print("=== test_highlight_opened_article ===")
    ctx = await browser.new_context(viewport={"width": 1280, "height": 800})
    page = await ctx.new_page()
    page.on("dialog", lambda d: asyncio.create_task(d.accept()))
    await goto_app(page)
    await open_inbox_tab(page)

    article_id = await open_first_bubble_in_pane(page)
    assert article_id, "no bubble to open"
    log(f"opened: {article_id}")

    h = await get_bubble_highlight(page, article_id)
    log(f"highlight: {h}")
    assert h["found"], f"bubble {article_id} not in DOM"
    assert h["hasClass"], f"expected current-article class on bubble {article_id}"
    assert "96, 165, 250" in h["boxShadow"], f"expected blue box-shadow, got {h['boxShadow']!r}"
    print("  PASS")
    await ctx.close()


async def test_highlight_switches_when_opening_another_article(browser):
    print("=== test_highlight_switches_when_opening_another_article ===")
    ctx = await browser.new_context(viewport={"width": 1280, "height": 800})
    page = await ctx.new_page()
    page.on("dialog", lambda d: asyncio.create_task(d.accept()))
    await goto_app(page)
    await open_inbox_tab(page)

    first = await open_first_bubble_in_pane(page)
    assert first, "no first bubble"
    log(f"first: {first}")

    second = await page.evaluate(f"""() => {{
        const cur = document.querySelector('.bubble.current-article')?.dataset?.articleId;
        const bs = [...document.querySelectorAll('#event-pane .bubble[data-article-id]')];
        const next = bs.find(b => b.dataset.articleId !== cur);
        if (next) {{ next.click(); return next.dataset.articleId; }}
        return null;
    }}""")
    assert second, "no second bubble to open"
    log(f"second: {second}")
    await page.wait_for_timeout(1500)

    h1 = await get_bubble_highlight(page, first)
    h2 = await get_bubble_highlight(page, second)
    log(f"first highlight: {h1}")
    log(f"second highlight: {h2}")
    assert h2["hasClass"], "second bubble should be highlighted"
    if first != second:
        assert not h1["hasClass"], "first bubble should NOT be highlighted anymore"
    print("  PASS")
    await ctx.close()


async def test_move_flow_navigates_immediately(browser):
    print("=== test_move_flow_navigates_immediately ===")
    ctx = await browser.new_context(viewport={"width": 1280, "height": 800})
    page = await ctx.new_page()
    page.on("dialog", lambda d: asyncio.create_task(d.accept()))
    await goto_app(page)
    await open_inbox_tab(page)

    article_id = await open_first_bubble_in_pane(page)
    assert article_id, "no bubble to open"
    log(f"opened: {article_id}")

    picker_visible = await get_picker_visible(page)
    log(f"picker visible: {picker_visible}")
    assert picker_visible, "article should be ungrouped, picker should be visible"

    options = await get_event_options(page)
    log(f"event options: {len(options)}")
    assert len(options) > 1, "no events in dropdown"

    picked_event_id = options[1]["value"]
    picked_event_name = options[1]["text"]
    log(f"picking event: {picked_event_id} {picked_event_name!r}")

    await page.evaluate(f"""() => {{
        const s = document.getElementById('reader-event-select');
        s.value = '{picked_event_id}';
    }}""")
    await page.evaluate("document.getElementById('btn-confirm-move').click()")
    log("clicked Move; waiting for LLM call + nav...")

    final = await wait_for_state(
        page,
        lambda s: (
            str(s["activeTabId"]) == str(picked_event_id)
            and s["removeBtnVisible"]
            and not s["pickerVisible"]
        ),
        timeout_s=60,
    )
    log(f"final state: {final}")
    assert str(final["activeTabId"]) == str(picked_event_id), (
        f"expected active tab {picked_event_id}, got {final['activeTabId']!r}"
    )
    assert final["removeBtnVisible"], "Remove button should be visible after move"
    assert not final["pickerVisible"], "Move picker should be hidden after move"

    h = await get_bubble_highlight(page, article_id)
    log(f"moved article highlight: {h}")
    assert h["found"], f"moved article {article_id} not in destination event pane"
    assert h["hasClass"], f"moved article {article_id} not highlighted"

    print("  PASS")
    await ctx.close()


async def test_remove_flow_navigates_immediately(browser):
    print("=== test_remove_flow_navigates_immediately ===")
    ctx = await browser.new_context(viewport={"width": 1280, "height": 800})
    page = await ctx.new_page()
    page.on("dialog", lambda d: asyncio.create_task(d.accept()))
    await goto_app(page)

    # Find an event tab and switch to it
    event_id = await page.evaluate("""() => {
        const t = document.querySelector('#event-tabs .event-tab[data-event-id]');
        if (t) { t.click(); return t.dataset.eventId; }
        return null;
    }""")
    assert event_id, "no event tab found"
    log(f"opened event: {event_id}")
    await page.wait_for_timeout(1500)

    # Open an article in this event
    article_id = await open_first_bubble_in_pane(page)
    assert article_id, "no article in event pane"
    log(f"opened article: {article_id}")

    state_before = await get_reader_state(page)
    log(f"state before remove: {state_before}")
    assert not state_before["pickerVisible"], "article should be in an event, no picker"

    # Click Remove from event
    await page.evaluate("document.getElementById('btn-remove-from-event').click()")
    log("clicked Remove; waiting for LLM call + nav...")

    # After remove, the reader should show the event summary (no article, no picker)
    final = await wait_for_state(
        page,
        lambda s: (
            str(s["activeTabId"]) == str(event_id)
            and s["bodyH1"] is not None
            and "Event Summary" in s["bodyH1"]
        ),
        timeout_s=60,
    )
    log(f"final state: {final}")
    assert str(final["activeTabId"]) == str(event_id), (
        f"expected active tab {event_id}, got {final['activeTabId']!r}"
    )
    assert "Event Summary" in (final["bodyH1"] or ""), (
        f"expected event summary header, got {final['bodyH1']!r}"
    )
    assert not final["pickerVisible"], "picker should be hidden"
    assert not final["removeBtnVisible"], "remove button should be hidden (summary view)"

    # The article should be gone from the timeline
    h = await get_bubble_highlight(page, article_id)
    log(f"removed article highlight: {h}")
    assert not h["found"], f"removed article {article_id} should not be in timeline anymore"

    print("  PASS")
    await ctx.close()


async def main():
    fast_only = "--fast" in sys.argv
    failed = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=HEADLESS,
            args=["--no-sandbox"],
        )
        all_tests = [
            ("highlight_opened_article", test_highlight_opened_article),
            ("highlight_switches_when_opening_another_article", test_highlight_switches_when_opening_another_article),
            ("move_flow_navigates_immediately", test_move_flow_navigates_immediately),
            ("remove_flow_navigates_immediately", test_remove_flow_navigates_immediately),
        ]
        if fast_only:
            tests = all_tests[:2]
            print("--fast: running only highlight tests (no LLM dependency)")
        else:
            tests = all_tests
        for name, fn in tests:
            try:
                await fn(browser)
            except AssertionError as e:
                print(f"  FAIL: {e}", flush=True)
                failed.append(name)
            except Exception as e:
                print(f"  ERROR: {type(e).__name__}: {e}", flush=True)
                failed.append(name)
        await browser.close()

    if failed:
        print(f"\n{len(failed)} test(s) FAILED: {failed}")
        sys.exit(1)
    else:
        print("\nAll tests PASSED")


if __name__ == "__main__":
    asyncio.run(main())
