/* Аэрофлот · Диспетчер ОТО */
const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = sec => {
  if (sec == null) return '—';
  sec = Math.max(0, Math.round(sec));
  return String(Math.floor(sec / 60)).padStart(2, '0') + ':' + String(sec % 60).padStart(2, '0');
};
const clockHM = simSec => {
  const base = new Date(); base.setHours(6, 0, 0, 0);
  const t = new Date(base.getTime() + (simSec || 0) * 1000);
  return String(t.getHours()).padStart(2, '0') + ':' + String(t.getMinutes()).padStart(2, '0');
};
const NS = 'http://www.w3.org/2000/svg';
const QCOL = {'Планер': 'var(--glider)', 'Двигатель': 'var(--engine)', 'Авионика': 'var(--avionics)'};
const AIRCRAFT_TYPES = ['A320', 'A330', 'A350', 'B737', 'B777', 'B787', 'SSJ-100', 'MC-21'];
const AC_COL = {inbound: '#5D8568', parked: '#577491', departing: '#AC7444'};
const NORM = 900;

let AIR = null, S = null, ws = null, ME = null;
let role = 'dispatcher';
let selReq = null, selTech = null, selAircraft = null, selVehicle = null;
const openCands = new Set();
const cache = {};
const mapEl = {edges: {}, stands: {}, techs: {}, aircraft: {}, gse: {}, vehicles: {}};
const mapMotion = new Map();
let mapMotionFrame = 0;
let vb = {x: 0, y: 0, w: 1, h: 1};
const bootStartedAt = performance.now();
let moveTechMode = false;

function bootStage(stage, message) {
  const status = $('#bootStatus');
  const progress = $('#bootProgress');
  if (status) status.textContent = message;
  if (progress) progress.style.width = `${[12, 48, 82, 100][stage] || 100}%`;
  document.querySelectorAll('.boot-stages [data-stage]').forEach(node => {
    const value = Number(node.dataset.stage);
    node.classList.toggle('active', value === Math.min(stage, 2));
    node.classList.toggle('done', value < stage);
  });
}

async function finishBoot() {
  bootStage(3, 'Смена готова к работе');
  const hold = Math.max(0, 1250 - (performance.now() - bootStartedAt));
  await new Promise(resolve => setTimeout(resolve, hold));
  document.body.classList.add('is-ready');
  setTimeout(() => $('#bootScreen')?.remove(), 760);
}

window.addEventListener('error', e => {
  console.error('[JS error]', e.message, (e.filename || '') + ':' + (e.lineno || ''));
});

/* ---------- сеть ---------- */
async function api(path, body) {
  const opts = {method: 'POST', headers: {'Content-Type': 'application/json'}, credentials: 'same-origin'};
  if (body !== undefined) opts.body = JSON.stringify(body);
  try {
    const r = await fetch(path, opts);
    if (r.status === 401) {
      location.replace('/login?next=/');
      throw new Error('Требуется вход');
    }
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      throw new Error(j.detail || 'Ошибка сервера');
    }
    return await r.json();
  } catch (e) { toast(e.message, true); throw e; }
}
function toast(text, bad) {
  const t = $('#toast');
  if (!t) return;
  t.textContent = text;
  t.className = 'toast' + (bad ? ' bad' : '');
  t.hidden = false;
  clearTimeout(toast.h);
  toast.h = setTimeout(() => (t.hidden = true), 3500);
}
function connect() {
  try {
    ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws');
    ws.onopen = () => { const c = $('#conn'); if (c) c.className = 'conn on'; };
    ws.onmessage = ev => { try { S = JSON.parse(ev.data); render(); } catch (e) { console.error(e); } };
    ws.onclose = ev => {
      const c = $('#conn'); if (c) c.className = 'conn off';
      if (ev.code === 4401) {
        location.replace('/login?next=/');
        return;
      }
      setTimeout(connect, 1500);
    };
    ws.onerror = () => {};
  } catch (e) {
    console.error('WS error', e);
    setTimeout(connect, 1500);
  }
}

/* ---------- смещение GSE от оси дороги ---------- */
function laneOffsetPoint(x, y, headingDeg, lane) {
  if (!lane) return [x, y];
  const rad = (headingDeg - 90) * Math.PI / 180;
  return [x + Math.cos(rad) * lane, y + Math.sin(rad) * lane];
}
function offsetPolyline(points, lane) {
  if (!lane || points.length < 2) return points;
  const out = [];
  for (let i = 0; i < points.length; i++) {
    let dx, dy;
    if (i === 0) { dx = points[1][0] - points[0][0]; dy = points[1][1] - points[0][1]; }
    else if (i === points.length - 1) { dx = points[i][0] - points[i-1][0]; dy = points[i][1] - points[i-1][1]; }
    else { dx = points[i+1][0] - points[i-1][0]; dy = points[i+1][1] - points[i-1][1]; }
    const len = Math.hypot(dx, dy) || 1;
    const nx = -dy / len, ny = dx / len;
    out.push([points[i][0] + nx * lane, points[i][1] + ny * lane]);
  }
  return out;
}
function avoidPlanes(x, y, planes, radius, push) {
  let ox = 0, oy = 0;
  for (const p of planes) {
    const dx = x - p.x, dy = y - p.y;
    const d = Math.hypot(dx, dy);
    if (d < radius && d > 0.01) {
      const k = (radius - d) / radius * push;
      ox += (dx / d) * k;
      oy += (dy / d) * k;
    }
  }
  return [ox, oy];
}

/* ---------- карта ---------- */
function el(tag, attrs = {}, parent) {
  const e = document.createElementNS(NS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(e);
  return e;
}
const PLANE = 'M0,-40 L6,-10 L40,10 L40,18 L6,8 L4,30 L16,38 L16,44 L0,40 L-16,44 L-16,38 L-4,30 L-6,8 L-40,18 L-40,10 L-6,-10 Z';

function setMapPosition(key, node, x, y, angle = 0) {
  const now = performance.now();
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) {
    let reduced = mapMotion.get(key);
    if (!reduced) { reduced = {node, x, y, angle, vx: 0, vy: 0, va: 0, toX: x, toY: y, toAngle: angle, lastFrame: now}; mapMotion.set(key, reduced); }
    Object.assign(reduced, {node, x, y, angle, toX: x, toY: y, toAngle: angle, vx: 0, vy: 0, va: 0, lastFrame: now});
    node.setAttribute('transform', `translate(${x} ${y}) rotate(${angle})`);
    return;
  }
  let state = mapMotion.get(key);
  if (!state) {
    state = {node, x, y, angle, vx: 0, vy: 0, va: 0, toX: x, toY: y, toAngle: angle, lastFrame: now};
    mapMotion.set(key, state);
    node.setAttribute('transform', `translate(${x} ${y}) rotate(${angle})`);
    return;
  }
  state.node = node;
  state.toX = x; state.toY = y; state.toAngle = angle;
  if (!mapMotionFrame) mapMotionFrame = requestAnimationFrame(animateMapMotion);
}

function animateMapMotion(now) {
  let moving = false;
  for (const state of mapMotion.values()) {
    const dt = Math.min(.032, Math.max(.001, (now - state.lastFrame) / 1000));
    state.lastFrame = now;
    const dx = state.toX - state.x, dy = state.toY - state.y;
    const da = ((state.toAngle - state.angle + 540) % 360) - 180;
    const k = 96, damping = 19.6;
    state.vx += (dx * k - state.vx * damping) * dt;
    state.vy += (dy * k - state.vy * damping) * dt;
    state.va += (da * 15 - state.va * 7.4) * dt;
    state.x += state.vx * dt; state.y += state.vy * dt; state.angle += state.va * dt;
    if (Math.abs(dx) < .06 && Math.abs(dy) < .06 && Math.abs(da) < .08 &&
        Math.abs(state.vx) < .1 && Math.abs(state.vy) < .1 && Math.abs(state.va) < .12) {
      state.x = state.toX; state.y = state.toY; state.angle = state.toAngle;
      state.vx = state.vy = state.va = 0;
    } else moving = true;
    state.node.setAttribute('transform', `translate(${state.x} ${state.y}) rotate(${state.angle})`);
  }
  mapMotionFrame = moving ? requestAnimationFrame(animateMapMotion) : 0;
}

function buildMap() {
  const svg = $('#map');
  if (!svg) return;
  svg.innerHTML = '';
  const N = Object.fromEntries(AIR.nodes.map(n => [n.id, n]));
  // стартовый вид — на перрон, а не на весь аэродром: на реальной карте
  // весь SVO/DME занимает 5–8 км и стоянки в общем виде теряются
  const standPts = (AIR.stands || []).map(s => N[s.node]).filter(Boolean);
  if (standPts.length) {
    const xs = standPts.map(p => p.x), ys = standPts.map(p => p.y);
    const cx = (Math.min(...xs) + Math.max(...xs)) / 2;
    const cy = (Math.min(...ys) + Math.max(...ys)) / 2;
    let w = Math.max(...xs) - Math.min(...xs) + 420;
    let h = Math.max(...ys) - Math.min(...ys) + 420;
    const box = svg.getBoundingClientRect();
    const ar = (box.width || window.innerWidth || 16) / (box.height || window.innerHeight || 9);
    if (w / h < ar) w = h * ar; else h = w / ar;
    vb = {x: cx - w / 2, y: cy - h / 2, w, h};
  } else {
    vb = {x: 0, y: 0, w: AIR.width, h: AIR.height};
  }
  applyVB();
  const gA = el('g', {}, svg), gP = el('g', {id: 'parking'}, svg),
        gR = el('g', {}, svg), gE = el('g', {}, svg),
        gAlt = el('g', {id: 'alt'}, svg), gRoute = el('g', {id: 'routes'}, svg);
  const gB = el('g', {}, svg), gS = el('g', {}, svg),
        gGSE = el('g', {id: 'gse'}, svg),
        gVehicles = el('g', {id: 'engineerVehicles'}, svg),
        gAC = el('g', {id: 'aircraft'}, svg),
        gT = el('g', {id: 'techs'}, svg),
        gLabels = el('g', {id: 'mapLabels'}, svg);

  (AIR.areas || []).forEach(a => {
    const p = el('polygon', {points: a.points.map(q => q.join(',')).join(' '), class: 'area-' + a.kind}, gA);
    p.setAttribute('stroke-width', 4);
    el('title', {}, p).textContent = a.name || a.kind;
    if (a.name && ['terminal', 'hangar'].includes(a.kind) && a.points.length) {
      const cx = a.points.reduce((sum, q) => sum + q[0], 0) / a.points.length;
      const cy = a.points.reduce((sum, q) => sum + q[1], 0) / a.points.length;
      el('text', {class: 'map-area-label', x: cx, y: cy}, gLabels).textContent = a.name;
    }
  });
  (AIR.parking || []).forEach(p => {
    el('polygon', {points: p.points.map(q => q.join(',')).join(' '), class: 'parking'}, gP);
  });
  (AIR.runways || []).forEach(r => {
    const pts = r.points.map(q => q.join(',')).join(' ');
    el('polyline', {points: pts, class: 'rwy', 'stroke-width': r.width || 45}, gR);
    el('polyline', {points: pts, class: 'rwy-mark', 'stroke-width': 4}, gR);
    el('title', {}, gR.lastChild).textContent = 'ВПП ' + (r.name || '');
    if (r.name && r.points.length) {
      const center = r.points[Math.floor(r.points.length / 2)];
      el('text', {class: 'map-runway-label', x: center[0], y: center[1] - 35}, gLabels).textContent = `ВПП ${r.name}`;
    }
  });
  AIR.edges.forEach(e => {
    const a = N[e.a], b = N[e.b];
    const line = el('line', {x1: a.x, y1: a.y, x2: b.x, y2: b.y, class: 'edge ' + (e.kind || ''),
      'stroke-width': e.kind === 'apron' ? 22 : 30}, gE);
    el('title', {}, line).textContent = (e.name ? 'РД ' + e.name : e.id) + ', ' + Math.round(e.length) + ' м';
    el('line', {x1: a.x, y1: a.y, x2: b.x, y2: b.y, class: 'edge-hit', 'data-edge': e.id}, gE);
    mapEl.edges[e.id] = line;
  });
  AIR.bases.forEach(b => {
    const n = N[b.node], g = el('g', {class: 'base', transform: `translate(${n.x},${n.y})`}, gB);
    el('rect', {x: -28, y: -28, width: 56, height: 56}, g);
    el('text', {y: 82}, g).textContent = b.name;
  });
  AIR.stands.forEach(s => {
    const n = N[s.node], g = el('g', {class: 'stand', 'data-stand': s.id, transform: `translate(${n.x},${n.y})`}, gS);
    el('rect', {class: 'ring', x: -58, y: -68, width: 116, height: 136, rx: 2}, g);
    el('path', {class: 'plane', d: PLANE, transform: `rotate(${s.heading ?? 0}) scale(0.95)`}, g);
    el('text', {y: 92}, g).textContent = s.id;
    el('title', {}, g).textContent = 'Стоянка ' + s.id;
    mapEl.stands[s.id] = g;
  });
  if (S) {
    (S.techs || []).forEach(t => techEl(t, gT));
    (S.aircraft || []).forEach(a => aircraftEl(a, gAC));
    (S.gse || []).forEach(g => gseEl(g, gGSE));
    (S.vehicles || []).forEach(v => vehicleEl(v, gVehicles));
  }
}
function techEl(t, parent) {
  const g = el('g', {class: 'tech', 'data-tech': t.id}, parent || $('#techs'));
  el('circle', {r: 30, fill: QCOL[t.qual]}, g);
  el('text', {}, g).textContent = t.id;
  el('title', {}, g).textContent = `${t.name}, ${t.qual}, ${t.speed} м/с`;
  mapEl.techs[t.id] = g;
  return g;
}
function aircraftEl(a, parent) {
  const g = el('g', {class: 'aircraft ' + a.status, 'data-aircraft': a.id}, parent || $('#aircraft'));
  el('path', {class: 'ac-plane', d: PLANE, transform: 'scale(0.9)'}, g);
  el('text', {class: 'ac-label', y: -78}, g).textContent = a.flight;
  el('title', {}, g).textContent = `${a.flight} ${a.model} — ${a.status_ru}${a.stand ? ' (' + a.stand + ')' : ''}`;
  mapEl.aircraft[a.id] = g;
  return g;
}
function gseEl(g, parent) {
  const grp = el('g', {class: 'gse ' + g.status, 'data-gse': g.id}, parent || $('#gse'));
  el('rect', {class: 'gse-body', x: -18, y: -12, width: 36, height: 24, rx: 4,
              fill: g.color}, grp);
  el('text', {class: 'gse-label', y: 3}, grp).textContent = g.short;
  el('title', {}, grp).textContent = `${g.label} — ${g.status_ru}`;
  mapEl.gse[g.id] = grp;
  return grp;
}
function vehicleEl(v, parent) {
  const grp = el('g', {class: 'engineer-vehicle ' + v.status, 'data-vehicle': v.id}, parent || $('#engineerVehicles'));
  el('path', {class: 'engineer-vehicle-shadow', d: 'M-19 -5 L-14 -14 Q-12 -18 -7 -18 H8 Q13 -18 15 -13 L20 -5 V10 Q20 13 17 13 H-16 Q-19 13 -19 10 Z'}, grp);
  el('path', {class: 'engineer-vehicle-body', d: 'M-16 -5 L-12 -13 Q-11 -15 -7 -15 H7 Q10 -15 12 -12 L16 -5 V8 H-16 Z'}, grp);
  el('path', {class: 'engineer-vehicle-glass', d: 'M-9 -12 H-2 V-6 H-13 Z M1 -12 H7 Q9 -12 10 -9 L12 -6 H1 Z'}, grp);
  el('rect', {class: 'engineer-vehicle-wheel', x: -14, y: 8, width: 6, height: 4, rx: 1.5}, grp);
  el('rect', {class: 'engineer-vehicle-wheel', x: 8, y: 8, width: 6, height: 4, rx: 1.5}, grp);
  el('circle', {class: 'engineer-vehicle-beacon', cx: 0, cy: -17, r: 2}, grp);
  el('text', {class: 'engineer-vehicle-label', x: 0, y: 25}, grp).textContent = `М${v.id}`;
  el('title', {}, grp).textContent = `${v.name} · ${v.status_ru}`;
  mapEl.vehicles[v.id] = grp;
  return grp;
}
function applyVB() {
  const m = $('#map');
  if (m) m.setAttribute('viewBox', `${vb.x} ${vb.y} ${vb.w} ${vb.h}`);
  updateMapScale();
}
function updateMapScale() {
  const map = $('#map'), line = $('#mapScaleLine'), label = $('#mapScaleLabel');
  if (!map || !line || !label || Map3D?.isActive?.()) return;
  const rect = map.getBoundingClientRect();
  if (!rect.width || !vb.w) return;
  const desiredMeters = 100 * vb.w / rect.width;
  const power = Math.pow(10, Math.floor(Math.log10(desiredMeters)));
  const unit = desiredMeters / power;
  const nice = (unit < 1.5 ? 1 : unit < 3.5 ? 2 : unit < 7.5 ? 5 : 10) * power;
  line.style.width = `${nice * rect.width / vb.w}px`;
  label.textContent = nice >= 1000 ? `${(nice / 1000).toFixed(nice % 1000 ? 1 : 0)} км` : `${Math.round(nice)} м`;
}
function zoom(k, cx, cy) {
  if (!AIR) return;
  const full = AIR.width;
  const nw = Math.min(Math.max(vb.w * k, full / 12), full * 1.2), kk = nw / vb.w;
  cx = cx ?? vb.x + vb.w / 2; cy = cy ?? vb.y + vb.h / 2;
  vb = {x: cx - (cx - vb.x) * kk, y: cy - (cy - vb.y) * kk, w: nw, h: vb.h * kk};
  applyVB();
}
function svgPoint(ev) {
  const svg = $('#map'), pt = svg.createSVGPoint();
  pt.x = ev.clientX; pt.y = ev.clientY;
  return pt.matrixTransform(svg.getScreenCTM().inverse());
}
function initMapEvents() {
  const svg = $('#map');
  if (!svg) return;
  window.addEventListener('resize', updateMapScale);
  svg.addEventListener('wheel', ev => {
    ev.preventDefault();
    const p = svgPoint(ev);
    zoom(ev.deltaY < 0 ? 0.87 : 1.15, p.x, p.y);
  }, {passive: false});
  let drag = null;
  svg.addEventListener('pointerdown', ev => {
    drag = {x: ev.clientX, y: ev.clientY, vx: vb.x, vy: vb.y, moved: false};
    svg.setPointerCapture(ev.pointerId);
  });
  svg.addEventListener('pointermove', ev => {
    if (!drag) return;
    const dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
    if (Math.abs(dx) + Math.abs(dy) > 5) { drag.moved = true; svg.classList.add('drag'); }
    if (!drag.moved) return;
    const r = svg.getBoundingClientRect(), s = Math.max(vb.w / r.width, vb.h / r.height);
    vb.x = drag.vx - dx * s; vb.y = drag.vy - dy * s;
    applyVB();
  });
  svg.addEventListener('pointerup', ev => {
    const moved = drag && drag.moved;
    drag = null; svg.classList.remove('drag');
    if (moved) return;

    if (moveTechMode) {
      const techId = selTech;
      const point = svgPoint(ev);
      moveTechMode = false;
      document.body.classList.remove('move-tech-mode');
      api(`/api/techs/${techId}/position`, {x: point.x, y: point.y})
        .then(result => toast(`Положение обновлено · ${result.node}`))
        .catch(() => {});
      return;
    }

    const t = document.elementFromPoint(ev.clientX, ev.clientY);
    const acEl = t && t.closest('[data-aircraft]');
    const edge = t && t.closest('[data-edge]');
    const st   = t && t.closest('[data-stand]');

    if (acEl) {
      const id = +acEl.dataset.aircraft;
      selAircraft = selAircraft === id ? null : id;
      selReq = null; selTech = null; selVehicle = null;
      render();
      return;
    }
    const vehicleEl = t && t.closest('[data-vehicle]');
    if (vehicleEl) {
      selVehicle = +vehicleEl.dataset.vehicle;
      selReq = null; selTech = null; selAircraft = null;
      render();
      return;
    }
    if (role !== 'dispatcher') return;
    if (st) openDialog(st.getAttribute('data-stand'));
    else if (edge) {
      const id = edge.getAttribute('data-edge');
      api(`/api/edges/${id}/block`, {blocked: !S.blocked.includes(id)}).catch(() => {});
    } else { selAircraft = null; selReq = null; selTech = null; selVehicle = null; render(); }
  });
  const zi = $('#zin'); if (zi) zi.onclick = () => Map3D.isActive() ? Map3D.zoom(0.8) : zoom(0.8);
  const zo = $('#zout'); if (zo) zo.onclick = () => Map3D.isActive() ? Map3D.zoom(1.25) : zoom(1.25);
  const zf = $('#zfit'); if (zf) zf.onclick = () => {
    if (Map3D.isActive()) { Map3D.fit(false); return; }
    vb = {x: 0, y: 0, w: AIR.width, h: AIR.height}; applyVB();
  };
  const v3 = $('#view3d'); if (v3) v3.onclick = () => {
    const next = !Map3D.isActive();
    Map3D.setActive(next);
    window.otoDesktop.setStartupView(next ? '3d' : '2d');
  };
  const settings = $('#appSettings'), settingsDlg = $('#settingsDlg'), startupView = $('#startupView');
  if (settings && settingsDlg) settings.onclick = () => settingsDlg.showModal();
  if (startupView) startupView.onchange = () => window.otoDesktop.setStartupView(startupView.value === '3d' ? '3d' : '2d');
  if (settingsDlg) settingsDlg.addEventListener('click', ev => {
    if (ev.target === settingsDlg) settingsDlg.close();
  });
}
function renderMap() {
  const seenAc = new Set();
  (S.aircraft || []).forEach(a => {
    seenAc.add(a.id);
    const g = mapEl.aircraft[a.id] || aircraftEl(a);
    setMapPosition(`aircraft:${a.id}`, g, a.x, a.y, a.heading || 0);
    let cls = 'aircraft ' + a.status;
    if (selAircraft === a.id) cls += ' sel';
    g.setAttribute('class', cls);
    const t = g.querySelector('title');
    if (t) t.textContent = `${a.flight} ${a.model} — ${a.status_ru}${a.stand ? ' (' + a.stand + ')' : ''}`;
  });
  for (const id in mapEl.aircraft) {
    if (!seenAc.has(+id)) { mapEl.aircraft[id].remove(); delete mapEl.aircraft[id]; }
  }

  const planes = (S.aircraft || []).filter(a => a.status !== 'departing');

  const seenGse = new Set();
  (S.gse || []).forEach(g => {
    seenGse.add(g.id);
    const el2 = mapEl.gse[g.id] || gseEl(g);
    const lane = (S.gse_types?.[g.gtype]?.lane) ?? g.lane ?? 0;
    let [px, py] = laneOffsetPoint(g.x, g.y, g.heading, lane);
    // Дополнительный отскок от самолётов, если близко
    const [ox, oy] = avoidPlanes(g.x, g.y, planes, 110, 30);
    px += ox; py += oy;
    setMapPosition(`gse:${g.id}`, el2, px, py, g.heading || 0);
    el2.setAttribute('class', 'gse ' + g.status);
    const t = el2.querySelector('title');
    if (t) t.textContent = `${g.label} — ${g.status_ru}${g.target ? ' (борт ' + g.target + ')' : ''}`;
  });
  for (const id in mapEl.gse) {
    if (!seenGse.has(+id)) { mapEl.gse[id].remove(); delete mapEl.gse[id]; }
  }

  const seenVehicles = new Set();
  (S.vehicles || []).forEach(v => {
    seenVehicles.add(v.id);
    const marker = mapEl.vehicles[v.id] || vehicleEl(v);
    setMapPosition(`vehicle:${v.id}`, marker, v.x, v.y, v.heading || 0);
    marker.setAttribute('class', 'engineer-vehicle ' + v.status + (selVehicle === v.id ? ' sel' : ''));
    const title = marker.querySelector('title');
    if (title) title.textContent = `${v.name} · ${v.status_ru}${v.request_id ? ' · заявка #' + v.request_id : ''}`;
  });
  for (const id in mapEl.vehicles) {
    if (!seenVehicles.has(+id)) { mapEl.vehicles[id].remove(); delete mapEl.vehicles[id]; }
  }

  (S.techs || []).forEach(t => {
    const g = mapEl.techs[t.id] || techEl(t);
    setMapPosition(`tech:${t.id}`, g, t.x, t.y);
    g.setAttribute('class', 'tech ' + t.status + (t.vehicle_id ? ' in-vehicle' : '') + (selTech === t.id ? ' sel' : ''));
  });

  const alt = $('#alt'); if (alt) {
    alt.innerHTML = '';
    const sel = selReq ? S.active.find(r => r.id === selReq) : null;
    if (sel && sel.candidates) {
      sel.candidates.forEach(c => {
        if (!c.path || c.path.length < 2) return;
        if (c.reason && c.reason.startsWith('выбран')) return;
        const tech = S.techs.find(t => t.id === c.tech_id);
        const col = tech ? QCOL[tech.qual] : '#8A8272';
        el('polyline', {class: 'alt-route', points: c.path.map(p => p.join(',')).join(' '), stroke: col}, alt);
      });
    }
  }

  const r = $('#routes'); if (r) {
    r.innerHTML = '';
    const focusReq = selReq ? S.active.find(q => q.id === selReq) : null;
    const focusTech = focusReq ? focusReq.tech_id : selTech;
    const focusVehicle = (S.vehicles || []).find(v => v.id === selVehicle);
    const focusRoute = (S.techs || []).find(t => t.id === focusTech)?.route || [];
    const chosen = focusReq
      ? ((focusReq.candidates || []).find(c => c.reason && c.reason.startsWith('выбран')) || focusReq.candidates?.[0])
      : null;
    const focusPath = focusVehicle?.route?.length > 1 ? focusVehicle.route
      : focusRoute.length > 1 ? focusRoute : (chosen?.path || []);
    if (focusPath.length > 1) {
      const pts = focusPath.map(p => p.join(',')).join(' ');
      el('polyline', {class: 'route-casing', points: pts}, r);
      el('polyline', {class: 'route', points: pts}, r);
    }
    (S.techs || []).forEach(t => {
      if (t.id !== focusTech && t.route.length > 1)
        el('polyline', {class: 'route-muted', points: t.route.map(p => p.join(',')).join(' ')}, r);
    });
    (S.aircraft || []).forEach(a => {
      if (a.route && a.route.length > 1)
        el('polyline', {class: 'ac-route', points: a.route.map(p => p.join(',')).join(' ')}, r);
    });
    (S.gse || []).forEach(g => {
      if (g.route && g.route.length > 1) {
        const lane = (S.gse_types?.[g.gtype]?.lane) ?? g.lane ?? 0;
        const off = offsetPolyline(g.route, lane);
        el('polyline', {class: 'gse-route', stroke: g.color,
                        points: off.map(p => p.join(',')).join(' ')}, r);
      }
    });
    (S.vehicles || []).forEach(v => {
      if (v.route && v.route.length > 1) {
        const pts = v.route.map(p => p.join(',')).join(' ');
        el('polyline', {class: 'engineer-vehicle-route-casing', points: pts}, r);
        el('polyline', {class: 'engineer-vehicle-route', points: pts}, r);
      }
    });
  }

  for (const id in mapEl.edges) mapEl.edges[id].classList.toggle('blocked', S.blocked.includes(id));
  const hot = {};
  S.active.forEach(q => { if (!hot[q.stand] || q.priority === 'emergency') hot[q.stand] = q.priority; });
  const selStand = S.active.find(q => q.id === selReq)?.stand;
  const parked = {};
  (S.aircraft || []).forEach(a => { if (a.stand && a.status === 'parked') parked[a.stand] = a; });
  for (const id in mapEl.stands) {
    const cls = 'stand'
      + (hot[id] ? ' hot' : '')
      + (hot[id] === 'emergency' ? ' em' : '')
      + (selStand === id ? ' sel' : '')
      + (parked[id] ? ' has-plane' : '');
    mapEl.stands[id].setAttribute('class', cls);
  }
}

/* ---------- списки ---------- */
function setHTML(id, html) {
  const node = $(id);
  if (!node) return;
  if (cache[id] === html) return;
  cache[id] = html;
  node.innerHTML = html;
}
function setDetailHTML(node, html) {
  if (node.dataset.renderedMarkup === html) return;
  node.dataset.renderedMarkup = html;
  node.innerHTML = html;
}
function reqCard(r) {
  const em = r.priority === 'emergency';
  const act = {
    enroute: ['arrive', 'Отметить прибытие'], arrived: ['start', 'Начать работу'], working: ['resolve', 'Дефект устранён'],
  }[r.status];
  const left = r.status === 'enroute' ? `<span data-live="left" data-id="${r.id}">—</span>`
    : r.status === 'waiting' ? 'ждёт' : 'на месте';
  const cands = (r.candidates || []).map(c => {
    const pick = c.reason && c.reason.startsWith('выбран');
    const delay = c.delay ? `<span class="muted"> +${fmt(c.delay)} простой</span>` : '';
    const transport = c.mode === 'vehicle' ? `Машина · ${esc(c.vehicle_name || 'ОТО')}` : 'Пешком';
    const walkCompare = c.eta_walk != null && c.mode === 'vehicle'
      ? `<small class="candidate-walk">пешком ${fmt(c.eta_walk)}</small>` : '';
    return `<tr class="${pick ? 'pick' : ''}">
      <td>${esc(c.name)}</td>
      <td>${c.dist == null ? '—' : c.dist + ' м'}</td>
      <td><b>${c.eta == null ? '—' : fmt(c.eta)}</b>${walkCompare}${delay}</td>
      <td>${transport}</td>
      <td>${esc(c.reason || 'подходит')}</td></tr>`;
  }).join('');
  return `<article class="req st-${r.status}${em ? ' em' : ''}${selReq === r.id ? ' sel' : ''}" data-id="${r.id}">
    <header>
      <span class="id">#${r.id}</span>
      ${r.flight ? `<span class="flight-tag">${esc(r.flight)}</span>` : ''}
      <span class="stand-tag">${esc(r.stand)}</span>
      <span class="qual q-${esc(r.qual)}">${esc(r.qual)}</span>
      ${em ? '<span class="badge em">ЧС</span>' : ''}
      <span class="status">${esc(r.status_ru)}</span>
    </header>
    <p class="desc">${esc(r.description)}${r.extra ? `<br><small class="muted">${esc(r.extra)}</small>` : ''}</p>
    ${r.note ? `<p class="note">${esc(r.note)}</p>` : ''}
    <dl class="kv">
      <div><dt>Техник</dt><dd>${esc(r.tech_name || '—')}</dd></div>
      <div><dt>До прибытия</dt><dd>${left}</dd></div>
      <div><dt>С момента вызова</dt><dd data-live="elapsed" data-id="${r.id}">—</dd></div>
    </dl>
    <div class="bar"><i data-live="bar" data-id="${r.id}"></i></div>
    <div class="actions dispatcher-only">
      ${act ? `<button class="primary" data-act="${act[0]}" data-id="${r.id}">${act[1]}</button>` : ''}
      <button class="danger" data-act="cancel" data-id="${r.id}">Отменить</button>
    </div>
    ${cands ? `<details data-det="${r.id}" ${openCands.has(r.id) ? 'open' : ''}>
      <summary>Почему выбран этот техник</summary>
      <table><tr><th>Техник</th><th>Путь</th><th>Прибытие</th><th>Транспорт</th><th>Решение</th></tr>${cands}</table></details>` : ''}
  </article>`;
}
function flightRow(a) {
  const eta = a.status === 'inbound' ? '→ стоянка' : a.status === 'departing' ? '→ вылет' : 'на перроне';
  return `<tr class="ac-${a.status}${selAircraft === a.id ? ' sel' : ''}" data-ac-row="${a.id}">
    <td><b>${esc(a.flight)}</b></td>
    <td>${esc(a.model)}</td>
    <td>${esc(a.stand || '—')}</td>
    <td>${esc(a.status_ru)}<small class="muted"> ${eta}</small></td>
  </tr>`;
}
function renderFlights() {
  const list = (S.aircraft || []).slice().sort((a, b) => a.id - b.id);
  if (!list.length) return '<p class="empty">Активных рейсов нет. Нажмите «+ Рейс» или подождите — борта прилетают автоматически.</p>';
  return `<table class="flights-table">
    <tr><th>Рейс</th><th>Тип ВС</th><th>Стоянка</th><th>Статус</th></tr>
    ${list.map(flightRow).join('')}
  </table>`;
}
function boardRow(a) {
  const b = a.brief || {};
  const origin = b.origin?.iata || '—';
  const dest = b.destination?.iata || '—';
  const route = `${origin} → ${dest}`;
  const schedTime = a.sched_arr_sim != null ? clockHM(a.sched_arr_sim) : '—';
  const actualTime = a.actual_arr_sim != null ? clockHM(a.actual_arr_sim) : null;
  const delaySec = a.delay_arr_sec || 0;
  const delayed = delaySec > 60;
  const airline = b.airline || '—';
  const reg = b.registration || '—';

  let badgeCls = 'bd-badge st-' + a.status;
  let badgeTxt = a.status_ru;
  if (a.status === 'inbound' && delayed) {
    badgeCls = 'bd-badge st-delayed';
    badgeTxt = `Задержан +${Math.round(delaySec/60)}м`;
  } else if (a.status === 'inbound') {
    badgeCls = 'bd-badge st-inbound';
    badgeTxt = 'В воздухе';
  } else if (a.status === 'parked') {
    const prog = a.services_progress || 0;
    badgeTxt = prog >= 100 ? 'Готов к вылету' : 'Обслуживание';
    if (prog >= 100) badgeCls = 'bd-badge st-ready';
  }

  const svcBar = a.services && a.services.length
    ? `<div class="board-bar"><i data-live="board-progress" data-id="${a.id}"></i></div>` : '';

  const when = actualTime
    ? `<b class="bd-time">${actualTime}</b><span class="bd-sched">факт</span>`
    : `<b class="bd-time">${schedTime}</b>${delayed
        ? `<span class="bd-delay">+${Math.round(delaySec / 60)}м</span>`
        : `<span class="bd-sched">план</span>`}`;

  return `<button type="button" class="board-row ac-${a.status}${selAircraft === a.id ? ' sel' : ''}" data-ac-row="${a.id}">
    <span class="bd-when">${when}</span>
    <span class="bd-main">
      <span class="bd-line">
        <b class="bd-flight">${esc(a.flight)}</b>
        <span class="bd-airline">${esc(airline)}</span>
        <span class="bd-reg">${esc(reg)}</span>
      </span>
      <span class="bd-sub">
        <span class="bd-route">${route}</span>
        <span class="bd-model">${esc(a.model)}</span>
        <span class="bd-stand">Стоянка ${esc(a.stand || '—')}</span>
        ${b.gate ? `<span class="bd-gate">Выход ${esc(b.gate)}</span>` : ''}
      </span>
    </span>
    <span class="bd-status"><span class="${badgeCls}">${esc(badgeTxt)}</span>${svcBar}</span>
  </button>`;
}
function renderBoard() {
  const list = (S.aircraft || []).slice().sort((a, b) => a.id - b.id);
  const header = `
    <div class="board-head">
      <div class="board-title">Табло рейсов</div>
      <div class="board-time">${new Date().toLocaleTimeString('ru-RU', {hour:'2-digit', minute:'2-digit'})}
        <small>местное время</small></div>
    </div>`;
  if (!list.length) {
    return header + '<p class="empty">Рейсов нет. Борта прилетают автоматически или по кнопке «+ Рейс».</p>';
  }
  const rows = list.map(boardRow).join('');
  return header + `<div class="board-list">${rows}</div>`;
}
function techRow(t) {
  const st = t.status === 'free' ? 'Свободен' : 'Занят';
  return `<div class="trow${selTech === t.id ? ' sel' : ''}" data-tech-row="${t.id}">
    <span class="dot" style="background:${QCOL[t.qual]}"></span>
    <div class="grow"><b>${esc(t.name)}</b><small>${esc(t.qual)} · ${(t.types || []).join(', ') || 'все типы'} · ${t.speed} м/с</small></div>
    <div>${st}${t.request_id ? `<small>заявка #${t.request_id}</small>` : ''}</div></div>`;
}
function registrationCard(u) {
  return `<article class="reg-card" data-reg-card="${u.id}">
    <div class="reg-main">
      <b>${esc(u.name)}</b>
      <small>${esc(u.qual || '—')} · ${esc((u.types || []).join(', ') || 'без допусков')}</small>
      <small>${esc(u.email)} · база ${esc(u.base || '—')}</small>
    </div>
    <div class="reg-actions">
      <button class="primary" data-reg-act="approve" data-reg-id="${u.id}">Допустить</button>
      <button class="danger" data-reg-act="reject" data-reg-id="${u.id}">Отклонить</button>
    </div>
  </article>`;
}
function renderTechs() {
  const pending = S.registrations || [];
  const regs = pending.length
    ? `<section class="reg-section">
        <h4>Заявки на регистрацию · ${pending.length}</h4>
        <p class="reg-note">Проверьте квалификацию и допуски. После подтверждения инженер появится в смене и сможет войти в приложение.</p>
        ${pending.map(registrationCard).join('')}
      </section>` : '';
  return regs + `<section class="tech-section">
    <h4>Инженеры на смене · ${(S.techs || []).length}</h4>
    ${(S.techs || []).map(techRow).join('')}
  </section>`;
}
function render() {
  if (!AIR || !S) return;
  const st = S.stats;
  const cards = [
    [st.aircraft_total, 'бортов'],
    [st.aircraft_parked, 'на стоянках'],
    [st.aircraft_inbound, 'на заходе'],
    [st.techs_free + '/' + st.techs_total, 'техников'],
    [st.gse_busy + '/' + st.gse_total, 'техники ОТО'],
    [st.norm_pct == null ? '—' : st.norm_pct + '%', 'в норматив'],
  ];
  setHTML('#stats', cards.map(c => `<div class="stat"><b>${c[0]}</b><span>${c[1]}</span></div>`).join(''));

  if (S.llm) {
    const pill = $('#llmPill');
    const m = $('#llmModel');
    if (m) m.textContent = S.llm.mode === 'llm' ? S.llm.model : 'встроенный';
    if (pill) pill.title = S.llm.mode === 'llm'
      ? `Провайдер: ${S.llm.url} · модель ${S.llm.model}`
      : 'LLM не подключена, используется встроенный генератор. Задайте LLM_BASE_URL для подключения.';
  }

  const cA = $('#cActive'); if (cA) cA.textContent = S.active.length ? `(${S.active.length})` : '';
  const cAc = $('#cFlights');
  if (cAc) cAc.textContent = (S.aircraft && S.aircraft.length) ? `(${S.aircraft.length})` : '';
  const cReg = $('#cReg');
  if (cReg) cReg.textContent = (S.registrations && S.registrations.length) ? `(${S.registrations.length})` : '';
  const sp = $('#speed'); if (sp && document.activeElement !== sp) sp.value = String(S.sim_speed);
  const simToggle = $('#simToggle');
  if (simToggle) {
    const running = S.sim_running !== false;
    document.body.classList.toggle('simulation-paused', !running);
    simToggle.classList.toggle('paused', !running);
    simToggle.setAttribute('aria-pressed', String(running));
    simToggle.setAttribute('aria-label', running ? 'Приостановить симуляцию' : 'Запустить симуляцию');
    const label = $('#simToggleText'); if (label) label.textContent = running ? 'Пауза' : 'Запустить';
  }

  setHTML('#pane-board', renderBoard());
  setHTML('#pane-active', S.active.length ? S.active.map(reqCard).join('')
    : '<p class="empty">Активных заявок нет. Нажмите «Создать заявку» или кликните по стоянке на карте.</p>');
  setHTML('#pane-flights', renderFlights());
  setHTML('#pane-techs', renderTechs());
  setHTML('#pane-log', S.events.map(e => `<div class="ev ${esc(e.kind)}"><time>${esc(e.ts)}</time><span>${esc(e.text)}</span></div>`).join('')
    + '<div class="tools dispatcher-only"><button class="danger" data-act="reset">Сбросить демо-данные</button></div>');

  const fleet = S.vehicles || [];
  const idleVehicles = fleet.filter(v => v.status === 'idle').length;
  const fleetNode = $('#vehicleCount');
  if (fleetNode) fleetNode.textContent = `Машины · ${idleVehicles} свободно из ${fleet.length}`;
  const airportNode = $('#mapHudAirport');
  if (airportNode) airportNode.textContent = AIR.name || 'Аэропорт';

  renderDetail();
  renderMap();
  renderLive();
  if (typeof Map3D !== 'undefined') Map3D.update(S, {
    tech: selTech, aircraft: selAircraft,
    vehicle: selVehicle,
    stand: selReq ? (S.active.find(q => q.id === selReq)?.stand || null) : null,
  });
}
function renderLive() {
  S.active.forEach(r => {
    const q = k => $(`[data-live="${k}"][data-id="${r.id}"]`);
    if (q('left')) q('left').textContent = fmt(r.eta_left);
    if (q('elapsed')) q('elapsed').textContent = fmt(r.elapsed);
    const b = q('bar');
    if (b) {
      b.style.width = Math.min(100, r.elapsed / NORM * 100) + '%';
      b.className = r.elapsed > NORM ? 'over' : r.elapsed > NORM * 0.7 ? 'warn' : '';
    }
    const dt = q('detail-timer');
    if (dt) dt.textContent = fmt(r.status === 'enroute' ? r.eta_left : r.elapsed);
  });
  (S.techs || []).forEach(t => {
    const node = $(`[data-live="tech-position"][data-id="${t.id}"]`);
    if (node) node.textContent = `${Math.round(t.x)} · ${Math.round(t.y)}`;
  });
  (S.vehicles || []).forEach(v => {
    const q = name => $(`[data-live="${name}"][data-id="${v.id}"]`);
    if (q('vehicle-position')) q('vehicle-position').textContent = `${Math.round(v.x)} · ${Math.round(v.y)}`;
    if (q('vehicle-route')) q('vehicle-route').textContent = v.route?.length > 1 ? `${v.route.length} точек` : 'Стоянка';
    const req = (S.active || []).find(r => r.id === v.request_id);
    if (q('vehicle-eta')) q('vehicle-eta').textContent = req?.eta_left != null ? fmt(req.eta_left) : '—';
  });
  (S.aircraft || []).forEach(a => {
    const q = name => $(`[data-live="${name}"][data-id="${a.id}"]`);
    if (q('aircraft-eta')) q('aircraft-eta').textContent = a.eta_left != null ? fmt(a.eta_left) : '—';
    if (q('aircraft-parked')) q('aircraft-parked').textContent = a.parked_left != null ? fmt(a.parked_left) : '—';
    if (q('aircraft-progress')) q('aircraft-progress').textContent = `${a.services_progress || 0}%`;
    if (q('board-progress')) q('board-progress').style.width = `${a.services_progress || 0}%`;
  });
}

/* ---------- Правая панель ---------- */
function requestDetail(r) {
  const em = r.priority === 'emergency';
  const done = r.status === 'done' || r.done_sim != null;
  const arrived = r.status === 'arrived' || r.status === 'working' || done;
  const working = r.status === 'working' || done;
  const created = clockHM(r.created_sim);
  const arrivedAt = r.arrived_sim != null ? clockHM(r.arrived_sim) : '';
  const doneAt = r.done_sim != null ? clockHM(r.done_sim) : '';
  const techInitial = (r.tech_name || '?').trim().charAt(0).toUpperCase() || '?';
  const act = {
    enroute: ['arrive', 'Инженер прибыл'],
    arrived: ['start', 'Начать работу'],
    working: ['resolve', 'Работа выполнена'],
  }[r.status];

  const step2 = r.tech_id
    ? `<li class="done"><span class="num">2</span><span class="txt"><b>Назначен инженер</b>${esc(r.tech_name || '')}</span><time>${created}</time></li>`
    : `<li class="now"><span class="num">2</span><span class="txt"><b>Ожидает инженера</b>Нет свободного специалиста с допуском</span><time></time></li>`;
  const step3Class = arrived ? 'done' : r.status === 'enroute' ? 'now' : '';
  const step4Class = working ? 'done' : r.status === 'arrived' ? 'now' : '';
  const step5Class = done ? 'done' : '';

  const timeline = `<div class="rq-timeline">
    <h3>Ход работы</h3>
    <ol>
      <li class="done"><span class="num">1</span><span class="txt"><b>Заявка создана</b>${esc(r.stand)}</span><time>${created}</time></li>
      ${step2}
      <li class="${step3Class}"><span class="num">3</span><span class="txt"><b>Инженер на стоянке</b>${arrived ? 'Прибытие зафиксировано' : 'Ожидается прибытие'}</span><time>${arrivedAt}</time></li>
      <li class="${step4Class}"><span class="num">4</span><span class="txt"><b>Работа</b>${working ? 'Дефект устраняется' : 'Начнётся после прибытия'}</span><time></time></li>
      <li class="${step5Class}"><span class="num">5</span><span class="txt"><b>Заявка закрыта</b>${done ? 'Работа принята' : 'Ожидает выполнения'}</span><time>${doneAt}</time></li>
    </ol>
  </div>`;

  const engineerBlock = r.tech_id
    ? `<div class="rq-engineer">
        <span class="avatar">${esc(techInitial)}</span>
        <div><b>${esc(r.tech_name || 'Инженер')}</b><span>${esc(r.qual)} · расчёт ${fmt(r.eta)}</span></div>
        <span class="badge">${esc(r.status_ru)}</span>
      </div>`
    : `<div class="rq-engineer">
        <span class="avatar">?</span>
        <div><b>Инженер не назначен</b><span>Ожидание свободного специалиста с допуском</span></div>
        <span class="badge">ожидание</span>
      </div>`;

  return `<header class="rq-head">
      <button class="rq-close" data-rq-close type="button" aria-label="Закрыть заявку">×</button>
      <div class="rq-stand">${esc(r.stand)}</div>
      <div class="rq-meta">
        <span>Заявка #${r.id}</span>
        ${r.flight ? `<span class="badge">${esc(r.flight)}</span>` : ''}
        ${em ? '<span class="badge em">ЧС</span>' : ''}
      </div>
    </header>
    <div class="rq-status">
      <span class="dot" style="background:${em ? 'var(--red)' : 'var(--blue)'}"></span>
      <span>${esc(r.status_ru)}</span>
      <span class="timer" data-live="detail-timer" data-id="${r.id}">—</span>
    </div>
    <div class="rq-block">
      <h3>Дефект</h3>
      <p>${esc(r.description)}</p>
      ${r.extra ? `<p class="brief-note">${esc(r.extra)}</p>` : ''}
    </div>
    <dl class="rq-facts">
      <div><dt>Квалификация</dt><dd>${esc(r.qual)}</dd></div>
      <div><dt>Стоянка</dt><dd>${esc(r.stand)}</dd></div>
      <div><dt>Создана</dt><dd>${created}</dd></div>
      <div><dt>Реакция</dt><dd>${r.response_sec == null ? '—' : fmt(r.response_sec)}</dd></div>
      <div><dt>Норматив</dt><dd style="color:${r.violation ? 'var(--red)' : 'var(--green)'}">${r.violation ? 'нарушен' : 'в норме'}</dd></div>
      <div><dt>Борт</dt><dd>${esc(r.flight || (r.aircraft_id ? '#' + r.aircraft_id : '—'))}</dd></div>
    </dl>
    ${engineerBlock}
    ${r.note ? `<div class="rq-block"><h3>Примечание</h3><p>${esc(r.note)}</p></div>` : ''}
    ${timeline}
    <div class="rq-actions">
      ${act ? `<button class="primary" data-act="${act[0]}" data-id="${r.id}">${act[1]}</button>` : ''}
      <button class="danger" data-act="cancel" data-id="${r.id}">Отменить</button>
    </div>`;
}

function shiftPanel() {
  const free = (S.techs || []).filter(t => t.status === 'free');
  const events = (S.events || []).slice(-6).reverse();
  const norm = S.stats?.norm_pct;
  const freeRows = free.length
    ? free.slice(0, 8).map(t => `<div class="shift-row" data-tech-row="${t.id}">
        <span class="dot" style="background:${QCOL[t.qual]}"></span>
        <b>${esc(t.name)}</b>
        <span>${esc(t.qual)}</span>
      </div>`).join('')
    : '<div class="shift-callout">Свободных инженеров нет. Новые заявки будут ждать освобождения смены.</div>';
  const eventRows = events.length
    ? events.map(e => `<div class="ev ${esc(e.kind)}"><time>${esc(e.ts)}</time><span>${esc(e.text)}</span></div>`).join('')
    : '<p class="empty">Событий пока нет.</p>';
  return `<div class="shift-panel">
    <h2>Смена на перроне</h2>
    <p>${esc(AIR?.name || 'Аэропорт')}, диспетчерская ОТО.</p>
    <div class="shift-norm"><b>15:00</b><span>норматив прибытия инженера${norm == null ? '' : ' · в норме ' + norm + '%'}</span></div>
    <h3>Свободные инженеры</h3>
    <div class="shift-list">${freeRows}</div>
    <h3 style="margin-top:18px">Последние события</h3>
    <div>${eventRows}</div>
    <div class="shift-callout">Выберите заявку в очереди или стоянку на карте. Карточка откроется здесь.</div>
  </div>`;
}

function vehiclePanel(vehicle) {
  const req = (S.active || []).find(r => r.id === vehicle.request_id);
  const tech = (S.techs || []).find(t => t.id === vehicle.tech_id);
  return `<header class="vehicle-detail-head">
      <span class="vehicle-detail-icon"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 15 1.5-5h11L19 15v4H5zM7 10l1.2-4h7.6L17 10M8 19v1m8-1v1"/></svg></span>
      <div><span class="panel-kicker">ТРАНСПОРТ ИНЖЕНЕРОВ</span><h2>${esc(vehicle.name)}</h2><span class="vehicle-detail-status ${vehicle.status === 'idle' ? 'idle' : ''}">${esc(vehicle.status_ru)}</span></div>
    </header>
    <dl class="vehicle-detail-facts">
      <div><dt>База</dt><dd>${esc(vehicle.base || '—')}</dd></div>
      <div><dt>Положение</dt><dd data-live="vehicle-position" data-id="${vehicle.id}">—</dd></div>
      <div><dt>Инженер</dt><dd>${tech ? esc(tech.name) : '—'}</dd></div>
      <div><dt>Заявка</dt><dd>${req ? `#${req.id} · ${esc(req.stand)}` : '—'}</dd></div>
      <div><dt>Маршрут</dt><dd data-live="vehicle-route" data-id="${vehicle.id}">—</dd></div>
      <div><dt>До борта</dt><dd data-live="vehicle-eta" data-id="${vehicle.id}">—</dd></div>
    </dl>
    <div class="vehicle-detail-note">В ETA включены подача свободной машины к инженеру, посадка и поездка до стоянки.</div>`;
}

function renderDetail() {
  const p = $('#detailPanel');
  if (!p) return;
  if (selReq != null) {
    const r = S.active.find(q => q.id === selReq);
    if (r) {
      setDetailHTML(p, requestDetail(r));
      p.hidden = false;
      document.body.classList.add('detail-open');
      return;
    }
    selReq = null;
  }
  if (selTech != null) {
    const tech = S.techs.find(item => item.id === selTech);
    if (tech) {
      if (p.contains(document.activeElement) && document.activeElement.matches(
        '#techQual, .tech-type input, [data-tech-save], [data-tech-move]')) return;
      setDetailHTML(p, technicianPanel(tech));
      p.hidden = false;
      document.body.classList.remove('detail-open');
      return;
    }
    selTech = null;
  }
  if (selVehicle != null) {
    const vehicle = (S.vehicles || []).find(item => item.id === selVehicle);
    if (vehicle) {
      setDetailHTML(p, vehiclePanel(vehicle));
      p.hidden = false;
      document.body.classList.remove('detail-open');
      return;
    }
    selVehicle = null;
  }
  if (selAircraft == null) {
    setDetailHTML(p, shiftPanel());
    p.hidden = false;
    document.body.classList.remove('detail-open');
    return;
  }
  const a = (S.aircraft || []).find(x => x.id === selAircraft);
  if (!a) {
    selAircraft = null;
    setDetailHTML(p, shiftPanel());
    p.hidden = false;
    document.body.classList.remove('detail-open');
    return;
  }

  const reqs = [...S.active, ...S.history].filter(r => r.aircraft_id === a.id);
  const b = a.brief || {};

  const etaBlock = a.status === 'inbound' && a.eta_left != null
    ? `<div><dt>Прибытие через</dt><dd data-live="aircraft-eta" data-id="${a.id}">—</dd></div>` : '';
  const parkedBlock = a.status === 'parked' && a.parked_left != null
    ? `<div><dt>Вылет через</dt><dd data-live="aircraft-parked" data-id="${a.id}">—</dd></div>` : '';

  const servicesHtml = (a.services || []).length
    ? `<ul class="svc-list">
        ${a.services.map(s => {
          const meta = S.gse_types?.[s.gtype] || {};
          return `<li class="svc-item st-${s.status}">
            <span class="svc-dot" style="background:${meta.color || '#888'}"></span>
            <span class="svc-name">${esc(meta.label || s.gtype)}</span>
            <span class="svc-status">${
              s.status === 'pending' ? 'ожидает' :
              s.status === 'active'  ? 'в работе' : 'готово'
            }</span>
          </li>`;
        }).join('')}
      </ul>` : '<p class="empty small">Сервисные работы не запланированы.</p>';

  const schedArr = a.sched_arr_sim != null ? clockHM(a.sched_arr_sim) : '—';
  const actualArr = a.actual_arr_sim != null ? clockHM(a.actual_arr_sim) : null;
  const delaySec = a.delay_arr_sec || 0;
  const delayLabel = delaySec > 60
    ? `<span class="delay-badge">+${Math.round(delaySec/60)} мин</span>` : '';
  const schedDep = a.sched_dep_sim != null ? clockHM(a.sched_dep_sim) : '—';

  const srcTag = a.brief
    ? `<span class="src-tag ${a.brief.source === 'llm' ? 'llm' : ''}">
         ${a.brief.source === 'llm' ? '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="8" width="14" height="11" rx="2"/><path d="M9 8V5h6v3M9 12h.01M15 12h.01M9 16h6"/></svg> LLM' : 'шаблон'}
       </span>`
    : `<span class="src-tag">…</span>`;

  const briefHtml = `
    <section class="dp-brief">
      <h4>Карточка рейса ${srcTag}</h4>

      <div class="brief-header">
        <div class="brief-airline">${esc(b.airline || '—')}</div>
        <div class="brief-flight">${esc(a.flight)}</div>
      </div>

      <div class="brief-route">
        <div class="brief-point">
          <div class="bp-iata">${esc(b.origin?.iata || '—')}</div>
          <div class="bp-city">${esc(b.origin?.city || '')}</div>
          <div class="bp-airport">${esc(b.origin?.airport || '')}</div>
        </div>
        <div class="brief-arrow" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="m3 13 7-2 5-7 2 1-3 7 6 2 1 2-8-1-4 5-2-.5 2-5-5-1.5z"/></svg></div>
        <div class="brief-point">
          <div class="bp-iata">${esc(b.destination?.iata || '—')}</div>
          <div class="bp-city">${esc(b.destination?.city || '')}</div>
          <div class="bp-airport">${esc(b.destination?.airport || '')}</div>
        </div>
      </div>

      <div class="brief-times">
        <div><span>По расписанию</span><b>${schedArr}</b></div>
        <div><span>Фактически</span><b>${actualArr || '—'} ${delayLabel}</b></div>
        <div><span>Вылет план</span><b>${schedDep}</b></div>
      </div>

      <div class="brief-grid">
        <div><span>Тип ВС</span><b>${esc(a.model)}</b></div>
        <div><span>Регистрация</span><b>${esc(b.registration || '—')}</b></div>
        <div><span>Выход</span><b>${esc(b.gate || '—')}</b></div>
        <div><span>Стоянка</span><b>${esc(a.stand || '—')}</b></div>
        <div><span>Пассажиры</span><b>${b.passengers ?? '—'}</b></div>
        <div><span>Экипаж</span><b>${b.crew ?? '—'}</b></div>
        <div><span>Груз</span><b>${b.cargo_kg != null ? b.cargo_kg + ' кг' : '—'}</b></div>
        <div><span>Сервисы</span><b data-live="aircraft-progress" data-id="${a.id}">—</b></div>
      </div>

      ${b.note ? `<p class="brief-note">${esc(b.note)}</p>` : ''}
    </section>`;

  const departBtn = (a.status === 'parked' && role === 'dispatcher')
    ? `<button class="primary" data-ac-depart="${a.id}">Отправить на вылет</button>` : '';

  setDetailHTML(p, `
    <header class="dp-head">
      <div class="dp-title">
        <span class="dp-flight">${esc(a.flight)}</span>
        <span class="dp-model">${esc(a.model)}${b.registration ? ' · ' + esc(b.registration) : ''}</span>
      </div>
      <button class="dp-close" data-ac-close aria-label="Закрыть">×</button>
    </header>

    <div class="dp-status st-${a.status}">
      <span class="dot" style="background:${AC_COL[a.status]}"></span>
      <span>${esc(a.status_ru)}</span>
    </div>

    <dl class="dp-kv">
      ${a.stand ? `<div><dt>Стоянка</dt><dd>${esc(a.stand)}</dd></div>` : ''}
      ${b.gate ? `<div><dt>Выход</dt><dd>${esc(b.gate)}</dd></div>` : ''}
      ${etaBlock}
      ${parkedBlock}
    </dl>

    ${briefHtml}

    <section class="dp-services">
      <h4>Обслуживание на перроне</h4>
      ${servicesHtml}
    </section>

    ${departBtn ? `<div class="dp-actions">${departBtn}</div>` : ''}

    <section class="dp-reqs">
      <h4>Заявки по борту</h4>
      ${reqs.length ? reqs.map(r => `
        <div class="dp-req st-${r.status}${r.priority === 'emergency' ? ' em' : ''}">
          <b>#${r.id}</b>
          <span class="dp-req-q qual q-${esc(r.qual)}">${esc(r.qual)}</span>
          <span class="dp-req-st">${esc(r.status_ru)}</span>
          <div class="dp-req-desc">${esc(r.description)}</div>
        </div>`).join('')
      : '<p class="empty small">Нет заявок на этот борт.</p>'}
    </section>
  `);
  p.hidden = false;
  document.body.classList.add('detail-open');
  requestAnimationFrame(() => p.classList.add('open'));
}

function technicianPanel(t) {
  const busy = t.status !== 'free';
  const types = new Set(t.types || []);
  return `<header class="tech-detail-head">
      <span class="tech-detail-mark" style="--tech-color:${QCOL[t.qual]}">${t.id}</span>
      <div><span class="panel-kicker">СОТРУДНИК СМЕНЫ</span><h2>${esc(t.name)}</h2><span class="tech-detail-status ${busy ? 'busy' : ''}">${busy ? `В работе · заявка #${t.request_id}` : 'Свободен'}</span></div>
    </header>
    <div class="tech-detail-facts">
      <div><span>ПОЛОЖЕНИЕ</span><b data-live="tech-position" data-id="${t.id}">—</b></div>
      <div><span>СКОРОСТЬ</span><b>${t.speed} м/с</b></div>
      <div><span>БАЗА</span><b>${esc(t.base || '—')}</b></div>
    </div>
    <section class="tech-sim-edit">
      <div class="panel-kicker">ПАРАМЕТРЫ СИМУЛЯЦИИ</div>
      <label for="techQual">Квалификация</label>
      <select id="techQual" ${busy ? 'disabled' : ''}>
        ${['Планер', 'Двигатель', 'Авионика'].map(q => `<option ${q === t.qual ? 'selected' : ''}>${q}</option>`).join('')}
      </select>
      <span class="tech-types-label">Допуски на тип ВС</span>
      <div class="tech-type-grid">${AIRCRAFT_TYPES.map(type => `<label class="tech-type"><input type="checkbox" value="${type}" ${types.has(type) ? 'checked' : ''} ${busy ? 'disabled' : ''}><span>${type}</span></label>`).join('')}</div>
      <button class="secondary tech-save" type="button" data-tech-save="${t.id}" ${busy ? 'disabled' : ''}>Сохранить профиль</button>
      <button class="primary tech-move" type="button" data-tech-move="${t.id}" ${busy ? 'disabled' : ''}>Переместить на карте</button>
      <p>Режим проверки: изменения действуют только в текущем запуске симулятора.</p>
    </section>`;
}

/* ---------- действия ---------- */
function updateFlightInfo() {
  const stand = $('#fStand').value;
  const info = $('#fFlightInfo');
  if (!info) return;
  const a = (S.aircraft || []).find(x => x.stand === stand && x.status === 'parked');
  if (a) {
    info.textContent = `Борт ${a.flight} · ${a.model}`;
    info.classList.add('has-flight');
  } else {
    info.textContent = 'Борт на стоянке отсутствует — заявка будет привязана к стоянке';
    info.classList.remove('has-flight');
  }
}
function openDialog(stand) {
  if (role !== 'dispatcher') return;
  const fStand = $('#fStand');
  if (!fStand) return;
  fStand.innerHTML = AIR.stands.map(s => `<option>${esc(s.id)}</option>`).join('');
  if (stand) fStand.value = stand;
  const fe = $('#fErr'); if (fe) fe.hidden = true;
  updateFlightInfo();
  const dlg = $('#dlg'); if (dlg) dlg.showModal();
  const fd = $('#fDesc'); if (fd) fd.focus();
}
function initUI() {
  const an = $('#airportName'); if (an) an.textContent = AIR.name + (AIR.source ? ', ' + AIR.source : '');
  document.body.className = role;
  if (ME) {
    const name = ME.name || 'Диспетчер';
    const avatar = name.trim().slice(0, 1).toUpperCase() || 'Д';
    const un = $('#userName'); if (un) un.textContent = name;
    const ua = $('#userAvatar'); if (ua) ua.textContent = avatar;
    const ur = $('#userRole'); if (ur) ur.textContent = 'диспетчер смены';
    const chip = $('#userChip'); if (chip) chip.title = `${ME.email} · диспетчер`;
  }
  const lo = $('#logoutBtn'); if (lo) lo.onclick = async () => {
    try { await api('/api/auth/logout'); } catch (e) {}
    location.replace('/login');
  };
  const sp = $('#speed'); if (sp) sp.onchange = e => api('/api/sim', {speed: +e.target.value}).catch(() => {});
  const simToggle = $('#simToggle'); if (simToggle) simToggle.onclick = () =>
    api('/api/sim', {running: S?.sim_running === false}).catch(() => {});
  const mb = $('#menuBtn'); if (mb) mb.onclick = () => document.body.classList.toggle('queue-open');
  const nb = $('#newBtn'); if (nb) nb.onclick = () => openDialog();
  const na = $('#newAcBtn'); if (na) na.onclick = () => api('/api/aircraft/spawn').catch(() => {});
  const fc = $('#fCancel'); if (fc) fc.onclick = () => { const d = $('#dlg'); if (d) d.close(); };
  const fs = $('#fStand'); if (fs) fs.onchange = updateFlightInfo;
  const form = $('#form'); if (form) form.onsubmit = async ev => {
    ev.preventDefault();
    try {
      await api('/api/requests', {
        stand: $('#fStand').value, description: $('#fDesc').value, qual: $('#fQual').value,
        extra: $('#fExtra').value, priority: $('#fEm').checked ? 'emergency' : 'normal',
      });
      const d = $('#dlg'); if (d) d.close();
      form.reset();
    } catch (e) { const fe = $('#fErr'); if (fe) { fe.textContent = e.message; fe.hidden = false; } }
  };
  const tabs = $('#tabs'); if (tabs) tabs.onclick = e => {
    const b = e.target.closest('[data-tab]'); if (!b) return;
    document.querySelectorAll('#tabs button').forEach(x => x.classList.toggle('on', x === b));
    document.querySelectorAll('.pane').forEach(p => p.classList.toggle('on', p.id === 'pane-' + b.dataset.tab));
  };
  document.addEventListener('click', e => {
    if (e.target.closest('[data-rq-close]')) { selReq = null; selTech = null; render(); return; }
    if (e.target.closest('[data-ac-close]')) { selAircraft = null; render(); return; }
    const dep = e.target.closest('[data-ac-depart]');
    if (dep) { e.stopPropagation(); api(`/api/aircraft/${dep.dataset.acDepart}/depart`).catch(() => {}); return; }
    const reg = e.target.closest('[data-reg-act]');
    if (reg) {
      e.stopPropagation();
      const id = reg.dataset.regId;
      const action = reg.dataset.regAct;
      if (action === 'reject' && !confirm('Отклонить заявку на регистрацию?')) return;
      reg.disabled = true;
      api(`/api/registrations/${id}/${action}`)
        .then(() => toast(action === 'approve' ? 'Инженер допущен к смене' : 'Заявка отклонена'))
        .catch(() => { reg.disabled = false; });
      return;
    }
    const b = e.target.closest('[data-act]');
    if (b) {
      e.stopPropagation();
      const id = b.dataset.id;
      if (b.dataset.act === 'reset') {
        if (confirm('Удалить все заявки и журнал?')) api('/api/reset').catch(() => {});
      } else api(`/api/requests/${id}/${b.dataset.act}`).catch(() => {});
      return;
    }
    const saveTech = e.target.closest('[data-tech-save]');
    if (saveTech) {
      const id = saveTech.dataset.techSave;
      const types = [...document.querySelectorAll('.tech-type input:checked')].map(input => input.value);
      api(`/api/techs/${id}/profile`, {qual: $('#techQual').value, types})
        .then(() => { document.activeElement.blur(); toast('Профиль специалиста обновлён'); render(); }).catch(() => {});
      return;
    }
    const moveTech = e.target.closest('[data-tech-move]');
    if (moveTech) {
      selTech = +moveTech.dataset.techMove;
      moveTechMode = true;
      document.body.classList.add('move-tech-mode');
      toast('Укажите новое положение инженера на карте');
      return;
    }
    const acRow = e.target.closest('[data-ac-row]');
    if (acRow) {
      selAircraft = +acRow.dataset.acRow; selReq = null;
      document.body.classList.remove('queue-open');
      render(); return;
    }
    const card = e.target.closest('.req');
    if (card && !e.target.closest('details')) {
      selReq = +card.dataset.id;
      const r = S.active.find(q => q.id === selReq);
      selTech = r ? r.tech_id : null;
      selAircraft = null;
      document.body.classList.remove('queue-open');
      render(); return;
    }
    const row = e.target.closest('[data-tech-row]');
    if (row) {
      const id = +row.dataset.techRow;
      selTech = selTech === id ? null : id;
      selReq = null; selAircraft = null;
      render();
    }
  });
  document.addEventListener('toggle', e => {
    const d = e.target.closest && e.target.closest('details[data-det]');
    if (!d) return;
    const id = +d.dataset.det;
    d.open ? openCands.add(id) : openCands.delete(id);
  }, true);
}

(async function main() {
  try {
    const meResponse = await fetch('/api/auth/me', {cache: 'no-store'});
    if (meResponse.status === 401) {
      location.replace('/login?next=/');
      return;
    }
    if (!meResponse.ok) throw new Error('Не удалось проверить сессию');
    ME = (await meResponse.json()).user;
    if (ME.role !== 'dispatcher') {
      location.replace('/engineer');
      return;
    }
    role = 'dispatcher';
    bootStage(1, 'Доступ подтверждён · загружаем данные аэропорта');

    const airportResponse = await fetch('/api/airport', {cache: 'no-store'});
    if (airportResponse.status === 401) {
      location.replace('/login?next=/');
      return;
    }
    AIR = await airportResponse.json();
    bootStage(2, 'Схема перрона получена · синхронизируем смену');
    const stateResponse = await fetch('/api/state', {cache: 'no-store'});
    if (stateResponse.status === 401) {
      location.replace('/login?next=/');
      return;
    }
    S = await stateResponse.json();

    // На первом экране сразу открываем первую активную заявку: карта и
    // рабочая карточка должны быть осмысленны без лишнего клика.
    if (window.innerWidth > 1180 && S.active?.length) {
      selReq = S.active[0].id;
      selTech = S.active[0].tech_id ?? null;
    }

    // WebSocket — до render, чтобы обновления шли даже при ошибках UI
    connect();

    initUI();
    initMapEvents();
    buildMap();
    Map3D.init(AIR, {
      onStand: id => openDialog(id),
      onAircraft: id => { selAircraft = id; selReq = null; render(); },
      onVehicle: id => { selVehicle = id; selReq = null; selTech = null; selAircraft = null; render(); },
    });
    const savedView = window.otoDesktop.getStartupView();
    const initialView = new URLSearchParams(location.search).get('view') || (location.hash === '#3d' ? '3d' : savedView);
    const startupView = $('#startupView');
    if (startupView) startupView.value = savedView;
    const settingsAirport = $('#settingsAirport');
    if (settingsAirport) settingsAirport.textContent = AIR.name || 'Шереметьево';
    if (initialView === '3d') {
      Map3D.setActive(true);
    }
    render();
    await finishBoot();
  } catch (e) {
    console.error('Ошибка инициализации:', e);
    const status = $('#bootStatus');
    if (status) status.textContent = `Не удалось загрузить смену: ${e.message}`;
    const retry = $('#bootRetry');
    if (retry) { retry.hidden = false; retry.onclick = () => location.reload(); }
    $('#bootScreen')?.classList.add('is-error');
  }
})();
