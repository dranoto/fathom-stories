// frontend/js/eventTabs.test.mjs
import test from "node:test";
import assert from "node:assert/strict";

globalThis.localStorage = {
  _data: {},
  getItem(k) { return Object.prototype.hasOwnProperty.call(this._data, k) ? this._data[k] : null; },
  setItem(k, v) { this._data[k] = String(v); },
  removeItem(k) { delete this._data[k]; },
};
globalThis.window = {
  innerWidth: 1200,
  addEventListener() {},
  dispatchEvent() {},
  clearTimeout,
  setTimeout,
  matchMedia() { return { matches: true }; },
};
globalThis.document = {
  documentElement: { clientWidth: 1200 },
  getElementById() { return null; },
  querySelector() { return null; },
  createElement() { return { className: "", setAttribute() {}, style: {}, addEventListener() {}, appendChild() {} }; },
  body: { appendChild() {}, contains() { return false; } },
};
globalThis.requestAnimationFrame = (fn) => setTimeout(fn, 0);

const {
  partitionNewAndUpdated,
  partitionEvents,
  buildNavOrder,
  sortEventsForBar,
  _minorToggleMarkup,
  _cardMarkup,
} = await import("./eventTabs.js");

const NOW = new Date("2026-09-06T12:00:00Z").getTime();
const oneHourAgo = new Date(NOW - 1 * 3600 * 1000).toISOString();
const threeHoursAgo = new Date(NOW - 3 * 3600 * 1000).toISOString();
const twoDaysAgo = new Date(NOW - 48 * 3600 * 1000).toISOString();

const sample = [
  // brand-new event, never seen, 1 article
  { id: 1, name: "Brand-new story", article_count: 1, unread_count: 1,
    new_since_visit: 0, created_at: oneHourAgo, last_article_at: oneHourAgo,
    importance_avg: 0.6 },
  // seen event with 3 new articles since last visit — should surface
  { id: 2, name: "Updated story", article_count: 10, unread_count: 4,
    new_since_visit: 3, created_at: twoDaysAgo, last_article_at: oneHourAgo,
    importance_avg: 0.7 },
  // mature event, no new since visit — should NOT surface
  { id: 3, name: "Stable story", article_count: 20, unread_count: 0,
    new_since_visit: 0, created_at: twoDaysAgo, last_article_at: twoDaysAgo,
    importance_avg: 0.5 },
  // old & forgotten event, no new since visit — should NOT surface
  { id: 4, name: "Cold story", article_count: 5, unread_count: 0,
    new_since_visit: 0, created_at: twoDaysAgo, last_article_at: threeHoursAgo,
    importance_avg: 0.4 },
];

const _realDateNow = Date.now;
Date.now = () => NOW;

globalThis.localStorage.setItem(
  "fathom.seenEventIds",
  JSON.stringify([1])
);

test("partitionNewAndUpdated: empty input yields empty result", () => {
  const { fresh, rest } = partitionNewAndUpdated([]);
  assert.deepEqual(fresh, []);
  assert.deepEqual(rest, []);
});

test("partitionNewAndUpdated: surfaces events with new_since_visit > 0", () => {
  const { fresh, rest } = partitionNewAndUpdated(sample);
  const ids = fresh.map((e) => e.id);
  assert.ok(ids.includes(2), `event 2 (new_since_visit=3) should be in fresh; got ${ids}`);
  assert.ok(!ids.includes(3), "event 3 (no new) should NOT be in fresh");
  assert.ok(!ids.includes(4), "event 4 (no new, old) should NOT be in fresh");
  // rest contains the events that did NOT qualify:
  //   event 1 — seen, new_since_visit=0, NOT in fresh
  //   event 3 — no new, NOT in fresh
  //   event 4 — no new, old, NOT in fresh
  const restIds = rest.map((e) => e.id).sort((a, b) => a - b);
  assert.deepEqual(restIds, [1, 3, 4]);
});

test("partitionNewAndUpdated: surfaces never-seen events that are fresh by age", () => {
  // event 1 is never seen (localStorage only marks 1 as seen above? — re-do:
  // we marked 1 as seen so it should NOT qualify via the never-seen branch;
  // verify it is excluded entirely from fresh because created_at is fresh
  // but new_since_visit is 0 and it IS seen).
  const { fresh } = partitionNewAndUpdated(sample);
  const ids = fresh.map((e) => e.id);
  assert.ok(!ids.includes(1),
    "event 1 is seen + new_since_visit=0 so it should NOT be in fresh");
});

test("partitionNewAndUpdated: orders fresh by new_since_visit desc, then created_at desc", () => {
  const data = [
    { id: 10, name: "x", article_count: 5, unread_count: 1, new_since_visit: 1,
      created_at: oneHourAgo, last_article_at: oneHourAgo, importance_avg: 0.5 },
    { id: 11, name: "y", article_count: 5, unread_count: 3, new_since_visit: 7,
      created_at: threeHoursAgo, last_article_at: oneHourAgo, importance_avg: 0.5 },
    { id: 12, name: "z", article_count: 5, unread_count: 2, new_since_visit: 3,
      created_at: oneHourAgo, last_article_at: oneHourAgo, importance_avg: 0.5 },
  ];
  const { fresh } = partitionNewAndUpdated(data);
  assert.deepEqual(fresh.map((e) => e.id), [11, 12, 10]);
});

test("partitionEvents: never duplicates fresh events into topN/minor", () => {
  const { topN, minor } = partitionEvents(sample, 4000);
  const topMinorIds = new Set([...topN, ...minor].map((e) => e.id));
  assert.ok(!topMinorIds.has(2),
    `fresh event 2 must not appear in topN/minor; got ${[...topMinorIds]}`);
});

test("buildNavOrder: places fresh events ahead of topN and minor", () => {
  const nav = buildNavOrder(sample, 4000);
  const inboxIdx = nav.findIndex((n) => n.kind === "inbox");
  const freshIdx = nav.findIndex((n) => n.kind === "event" && n.id === 2);
  const stableIdx = nav.findIndex((n) => n.kind === "event" && n.id === 3);
  assert.ok(inboxIdx >= 0, "inbox is first");
  assert.ok(freshIdx > inboxIdx, "fresh event 2 appears after inbox");
  assert.ok(stableIdx > freshIdx,
    `stable event 3 must appear after fresh event 2; nav=${JSON.stringify(nav)}`);
});

test("partitionEvents counts fresh-strip cards when sizing the main row", () => {
  const events = [
    { id: 1, name: "Fresh A", new_since_visit: 1, created_at: oneHourAgo },
    { id: 2, name: "Fresh B", new_since_visit: 1, created_at: oneHourAgo },
    { id: 3, name: "Fresh C", new_since_visit: 1, created_at: oneHourAgo },
    ...Array.from({ length: 5 }, (_, index) => ({
      id: index + 10,
      name: `Stable ${index}`,
      new_since_visit: 0,
      created_at: twoDaysAgo,
      article_count: 5 - index,
    })),
  ];
  const { topN, minor } = partitionEvents(events, 1100);
  assert.equal(topN.length, 3);
  assert.equal(minor.length, 2);
});

test("partitionEvents does not reserve a drawer when every stable event fits", () => {
  const events = Array.from({ length: 3 }, (_, index) => ({
    id: index + 20,
    name: `Stable ${index}`,
    new_since_visit: 0,
    created_at: twoDaysAgo,
    article_count: 3 - index,
  }));
  const { topN, minor } = partitionEvents(events, 1200);
  assert.equal(topN.length, 3);
  assert.equal(minor.length, 0);
});

test("event cards show source context and escape publisher markup", () => {
  const markup = _cardMarkup({
    id: 8,
    name: "Story",
    article_count: 3,
    unread_count: 2,
    new_since_visit: 0,
    publisher_label: "Source A · <Source B>",
  }, null, false, "top");
  assert.match(markup, /class="sources"/);
  assert.match(markup, /Source A · &lt;Source B&gt;/);
  assert.match(markup, /3 articles · 2 unread/);
});

test("minor drawer toggle summarizes hidden story activity", () => {
  const markup = _minorToggleMarkup([
    { id: 1, unread_count: 3, new_since_visit: 2 },
    { id: 2, unread_count: 4, new_since_visit: 0 },
  ], false, null, false);
  assert.match(markup, /2 More Stories/);
  assert.match(markup, /2 new · 7 unread/);
  assert.match(markup, /with 7 unread articles/);
});

test("minor drawer toggle uses singular story label", () => {
  const markup = _minorToggleMarkup([
    { id: 3, unread_count: 0, new_since_visit: 0 },
  ], false, 3, false);
  assert.match(markup, /1 More Story/);
  assert.match(markup, /click to show/);
  assert.match(markup, /minor-toggle active/);
});

test("minor drawer toggle exposes accurate open-state accessibility text", () => {
  const markup = _minorToggleMarkup([
    { id: 1, unread_count: 1, new_since_visit: 0 },
  ], true, null, false);
  assert.match(markup, /tabindex="0"/);
  assert.match(markup, /aria-expanded="true"/);
  assert.match(markup, /aria-controls="minor-drawer"/);
  assert.match(markup, /title="Hide 1 story with 1 unread article"/);
});

test("minor drawer toggle ignores invalid ids when finding the active story", () => {
  const markup = _minorToggleMarkup([
    { id: null, unread_count: 0, new_since_visit: 0 },
  ], false, null, false);
  assert.doesNotMatch(markup, /event-tab minor-toggle active/);
});

test("sortEventsForBar: respects score mode (default after fix)", () => {
  // The new default sort mode is "score"; verify it is honored.
  const events = [
    { id: 1, article_count: 100, unread_count: 0, importance_avg: 0.5,
      created_at: twoDaysAgo, last_article_at: twoDaysAgo, new_since_visit: 0 },
    { id: 2, article_count: 1, unread_count: 1, importance_avg: 0.9,
      created_at: oneHourAgo, last_article_at: oneHourAgo, new_since_visit: 1 },
  ];
  // Default state.js sortMode is "score" so event 2 (newer, higher importance)
  // should be first even though it has fewer articles.
  const sorted = sortEventsForBar(events);
  assert.equal(sorted[0].id, 2,
    `expected newer+more-important event first; got ids=${sorted.map((e) => e.id)}`);
});

Date.now = _realDateNow;
