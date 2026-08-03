// frontend/js/score.js
export function computeEventScore(event, knobs) {
  const base = Number(knobs && knobs.base) || 2.0;
  const halfLife = Number(knobs && knobs.halfLifeHours) || 8.0;
  const floor = Number(knobs && knobs.importanceFloor);
  const floorW = Number.isFinite(floor) ? floor : 0.5;
  const capRaw = Number(knobs && knobs.magnitudeCap);
  const cap = Number.isFinite(capRaw) ? capRaw : 6.0;
  const count = Math.max(0, Number(event && event.article_count) || 0);
  const magnitude = Math.log(1 + count) / Math.log(base);
  const m = cap > 0 ? Math.min(magnitude, cap) : magnitude;
  const lastAt = event && event.last_article_at ? new Date(event.last_article_at) : null;
  let freshness = 0.5;
  if (lastAt && !isNaN(lastAt.getTime()) && halfLife > 0) {
    const ageHours = Math.max(0, (Date.now() - lastAt.getTime()) / 3600000);
    freshness = Math.pow(0.5, ageHours / halfLife);
  }
  const imp = Math.max(0, Math.min(1, Number(event && event.importance_avg) || 0));
  const importanceFactor = floorW + (1 - floorW) * imp;
  const baseScore = m * freshness * importanceFactor;
  return baseScore * computeNewnessBoost(event, knobs) * computeReadAllDemotion(event, knobs);
}

export function computeNewnessBoost(event, knobs) {
  const boostHours = Number(knobs && knobs.newEventBoostHours);
  const boostMax = Number(knobs && knobs.newEventBoostMax);
  if (!Number.isFinite(boostHours) || boostHours <= 0) return 1.0;
  if (!Number.isFinite(boostMax) || boostMax <= 1.0) return 1.0;
  const created = event && event.created_at ? new Date(event.created_at) : null;
  if (!created || isNaN(created.getTime())) return 1.0;
  const ageHours = Math.max(0, (Date.now() - created.getTime()) / 3600000);
  if (ageHours >= boostHours) return 1.0;
  const proximity = 1 - ageHours / boostHours;
  return 1 + (boostMax - 1) * proximity;
}

export function computeReadAllDemotion(event, knobs) {
  const demotion = Number(knobs && knobs.readAllDemotion);
  if (!Number.isFinite(demotion) || demotion >= 1.0) return 1.0;
  const count = Number(event && event.article_count) || 0;
  const unread = Number(event && event.unread_count);
  if (count <= 0) return 1.0;
  if (Number.isFinite(unread) && unread > 0) return 1.0;
  return demotion;
}

export function sortEventsByScore(events, knobs) {
  return [...(events || [])].sort((a, b) => {
    const sa = computeEventScore(a, knobs);
    const sb = computeEventScore(b, knobs);
    if (sb !== sa) return sb - sa;
    const ac = a.article_count || 0;
    const bc = b.article_count || 0;
    if (bc !== ac) return bc - ac;
    const at = a.last_article_at ? new Date(a.last_article_at).getTime() : 0;
    const bt = b.last_article_at ? new Date(b.last_article_at).getTime() : 0;
    return bt - at;
  });
}
