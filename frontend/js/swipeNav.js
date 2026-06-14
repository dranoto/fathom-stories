// frontend/js/swipeNav.js
const SWIPE_THRESHOLD = 60;
const SWIPE_VELOCITY = 0.3;
const HORIZONTAL_LOCK = 0.7;

let tracking = null;

function isSwipeableSurface(el) {
  if (!el) return false;
  if (el.closest(".reader-pane:not([hidden])")) return false;
  if (el.closest("#mobile-menu:not([hidden])")) return false;
  if (el.closest("button")) return false;
  if (el.closest("a")) return false;
  if (el.closest("select")) return false;
  if (el.closest("input")) return false;
  if (el.closest("textarea")) return false;
  return true;
}

function onTouchStart(e) {
  if (e.touches.length !== 1) return;
  if (!isSwipeableSurface(e.target)) return;
  const t = e.touches[0];
  tracking = {
    startX: t.clientX,
    startY: t.clientY,
    startT: Date.now(),
    lastX: t.clientX,
    lastT: Date.now(),
    el: e.target,
  };
}

function onTouchMove(e) {
  if (!tracking) return;
  const t = e.touches[0];
  const dx = t.clientX - tracking.startX;
  const dy = t.clientY - tracking.startY;
  if (Math.abs(dx) > 5 && Math.abs(dx) > Math.abs(dy) * 1.2) {
    e.preventDefault();
  }
  tracking.lastX = t.clientX;
  tracking.lastT = Date.now();
}

function onTouchEnd(e) {
  if (!tracking) return;
  const dt = Date.now() - tracking.startT;
  const dx = tracking.lastX - tracking.startX;
  const absDx = Math.abs(dx);
  const dy = (e.changedTouches[0] ? e.changedTouches[0].clientY : tracking.startY) - tracking.startY;
  const absDy = Math.abs(dy);
  const velocity = absDx / Math.max(1, dt);
  const horizontalEnough = absDx > SWIPE_THRESHOLD && absDx > absDy * HORIZONTAL_LOCK;
  const fastEnough = velocity > SWIPE_VELOCITY;
  if (horizontalEnough || (fastEnough && absDx > 30 && absDx > absDy)) {
    const direction = dx < 0 ? "next" : "prev";
    window.dispatchEvent(new CustomEvent("swipe-nav", { detail: { direction } }));
  }
  tracking = null;
}

export function setupSwipeNav(target) {
  if (!target) return;
  target.addEventListener("touchstart", onTouchStart, { passive: true });
  target.addEventListener("touchmove", onTouchMove, { passive: false });
  target.addEventListener("touchend", onTouchEnd, { passive: true });
  target.addEventListener("touchcancel", () => { tracking = null; }, { passive: true });
}
