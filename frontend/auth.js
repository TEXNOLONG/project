/* Вход и регистрация инженера */
const $ = (s, r = document) => r.querySelector(s);

let META = null;
let lastRegisteredEmail = "";
const startupStartedAt = performance.now();

async function finishStartup() {
  const status = $('#authBootStatus');
  const progress = $('#authBootProgress');
  if (status) status.textContent = 'Рабочее пространство готово';
  if (progress) progress.style.width = '100%';
  document.querySelectorAll('[data-auth-stage]').forEach(node => {
    const stage = Number(node.dataset.authStage);
    node.classList.toggle('active', stage === 2);
    node.classList.toggle('done', stage < 2);
  });
  await new Promise(resolve => setTimeout(resolve, Math.max(0, 1250 - (performance.now() - startupStartedAt))));
  document.body.classList.remove('auth-starting');
  await new Promise(resolve => setTimeout(resolve, 420));
  $('#authStartup')?.remove();
}

function showMessage(node, text, kind = 'bad') {
  if (!node) return;
  node.textContent = text;
  node.className = 'form-message ' + kind;
  node.hidden = false;
}

function clearMessage(node) {
  if (!node) return;
  node.textContent = '';
  node.hidden = true;
}

async function request(path, body) {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  let data = {};
  try { data = await response.json(); } catch (_) {}
  if (!response.ok) {
    const detail = data.detail;
    throw new Error(typeof detail === 'string' ? detail : 'Проверьте заполнение полей');
  }
  return data;
}

function setBusy(button, busy, text) {
  if (!button) return;
  button.disabled = busy;
  if (busy) {
    button.dataset.label = button.textContent;
    button.textContent = text || 'Подождите…';
  } else if (button.dataset.label) {
    button.textContent = button.dataset.label;
  }
}

function setMode(mode) {
  const login = mode !== 'register';
  $('#loginPane').hidden = !login;
  $('#registerPane').hidden = login;
  $('#successPane').hidden = true;
  document.querySelectorAll('[data-auth-tab]').forEach(button => {
    button.classList.toggle('on', button.dataset.authTab === mode);
  });
  const url = new URL(location.href);
  if (login) url.searchParams.delete('mode');
  else url.searchParams.set('mode', 'register');
  history.replaceState(null, '', url);
}

async function demoLogin(email, password, button) {
  const msg = $('#loginMsg');
  clearMessage(msg);
  setBusy(button, true, 'Входим…');
  try {
    const data = await request('/api/auth/login', { email, password });
    location.replace(data.redirect || '/');
  } catch (e) {
    showMessage(msg, e.message, 'bad');
    setBusy(button, false);
  }
}

function fillMeta() {
  if (!META) return;
  const qual = $('#regQual');
  const base = $('#regBase');
  const chips = $('#typeChips');
  qual.innerHTML = META.quals.map(q => `<option>${q}</option>`).join('');
  base.innerHTML = META.bases.map(b => `<option value="${b.id}">${b.name}</option>`).join('');
  chips.innerHTML = META.types.map((type, i) => `
    <label class="type-chip">
      <input type="checkbox" name="types" value="${type}" ${i === 0 ? 'checked' : ''}>
      <span>${type}</span>
    </label>`).join('');

  if (META.demo && META.demo.enabled) {
    const box = $('#demoBox');
    const dr = $('#demoDispatcher');
    const en = $('#demoEngineer');
    if (dr) dr.textContent = `${META.demo.dispatcher_email} / ${META.demo.dispatcher_password}`;
    if (en) en.textContent = `${META.demo.engineer_email} / ${META.demo.engineer_password}`;
    if (box) box.hidden = false;
  }
}

async function init() {
  const status = $('#authBootStatus');
  const progress = $('#authBootProgress');
  if (status) status.textContent = 'Проверяем сервис и настройки смены…';
  if (progress) progress.style.width = '42%';
  document.querySelectorAll('[data-auth-stage]').forEach(node => {
    const stage = Number(node.dataset.authStage);
    node.classList.toggle('active', stage === 1);
    node.classList.toggle('done', stage < 1);
  });
  try {
    META = await (await fetch('/api/meta')).json();
    fillMeta();
    if (status) status.textContent = 'Сервис готов · подготовили вход и регистрацию';
    if (progress) progress.style.width = '82%';
  } catch (e) {
    showMessage($('#loginMsg'), 'Сервер недоступен. Проверьте, что он запущен.', 'bad');
    if (status) status.textContent = 'Сервис не отвечает · проверьте подключение';
  }

  const mode = new URLSearchParams(location.search).get('mode');
  setMode(mode === 'register' ? 'register' : 'login');

  if (META?.demo?.enabled) {
    const d = $('#demoDispatcherBtn');
    const e = $('#demoEngineerBtn');
    if (d) d.addEventListener('click', () => demoLogin(META.demo.dispatcher_email, META.demo.dispatcher_password, d));
    if (e) e.addEventListener('click', () => demoLogin(META.demo.engineer_email, META.demo.engineer_password, e));
  }

  document.querySelectorAll('[data-auth-tab]').forEach(button => {
    button.addEventListener('click', () => setMode(button.dataset.authTab));
  });

  $('#loginForm').addEventListener('submit', async event => {
    event.preventDefault();
    const msg = $('#loginMsg');
    clearMessage(msg);
    const button = $('#loginSubmit');
    setBusy(button, true, 'Входим…');
    try {
      const data = await request('/api/auth/login', {
        email: $('#loginEmail').value.trim(),
        password: $('#loginPassword').value,
      });
      location.replace(data.redirect || '/');
    } catch (e) {
      showMessage(msg, e.message, 'bad');
      setBusy(button, false);
    }
  });

  $('#registerForm').addEventListener('submit', async event => {
    event.preventDefault();
    const msg = $('#registerMsg');
    clearMessage(msg);
    const types = [...document.querySelectorAll('input[name="types"]:checked')].map(x => x.value);
    if (!types.length) {
      showMessage(msg, 'Выберите хотя бы один тип воздушного судна', 'bad');
      return;
    }
    const button = $('#registerSubmit');
    setBusy(button, true, 'Отправляем…');
    try {
      const email = $('#regEmail').value.trim();
      const data = await request('/api/auth/register', {
        name: $('#regName').value.trim(),
        email,
        password: $('#regPassword').value,
        qual: $('#regQual').value,
        types,
        base: $('#regBase').value,
      });
      lastRegisteredEmail = email;
      $('#loginPane').hidden = true;
      $('#registerPane').hidden = true;
      $('#successPane').hidden = false;
      document.querySelectorAll('[data-auth-tab]').forEach(b => b.classList.remove('on'));
      const successText = $('#successPane p');
      if (successText) successText.textContent = data.message;
    } catch (e) {
      showMessage(msg, e.message, 'bad');
      setBusy(button, false);
    }
  });

  $('#successLogin').addEventListener('click', () => {
    setMode('login');
    if (lastRegisteredEmail) $('#loginEmail').value = lastRegisteredEmail;
    $('#loginPassword').focus();
  });
}

init().finally(finishStartup);
