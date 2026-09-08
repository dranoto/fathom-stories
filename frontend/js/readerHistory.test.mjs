// frontend/js/readerHistory.test.mjs
import test from "node:test";
import assert from "node:assert/strict";

let popstateHandler = null;
const pushed = [];
let backCalls = 0;

globalThis.window = {
  matchMedia() { return { matches: true }; },
  addEventListener(type, handler) {
    if (type === "popstate") popstateHandler = handler;
  },
};

globalThis.history = {
  pushState(state, _title, hash) {
    pushed.push({ state, hash });
  },
  back() {
    backCalls += 1;
  },
};

const { setupReaderHistory } = await import("./readerHistory.js");

test("mobile closeTop closes a single modal immediately and consumes popstate", () => {
  const popped = [];
  const nav = setupReaderHistory((entry) => popped.push(entry));

  nav.push({ kind: "menu" });
  assert.equal(nav.top().kind, "menu");
  nav.closeTop();

  assert.equal(nav.top(), null);
  assert.deepEqual(popped, [null]);
  assert.equal(backCalls, 1);
  assert.equal(pushed.at(-1).hash, "#menu");

  popstateHandler();
  assert.equal(nav.top(), null);
  assert.deepEqual(popped, [null]);
});

test("mobile closeTop reveals the previous modal without double-popping", () => {
  const popped = [];
  const nav = setupReaderHistory((entry) => popped.push(entry));

  nav.push({ kind: "summary", eventId: 7 });
  nav.push({ kind: "article", articleId: 42 });
  nav.closeTop();

  assert.deepEqual(nav.top(), { kind: "summary", eventId: 7 });
  assert.deepEqual(popped, [{ kind: "summary", eventId: 7 }]);

  popstateHandler();
  assert.deepEqual(nav.top(), { kind: "summary", eventId: 7 });
  assert.equal(popped.length, 1);

  nav.closeTop();
  assert.equal(nav.top(), null);
  assert.equal(popped.at(-1), null);
});

test("closeTop suppression expires when history.back emits no popstate", async () => {
  const popped = [];
  const nav = setupReaderHistory((entry) => popped.push(entry));

  nav.push({ kind: "article", articleId: 99 });
  nav.closeTop();
  await new Promise((resolve) => setTimeout(resolve, 550));

  nav.push({ kind: "summary", eventId: 8 });
  popstateHandler();
  assert.equal(nav.top(), null);
  assert.equal(popped.at(-1), null);
});
