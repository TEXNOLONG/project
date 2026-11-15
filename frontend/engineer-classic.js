/* Личное приложение инженера */
const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = sec => {
  if (sec == null) return '—';
  sec = Math.max(0, Math.round(sec));
  return String(Math.floor(sec / 60)).padStart(2, '0') + ':' + String(sec % 60).padStart(2, '0');
};
const QCOL = {'Планер': 'var(--glider)', 'Двигатель': 'var(--engine)', 'Авионика': 'var(--avionics)'};
const ACTION = {
  enroute: ['arrive', 'Я на месте'],
  arrived: ['start', 'Начать работу'],
  working: ['resolve', 'Работа выполнена'],
};

let AIR = null, S = null, ws = null, reconnectTimer = null;
let shouldReconnect = true;
let retry = 0;
const busy = new Set();

function toast(text, bad = false) {
  const t = $('#engToast');
  if (!t) return;
  t.textContent = text;
  t.className = 'toast' + (bad ? ' bad' : '');
  t.hidden = false;
  clearTimeout(toast.h);
  toast.h = setTimeout(() => { t.hidden = true; }, 3500);
}

async function api(path, body) {
  const opts = {method: 'POST', headers: {}};
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const response = await fetch(path, opts);
  let data = {};
  try { data = await response.json(); } catch (_) {}
  if (response.status === 401) {
    location.replace('/login?next=/engineer');
    throw new Error('Сессия истекла');
  }
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Ошибка сервера');
  return data;
}

function setConnection(on) {
  const el = $('#engConn');
  if (!el) return;
  el.className = 'eng-conn ' + (on ? 'on' : 'off');
  el.querySelector('span').textContent = on ? 'Онлайн' : 'Нет связи';
}

function connect() {
  clearTimeout(reconnectTimer);
  if (!shouldReconnect) return;
  const proto = location.protocol === 'https:' ? 'wss://' : 'ws://';
  try {
    ws = new WebSocket(proto + location.host + '/ws');
    ws.onopen = () => { retry = 0; setConnection(true); };
    ws.onmessage = event => {
      try {
        S = JSON.parse(event.data);
        render();
      } catch (e) {
        console.error('Некорректное сообщение WebSocket', e);
      }
    };
    ws.onclose = event => {
      setConnection(false);
      if (event.code === 4401) {
        shouldReconnect = false;
        location.replace('/login?next=/engineer');
        return;
      }
      const delay = Math.min(1000 * Math.pow(1.7, retry++), 15000);
      reconnectTimer = setTimeout(connect, delay);
    };
    ws.onerror = () => {};
  } catch (e) {
    setConnection(false);
    reconnectTimer = setTimeout(connect, 3000);
  }
}

async function loadState() {
  try {
    const response = await fetch('/api/state', {cache: 'no-store'});
    if (response.status === 401) {
      location.replace('/login?next=/engineer');
      return;
    }
    if (!response.ok) throw new Error('Не удалось загрузить состояние');
    S = await response.json();
    render();
  } catch (e) {
    toast(e.message, true);
  }
}

function baseName(id) {
  const b = (AIR && AIR.bases || []).find(x => x.id === id);
  return b ? b.name : (id || '—');
}

function renderHeader() {
  const account = S.account || {};
  const me = S.me || {};
  const name = account.name || me.name || 'Инженер';
  const initial = name.trim().slice(0, 1).toUpperCase() || 'И';
  $('#engName').textContent = name;
  $('#engAvatar').textContent = initial;
  const airport = S.airport_name || (AIR && AIR.name) || 'Смена';
  $('#engAirport').textContent = airport;
  $('#engSub').textContent = [
    account.qual || me.qual || '—',
    'База: ' + baseName(account.base || me.base),
    account.email || '',
  ].filter(Boolean).join(' · ');
}

function renderStats() {
  const st = S.stats || {};
  const cards = [
    [st.active ?? 0, 'активных'],
    [st.done ?? 0, 'выполнено'],
    [st.norm_pct == null ? '—' : st.norm_pct + '%', 'в норматив'],
    [st.avg_response == null ? '—' : fmt(st.avg_response), 'среднее прибытие'],
  ];
  $('#engStats').innerHTML = cards.map(([value, label]) =>
    `<div class="eng-stat"><b>${esc(value)}</b><span>${esc(label)}</span></div>`
  ).join('');
}

function aircraftFor(r) {
  return (S.aircraft || []).find(a => a.id === r.aircraft_id) || null;
}

function taskCard(r) {
  const em = r.priority === 'emergency';
  const action = ACTION[r.status];
  const aircraft = aircraftFor(r);
  const eta = r.status === 'enroute' ? fmt(r.eta_left) : null;
  const elapsed = r.elapsed != null ? r.elapsed : 0;
  const progress = Math.min(100, Math.max(0, elapsed / (S.norm || 900) * 100));
  const left = r.status === 'enroute' ? 'До прибытия' : r.status === 'arrived' ? 'На стоянке' : 'В работе';
  const flight = r.flight || (aircraft && aircraft.flight) || '—';
  return `<article class="eng-task st-${esc(r.status)}${em ? ' em' : ''}" data-task="${r.id}">
    <header class="eng-task-head">
      <span class="eng-task-id">#${r.id}</span>
      <span class="eng-task-stand">${esc(r.stand)}</span>
      <span class="eng-badge st-${esc(r.status)}">${esc(r.status_ru)}</span>
      ${em ? '<span class="eng-badge em">ЧС</span>' : ''}
    </header>
    <h3>${esc(r.description)}</h3>
    ${r.extra ? `<p class="eng-task-extra">${esc(r.extra)}</p>` : ''}
    <div class="eng-task-facts">
      <div><span>Рейс</span><b>${esc(flight)}</b></div>
      <div><span>Квалификация</span><b>${esc(r.qual)}</b></div>
      <div><span>${left}</span><b>${eta || (r.status === 'working' ? fmt(elapsed) : '—')}</b></div>
    </div>
    ${r.note ? `<p class="eng-task-note">${esc(r.note)}</p>` : ''}
    <div class="eng-progress" title="Время с момента вызова">
      <i style="width:${progress}%"></i>
    </div>
    <div class="eng-task-foot">
      <span class="eng-elapsed">С момента вызова: <b>${fmt(elapsed)}</b></span>
      ${action ? `<button class="primary eng-action" data-eng-act="${action[0]}" data-id="${r.id}"
        ${busy.has(`${r.id}:${action[0]}`) ? 'disabled' : ''}>
        ${busy.has(`${r.id}:${action[0]}`) ? 'Сохраняем…' : action[1]}</button>` : ''}
    </div>
  </article>`;
}

function renderTasks() {
  const tasks = S.active || [];
  const hint = $('#engTaskHint');
  if (hint) hint.textContent = tasks.length
    ? `Назначено: ${tasks.length}. Отмечайте этапы по мере работы.`
    : 'Активных назначений нет';
  $('#engTasks').innerHTML = tasks.length
    ? tasks.map(taskCard).join('')
    : `<div class="eng-free">
        <div class="eng-free-mark">✓</div>
        <div><b>Вы свободны</b>
        <p>Диспетчер назначит заявку автоматически — по квалификации, допуску на тип ВС и ближайшему маршруту.</p></div>
      </div>`;
}

function renderHistory() {
  const items = (S.history || []).slice(0, 10);
  $('#engHistory').innerHTML = items.length ? items.map(r => `
    <div class="eng-history-row">
      <span class="eng-history-id">#${r.id}</span>
      <span class="eng-history-stand">${esc(r.stand)}</span>
      <span class="eng-history-desc">${esc(r.description)}</span>
      <span class="eng-badge st-${esc(r.status)}">${esc(r.status_ru)}</span>
    </div>`).join('')
    : '<p class="empty small">Пока нет выполненных заявок.</p>';
}

function renderEvents() {
  const events = (S.events || []).slice(0, 16);
  $('#engEvents').innerHTML = events.length ? events.map(e => `
    <div class="eng-event ${esc(e.kind || '')}">
      <time>${esc(e.ts)}</time>
      <span>${esc(e.text)}</span>
    </div>`).join('')
    : '<p class="empty small">События появятся после назначения заявки.</p>';
}

function renderMap() {
  const svg = $('#engMap');
  const empty = $('#engMapEmpty');
  if (!svg || !AIR) return;
  const me = S.me;
  if (!me) {
    svg.innerHTML = '';
    if (empty) empty.hidden = false;
    return;
  }
  const active = (S.active || [])[0] || null;
  const points = [];
  if (me.x != null && me.y != null) points.push([me.x, me.y]);
  (me.route || []).forEach(p => points.push([p[0], p[1]]));
  let stand = null;
  if (active) {
    stand = (AIR.stands || []).find(s => s.id === active.stand);
    if (stand && stand.node != null) {
      const node = (AIR.nodes || []).find(n => n.id === stand.node);
      if (node) points.push([node.x, node.y]);
    }
  }
  if (!points.length) {
    svg.innerHTML = '';
    if (empty) empty.hidden = false;
    return;
  }
  if (empty) empty.hidden = true;

  const xs = points.map(p => p[0]), ys = points.map(p => p[1]);
  let minX = Math.min(...xs), maxX = Math.max(...xs);
  let minY = Math.min(...ys), maxY = Math.max(...ys);
  const pad = 70;
  const w = Math.max(maxX - minX, 220) + pad * 2;
  const h = Math.max(maxY - minY, 160) + pad * 2;
  const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
  svg.setAttribute('viewBox', `${cx - w / 2} ${cy - h / 2} ${w} ${h}`);

  const route = (me.route || []).length > 1
    ? `<polyline class="eng-route-line" points="${[ [me.x, me.y], ...me.route ].map(p => p.join(',')).join(' ')}"/>`
    : '';
  const standHtml = stand ? `
    <circle class="eng-stand-ring" cx="${points[points.length - 1][0]}" cy="${points[points.length - 1][1]}" r="42"/>
    <text class="eng-stand-label" x="${points[points.length - 1][0]}" y="${points[points.length - 1][1] + 76}"
      text-anchor="middle">${esc(active.stand)}</text>` : '';
  svg.innerHTML = `
    <circle class="eng-map-grid" cx="${cx}" cy="${cy}" r="${Math.max(w, h) * 0.34}"/>
    ${route}
    ${standHtml}
    <circle class="eng-me-ring" cx="${me.x}" cy="${me.y}" r="28"/>
    <circle class="eng-me" cx="${me.x}" cy="${me.y}" r="10" style="fill:${QCOL[me.qual] || 'var(--accent)'}"/>
    <text class="eng-me-label" x="${me.x}" y="${me.y - 44}" text-anchor="middle">Вы</text>`;
}

function render() {
  if (!S) return;
  renderHeader();
  renderStats();
  renderTasks();
  renderHistory();
  renderEvents();
  renderMap();
}

async function act(button) {
  const key = `${button.dataset.id}:${button.dataset.engAct}`;
  if (busy.has(key)) return;
  busy.add(key);
  button.disabled = true;
  const label = button.textContent;
  button.textContent = 'Сохраняем…';
  try {
    await api(`/api/engineer/requests/${button.dataset.id}/${button.dataset.engAct}`, {});
    toast('Статус обновлён');
    await loadState();
  } catch (e) {
    toast(e.message, true);
  } finally {
    busy.delete(key);
    if (button.isConnected) {
      button.disabled = false;
      button.textContent = label;
    }
  }
}

async function init() {
  try {
    const airportResponse = await fetch('/api/airport', {cache: 'no-store'});
    if (airportResponse.status === 401) {
      location.replace('/login?next=/engineer');
      return;
    }
    AIR = await airportResponse.json();
    await loadState();
    connect();
  } catch (e) {
    toast('Не удалось запустить приложение: ' + e.message, true);
  }

  $('#engLogout').addEventListener('click', async () => {
    shouldReconnect = false;
    clearTimeout(reconnectTimer);
    try { await api('/api/auth/logout', {}); } catch (_) {}
    location.replace('/login');
  });

  $('#engRefresh').addEventListener('click', () => loadState());

  document.addEventListener('click', event => {
    const button = event.target.closest('[data-eng-act]');
    if (button) act(button);
  });

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') loadState();
  });
}

init();
