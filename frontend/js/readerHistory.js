// frontend/js/readerHistory.js
//
// Linear back-stack for the mobile UI. Each entry is a "modal-level" view
// the user can back out of. The stack is strictly LIFO with no replacement:
// there is no in-place view switching on mobile because the reader/menu
// fills the screen.
//
// The stack is mirrored to history.pushState so the OS back button (and
// the PWA edge gesture) drive the popstate listener. On desktop the
// pushState calls are skipped (no back button in the browser chrome and
// the URL bar showing "#menu" would be noise).
//
// Stack entries:
//   { kind: "summary", eventId }
//   { kind: "article", articleId }
//   { kind: "chat",    eventId }
//   { kind: "menu" }

let _stack = [];
let _suppressPop = false;
let _suppressTimer = null;
let _onPop = null;
let _nav = null;

function _isMobile() {
  if (typeof window === "undefined") return false;
  return window.matchMedia("(max-width: 699px)").matches;
}

function _hashFor(entry) {
  switch (entry.kind) {
    case "summary": return `#summary/${entry.eventId}`;
    case "article": return `#article/${entry.articleId}`;
    case "chat":    return `#chat/${entry.eventId}`;
    case "menu":    return "#menu";
    default:        return "";
  }
}

function _onPopState() {
  if (_suppressPop) {
    _suppressPop = false;
    if (_suppressTimer !== null) {
      clearTimeout(_suppressTimer);
      _suppressTimer = null;
    }
    return;
  }
  if (_stack.length === 0) {
    return;
  }
  _stack.pop();
  if (_stack.length === 0) {
    if (_onPop) _onPop(null);
    return;
  }
  const top = _stack[_stack.length - 1];
  if (_onPop) _onPop(top);
}

export function setupReaderHistory(onPop) {
  _onPop = onPop;
  window.addEventListener("popstate", _onPopState);
  _nav = {
    push(entry) {
      if (!_isMobile()) return;
      _stack.push(entry);
      try {
        history.pushState({ reader: true, kind: entry.kind }, "", _hashFor(entry));
      } catch (_) {}
    },
    closeTop() {
      if (_stack.length === 0) {
        if (_onPop) _onPop(null);
        return;
      }
      const isMobile = _isMobile();
      _stack.pop();
      const next = _stack.length > 0 ? _stack[_stack.length - 1] : null;
      if (_onPop) _onPop(next);
      if (!isMobile) return;
      _suppressPop = true;
      if (_suppressTimer !== null) clearTimeout(_suppressTimer);
      _suppressTimer = setTimeout(() => {
        _suppressPop = false;
        _suppressTimer = null;
      }, 500);
      try {
        history.back();
      } catch (_) {
        _suppressPop = false;
        clearTimeout(_suppressTimer);
        _suppressTimer = null;
      }
    },
    isEmpty: () => _stack.length === 0,
    top: () => (_stack.length > 0 ? _stack[_stack.length - 1] : null),
    isMobile: _isMobile,
  };
  return _nav;
}
