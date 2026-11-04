'use strict';
// OmniPay web app. No framework, no build step. Every piece of text goes through text nodes (never innerHTML), so memos,
// names and assistant replies written by other people cannot inject markup. SVG is built with createElementNS.
const S = { token: null, me: null, cfg: null, assets: [], rates: {}, names: {}, timers: [], route: { view: 'home', q: new URLSearchParams() } };
try { S.token = sessionStorage.getItem('omnipay_token'); } catch { /* storage blocked: stay signed out */ }
const $ = (s) => document.querySelector(s);
const SVGNS = 'http://www.w3.org/2000/svg';

function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === 'class') e.className = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else if (k === 'value') e.value = v;
    else if (v === true) e.setAttribute(k, '');
    else if (v !== false && v != null) e.setAttribute(k, v);
  }
  for (const c of kids.flat(Infinity)) { if (c == null || c === false) continue; e.append(typeof c === 'object' ? c : document.createTextNode(String(c))); }
  return e;
}
function svg(tag, attrs, ...kids) {
  const e = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
  for (const c of kids) if (c) e.append(c);
  return e;
}
const ICONS = {
  home: 'M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z',
  send: 'M7 17 17 7M8 7h9v9',
  receive: 'M17 7 7 17M16 17H7V8',
  qr: 'M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h2v2h-2zM18 18h2v2h-2zM14 20h2M20 14v2',
  wallet: 'M3 7a2 2 0 0 1 2-2h13v4M3 7v11a2 2 0 0 0 2 2h15V9H5a2 2 0 0 1-2-2zM16 14.5h1',
  promote: 'M4 10v4h3l7 4V6l-7 4zM17.5 9a4 4 0 0 1 0 6',
  spark: 'M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8z',
  shield: 'M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6zM9 12l2 2 4-4',
  search: 'M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM20 20l-4-4',
  sun: 'M12 16.5a4.5 4.5 0 1 0 0-9 4.5 4.5 0 0 0 0 9zM12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4',
  moon: 'M20.5 13.5A8.5 8.5 0 1 1 10.5 3.5a6.6 6.6 0 0 0 10 10z',
  logout: 'M15 4h4a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-4M10 17l5-5-5-5M15 12H3',
  check: 'M5 12.5l4.5 4.5L19 7.5',
  copy: 'M9 9h11v11H9zM5 15H4V4h11v1',
  plus: 'M12 5v14M5 12h14',
  x: 'M6 6l12 12M18 6 6 18',
  convert: 'M5 8h12l-3-3M19 16H7l3 3',
  lock: 'M6 11h12v10H6zM8 11V7a4 4 0 0 1 8 0v4',
  refresh: 'M20 11a8 8 0 0 0-14.9-3M4 4v4h4M4 13a8 8 0 0 0 14.9 3M20 20v-4h-4',
  more: 'M4 7h16M4 12h16M4 17h16',
  add: 'M12 4v12M6 10l6 6 6-6M5 20h14',
  out: 'M12 20V8M6 14l6-6 6 6M5 4h14',
  alert: 'M12 8v5M12 16.5v.01M10.3 3.9 2 18a2 2 0 0 0 1.7 3h16.6a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z',
  doc: 'M7 3h7l5 5v13H7zM14 3v5h5M10 13h6M10 17h6',
  scan: 'M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3M4 12h16',
  user: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21a8 8 0 0 1 16 0',
  share: 'M12 15V3M8 7l4-4 4 4M5 12v7a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-7',
  image: 'M4 5h16v14H4zM4 15l4-4 5 5M14 14l2-2 4 4M15.5 9.5h.01',
  phone: 'M8 3h8a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zM11 18h2',
  keyboard: 'M3 7h18v10H3zM7 11h.01M11 11h.01M15 11h.01M8 14h8',
};
const icon = (name, attrs) => svg('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.8', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true', ...(attrs || {}) }, svg('path', { d: ICONS[name] }));
function brandMark() {
  return svg('svg', { viewBox: '0 0 32 32', class: 'brand-mark', 'aria-hidden': 'true' },
    svg('rect', { width: '32', height: '32', rx: '9', fill: 'currentColor' }),
    svg('circle', { cx: '13', cy: '16', r: '6.5', fill: 'none', stroke: 'var(--paper)', 'stroke-width': '2.6' }),
    svg('circle', { cx: '20.5', cy: '16', r: '6.5', fill: 'none', stroke: '#6F87FF', 'stroke-width': '2.6' }));
}

// ---------------------------------------------------------------- assets and formatting
const META = {
  USD: ['$', 'US dollar'], EUR: ['€', 'Euro'], GBP: ['£', 'British pound'], INR: ['₹', 'Indian rupee'], JPY: ['¥', 'Japanese yen'],
  USDC: ['U', 'USD Coin'], BTC: ['₿', 'Bitcoin'], ETH: ['Ξ', 'Ether'], AAPL: ['A', 'Apple shares'], TSLA: ['T', 'Tesla shares'],
};
const KIND = { fiat: 'Cash', crypto: 'Crypto', equity: 'Shares' };
const asset = (code) => S.assets.find((a) => a.code === code) || { code, kind: 'fiat', dp: 2, simulated: false, enabled: true };
const assetName = (code) => (META[code] || [code, code])[1];
const token = (code, size) => { const a = asset(code); return h('span', { class: `token ${a.kind} ${size || ''}`, 'aria-hidden': 'true' }, (META[code] || [code[0]])[0]); };
function fmt(amount, code) {
  const a = asset(code), n = Number(amount);
  if (!Number.isFinite(n)) return `${amount} ${code}`;
  if (a.kind === 'fiat') return new Intl.NumberFormat('en-US', { style: 'currency', currency: code, minimumFractionDigits: a.dp, maximumFractionDigits: a.dp }).format(n);
  return `${n.toLocaleString('en-US', { maximumFractionDigits: a.dp })} ${code}`;
}
const usd = (n) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(Number(n) || 0);
const toUsd = (amount, code) => (S.rates[code] != null ? Number(amount) * Number(S.rates[code]) : null);
const fiatCodes = () => S.assets.filter((a) => a.kind === 'fiat').map((a) => a.code);
const shortName = (handle) => { const s = String(handle || '').split('@')[0]; return s.charAt(0).toUpperCase() + s.slice(1); };
const nameOf = (handle) => S.names[handle] || shortName(handle);
const AV = ['#2747E8', '#D9900A', '#0F9B70', '#7445E0', '#C8322B', '#0E7C9B', '#B4467F'];
function avatar(handle, size) {
  let x = 0; for (const ch of String(handle)) x = (x * 31 + ch.charCodeAt(0)) >>> 0;
  return h('span', { class: `avatar ${size || ''}`, style: `--av:${AV[x % AV.length]}`, 'aria-hidden': 'true' }, nameOf(handle).slice(0, 1).toUpperCase());
}
const when = (ts) => { if (!ts) return ''; const d = new Date(ts * 1000), now = new Date(); return d.toDateString() === now.toDateString() ? d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : d.toLocaleDateString([], { month: 'short', day: 'numeric' }); };
const tag = (t, cls) => h('span', { class: 'tag ' + (cls || '') }, t);
const simTag = (code) => (asset(code).simulated ? tag('Simulated', 'sim') : null);
const stateTag = (s) => tag(s.charAt(0) + s.slice(1).toLowerCase().replace(/_/g, ' '), ['SETTLED', 'SUCCESS', 'PAID'].includes(s) ? 'good' : ['FAILED', 'REJECTED', 'RETURNED', 'CANCELLED'].includes(s) ? 'bad' : 'warn');

// ---------------------------------------------------------------- api
const FRIENDLY = {
  BAD_LOGIN: 'That handle and password don\'t match.', BAD_PIN: 'That PIN didn\'t match. Try again.', PIN_LOCKED: 'Too many wrong PINs. Wait a few minutes, then try again.',
  LOGIN_LOCKED: 'Too many attempts. Wait a few minutes, then try again.', QUOTE_EXPIRED: 'The price expired. Get fresh prices and try again.', RATE_LIMITED: 'Slow down a little, then try again.',
  UNKNOWN_USER: 'There is no OmniPay account with that handle.', HANDLE_TAKEN: 'That handle is taken. Pick another.', WEAK_PASSWORD: 'Use a password of 10 or more characters.',
  BAD_PIN_FORMAT: 'Your PIN must be 4 to 8 digits.', REGION_BLOCKED: 'OmniPay isn\'t available in that country.', KILL_SWITCH: 'This is paused by the OmniPay team right now.',
  UNKNOWN_INVOICE: 'No request with that ID exists.', STEP_UP_REQUIRED: 'Confirm your password to continue.', BAD_EMAIL: 'Enter a valid email address.',
};
async function rawApi(method, path, body) {
  const r = await fetch(path, { method, headers: { 'Content-Type': 'application/json', ...(S.token ? { Authorization: 'Bearer ' + S.token } : {}) }, body: body === undefined ? undefined : JSON.stringify(body) });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(FRIENDLY[d.error] || d.detail || d.error || r.statusText); e.code = d.error; e.detail = d.detail; e.status = r.status; throw e; }
  return d;
}
async function api(method, path, body) {
  try { return await rawApi(method, path, body); } catch (e) {
    if (e.code === 'STEP_UP_REQUIRED') {                       // sensitive action: re-enter the password, then retry once
      const pw = await askPassword();
      if (pw == null) throw e;
      await rawApi('POST', '/v1/auth/stepup', { password: pw });
      return rawApi(method, path, body);
    }
    if (e.code === 'UNAUTHENTICATED' && S.me) logout(true);
    throw e;
  }
}
function guard(out, fn) {                                        // run an async action, show any error inline
  return async (ev) => {
    if (out) { out.textContent = ''; out.className = 'msg-line'; }
    const btn = ev && ev.currentTarget && ev.currentTarget.tagName === 'BUTTON' ? ev.currentTarget : null;
    if (btn) btn.disabled = true;
    try { await fn(ev); } catch (e) { if (out) { out.className = 'msg-line err'; out.textContent = e.message; } else toast(e.message, 'bad'); } finally { if (btn) btn.disabled = false; }
  };
}
function toast(text, kind) {
  const t = h('div', { class: 'toast ' + (kind || '') }, icon(kind === 'bad' ? 'alert' : 'check'), h('span', {}, text));
  const box = $('#toasts'); if (!box) return; box.append(t);
  setTimeout(() => t.remove && t.remove(), 3800);
}
function later(fn, ms) { const id = setTimeout(fn, ms); S.timers.push(() => clearTimeout(id)); }
function every(fn, ms) { const id = setInterval(fn, ms); S.timers.push(() => clearInterval(id)); return id; }
function countdown(el, expiresAt, onExpire) {
  const tick = () => {
    const s = Math.max(0, Math.round(expiresAt - Date.now() / 1000));
    el.textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
    if (s === 0 && onExpire) { onExpire(); onExpire = null; }
  };
  tick(); every(tick, 1000);
}
async function copy(text, what) { try { await navigator.clipboard.writeText(text); toast(`${what} copied`); } catch { toast('Copy isn\'t available here. Select the text instead.', 'bad'); } }

// ---------------------------------------------------------------- sheet (modal dialog)
function openSheet(build, opts) {
  const dlg = $('#sheet');
  const close = () => { try { dlg.close(); } catch { /* already closed */ } };
  const body = h('div', { class: 'sheet ' + ((opts && opts.cls) || '') });
  dlg.replaceChildren(body);
  build(body, close);
  if (!dlg.open) dlg.showModal();
  return { dlg, body, close };
}
function sheetHead(title, close) {
  return h('div', { class: 'sheet-head' }, h('h2', { id: 'sheet-title' }, title), h('button', { class: 'ghost icon', 'aria-label': 'Close', onclick: close }, icon('x')));
}
function askPassword() {
  return new Promise((res) => {
    let done = false;
    const finish = (v) => { if (!done) { done = true; res(v); } };
    const { dlg, close } = openSheet((b, close) => {
      const pw = h('input', { id: 'stepup_pw', type: 'password', autocomplete: 'current-password' });
      b.append(sheetHead('Confirm it\'s you', close), h('p', { class: 'mute' }, 'This action moves money or changes where it goes, so we ask for your password again.'),
        h('label', { class: 'field' }, 'Password', pw),
        h('button', { class: 'primary big wide', id: 'stepup_ok', onclick: () => { finish(pw.value); close(); } }, icon('lock'), 'Confirm'));
      pw.addEventListener('keydown', (e) => { if (e.key === 'Enter') { finish(pw.value); close(); } });
      later(() => pw.focus && pw.focus(), 50);
    });
    dlg.addEventListener('close', () => finish(null), { once: true });
  });
}

// ---------------------------------------------------------------- theme
function getTheme() { try { return localStorage.getItem('omnipay_theme'); } catch { return null; } }
function applyTheme(t) { if (document.documentElement) { if (t) document.documentElement.setAttribute('data-theme', t); else document.documentElement.removeAttribute('data-theme'); } }
function isDark() { const t = getTheme(); if (t) return t === 'dark'; try { return window.matchMedia('(prefers-color-scheme: dark)').matches; } catch { return false; } }
function toggleTheme() { const t = isDark() ? 'light' : 'dark'; try { localStorage.setItem('omnipay_theme', t); } catch { /* per-session only */ } applyTheme(t); render(); }

// ---------------------------------------------------------------- routing
const NAV = [
  ['home', 'Home', 'home'], ['pay', 'Pay', 'send'], ['receive', 'Receive', 'qr'], ['wallet', 'Wallet', 'wallet'],
  ['assistant', 'Assistant', 'spark'], ['promote', 'Promote', 'promote'], ['admin', 'Admin', 'shield'],
];
function parseHash() {
  const raw = (location.hash || '').replace(/^#\/?/, ''), [path, qs] = raw.split('?');
  return { view: path.split('/')[0] || 'home', q: new URLSearchParams(qs || '') };
}
function go(path) { const want = '#/' + path; if (location.hash === want) render(); else location.hash = want; }
window.addEventListener('hashchange', () => render());
window.addEventListener('keydown', (e) => { if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K') && S.me) { e.preventDefault(); openPalette(); } });

async function boot() {
  applyTheme(getTheme());
  [S.cfg, S.assets, S.rates] = await Promise.all([rawApi('GET', '/v1/config'), rawApi('GET', '/v1/assets').then((d) => d.assets), rawApi('GET', '/v1/rates').then((d) => d.rates).catch(() => ({}))]);
  try { if ('serviceWorker' in navigator && window.isSecureContext) navigator.serviceWorker.register('/sw.js').catch(() => {}); } catch { /* not installable here */ }
  if (S.token) { try { S.me = await api('GET', '/v1/me'); } catch { S.token = null; try { sessionStorage.removeItem('omnipay_token'); } catch { /* ignore */ } } }
  render();
}
function clearTimers() { S.timers.splice(0).forEach((f) => f()); }
function render() {
  clearTimers();
  S.route = parseHash();
  if (!S.me) return viewAuth();
  let openScan = false;
  if (S.route.view === 'scan') { S.route.view = 'home'; openScan = true; }
  const allowed = NAV.filter(([k]) => k !== 'admin' || S.me.admin).map(([k]) => k).concat('profile');
  if (!allowed.includes(S.route.view)) S.route.view = 'home';
  const views = { profile: viewProfile, home: viewHome, pay: viewPay, wallet: viewWallet, receive: viewReceive, promote: viewPromote, assistant: viewAssistant, admin: viewAdmin };
  const titles = { profile: 'Profile', home: greeting(), pay: 'Pay', wallet: 'Wallet', receive: 'Receive', promote: 'Promote an asset', assistant: 'Assistant', admin: 'Operations' };
  const view = h('div', { id: 'view', class: 'stack l' }, h('div', { class: 'skeleton' }));
  $('#app').replaceChildren(shell(titles[S.route.view], view));
  const mine = S.route.view;
  views[mine](S.route.q).then((els) => { if (S.route.view === mine) view.replaceChildren(...[els].flat()); })
    .catch((e) => view.replaceChildren(h('div', { class: 'panel' }, h('p', { class: 'err' }, e.message))));
  if (openScan) later(openScanner, 50);
}
function greeting() { const hr = new Date().getHours(); return `${hr < 12 ? 'Good morning' : hr < 18 ? 'Good afternoon' : 'Good evening'}, ${shortName(S.me.handle)}`; }
function envTags() {
  return h('div', { class: 'env' }, S.cfg.paypal_simulated ? tag('Simulated PayPal', 'sim') : tag('PayPal ' + S.cfg.paypal_env, 'cobalt'), S.cfg.demo ? tag('Demo data', 'warn') : null);
}
function shell(title, view) {
  const cur = S.route.view;
  const items = NAV.filter(([k]) => k !== 'admin' || S.me.admin);
  const side = h('aside', { class: 'side' },
    h('a', { class: 'brand', href: '#/home' }, brandMark(), 'OmniPay'),
    h('nav', { id: 'nav', class: 'stack s', 'aria-label': 'Main' }, items.map(([k, label, ic]) => h('a', { class: 'nav-item', href: '#/' + k, 'aria-current': cur === k ? 'page' : false }, icon(ic), label))),
    h('div', { class: 'grow' }),
    h('div', { class: 'side-foot', id: 'who' },
      h('a', { class: 'me-chip nav-item', href: '#/profile', 'aria-current': cur === 'profile' ? 'page' : false }, avatar(S.me.handle, 'sm'), h('div', { class: 'main' }, h('div', { class: 't1' }, S.me.handle), h('div', { class: 'small', style: 'opacity:.75' }, 'Profile and your QR code'))),
      h('button', { class: 'nav-item', onclick: toggleTheme }, icon(isDark() ? 'sun' : 'moon'), isDark() ? 'Light theme' : 'Dark theme'),
      h('button', { class: 'nav-item', id: 'logout', onclick: () => logout() }, icon('logout'), 'Log out')));
  const top = h('div', { class: 'topbar' }, h('h1', {}, title), envTags(),
    h('button', { class: 'accent', id: 'scan_top', onclick: openScanner }, icon('scan'), 'Scan'),
    h('button', { class: 'search-btn', onclick: openPalette, 'aria-label': 'Search and commands' }, icon('search'), 'Jump to…', h('kbd', {}, '⌘K')));
  const mobileTop = h('div', { class: 'mobile-top' }, h('a', { class: 'brand', href: '#/home' }, brandMark(), 'OmniPay'),
    h('button', { class: 'ghost icon', 'aria-label': 'Search and commands', onclick: openPalette }, icon('search')),
    h('a', { href: '#/profile', 'aria-label': 'Profile and your QR code' }, avatar(S.me.handle, 'sm')));
  const tab = (k, label, ic) => h('a', { href: '#/' + k, 'aria-current': cur === k ? 'page' : false }, h('span', { class: 'ic' }, icon(ic)), label);
  const tabbar = h('nav', { class: 'tabbar', 'aria-label': 'Main' }, tab('home', 'Home', 'home'), tab('pay', 'Pay', 'send'),
    h('button', { class: 'scan-tab', id: 'scan_tab', onclick: openScanner }, h('span', { class: 'ic' }, icon('scan')), 'Scan'),
    tab('wallet', 'Wallet', 'wallet'), tab('profile', 'Profile', 'user'));
  const foot = h('footer', { class: 'foot', id: 'foot' }, h('p', {}, S.cfg.fee_statement),
    h('p', {}, 'Crypto and shares are simulated in this version: no real asset moves, and prices are demo prices. Nothing here is financial, legal or tax advice.'));
  return h('div', { class: 'shell' }, side, h('div', {}, mobileTop, h('main', { class: 'content', id: 'main' }, top, view, foot)), tabbar);
}
async function logout(silent) {
  try { if (!silent) await rawApi('POST', '/v1/auth/logout', {}); } catch { /* session already gone */ }
  S.token = null; S.me = null; try { sessionStorage.removeItem('omnipay_token'); } catch { /* ignore */ }
  if (location.hash && location.hash !== '#/') location.hash = '/'; render();
}
async function refreshMe() { S.me = await api('GET', '/v1/me'); }
async function signIn(path, body) {
  const d = await rawApi('POST', path, body); S.token = d.token;
  try { sessionStorage.setItem('omnipay_token', d.token); } catch { /* this tab only */ }
  await refreshMe(); render();
}

// ---------------------------------------------------------------- auth
function viewAuth() {
  const out = h('p', { class: 'msg-line' });
  let mode = 'in';
  const hIn = h('input', { id: 'a_h', autocomplete: 'username', placeholder: 'name@omni', spellcheck: 'false' });
  const pIn = h('input', { id: 'a_p', type: 'password', autocomplete: 'current-password' });
  const pinIn = h('input', { id: 'a_pin', type: 'password', inputmode: 'numeric', maxlength: '8', autocomplete: 'off' });
  const region = h('select', { id: 'a_r' }, [['US', 'United States'], ['IN', 'India'], ['GB', 'United Kingdom'], ['DE', 'Germany'], ['FR', 'France']].map(([v, l]) => h('option', { value: v }, l)));
  const extra = h('div', { class: 'stack s' });
  const submit = h('button', { class: 'primary big wide', id: 'a_go' });
  const seg = h('div', { class: 'seg', role: 'group', 'aria-label': 'Sign in or create an account' });
  const draw = () => {
    seg.replaceChildren(h('button', { 'aria-pressed': String(mode === 'in'), onclick: () => { mode = 'in'; draw(); } }, 'Sign in'), h('button', { 'aria-pressed': String(mode === 'up'), onclick: () => { mode = 'up'; draw(); } }, 'Create account'));
    extra.replaceChildren(...(mode === 'up' ? [h('div', { class: 'grid-2' }, h('label', { class: 'field' }, 'Payment PIN', pinIn, h('span', { class: 'hint' }, '4 to 8 digits. You enter it for every payment.')), h('label', { class: 'field' }, 'Country', region))] : []));
    submit.replaceChildren(mode === 'in' ? 'Sign in' : 'Create account');
    pIn.setAttribute('autocomplete', mode === 'in' ? 'current-password' : 'new-password');
  };
  const go_ = guard(out, () => (mode === 'in' ? signIn('/v1/auth/login', { handle: hIn.value.trim(), password: pIn.value })
    : signIn('/v1/auth/signup', { handle: hIn.value.trim(), password: pIn.value, pin: pinIn.value, region: region.value })));
  submit.addEventListener('click', go_);
  pIn.addEventListener('keydown', (e) => { if (e.key === 'Enter') go_(); });
  draw();

  const form = h('section', { class: 'auth-form' },
    h('div', { class: 'stack s' }, h('h2', {}, 'Welcome to OmniPay'), h('p', { class: 'mute' }, 'Sign in with your handle, or create one in a few seconds.')), seg,
    h('div', { class: 'stack' }, h('label', { class: 'field' }, 'Handle', hIn), h('label', { class: 'field' }, 'Password', pIn, h('span', { class: 'hint' }, '10 characters or more.')), extra, submit, out));
  if (S.cfg.demo) {
    const dout = h('p', { class: 'msg-line' });
    const persona = (handle, pw, title, sub) => h('button', { class: 'persona', onclick: guard(dout, () => signIn('/v1/auth/login', { handle, password: pw })) }, avatar(handle), h('span', {}, h('b', {}, title), h('small', {}, sub)));
    form.append(h('div', { class: 'stack s' }, h('h3', {}, 'Try it with a demo account'),
      h('div', { class: 'personas' },
        persona('alice@omni', 'demo-password-1', 'Alice', 'Holds dollars, euros, crypto and shares'),
        persona('dana@omni', 'demo-password-1', 'Dana', 'Gets paid in euros, BTC, ETH or Apple shares'),
        persona('coinco@omni', 'demo-password-1', 'Coinco', 'Promotes USDC as a way to pay'),
        persona('admin@omni', 'demo-admin-password-1', 'Admin', 'Kill switches, ledger and reconciliation')),
      h('p', { class: 'hint' }, 'Demo password demo-password-1, payment PIN 1234. Data is fake and resets on demand.'),
      h('div', { class: 'row center' }, h('button', { class: 'ghost', onclick: guard(dout, async () => { await rawApi('POST', '/v1/demo/reset', {}); dout.className = 'msg-line ok'; dout.textContent = 'Demo data reset.'; }) }, icon('refresh'), 'Reset demo data')), dout));
  }
  if (S.cfg.lan_url && isLocalHost()) form.append(phoneCard(true));
  form.append(h('p', { class: 'hint', id: 'foot' }, S.cfg.fee_statement));

  // the story: one request, every way to pay it, priced live from /v1/rates
  const ask = 120, equiv = h('div', { class: 'equiv' });
  const codes = ['USD', 'EUR', 'GBP', 'USDC', 'BTC', 'ETH', 'AAPL', 'TSLA'].filter((c) => S.rates[c]);
  const storyAmt = (c) => { const v = ask / Number(S.rates[c]); return asset(c).kind === 'fiat' ? fmt(v, c) : fmt(Number(v.toPrecision(4)), c); };
  const cells = codes.map((c) => h('div', {}, token(c), h('span', {}, h('b', {}, storyAmt(c)), h('small', {}, KIND[asset(c).kind]))));
  equiv.append(...cells);
  let lit = 0; const light = () => { cells.forEach((c, i) => { c.className = i === lit ? 'lit' : ''; }); lit = (lit + 1) % cells.length; };
  light();
  let still = false; try { still = window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch { /* default to motion */ }
  if (!still && cells.length) every(light, 1600);
  const story = h('section', { class: 'auth-story' },
    h('div', { class: 'brand' }, brandMark(), 'OmniPay'),
    h('div', { class: 'stack' }, h('h1', {}, 'Pay with whatever you hold.'),
      h('p', { class: 'lede' }, 'Dollars, euros, rupees, stablecoins, bitcoin, even shares. You choose what leaves your wallet; the person you pay chooses what lands in theirs. OmniPay converts at mid-market and charges nothing for it.')),
    h('div', { class: 'invoice-demo', 'aria-label': 'Example: one request, many ways to pay' },
      h('div', { class: 'head' }, h('span', {}, 'Dana asks for'), h('b', {}, usd(ask))), equiv));
  $('#app').replaceChildren(h('div', { class: 'auth' }, story, form));
}

// ---------------------------------------------------------------- home
async function viewHome() {
  const [w, pays] = await Promise.all([api('GET', '/v1/wallet'), api('GET', '/v1/me/payments')]);
  const rows = w.balances.map((b) => ({ ...b, v: Number(b.usd || 0), kind: asset(b.asset).kind })).sort((a, b) => b.v - a.v);
  const total = rows.reduce((s, b) => s + b.v, 0);
  const byKind = ['fiat', 'crypto', 'equity'].map((k) => ({ k, v: rows.filter((r) => r.kind === k).reduce((s, r) => s + r.v, 0) })).filter((x) => x.v > 0);
  const [whole, cents] = usd(total).split('.');
  const holdings = h('section', { class: 'panel holdings', 'aria-label': 'Total balance' },
    h('div', { class: 'row center' }, h('span', { class: 'mute' }, 'Total balance'), h('span', { class: 'spacer' }), tag('Valued in USD at demo prices', 'sim')),
    h('div', { class: 'total num' }, whole, h('span', { class: 'cents' }, '.' + (cents || '00'))),
    h('p', { class: 'mute small' }, rows.length ? `Across ${rows.length} asset${rows.length === 1 ? '' : 's'}. Every one of them can pay anyone on OmniPay.` : 'Nothing here yet. Add money through PayPal, or share your QR code to get paid.'),
    total > 0 ? h('div', { class: 'mixbar', role: 'img', 'aria-label': byKind.map((x) => `${KIND[x.k]} ${Math.round((x.v / total) * 100)}%`).join(', ') },
      byKind.map((x) => h('span', { style: `width:${(x.v / total) * 100}%;background:var(--${x.k === 'fiat' ? 'fiat' : x.k})` }))) : null,
    h('div', { class: 'mix-legend' }, byKind.map((x) => h('span', {}, h('i', { style: `background:var(--${x.k})` }), `${KIND[x.k]} ${usd(x.v)} (${Math.round((x.v / total) * 100)}%)`))));
  const action = (ic, title, sub, path) => h('button', { class: 'action', onclick: () => go(path) }, icon(ic), h('div', {}, title, h('span', {}, h('br'), sub)));
  const actions = h('div', { class: 'actions' },
    h('button', { class: 'action', id: 'scan_home', onclick: openScanner }, icon('scan'), h('div', {}, 'Scan and pay', h('span', {}, h('br'), 'Any OmniPay or UPI QR code'))),
    action('send', 'Pay', 'In any asset you hold', 'pay'), action('qr', 'Receive', 'Your QR code and requests', 'receive'),
    action('add', 'Add money', 'Top up through PayPal', 'wallet'));
  const assetsList = h('section', { class: 'panel' }, h('div', { class: 'panel-head' }, h('h2', {}, 'Your assets'), h('a', { href: '#/wallet', class: 'small' }, 'Wallet')),
    rows.length ? h('div', { class: 'list' }, rows.map((b) => h('div', { class: 'li' }, token(b.asset), h('div', { class: 'main' }, h('div', { class: 't1' }, assetName(b.asset)), h('div', { class: 't2' }, KIND[b.kind], ' ', simTag(b.asset))),
      h('div', { class: 'end num' }, h('div', { class: 't1' }, fmt(b.amount, b.asset)), h('div', { class: 't2' }, b.usd ? usd(b.usd) : '')))))
      : h('div', { class: 'empty' }, h('strong', {}, 'Nothing here yet'), 'Add money through PayPal to start paying.'));
  const reqIn = h('input', { id: 'h_inv', placeholder: 'inv-…', spellcheck: 'false', autocomplete: 'off' }), rout = h('p', { class: 'msg-line' });
  const openReq = guard(rout, async () => { const id = reqIn.value.trim(); if (!/^inv-[a-z0-9]{6,20}$/.test(id)) throw new Error('Request IDs look like inv-1a2b3c4d5e.'); go('pay?invoice=' + encodeURIComponent(id)); });
  reqIn.addEventListener('keydown', (e) => { if (e.key === 'Enter') openReq(); });
  const activity = h('section', { class: 'panel' }, h('div', { class: 'panel-head' }, h('h2', {}, 'Activity')), activityList(pays.payments.slice(0, 8)),
    h('hr', { class: 'divider' }), h('div', { class: 'stack s', style: 'margin-top:12px' }, h('h3', {}, 'Got a payment request?'), h('div', { class: 'row' }, h('div', { class: 'grow' }, reqIn), h('button', { onclick: openReq }, 'Open')), rout));
  return [holdings, actions, h('div', { class: 'grid-2' }, assetsList, activity)];
}
function activityList(pays) {
  if (!pays.length) return h('div', { class: 'empty' }, h('strong', {}, 'No payments yet'), 'Payments you send or receive show up here.');
  return h('div', { class: 'list' }, pays.map((p) => {
    const out = p.payer === S.me.handle, other = out ? p.payee : p.payer;
    const amt = out ? fmt(p.amount, p.pay_asset) : fmt(p.receive, p.receive_asset);
    const how = p.route === 'direct' ? `Paid in ${p.pay_asset}` : `${p.pay_asset} converted to ${p.receive_asset}`;
    return h('div', { class: 'li' }, avatar(other), h('div', { class: 'main' }, h('div', { class: 't1' }, out ? `To ${nameOf(other)}` : `From ${nameOf(other)}`), h('div', { class: 't2' }, `${how}, ${when(p.at)}`)),
      h('div', { class: 'end num' }, h('div', { class: 't1', style: out ? '' : 'color:var(--good)' }, (out ? '−' : '+') + amt), p.state !== 'SETTLED' ? stateTag(p.state) : null));
  }));
}

// ---------------------------------------------------------------- pay
async function viewPay(q) {
  const [w, pays] = await Promise.all([api('GET', '/v1/wallet'), api('GET', '/v1/me/payments')]);
  const bal = Object.fromEntries(w.balances.map((b) => [b.asset, b]));
  const st = { listing: null, sel: null, payee: null, invoice: null, seq: 0 };
  const payeeIn = h('input', { id: 'p_payee', placeholder: 'name@omni', value: q.get('to') || '', autocomplete: 'off', spellcheck: 'false', 'aria-describedby': 'payee-card' });
  const amtIn = h('input', { id: 'p_amt', inputmode: 'decimal', placeholder: '0', value: q.get('amount') || '', 'aria-label': 'Amount', autocomplete: 'off' });
  const curSel = h('select', { id: 'p_cur', 'aria-label': 'Currency of the amount' }, fiatCodes().map((c) => h('option', { value: c, selected: c === (q.get('currency') || 'USD') }, c)));
  const card = h('div', { class: 'payee-card', id: 'payee-card' }), memoSlot = h('div'), out = h('p', { class: 'msg-line' });
  const right = h('section', { class: 'panel', 'aria-live': 'polite' });
  const recents = [...new Set(pays.payments.map((p) => (p.payer === S.me.handle ? p.payee : p.payer)))].filter((x) => x !== S.me.handle).slice(0, 5);
  for (const r of ['dana@omni', 'bob@omni']) if (S.cfg.demo && !recents.includes(r) && r !== S.me.handle && recents.length < 5) recents.push(r);

  const emptyRight = () => right.replaceChildren(h('div', { class: 'panel-head' }, h('h2', {}, 'Pay with')),
    h('div', { class: 'empty' }, h('strong', {}, 'Every way you can pay, side by side'), 'Choose who to pay and how much. You\'ll see the exact amount in each asset you hold, all at the same mid-market value.'));
  const drawCard = (info, err) => {
    if (err) return card.replaceChildren(h('span', { class: 'avatar', style: '--av:var(--ink-3)' }, '?'), h('div', { class: 'main' }, h('div', { class: 't1' }, 'No one found'), h('div', { class: 'small mute' }, err)));
    if (!info) return card.replaceChildren(h('div', { class: 'main mute small' }, 'Enter a handle to see who you\'re paying and what they accept.'));
    S.names[info.handle] = info.name || S.names[info.handle];
    card.replaceChildren(avatar(info.handle, 'lg'), h('div', { class: 'main' }, h('div', { class: 't1' }, nameOf(info.handle), ' ', h('span', { class: 'faint small' }, info.handle)),
      h('div', { class: 'small mute' }, `Receives ${info.settle_in}. Also takes these as they are:`),
      h('div', { class: 'accepts' }, info.accepts.filter((a) => a !== info.settle_in).map((a) => h('span', { class: 'tag' }, token(a, 'sm'), a)), info.accepts.length <= 1 ? h('span', { class: 'small faint' }, 'nothing else') : null)));
  };
  let lookT = null;
  const lookup = (now) => {
    clearTimeout(lookT); const handle = payeeIn.value.trim(); const my = ++st.seq;
    if (!handle) { st.payee = null; return drawCard(null); }
    lookT = setTimeout(async () => {
      try { const info = await rawApi('GET', '/v1/payees/' + encodeURIComponent(handle)); if (my === st.seq) { st.payee = info; drawCard(info); } }
      catch { if (my === st.seq) { st.payee = null; drawCard(null, `No OmniPay account is called ${handle}.`); } }
    }, now === true ? 0 : 280);
  };
  payeeIn.addEventListener('input', () => { lookup(); st.listing = null; emptyRight(); });
  amtIn.addEventListener('input', () => { st.listing = null; emptyRight(); });
  curSel.addEventListener('change', () => { st.listing = null; emptyRight(); });

  const find = async () => {
    const payee = payeeIn.value.trim(), amount = amtIn.value.trim().replace(/,/g, '');
    if (!payee) throw new Error('Enter who you\'re paying, like dana@omni.');
    if (!(Number(amount) > 0)) throw new Error('Enter an amount above zero.');
    const d = await api('POST', '/v1/pay/options', { payee, amount, currency: curSel.value });
    st.listing = { ...d, payee, amount, currency: curSel.value }; st.sel = d.default; drawOptions();
  };
  const optRow = (o) => {
    const who = nameOf(st.listing.payee), u = toUsd(o.amount, o.asset), held = bal[o.asset];
    const left = held ? Number(held.amount) - Number(o.amount) : null;
    const route = o.route === 'direct' ? `${who} receives it as ${asset(o.asset).kind === 'equity' ? 'shares' : o.asset}` : `Converted to ${fmt(o.receive, o.receive_asset)} for ${who}`;
    return h('button', { class: 'opt' + (st.sel === o.asset ? ' sel' : ''), role: 'radio', 'aria-checked': String(st.sel === o.asset), 'data-asset': o.asset, onclick: () => { st.sel = o.asset; drawOptions(); } },
      h('span', { class: 'opt-lead' }, h('span', { class: 'radio' }), token(o.asset)),
      h('span', { class: 'what' }, h('span', { class: 'name' }, assetName(o.asset), o.sponsored ? tag('Sponsored', 'sponsored') : null, simTag(o.asset), o.asset === st.listing.default ? tag('Suggested', 'cobalt') : null),
        h('span', { class: 'route', style: 'display:block' }, route, o.notice ? ' ' + o.notice : ''),
        left != null ? h('span', { class: 'route faint', style: 'display:block' }, `Leaves ${fmt(Math.max(0, left).toFixed(asset(o.asset).dp), o.asset)}`) : null),
      h('span', { class: 'amt num' }, h('b', {}, fmt(o.amount, o.asset)), u != null && o.asset !== 'USD' ? h('small', {}, '≈ ' + usd(u)) : null));
  };
  const drawOptions = () => {
    const L = st.listing, timer = h('span', { class: 'timer' });
    const list = L.options.length ? h('div', { class: 'options', role: 'radiogroup', 'aria-label': 'Pay with' }, L.options.map(optRow))
      : h('div', { class: 'empty' }, h('strong', {}, 'Nothing you hold can pay this'), 'Add money to your wallet, or ask the payee to accept another asset.');
    right.replaceChildren(
      h('div', { class: 'panel-head' }, h('div', {}, h('h2', {}, 'Pay with'), h('p', { class: 'small mute' }, `${fmt(L.amount, L.currency)} to ${nameOf(L.payee)}, in whichever asset suits you.`)),
        h('span', { class: 'tag' }, icon('lock', { width: '12', height: '12' }), 'Prices held ', timer)),
      list,
      h('div', { class: 'fee-strip' }, h('span', {}, 'OmniPay fee ', h('b', {}, usd(0))), h('span', {}, 'Conversion spread ', h('b', {}, 'none')), h('span', {}, 'Rows marked Sponsored are paid placements. They never change your price and are never picked for you.')),
      h('p', { class: 'sr' }, 'You pay the invoice\'s mid-market value in the asset you choose: no OmniPay fee, no conversion fee, no spread.'),
      L.options.length ? h('div', { style: 'margin-top:16px' }, h('button', { class: 'primary big wide', id: 'p_review', onclick: guard(out, review) }, 'Review payment')) : null);
    countdown(timer, L.expires_at, () => { right.replaceChildren(h('div', { class: 'empty' }, h('strong', {}, 'Prices expired'), 'Prices are held for a short time so they stay fair.', h('div', { style: 'margin-top:12px' }, h('button', { onclick: guard(out, find) }, icon('refresh'), 'Get fresh prices')))); st.listing = null; });
  };
  const review = async () => {
    const L = st.listing, o = L.options.find((x) => x.asset === st.sel);
    const qt = await api('POST', '/v1/pay/quote', { listing_id: L.listing_id, asset: st.sel });
    openReview(qt, o, L, st.invoice);
  };

  if (q.get('invoice')) {
    try {
      const inv = await api('GET', '/v1/invoices/' + encodeURIComponent(q.get('invoice')));
      st.invoice = inv; payeeIn.value = inv.payee; amtIn.value = inv.amount; curSel.value = inv.currency;
      [payeeIn, amtIn, curSel].forEach((e) => e.setAttribute('disabled', ''));
      memoSlot.replaceChildren(h('div', { class: 'stack s' },
        h('div', { class: 'row center' }, tag(inv.status === 'OPEN' ? 'Paying request ' + inv.id : `Request ${inv.id} is ${inv.status.toLowerCase()}`, inv.status === 'OPEN' ? 'cobalt' : 'warn'), h('span', { class: 'spacer' }), h('button', { class: 'ghost', onclick: () => go('pay') }, 'Pay without the request')),
        inv.memo ? h('div', { class: 'memo' }, h('small', {}, 'Note from the requester (shown as written, not checked by OmniPay)'), inv.memo) : null));
    } catch (e) { memoSlot.replaceChildren(h('p', { class: 'err' }, e.message)); }
  } else if (q.get('via')) {
    memoSlot.replaceChildren(h('div', { class: 'stack s' },
      h('div', { class: 'row center' }, tag(q.get('via') === 'upi' ? 'From a scanned UPI code' : 'From a scanned QR code', 'cobalt'), h('span', { class: 'spacer' }), h('button', { class: 'ghost', onclick: openScanner }, icon('scan'), 'Scan again')),
      q.get('note') ? h('div', { class: 'memo' }, h('small', {}, 'Note in the code (shown as written, not checked by OmniPay)'), q.get('note')) : null));
  }
  const findBtn = h('button', { class: 'accent big wide', id: 'p_find', onclick: guard(out, find) }, 'See ways to pay');
  const left = h('section', { class: 'panel stack' },
    h('div', { class: 'field' }, h('label', { for: 'p_payee' }, 'To'), h('div', { class: 'row center', style: 'flex-wrap:nowrap;gap:8px' }, payeeIn, st.invoice ? null : h('button', { class: 'icon', id: 'p_scan', 'aria-label': 'Scan a QR code', onclick: openScanner }, icon('scan')))),
    recents.length && !st.invoice ? h('div', { class: 'chips', 'aria-label': 'Recent' }, recents.map((r) => h('button', { class: 'chip', onclick: () => { payeeIn.value = r; lookup(); emptyRight(); } }, r))) : null,
    card, memoSlot,
    h('div', { class: 'field' }, h('span', {}, 'Amount'), h('div', { class: 'amount-field' }, amtIn, curSel), h('span', { class: 'hint' }, 'Enter what the request is worth. You\'ll choose what to pay it with next.')),
    findBtn, out);
  drawCard(null); emptyRight();
  if (payeeIn.value) lookup(true);
  if (st.invoice || (q.get('to') && q.get('amount'))) later(() => findBtn.click(), 30);
  return h('div', { class: 'grid-pay' }, left, right);
}
function routeMap(qt, payeeHandle) {
  const who = nameOf(payeeHandle);
  const you = h('div', { class: 'node' }, token(qt.pay_asset, 'lg'), h('b', {}, fmt(qt.amount, qt.pay_asset)), 'from you');
  const them = h('div', { class: 'node' }, avatar(payeeHandle, 'lg'), h('b', {}, fmt(qt.receive, qt.receive_asset)), `to ${who}`);
  if (qt.route === 'direct') return h('div', { class: 'route-map direct' }, you, h('div', { class: 'wire', 'aria-label': 'paid as is' }), them);
  return h('div', { class: 'route-map' }, you, h('div', { class: 'wire' }), h('div', { class: 'node' }, h('span', { class: 'hub' }, icon('convert')), h('b', {}, 'OmniPay'), 'converts at mid-market'), h('div', { class: 'wire' }), them);
}
function openReview(qt, o, L, invoice) {
  openSheet((b, close) => {
    const pin = h('input', { id: 'p_pin', class: 'pin-one', type: 'password', inputmode: 'numeric', maxlength: '8', autocomplete: 'off', 'aria-label': 'Payment PIN' });
    const out = h('p', { class: 'msg-line', role: 'alert' }), timer = h('span', { class: 'timer' }), map = routeMap(qt, L.payee);
    const u = qt.usd != null ? Number(qt.usd) : toUsd(qt.amount, qt.pay_asset);
    const payBtn = h('button', { class: 'primary big wide', id: 'p_confirm' }, icon('lock'), `Pay ${fmt(qt.amount, qt.pay_asset)}`);
    const pay = guard(out, async () => {
      if (!pin.value) throw new Error('Enter your payment PIN.');
      map.className += ' paying';
      try {
        const body = { quote_id: qt.quote_id, pin: pin.value };
        if (invoice && invoice.status === 'OPEN') body.invoice_id = invoice.id;
        const p = await api('POST', '/v1/pay', body);
        showResult(b, close, p, qt, L);
      } finally { map.className = map.className.replace(' paying', ''); pin.value = ''; }
    });
    payBtn.addEventListener('click', pay);
    pin.addEventListener('keydown', (e) => { if (e.key === 'Enter') pay(); });
    b.append(...[sheetHead('Review payment', close),
      h('div', { class: 'stack s', style: 'text-align:center' }, h('div', { class: 'big-amount num' }, fmt(qt.amount, qt.pay_asset)), h('p', { class: 'mute' }, `${u != null ? '≈ ' + usd(u) + ' ' : ''}to ${nameOf(L.payee)} (${L.payee})`)),
      map,
      h('dl', { class: 'kv' },
        h('dt', {}, `${nameOf(L.payee)} receives`), h('dd', { class: 'num' }, fmt(qt.receive, qt.receive_asset)),
        h('dt', {}, invoice ? 'Request value' : 'Value'), h('dd', { class: 'num' }, fmt(L.amount, L.currency)),
        h('dt', {}, 'OmniPay fee'), h('dd', { class: 'ok' }, usd(0)),
        h('dt', {}, 'Conversion spread'), h('dd', { class: 'ok' }, 'None'),
        h('dt', {}, 'Price held for'), h('dd', {}, timer)),
      o && o.sponsored ? h('div', { class: 'note sponsor small' }, `${o.asset} is a sponsored option. A promoter pays OmniPay when it's used; your price is exactly the same.`) : null,
      asset(qt.pay_asset).simulated ? h('div', { class: 'note warn small' }, `${assetName(qt.pay_asset)} is simulated in this version. No real ${KIND[asset(qt.pay_asset).kind].toLowerCase()} move.`) : null,
      h('label', { class: 'field' }, 'Payment PIN', pin), payBtn, out].filter(Boolean));
    countdown(timer, qt.expires_at, () => { payBtn.disabled = true; out.className = 'msg-line err'; out.textContent = 'This price expired. Close this and get fresh prices.'; });
    later(() => pin.focus && pin.focus(), 60);
  });
}
function showResult(b, close, p, qt, L) {
  const ok = p.state === 'SETTLED';
  const done = () => { close(); go('home'); };
  if (ok) toast(`Paid ${fmt(L.amount, L.currency)} to ${L.payee}`);
  b.replaceChildren(
    h('div', { class: ok ? 'done-mark' : 'done-mark', style: ok ? '' : 'background:var(--warn-soft);color:var(--warn)' }, icon(ok ? 'check' : 'alert', { 'stroke-width': '2.4' })),
    h('div', { class: 'stack s', style: 'text-align:center' }, h('h2', { id: 'sheet-title' }, ok ? `Paid ${fmt(qt.amount, qt.pay_asset)}` : `Payment ${p.state.toLowerCase()}`),
      h('p', { class: 'mute' }, ok ? `${nameOf(L.payee)} received ${fmt(qt.receive, qt.receive_asset)}.` : (p.reason || 'Your funds are held safely until the payment is confirmed.'))),
    h('dl', { class: 'kv' }, h('dt', {}, 'Payment'), h('dd', {}, p.payment_id), h('dt', {}, 'Route'), h('dd', {}, qt.route === 'direct' ? `Paid as ${qt.pay_asset}` : `${qt.pay_asset} to ${qt.receive_asset} at mid-market`),
      h('dt', {}, 'OmniPay fee'), h('dd', {}, usd(0)), p.invoice_paid ? [h('dt', {}, 'Request'), h('dd', {}, 'Marked paid')] : null, h('dt', {}, 'Time'), h('dd', {}, new Date().toLocaleString())),
    h('div', { class: 'grid-2' }, h('button', { onclick: () => { close(); go('pay?r=' + Date.now()); } }, 'Pay someone else'), h('button', { class: 'primary', id: 'p_done', onclick: done }, 'Done')));
}

// ---------------------------------------------------------------- wallet
async function viewWallet() {
  const w = await api('GET', '/v1/wallet'), pays = (await api('GET', '/v1/me/payments')).payments, fiat = S.cfg.paypal_currencies;
  let filter = 'all';
  const tableSlot = h('div', { class: 'table-wrap' }), seg = h('div', { class: 'seg', role: 'group', 'aria-label': 'Filter by kind' });
  const drawTable = () => {
    seg.replaceChildren(...[['all', 'All'], ['fiat', 'Cash'], ['crypto', 'Crypto'], ['equity', 'Shares']].map(([k, l]) => h('button', { 'aria-pressed': String(filter === k), onclick: () => { filter = k; drawTable(); } }, l)));
    const rows = w.balances.filter((b) => filter === 'all' || asset(b.asset).kind === filter);
    tableSlot.replaceChildren(rows.length ? h('table', {}, h('thead', {}, h('tr', {}, h('th', {}, 'Asset'), h('th', {}, 'Kind'), h('th', { style: 'text-align:right' }, 'Amount'), h('th', { style: 'text-align:right' }, 'Value (USD)'))),
      h('tbody', {}, rows.map((b) => h('tr', {}, h('td', {}, h('span', { class: 'row center', style: 'gap:10px;flex-wrap:nowrap' }, token(b.asset, 'sm'), h('span', {}, h('b', { style: 'font-weight:600' }, b.asset), ' ', h('span', { class: 'faint small' }, assetName(b.asset))))),
        h('td', {}, KIND[asset(b.asset).kind], ' ', simTag(b.asset)), h('td', { class: 'num', style: 'text-align:right' }, fmt(b.amount, b.asset)), h('td', { class: 'num', style: 'text-align:right' }, b.usd ? usd(b.usd) : '–')))))
      : h('div', { class: 'empty' }, h('strong', {}, 'No assets of this kind'), 'Payments you receive in this kind will show up here.'));
  };
  drawTable();

  const fout = h('p', { class: 'msg-line' }), fslot = h('div', { class: 'stack s' }), pout = h('p', { class: 'msg-line' });
  const fCur = h('select', { id: 'f_cur' }, fiat.map((c) => h('option', { value: c }, c))), fAmt = h('input', { id: 'f_amt', value: '50', inputmode: 'decimal' });
  const startFund = async () => {
    const o = await api('POST', '/v1/wallet/fund/orders', { currency: fCur.value, amount: fAmt.value });
    const capture = async () => {
      const r = await api('POST', '/v1/wallet/fund/capture', { order_id: o.order_id });
      fout.className = r.credited ? 'msg-line ok' : 'msg-line'; fout.textContent = r.credited ? `Added ${r.amount} ${r.currency} to your wallet.` : `PayPal says: ${r.state}`;
      if (r.credited) { toast(`Added ${fmt(r.amount, r.currency)}`); later(render, 1400); }
    };
    if (o.simulated) {
      fslot.replaceChildren(h('div', { class: 'note small' }, 'Simulated PayPal checkout. No real money moves. Approve it the way a buyer would in the PayPal window.'),
        h('button', { class: 'accent', onclick: guard(fout, async () => { await api('POST', '/v1/demo/paypal/approve', { paypal_order_id: o.paypal_order_id }); await capture(); }) }, 'Approve simulated checkout'));
    } else {
      await loadSdk(S.cfg.paypal_client_id, o.currency);
      fslot.replaceChildren(); window.paypal.Buttons({ createOrder: () => o.paypal_order_id, onApprove: () => capture(), onError: (e) => { fout.className = 'msg-line err'; fout.textContent = String(e); } }).render(fslot);
    }
  };
  const wCur = h('select', { id: 'w_cur' }, fiat.map((c) => h('option', { value: c }, c))), wAmt = h('input', { id: 'w_amt', value: '10', inputmode: 'decimal' }), wPin = h('input', { id: 'w_pin', type: 'password', inputmode: 'numeric', maxlength: '8' });
  const payout = async () => {
    if (!S.me.payout_email) throw new Error('Add the PayPal email you want the money sent to first.');
    const r = await api('POST', '/v1/wallet/payouts', { currency: wCur.value, amount: wAmt.value, pin: wPin.value });
    pout.className = 'msg-line ok'; pout.textContent = `Withdrawal ${r.state.toLowerCase()}.`; wPin.value = ''; toast('Withdrawal requested'); later(render, 1200);
  };
  const demoSettle = (p) => (S.cfg.demo && S.cfg.paypal_simulated && ['SUBMITTED', 'PENDING'].includes(p.state)
    ? h('button', { class: 'ghost small', onclick: guard(null, async () => { await api('POST', '/v1/demo/paypal/settle-payout', { payout_id: p.id }); render(); }) }, 'Simulate delivery') : null);
  const mail = h('input', { id: 'w_mail', type: 'email', placeholder: 'you@example.com', value: S.me.payout_email || '' }), mout = h('p', { class: 'msg-line' });
  const saveMail = guard(mout, async () => { await api('POST', '/v1/me/payout-email', { email: mail.value.trim() }); await refreshMe(); mout.className = 'msg-line ok'; mout.textContent = 'Saved.'; });

  return [
    h('section', { class: 'panel' }, h('div', { class: 'panel-head' }, h('div', {}, h('h2', {}, 'Balances'), h('p', { class: 'small mute' }, 'Everything you hold can pay anyone, in any currency they choose to receive.')), seg), tableSlot),
    h('div', { class: 'grid-2' },
      h('section', { class: 'panel stack' }, h('div', {}, h('h2', {}, 'Add money'), h('p', { class: 'small mute' }, 'Top up through PayPal. PayPal\'s own fees and rules apply and sit outside OmniPay\'s pricing. Payments between people stay inside OmniPay.')),
        h('div', { class: 'row' }, h('label', { class: 'field grow' }, 'Amount', fAmt), h('label', { class: 'field' }, 'Currency', fCur), h('button', { class: 'primary', id: 'f_go', onclick: guard(fout, startFund) }, 'Continue')), fslot, fout),
      h('section', { class: 'panel stack' }, h('div', {}, h('h2', {}, 'Withdraw to PayPal'), h('p', { class: 'small mute' }, 'Needs your password and PIN, because it sends money out of OmniPay.')),
        h('div', { class: 'row' }, h('label', { class: 'field grow' }, 'PayPal email', mail), h('button', { onclick: saveMail }, 'Save')), mout,
        h('div', { class: 'row' }, h('label', { class: 'field grow' }, 'Amount', wAmt), h('label', { class: 'field' }, 'Currency', wCur), h('label', { class: 'field', style: 'width:110px' }, 'PIN', wPin), h('button', { class: 'primary', id: 'w_go', onclick: guard(pout, payout) }, 'Withdraw')), pout,
        w.payouts.length ? h('div', { class: 'list' }, w.payouts.map((p) => h('div', { class: 'li' }, h('span', { class: 'token sm' }, icon('out', { width: '14', height: '14' })), h('div', { class: 'main' }, h('div', { class: 't1 num' }, fmt(p.amount, p.currency)), h('div', { class: 't2' }, when(p.created_at))), h('div', { class: 'end' }, stateTag(p.state), ' ', demoSettle(p))))) : null)),
    h('section', { class: 'panel' }, h('div', { class: 'panel-head' }, h('h2', {}, 'All payments')), activityList(pays)),
  ];
}
let sdkLoaded = null;
function loadSdk(clientId, currency) {
  if (sdkLoaded) return sdkLoaded;
  return (sdkLoaded = new Promise((res, rej) => { const s = document.createElement('script'); s.src = `https://www.paypal.com/sdk/js?client-id=${encodeURIComponent(clientId)}&currency=${encodeURIComponent(currency)}&intent=capture`; s.onload = res; s.onerror = () => rej(new Error('Could not load the PayPal checkout')); document.head.append(s); }));
}

// ---------------------------------------------------------------- receive
function qrSvg(text, label) {
  const q = window.OmniQR.encode(text), n = q.size; let d = '';
  for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) if (q.dark(x, y)) d += `M${x} ${y}h1v1h-1z`;
  return svg('svg', { viewBox: `-2 -2 ${n + 4} ${n + 4}`, role: 'img', 'aria-label': label, 'shape-rendering': 'crispEdges' }, svg('path', { d, fill: '#132034' }));
}
const origin = () => (location.origin && location.origin !== 'null' ? location.origin : '');
// Links in QR codes must work on the phone that scans them: when this page is on localhost, use the LAN address.
const isLocalHost = () => ['localhost', '127.0.0.1', '[::1]', ''].includes(location.hostname || '');
const shareBase = () => (S.cfg && S.cfg.lan_url && isLocalHost() ? S.cfg.lan_url : origin());
function myPayLink(amount, currency) {
  const q = new URLSearchParams({ to: S.me.handle });
  if (amount && Number(amount) > 0) { q.set('amount', amount); q.set('currency', currency || S.me.settle_in); }
  return `${shareBase()}/#/pay?${q.toString()}`;
}
async function shareLink(url, text) {
  try { if (navigator.share) { await navigator.share({ title: 'Pay me on OmniPay', text, url }); return; } } catch (e) { if (e && e.name === 'AbortError') return; }
  copy(url, 'Link');
}
function saveQrImage(text, title, sub) {                         // a PNG people can print or send: QR + who it pays
  const q = window.OmniQR.encode(text), n = q.size, W = 720, pad = 70, cell = Math.floor((W - pad * 2) / (n + 2)), size = cell * (n + 2);
  const c = document.createElement('canvas'); c.width = W; c.height = W + 150;
  const g = c.getContext('2d'); g.fillStyle = '#ffffff'; g.fillRect(0, 0, c.width, c.height);
  const x0 = (W - size) / 2 + cell, y0 = pad + cell; g.fillStyle = '#132034';
  for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) if (q.dark(x, y)) g.fillRect(x0 + x * cell, y0 + y * cell, cell, cell);
  g.textAlign = 'center'; g.font = '700 38px Adventor, Inter, sans-serif'; g.fillText(title, W / 2, pad + size + 62);
  g.fillStyle = '#56627A'; g.font = '400 24px Inter, sans-serif'; g.fillText(sub, W / 2, pad + size + 102);
  const a = document.createElement('a'); a.href = c.toDataURL('image/png'); a.download = `omnipay-${S.me.handle.replace(/[^\w.-]/g, '_')}.png`;
  document.body.append(a); a.click(); a.remove(); toast('QR code saved');
}
// The person's own code: unique to their handle, optionally for a set amount.
function myQrCard(name, opts) {
  const frame = h('div', { class: 'qr-frame big' }), caption = h('p', { class: 'small mute' }), amt = h('input', { id: 'my_amt', inputmode: 'decimal', placeholder: 'Any amount', autocomplete: 'off', 'aria-label': 'Amount for your code' });
  const cur = h('select', { id: 'my_cur', 'aria-label': 'Currency' }, fiatCodes().map((c) => h('option', { value: c, selected: c === S.me.settle_in }, c)));
  let link = '';
  const draw = () => {
    const a = amt.value.trim().replace(/,/g, ''), ok = Number(a) > 0;
    link = myPayLink(ok ? a : '', cur.value);
    frame.replaceChildren(qrSvg(link, ok ? `QR code to pay ${S.me.handle} ${fmt(a, cur.value)}` : `QR code to pay ${S.me.handle}`));
    caption.textContent = ok ? `Scanning this asks for ${fmt(a, cur.value)}. They can pay it with anything they hold.` : 'This code is yours alone. Anyone who scans it can pay you, in whatever they hold.';
  };
  let t = null; amt.addEventListener('input', () => { clearTimeout(t); t = setTimeout(draw, 200); }); cur.addEventListener('change', draw);
  draw();
  return h('section', { class: 'panel qr-card', id: 'my_qr' },
    h('div', { class: 'row center', style: 'justify-content:center;gap:12px' }, avatar(S.me.handle, 'lg'), h('div', { style: 'text-align:left' }, h('div', { class: 'handle-big' }, name || nameOf(S.me.handle)), h('div', { class: 'mute' }, S.me.handle))),
    frame, caption,
    h('div', { class: 'row center my-amount' }, h('div', { class: 'grow' }, amt), cur),
    h('div', { class: 'row center', style: 'justify-content:center' },
      h('button', { class: 'primary', onclick: () => shareLink(link, `Pay ${S.me.handle} on OmniPay`) }, icon('share'), 'Share'),
      h('button', { onclick: () => saveQrImage(link, name || nameOf(S.me.handle), `Pay ${S.me.handle} with anything you hold`) }, icon('image'), 'Save image'),
      h('button', { onclick: () => copy(link, 'Link') }, icon('copy'), 'Copy link')),
    opts && opts.scan ? h('button', { class: 'accent big wide', onclick: openScanner }, icon('scan'), 'Scan a code to pay') : null);
}
// Lets a laptop hand the app to a phone on the same Wi-Fi (demo mode only, where the server shares its LAN address).
function phoneCard(compact) {
  if (!S.cfg.lan_url) return null;
  const url = S.cfg.lan_url + '/#/home', secure = S.cfg.lan_url.startsWith('https');
  return h('section', { class: 'panel phone-card' + (compact ? ' compact' : '') },
    h('div', { class: 'qr-frame' }, qrSvg(url, 'QR code to open OmniPay on your phone')),
    h('div', { class: 'stack s' }, h('h3', {}, 'Open OmniPay on your phone'), h('p', { class: 'small mute' }, 'Point your phone camera at this code. Your phone needs to be on the same Wi-Fi as this computer.'),
      secure ? h('p', { class: 'small mute' }, 'The first time, your phone warns that the certificate is self-signed. Choose to continue; it\'s this demo\'s own certificate.')
        : h('p', { class: 'small mute' }, 'To use the camera scanner on a phone, start the server with OMNIPAY_HTTPS=1. Phones only allow the camera on secure pages.'),
      h('p', { class: 'small' }, h('span', { class: 'faint' }, 'Address: '), S.cfg.lan_url)));
}
async function viewProfile() {
  await refreshMe();
  let name = null; try { name = (await rawApi('GET', '/v1/payees/' + encodeURIComponent(S.me.handle))).name; } catch { /* no display name */ }
  if (name) S.names[S.me.handle] = name;
  const row = (ic, title, sub, run, id) => h('button', { class: 'li setting', id, onclick: run }, h('span', { class: 'token sm' }, icon(ic, { width: '15', height: '15' })), h('span', { class: 'main' }, h('span', { class: 't1', style: 'display:block' }, title), h('span', { class: 't2', style: 'display:block' }, sub)));
  const accepts = S.me.accepts.filter((a) => a !== S.me.settle_in);
  const settings = h('section', { class: 'panel' }, h('div', { class: 'panel-head' }, h('h2', {}, 'Account')),
    h('div', { class: 'list' },
      row('wallet', `Main currency: ${S.me.settle_in}`, accepts.length ? `Also accepts ${accepts.join(', ')}` : 'Everything else is converted for you at mid-market', () => go('receive')),
      row('qr', 'Requests', 'Ask for a set amount with its own QR code', () => go('receive')),
      row('out', 'PayPal payouts', S.me.payout_email ? `Sent to ${S.me.payout_email}` : 'Add the PayPal email for withdrawals', () => go('wallet')),
      row('spark', 'Assistant limits', 'What the assistant may spend for you', () => go('assistant')),
      row(isDark() ? 'sun' : 'moon', isDark() ? 'Light theme' : 'Dark theme', 'Switch how OmniPay looks', toggleTheme),
      row('logout', 'Log out', `Signed in as ${S.me.handle}, ${S.me.region}`, () => logout(), 'logout_profile')));
  return h('div', { class: 'grid-side profile' }, h('div', { class: 'stack l' }, myQrCard(name, { scan: true })), h('div', { class: 'stack l' }, settings, isLocalHost() ? phoneCard() : null));
}

// ---------------------------------------------------------------- scanner
function loadScript(src) {
  return new Promise((res, rej) => { const s = document.createElement('script'); s.src = src; s.onload = res; s.onerror = () => rej(new Error('Could not load the QR reader')); document.head.append(s); });
}
let detectorP = null;
function getDetector() {                                          // the browser's own reader when it has one, jsQR otherwise (iPhone, desktop Linux…)
  if (detectorP) return detectorP;
  return (detectorP = (async () => {
    try {
      if ('BarcodeDetector' in window && (await window.BarcodeDetector.getSupportedFormats()).includes('qr_code')) {
        const d = new window.BarcodeDetector({ formats: ['qr_code'] });
        return { name: 'native', detect: async (src) => { const r = await d.detect(src); return r[0] ? r[0].rawValue : null; } };
      }
    } catch { /* fall through to jsQR */ }
    if (!window.jsQR) await loadScript('/vendor/jsQR.js');
    const c = document.createElement('canvas'), g = c.getContext('2d', { willReadFrequently: true });
    return { name: 'jsqr', detect: async (src) => {
      const w = src.videoWidth || src.width, hgt = src.videoHeight || src.height; if (!w || !hgt) return null;
      const k = Math.min(1, 900 / Math.max(w, hgt)); c.width = Math.round(w * k); c.height = Math.round(hgt * k);
      g.drawImage(src, 0, 0, c.width, c.height);
      const r = window.jsQR(g.getImageData(0, 0, c.width, c.height).data, c.width, c.height, { inversionAttempts: 'attemptBoth' });
      return r ? r.data : null;
    } };
  })().catch((e) => { detectorP = null; throw e; }));
}
function openScanner() {
  let stream = null, timer = null, done = false;
  const stop = () => { done = true; clearInterval(timer); if (stream) stream.getTracks().forEach((t) => t.stop()); stream = null; };
  const { dlg, body } = openSheet((b, close) => {
    const video = h('video', { id: 'scan_video', playsinline: true, muted: true, autoplay: true, 'aria-label': 'Camera view' });
    const status = h('p', { class: 'scan-status', role: 'status' }, 'Starting the camera…');
    const view = h('div', { class: 'scan-view' }, video, h('div', { class: 'finder', 'aria-hidden': 'true' }, h('i'), h('i'), h('i'), h('i'), h('span', { class: 'laser' })));
    const file = h('input', { id: 'scan_file', type: 'file', accept: 'image/*', class: 'sr', 'aria-label': 'Choose a photo of a QR code' });
    const typed = h('input', { id: 'scan_text', placeholder: 'Paste a link, UPI code, handle or request ID', autocomplete: 'off', spellcheck: 'false' });
    const out = h('p', { class: 'msg-line', role: 'alert' });
    const found = async (text) => { if (done) return; stop(); if (navigator.vibrate) { try { navigator.vibrate(40); } catch { /* optional */ } } await handleScan(text, b, close); };
    file.addEventListener('change', guard(out, async () => {
      const f = file.files && file.files[0]; if (!f) return;
      status.textContent = 'Reading the photo…';
      const det = await getDetector(), bmp = await createImageBitmap(f), text = await det.detect(bmp);
      file.value = '';
      if (text) await found(text); else { status.textContent = 'No QR code found in that photo. Try a closer, sharper shot.'; }
    }));
    const submitTyped = guard(out, async () => { if (!typed.value.trim()) throw new Error('Paste or type a code first.'); await found(typed.value); });
    typed.addEventListener('keydown', (e) => { if (e.key === 'Enter') submitTyped(); });
    b.append(sheetHead('Scan to pay', close), view, status,
      h('div', { class: 'row', style: 'flex-wrap:nowrap' }, h('label', { class: 'btn grow', for: 'scan_file', style: 'flex:1' }, icon('image'), 'Upload a photo', file)),
      h('details', { class: 'scan-manual' }, h('summary', {}, icon('keyboard', { width: '16', height: '16' }), 'Enter a code instead'),
        h('div', { class: 'row', style: 'margin-top:10px;flex-wrap:nowrap' }, h('div', { class: 'grow' }, typed), h('button', { class: 'primary', id: 'scan_go', onclick: submitTyped }, 'Continue'))),
      h('p', { class: 'hint' }, 'Reads OmniPay codes and links, UPI codes (upi://pay), handles like dana@omni and request IDs. Links to other websites are never opened.'), out);
    (async () => {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        status.textContent = window.isSecureContext === false ? 'The camera only works on secure (https) pages. Upload a photo of the code instead.' : 'This browser can\'t use the camera here. Upload a photo of the code instead.';
        view.className += ' off'; return;
      }
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false });
        if (done) { stop(); return; }
        video.srcObject = stream; await video.play().catch(() => {});
        status.textContent = 'Point your camera at a QR code.';
        const det = await getDetector(); let busy = false;
        timer = setInterval(async () => {
          if (busy || done || video.readyState < 2) return; busy = true;
          try { const text = await det.detect(video); if (text) await found(text); } catch { /* keep trying */ } finally { busy = false; }
        }, 180);
      } catch (e) {
        view.className += ' off';
        status.textContent = e && e.name === 'NotAllowedError' ? 'Camera access is blocked. Allow it in your browser settings, or upload a photo of the code.'
          : e && e.name === 'NotFoundError' ? 'No camera found. Upload a photo of the code instead.' : 'The camera couldn\'t start. Upload a photo of the code instead.';
      }
    })();
  }, { cls: 'scanner' });
  // a late 'close' event from the previous sheet must not stop this scanner: only stop once the dialog is really closed
  const onClose = () => { if (dlg.open && dlg.firstChild === body) return; stop(); dlg.removeEventListener('close', onClose); };
  dlg.addEventListener('close', onClose);
  return body;
}
async function handleScan(raw, b, close) {
  const r = window.OmniScan.parse(raw, { origin: origin() });
  const result = (ic, title, msg, extra) => b.replaceChildren(sheetHead('Scan to pay', close),
    h('div', { class: 'done-mark', style: 'background:var(--warn-soft);color:var(--warn)' }, icon(ic, { 'stroke-width': '2' })),
    h('div', { class: 'stack s', style: 'text-align:center' }, h('h2', {}, title), h('p', { class: 'mute' }, msg)), ...(extra || []),
    h('div', { class: 'grid-2' }, h('button', { onclick: () => { close(); later(openScanner, 60); } }, icon('scan'), 'Scan another'), h('button', { class: 'primary', onclick: close }, 'Close')));
  if (r.kind === 'empty' || r.kind === 'unknown') {
    return result('alert', 'Not a payment code', r.reason || 'This QR code isn\'t something OmniPay can pay.', [h('pre', { class: 'code' }, String(raw).slice(0, 300))]);
  }
  if (r.invoice) { close(); go(window.OmniScan.payRoute(r, { via: 'scan' })); return; }
  if (r.to === S.me.handle) return result('user', 'That\'s your own code', 'Show it to someone else so they can pay you.');
  try { await rawApi('GET', '/v1/payees/' + encodeURIComponent(r.to)); }
  catch {
    if (r.kind === 'upi') {
      return result('alert', `${r.name || r.to} uses UPI`, 'Paying UPI IDs outside OmniPay needs a connection to the UPI network, which this version doesn\'t have yet. Nothing was paid.',
        [h('dl', { class: 'kv' }, h('dt', {}, 'UPI ID'), h('dd', {}, r.to), r.amount ? [h('dt', {}, 'Amount'), h('dd', {}, fmt(r.amount, r.currency))] : null, r.note ? [h('dt', {}, 'Note'), h('dd', {}, r.note)] : null)]);
    }
    return result('alert', 'No one found', `There's no OmniPay account called ${r.to}${r.foreign ? ` (this code came from ${r.host})` : ''}.`);
  }
  close(); toast(r.kind === 'upi' ? 'UPI code read' : 'QR code read');
  go(window.OmniScan.payRoute(r, { via: r.kind === 'upi' ? 'upi' : 'scan' }));
}
async function viewReceive() {
  await refreshMe();
  const invs = (await api('GET', '/v1/me/invoices')).invoices;
  const out = h('p', { class: 'msg-line' }), prev = h('div'), iout = h('p', { class: 'msg-line' });
  const payLink = myPayLink();
  const qrFrame = h('div', { class: 'qr-frame' }, qrSvg(payLink, `QR code to pay ${S.me.handle}`));
  const qrCaption = h('p', { class: 'small mute' }, 'Anyone on OmniPay can scan this and pay you in whatever they hold.');
  let shownLink = payLink;
  const backBtn = h('button', { class: 'ghost', hidden: true, onclick: () => { showQr(payLink, 'Anyone on OmniPay can scan this and pay you in whatever they hold.'); backBtn.setAttribute('hidden', ''); } }, 'Show my own code');
  const qrCard = h('section', { class: 'panel qr-card', id: 'receive_qr' }, avatar(S.me.handle, 'lg'), h('div', { class: 'handle-big' }, S.me.handle), qrFrame, qrCaption,
    h('div', { class: 'row center', style: 'justify-content:center' }, h('button', { onclick: () => copy(shownLink, 'Link') }, icon('copy'), 'Copy link'), h('button', { onclick: () => shareLink(shownLink, qrCaption.textContent) }, icon('share'), 'Share')), backBtn);
  const showQr = (link, caption) => { shownLink = link; qrFrame.replaceChildren(qrSvg(link, caption)); qrCaption.textContent = caption; if (link !== payLink) backBtn.removeAttribute('hidden'); };

  const iAmt = h('input', { id: 'i_amt', value: '25', inputmode: 'decimal' }), iCur = h('select', { id: 'i_cur' }, fiatCodes().map((c) => h('option', { value: c, selected: c === S.me.settle_in }, c))), iMemo = h('input', { id: 'i_memo', placeholder: 'Dinner on Friday', maxlength: '300' });
  const inv = async () => {
    const r = await api('POST', '/v1/invoices', { amount: iAmt.value, currency: iCur.value, memo: iMemo.value });
    iout.className = 'msg-line ok'; iout.textContent = `Request ${r.id} created. Its QR code is showing now.`;
    showQr(`${shareBase()}/#/pay?invoice=${r.id}`, `Scan to pay ${fmt(iAmt.value, iCur.value)} for request ${r.id}.`);
    toast('Request created'); drawReqs((await api('GET', '/v1/me/invoices')).invoices);
  };
  const reqSlot = h('div');
  const drawReqs = (invs) => reqSlot.replaceChildren(invs.length ? h('div', { class: 'list' }, invs.map((x) => h('div', { class: 'li' }, h('span', { class: 'token sm' }, icon('doc', { width: '14', height: '14' })),
    h('div', { class: 'main' }, h('div', { class: 't1 num' }, fmt(x.amount, x.currency)), h('div', { class: 't2' }, x.memo || x.id)),
    h('div', { class: 'end row center', style: 'gap:6px;flex-wrap:nowrap' }, stateTag(x.status), x.status === 'OPEN' ? h('button', { class: 'ghost icon', 'aria-label': 'Show QR for ' + x.id, onclick: () => showQr(`${shareBase()}/#/pay?invoice=${x.id}`, `Scan to pay ${fmt(x.amount, x.currency)} for request ${x.id}.`) }, icon('qr')) : null))))
    : h('div', { class: 'empty' }, h('strong', {}, 'No requests yet'), 'Create one above and share its QR code or link.'));
  drawReqs(invs);

  // what you accept
  const settle = h('select', { id: 'r_settle' }, fiatCodes().map((c) => h('option', { value: c, selected: c === S.me.settle_in }, `${c}, ${assetName(c)}`)));
  const tiles = (kind) => S.assets.filter((a) => a.kind === kind).map((a) => h('label', { class: 'toggle-tile' }, token(a.code, 'sm'),
    h('span', { class: 'main' }, a.code, h('small', {}, a.enabled ? (a.simulated ? 'Simulated' : assetName(a.code)) : 'Paused by OmniPay')),
    h('span', { class: 'switch' }, h('input', { type: 'checkbox', class: 'acc', value: a.code, checked: S.me.accepts.includes(a.code), disabled: a.code === S.me.settle_in || !a.enabled, 'aria-label': `Accept ${a.code}` }), h('span', {}))));
  const save = async () => {
    const accepts = [...document.querySelectorAll('.acc')].filter((c) => c.checked).map((c) => c.value);
    await api('PUT', '/v1/me/profile', { settle_in: settle.value, accepts: [...new Set([...accepts, settle.value])] });
    out.className = 'msg-line ok'; out.textContent = 'Saved.'; toast('Payment preferences saved'); await refreshMe();
  };
  const nlIn = h('textarea', { id: 'r_nl', placeholder: 'Pay me in euros, but I\'ll also take bitcoin and Apple shares' });
  const nl = async () => {
    const p = await api('POST', '/v1/agent/payee-setup', { text: nlIn.value });
    prev.replaceChildren(h('div', { class: 'note stack s' }, h('h3', {}, 'Here\'s what that would set'),
      h('p', {}, 'Receive ', h('b', {}, p.profile.settle_in), ', and also accept ', h('b', {}, p.profile.accepts.filter((a) => a !== p.profile.settle_in).join(', ') || 'nothing else'), ' as they are.'),
      p.adds.length ? h('p', { class: 'small' }, 'Adds ' + p.adds.join(', ')) : null, p.removes.length ? h('p', { class: 'small' }, 'Removes ' + p.removes.join(', ')) : null,
      ...p.warnings.map((w) => h('p', { class: 'small mute' }, w)),
      h('div', { class: 'row' }, h('button', { class: 'primary', onclick: guard(out, async () => { await api('POST', '/v1/agent/payee-setup/confirm', { id: p.pending_id }); prev.replaceChildren(); toast('Payment preferences saved'); render(); }) }, 'Save these preferences'), h('button', { class: 'ghost', onclick: () => prev.replaceChildren() }, 'Discard'))));
  };
  const accept = h('section', { class: 'panel stack' },
    h('div', {}, h('h2', {}, 'How you get paid'), h('p', { class: 'small mute' }, 'Payers can pay in anything they hold. If it\'s something you accept, it reaches you as it is. Otherwise OmniPay converts it into your main currency at mid-market, free. Shares only reach you if you switch them on, and are never sold for you.')),
    h('label', { class: 'field' }, 'Main currency', settle),
    ['fiat', 'crypto', 'equity'].map((k) => h('div', { class: 'accept-group' }, h('h3', {}, `Also accept ${KIND[k].toLowerCase()}`), h('div', { class: 'tiles' }, tiles(k)))),
    h('div', { class: 'row center' }, h('button', { class: 'primary', id: 'r_save', onclick: guard(out, save) }, 'Save preferences'), out));
  const words = h('section', { class: 'panel stack' }, h('div', {}, h('h2', {}, 'Or say it in your own words'), h('p', { class: 'small mute' }, 'We\'ll show you exactly what changes before anything is saved.')), nlIn,
    h('div', {}, h('button', { onclick: guard(out, nl) }, icon('spark'), 'Preview')), prev);
  const request = h('section', { class: 'panel stack' }, h('div', {}, h('h2', {}, 'Request money'), h('p', { class: 'small mute' }, 'Creates a request with its own QR code and link.')),
    h('div', { class: 'row' }, h('label', { class: 'field grow' }, 'Amount', iAmt), h('label', { class: 'field' }, 'Currency', iCur)), h('label', { class: 'field' }, 'Note (optional)', iMemo),
    h('div', {}, h('button', { class: 'primary', id: 'i_go', onclick: guard(iout, inv) }, icon('plus'), 'Create request')), iout, h('hr', { class: 'divider' }), h('h3', {}, 'Your requests'), reqSlot);
  return h('div', { class: 'grid-side receive' }, h('div', { class: 'stack l' }, request, accept, words), h('div', { class: 'stack l receive-qr' }, qrCard));
}

// ---------------------------------------------------------------- chat (assistant + advisor)
function chatBox(kind, onReply, suggestions) {
  const log = h('div', { class: 'chat', id: kind + '_log', 'aria-live': 'polite' });
  const inp = h('input', { id: kind === 'advisor' ? 'adv_in' : 'chat_in', placeholder: kind === 'advisor' ? 'How should I bid on USDC?' : 'Pay dana 25 dollars with bitcoin', autocomplete: 'off' });
  const out = h('p', { class: 'msg-line' }); let session = null;
  const intro = kind === 'advisor' ? 'Ask about the competition, or "what if I bid 40 on USDC". I only propose changes; you confirm them.'
    : 'Tell me who to pay and how much. I stay inside the limits you set, label sponsored options, never pick one because it\'s sponsored, and log everything I do.';
  log.append(h('div', { class: 'bubble' }, intro));
  const send = async (text) => {
    text = (text || inp.value).trim(); if (!text) return; inp.value = '';
    log.append(h('div', { class: 'bubble me' }, text));
    const wait = h('div', { class: 'bubble thinking' }, 'Working on it…'); log.append(wait); log.scrollTop = 1e9;
    try {
      const r = await api('POST', '/v1/agent/' + kind, { text, session }); session = r.session;
      wait.remove ? wait.remove() : null;
      log.append(h('div', { class: 'bubble' }, r.reply, h('span', { class: 'meta' }, `${r.mode === 'llm' ? 'AI model' : r.mode === 'off' ? 'Assistant paused' : 'Rule-based planner'}. Tools used: ${r.tool_calls.join(', ') || 'none'}.`)));
      if (onReply) onReply(r);
    } catch (e) { if (wait.remove) wait.remove(); throw e; } finally { log.scrollTop = 1e9; }
  };
  inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') guard(out, () => send())(); });
  return h('div', { class: 'chat-wrap ' + kind }, log,
    suggestions ? h('div', { class: 'suggest' }, suggestions.map((s) => h('button', { onclick: guard(out, () => send(s)) }, s))) : null,
    h('div', { class: 'composer' }, inp, h('button', { class: 'primary', id: kind + '_send', onclick: guard(out, () => send()) }, 'Send')), out);
}

// ---------------------------------------------------------------- assistant
async function viewAssistant() {
  const m = (await api('GET', '/v1/agent/mandate')).mandate || { enabled: false, per_payment_usd: '100', daily_usd: '300', allowed_assets: ['USD', 'EUR', 'USDC'], payee_allowlist: [], confirm_above_usd: '50' };
  const pendingSlot = h('div', { class: 'stack s' }), logSlot = h('div', { class: 'table-wrap' }), cout = h('p', { class: 'msg-line' });
  const drawPending = async () => {
    const pending = (await api('GET', '/v1/agent/pending')).pending.filter((p) => p.kind.startsWith('pay'));
    pendingSlot.replaceChildren(...pending.map((p) => {
      const pin = h('input', { type: 'password', class: 'cpin', inputmode: 'numeric', maxlength: '8', 'aria-label': 'Payment PIN', placeholder: 'PIN' });
      return h('div', { class: 'approval' }, h('div', { class: 'row center' }, icon('lock', { width: '18', height: '18' }), h('b', {}, 'Waiting for your approval')),
        h('p', {}, `About ${usd(p.usd)} to ${p.summary.payee}, paid with ${p.summary.pay_asset}.`),
        h('div', { class: 'row' }, h('div', { style: 'width:120px' }, pin), h('button', { class: 'primary', onclick: guard(cout, async () => { const r = await api('POST', '/v1/agent/confirm', { id: p.id, pin: pin.value }); toast('Payment ' + r.state.toLowerCase()); drawPending(); drawLog(); }) }, 'Approve and pay'),
          h('button', { class: 'ghost', onclick: guard(cout, async () => { await api('POST', '/v1/agent/reject', { id: p.id }); drawPending(); }) }, 'Decline')));
    }), cout);
  };
  const drawLog = async () => {
    const log = (await api('GET', '/v1/agent/log')).log;
    logSlot.replaceChildren(log.length ? h('table', {}, h('tbody', {}, log.slice(0, 40).map((l) => h('tr', {}, h('td', { class: 'small faint', style: 'white-space:nowrap' }, new Date(l.ts * 1000).toLocaleTimeString()),
      h('td', {}, tag(l.kind, l.kind === 'blocked' ? 'bad' : '')), h('td', { class: 'small' }, h('b', { style: 'font-weight:600' }, l.name), ' ', h('span', { class: 'mute' }, String(l.body).slice(0, 140)))))))
      : h('div', { class: 'empty' }, 'Nothing yet. Every step the assistant takes will be listed here.'));
  };
  const chat = chatBox('concierge', () => { drawPending(); drawLog(); }, S.cfg.demo ? ['Pay invoice inv-demo000001', 'Pay dana 25 dollars with bitcoin', 'What does dana accept?', 'What are my balances?'] : ['What are my balances?']);
  await Promise.all([drawPending(), drawLog()]);

  const mout = h('p', { class: 'msg-line' });
  const on = h('input', { id: 'm_on', type: 'checkbox', checked: m.enabled, 'aria-label': 'Let the assistant pay for me' });
  const num = (id, label, v, hint) => h('label', { class: 'field' }, label, h('input', { id, value: v, inputmode: 'decimal' }), hint ? h('span', { class: 'hint' }, hint) : null);
  const chosen = new Set(m.allowed_assets);
  const chips = h('div', { class: 'chips' });
  const drawChips = () => chips.replaceChildren(...S.assets.map((a) => h('button', { class: 'chip', 'aria-pressed': String(chosen.has(a.code)), onclick: () => { chosen.has(a.code) ? chosen.delete(a.code) : chosen.add(a.code); drawChips(); } }, a.code)));
  drawChips();
  const allow = h('input', { id: 'm_al', value: (m.payee_allowlist || []).join(', '), placeholder: 'bob@omni, dana@omni  (or * for anyone)' });
  const saveM = async () => {
    await api('PUT', '/v1/agent/mandate', { enabled: on.checked, per_payment_usd: $('#m_pp').value, daily_usd: $('#m_d').value, confirm_above_usd: $('#m_c').value,
      allowed_assets: [...chosen], payee_allowlist: allow.value.split(',').map((s) => s.trim()).filter(Boolean) });
    mout.className = 'msg-line ok'; mout.textContent = 'Limits saved.'; toast('Assistant limits saved');
  };
  const mandate = h('section', { class: 'panel stack' }, h('div', {}, h('h2', {}, 'Limits'), h('p', { class: 'small mute' }, 'The assistant can only spend inside these limits. OmniPay\'s server enforces them, whatever the assistant says.')),
    h('label', { class: 'row center', style: 'justify-content:space-between;flex-wrap:nowrap' }, h('span', {}, h('b', { style: 'font-weight:600' }, 'Let the assistant pay for me')), h('span', { class: 'switch' }, on, h('span', {}))),
    h('div', { class: 'grid-2', style: 'gap:12px' }, num('m_pp', 'Per payment (USD)', m.per_payment_usd), num('m_d', 'Per day (USD)', m.daily_usd)),
    num('m_c', 'Ask me first above (USD)', m.confirm_above_usd, 'Bigger payments wait for your PIN.'),
    h('div', { class: 'field' }, h('span', {}, 'Assets it may spend'), chips),
    h('label', { class: 'field' }, 'Who it may pay', allow),
    h('div', { class: 'row center' }, h('button', { class: 'primary', onclick: guard(mout, saveM) }, 'Save limits'), mout));
  return h('div', { class: 'grid-side' },
    h('div', { class: 'stack l' }, h('section', { class: 'panel stack' }, chat, pendingSlot), h('section', { class: 'panel' }, h('div', { class: 'panel-head' }, h('h2', {}, 'What the assistant did')), logSlot)),
    mandate);
}

// ---------------------------------------------------------------- promote
async function viewPromote() {
  const d = await api('GET', '/v1/promoter/dashboard'), codes = S.assets.map((a) => a.code);
  const out = h('p', { class: 'msg-line' }), sim = h('div'), land = h('div');
  const sel = (id, opts, v) => h('select', { id }, opts.map((c) => h('option', { value: c, selected: c === v }, c)));
  const bA = sel('b_a', codes, 'USDC'), bF = sel('b_f', S.cfg.paypal_currencies, 'USD'), bBps = h('input', { id: 'b_bps', value: '40', inputmode: 'numeric' }), bBud = h('input', { id: 'b_bud', value: '100', inputmode: 'decimal' });
  const drawLand = async () => {
    try {
      const l = await api('GET', '/v1/promoter/landscape?asset=' + encodeURIComponent(bA.value));
      land.replaceChildren(h('div', { class: 'stats' }, h('div', { class: 'stat' }, h('b', {}, String(l.bids_on_asset)), h('span', {}, `bids on ${l.asset}`)),
        h('div', { class: 'stat' }, h('b', {}, (l.top_bps || '0') + ' bps'), h('span', {}, 'top bid')), h('div', { class: 'stat' }, h('b', {}, String(l.slots)), h('span', {}, 'sponsored slots per payment')),
        h('div', { class: 'stat' }, h('b', {}, (l.reserve_bps.convert || '0') + ' bps'), h('span', {}, 'reserve price'))));
    } catch { land.replaceChildren(); }
  };
  bA.addEventListener('change', () => { sim.replaceChildren(); drawLand(); });
  const bid = async () => { await api('POST', '/v1/promoter/bids', { asset: bA.value, bps: bBps.value, fund_asset: bF.value, budget: bBud.value }); toast(`Bid placed on ${bA.value}`); render(); };
  const simulate = async () => {
    const s = await api('POST', '/v1/promoter/simulate', { asset: bA.value, bps: bBps.value, route: 'convert' });
    sim.replaceChildren(h('div', { class: 'note sponsor stack s' }, h('div', { class: 'rank-meter', 'aria-hidden': 'true' }, [1, 2, 3].map((i) => h('span', { class: s.shown && i === s.rank ? 'on' : '' }))),
      h('p', {}, s.shown ? `At ${s.bid_bps} bps you'd rank #${s.rank} and be shown, paying ${s.would_pay_bps} bps per completed payment.` : `At ${s.bid_bps} bps you'd rank #${s.rank} and would not be shown.`),
      h('p', { class: 'small mute' }, s.explain.charAt(0).toUpperCase() + s.explain.slice(1) + '.')));
  };
  const pend = (await api('GET', '/v1/agent/pending')).pending.filter((p) => p.kind.startsWith('advisor'));
  const proposal = (p) => h('div', { class: 'approval' }, h('b', {}, 'The advisor proposes a change'), h('pre', { class: 'code' }, JSON.stringify(p.summary, null, 2)),
    h('div', {}, h('button', { class: 'primary', onclick: guard(out, async () => { await api('POST', '/v1/agent/advisor/confirm', { id: p.id }); toast('Change applied'); render(); }) }, 'Apply this change')));
  const bids = d.stats.bids;
  const escrow = bids.reduce((s, b) => s + (toUsd(b.escrow, b.fund_asset) || 0), 0), billed = d.history.reduce((s, x) => s + (toUsd(x.billed, x.fund_asset) || 0), 0);
  await drawLand();
  return [
    h('section', { class: 'panel' }, h('p', { class: 'mute', style: 'max-width:72ch' }, 'Pay to have your asset shown as a labelled, sponsored option when people pay. You\'re only charged when a payment completes, from money you prepay. It never changes what the payer is charged and is never chosen for them.'),
      h('div', { class: 'stats', style: 'margin-top:18px' }, h('div', { class: 'stat' }, h('b', {}, String(bids.length)), h('span', {}, 'active bids')), h('div', { class: 'stat' }, h('b', { class: 'num' }, usd(escrow)), h('span', {}, 'prepaid, unspent')),
        h('div', { class: 'stat' }, h('b', {}, String(d.history.length)), h('span', {}, 'recent placements')), h('div', { class: 'stat' }, h('b', { class: 'num' }, usd(billed)), h('span', {}, 'billed')))),
    h('div', { class: 'grid-2' },
      h('section', { class: 'panel stack' }, h('h2', {}, 'New bid'),
        h('div', { class: 'grid-2', style: 'gap:12px' }, h('label', { class: 'field' }, 'Asset to promote', bA), h('label', { class: 'field' }, 'Bid (basis points)', bBps, h('span', { class: 'hint' }, '100 bps = 1% of each completed payment.')),
          h('label', { class: 'field' }, 'Prepay with', bF), h('label', { class: 'field' }, 'Budget', bBud)),
        land, sim, h('div', { class: 'row' }, h('button', { onclick: guard(out, simulate) }, 'Preview my rank'), h('button', { class: 'primary', onclick: guard(out, bid) }, 'Place bid')), out),
      h('section', { class: 'panel stack' }, h('h2', {}, 'Your bids'),
        bids.length ? h('div', { class: 'list' }, bids.map((b) => h('div', { class: 'li' }, token(b.asset), h('div', { class: 'main' }, h('div', { class: 't1' }, `${b.asset} at ${b.bps} bps`), h('div', { class: 't2 num' }, `${fmt(b.escrow, b.fund_asset)} prepaid`)),
          h('button', { class: 'ghost danger', onclick: guard(out, async () => { await api('DELETE', '/v1/promoter/bids/' + b.asset); toast('Bid withdrawn'); render(); }) }, 'Withdraw'))))
          : h('div', { class: 'empty' }, h('strong', {}, 'No bids yet'), 'Place one to start appearing as a sponsored option.'),
        h('hr', { class: 'divider' }), h('h3', {}, 'Recent placements'),
        d.history.length ? h('div', { class: 'table-wrap' }, h('table', {}, h('tbody', {}, d.history.map((x) => h('tr', {}, h('td', {}, x.asset), h('td', {}, stateTag(x.state)), h('td', { class: 'num' }, x.price_bps + ' bps'), h('td', { class: 'num' }, fmt(x.billed, x.fund_asset)))))))
          : h('p', { class: 'small mute' }, 'None yet.'))),
    h('section', { class: 'panel stack' }, h('div', {}, h('h2', {}, 'Bid advisor'), h('p', { class: 'small mute' }, d.rules)), chatBox('advisor', () => later(render, 400), ['What if I bid 50 on USDC?', 'How competitive is ETH?']), ...pend.map(proposal)),
  ];
}

// ---------------------------------------------------------------- admin
async function viewAdmin() {
  const o = await api('GET', '/v1/admin/overview'), audit = (await api('GET', '/v1/admin/audit?limit=30')).audit;
  const out = h('p', { class: 'msg-line' });
  const sw = async (scope, key, enabled, reason) => { await api('POST', '/v1/admin/switch', { scope, key, enabled, reason: reason || 'admin console' }); toast(`${scope}${key !== '*' ? ' ' + key : ''} turned ${enabled ? 'on' : 'off'}`); render(); };
  const swOn = (scope, key) => { const s = o.switches.find((x) => x.scope === scope && x.key === key); return s ? s.enabled : true; };
  const act = (label, fn, cls) => h('button', { class: cls || '', onclick: guard(out, async () => { const r = await fn(); out.className = 'msg-line ok'; out.textContent = 'Done. ' + JSON.stringify(r).slice(0, 220); later(render, 900); }) }, label);
  const flag = (scope, title, sub) => h('label', { class: 'toggle-tile' }, h('span', { class: 'main' }, title, h('small', {}, sub)),
    h('span', { class: 'switch' }, h('input', { type: 'checkbox', checked: swOn(scope, '*'), 'aria-label': title, onchange: guard(null, () => sw(scope, '*', !swOn(scope, '*'))) }), h('span', {})));
  const ledgerOk = !o.ledger_problems.length;
  const sc = h('select', { id: 's_sc' }, ['global', 'asset', 'kind', 'region', 'paypal_in', 'paypal_out', 'agents'].map((c) => h('option', {}, c))), sk = h('input', { id: 's_k', value: '*' }), sr = h('input', { id: 's_r', placeholder: 'Why' });
  const tb = Object.entries(o.trial_balance);
  return [
    h('div', { class: 'stats panel' },
      h('div', { class: 'stat' }, h('b', { class: ledgerOk ? 'ok' : 'err' }, ledgerOk ? 'Balanced' : 'Problems'), h('span', {}, 'Ledger balanced')),
      h('div', { class: 'stat' }, h('b', {}, String(o.breaks.length)), h('span', {}, 'open reconciliation breaks')),
      h('div', { class: 'stat' }, h('b', {}, String(o.held_payments.length)), h('span', {}, 'held payments')),
      h('div', { class: 'stat' }, h('b', {}, String(o.stuck_payouts.length)), h('span', {}, 'open payouts'))),
    h('div', { class: 'grid-2' },
      h('section', { class: 'panel stack' }, h('div', {}, h('h2', {}, 'Kill switches'), h('p', { class: 'small mute' }, 'Turning one off stops that part of OmniPay immediately. Every change is logged.')),
        h('div', { class: 'stack s' }, flag('global', 'All payments', 'Master switch'), flag('paypal_in', 'PayPal top-ups', 'Money coming in'), flag('paypal_out', 'PayPal withdrawals', 'Money going out'), flag('agents', 'Assistants', 'Concierge, advisor and setup')),
        h('h3', {}, 'Assets'), h('div', { class: 'tiles' }, o.assets.map((a) => h('label', { class: 'toggle-tile' }, token(a.code, 'sm'), h('span', { class: 'main' }, a.code, h('small', {}, KIND[a.kind] + (a.simulated ? ', simulated' : ''))),
          h('span', { class: 'switch' }, h('input', { type: 'checkbox', checked: a.enabled, 'aria-label': `${a.code} enabled`, onchange: guard(null, () => sw('asset', a.code, !a.enabled)) }), h('span', {}))))),
        h('details', {}, h('summary', { class: 'small mute', style: 'cursor:pointer' }, 'Other switch (kind, region…)'),
          h('div', { class: 'row', style: 'margin-top:10px' }, h('label', { class: 'field' }, 'Scope', sc), h('label', { class: 'field grow' }, 'Key', sk), h('label', { class: 'field grow' }, 'Reason', sr), h('button', { class: 'danger', onclick: guard(out, () => sw(sc.value, sk.value, false, sr.value)) }, 'Switch off'))),
        o.switches.length ? h('div', { class: 'list' }, o.switches.map((s) => h('div', { class: 'li' }, h('div', { class: 'main' }, h('div', { class: 't1' }, `${s.scope}: ${s.key}`), h('div', { class: 't2' }, s.reason || '')), tag(s.enabled ? 'On' : 'Off', s.enabled ? 'good' : 'bad'),
          h('button', { class: 'ghost', onclick: guard(out, () => sw(s.scope, s.key, !s.enabled)) }, s.enabled ? 'Turn off' : 'Turn on')))) : null),
      h('section', { class: 'panel stack' }, h('div', {}, h('h2', {}, 'Reconciliation and integrity'), h('p', { class: 'small mute' }, 'Unknown outcomes stay on hold until someone checks the rail.')),
        h('div', { class: 'row' }, act('Run reconciliation', () => api('POST', '/v1/admin/reconcile', {}), 'primary'), act('Run background jobs now', () => api('POST', '/v1/admin/jobs', {}))), out,
        h('div', { class: 'row center' }, o.breaks.length ? tag(o.breaks.length + ' open break(s)', 'bad') : tag('No open breaks', 'good'), ledgerOk ? tag('Ledger balanced', 'good') : tag('Ledger problems', 'bad')),
        o.breaks.length ? h('div', { class: 'list' }, o.breaks.map((b) => h('div', { class: 'li' }, h('div', { class: 'main' }, h('div', { class: 't1' }, b.kind), h('div', { class: 't2' }, `${b.ref}: ${b.detail}`))))) : null,
        h('h3', {}, 'Held payments'), o.held_payments.length ? h('div', { class: 'list' }, o.held_payments.map((id) => h('div', { class: 'li' }, h('div', { class: 'main t1' }, id),
          act('Executed', () => api('POST', `/v1/admin/payments/${id}/resolve`, { executed: true })), act('Refund', () => api('POST', `/v1/admin/payments/${id}/resolve`, { executed: false }), 'danger')))) : h('p', { class: 'small mute' }, 'None.'),
        h('h3', {}, 'Open payouts'), o.stuck_payouts.length ? h('div', { class: 'list' }, o.stuck_payouts.map((p) => h('div', { class: 'li' }, h('div', { class: 'main' }, h('div', { class: 't1 num' }, fmt(p.amount, p.currency)), h('div', { class: 't2' }, `${p.id}, ${p.state.toLowerCase()}`)),
          p.state === 'UNKNOWN' ? [act('Delivered', () => api('POST', `/v1/admin/payouts/${p.id}/resolve`, { delivered: true })), act('Refund', () => api('POST', `/v1/admin/payouts/${p.id}/resolve`, { delivered: false }), 'danger')] : stateTag(p.state)))) : h('p', { class: 'small mute' }, 'None.'),
        h('h3', {}, 'Trial balance'), h('p', { class: 'small mute' }, 'Every asset must net to zero across all accounts.'),
        h('div', { class: 'chips' }, tb.map(([k, v]) => tag(`${k} ${Number(v) === 0 ? '0' : v}`, Number(v) === 0 ? 'good' : 'bad'))),
        h('h3', {}, 'Promoter revenue'), Object.keys(o.revenue).length ? h('div', { class: 'chips' }, Object.entries(o.revenue).map(([k, v]) => tag(fmt(v, k), 'cobalt'))) : h('p', { class: 'small mute' }, 'None yet.'),
        h('p', { class: 'small mute' }, `Blocked regions: ${o.policy.blocked.join(', ')}. Country asset lists: ${Object.entries(o.policy.allowed).map(([k, v]) => `${k} ${v.join('/')}`).join('; ') || 'none'}.`))),
    h('section', { class: 'panel' }, h('div', { class: 'panel-head' }, h('h2', {}, 'Audit log')), h('div', { class: 'table-wrap' }, h('table', {}, h('thead', {}, h('tr', {}, h('th', {}, 'When'), h('th', {}, 'Who'), h('th', {}, 'What'), h('th', {}, 'Target'), h('th', {}, 'Detail'))),
      h('tbody', {}, audit.map((a) => h('tr', {}, h('td', { class: 'small faint', style: 'white-space:nowrap' }, new Date(a.ts * 1000).toLocaleString()), h('td', { class: 'small' }, a.actor), h('td', { class: 'small' }, a.action), h('td', { class: 'small' }, a.target), h('td', { class: 'small mute' }, String(a.detail).slice(0, 100)))))))),
  ];
}

// ---------------------------------------------------------------- command palette
function openPalette() {
  openSheet((b, close) => {
    const inp = h('input', { id: 'pal_in', placeholder: 'Type a page, a handle, or a request ID', autocomplete: 'off', 'aria-label': 'Search' });
    const list = h('ul', { role: 'listbox' }); let idx = 0, items = [];
    const base = () => {
      const nav = NAV.filter(([k]) => k !== 'admin' || S.me.admin).map(([k, l, ic]) => ({ label: `Go to ${l}`, ic, run: () => go(k) }));
      return [{ label: 'Scan a QR code', ic: 'scan', run: openScanner }, { label: 'Show my QR code', ic: 'qr', run: () => go('profile') }, { label: 'Pay someone', ic: 'send', run: () => go('pay') }, { label: 'Request money', ic: 'qr', run: () => go('receive') }, { label: 'Add money', ic: 'add', run: () => go('wallet') },
        ...nav, { label: isDark() ? 'Switch to light theme' : 'Switch to dark theme', ic: isDark() ? 'sun' : 'moon', run: toggleTheme }, { label: 'Log out', ic: 'logout', run: () => logout() }];
    };
    const draw = () => {
      const qv = inp.value.trim().toLowerCase();
      items = base().filter((x) => !qv || x.label.toLowerCase().includes(qv));
      if (/^inv-[a-z0-9]{6,20}$/.test(qv)) items.unshift({ label: `Pay request ${qv}`, ic: 'doc', run: () => go('pay?invoice=' + qv) });
      else if (/^[\w.-]+@[\w.-]+$/.test(qv)) items.unshift({ label: `Pay ${qv}`, ic: 'send', run: () => go('pay?to=' + encodeURIComponent(qv)) });
      idx = Math.min(idx, Math.max(0, items.length - 1));
      list.replaceChildren(...items.map((x, i) => h('li', {}, h('button', { role: 'option', 'aria-selected': String(i === idx), onclick: () => { close(); x.run(); } }, icon(x.ic), x.label))));
      if (!items.length) list.replaceChildren(h('li', { class: 'empty' }, 'Nothing matches. Try a handle like dana@omni.'));
    };
    inp.addEventListener('input', () => { idx = 0; draw(); });
    inp.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown') { idx = Math.min(items.length - 1, idx + 1); draw(); e.preventDefault(); }
      if (e.key === 'ArrowUp') { idx = Math.max(0, idx - 1); draw(); e.preventDefault(); }
      if (e.key === 'Enter' && items[idx]) { close(); items[idx].run(); }
    });
    b.append(inp, list); draw(); later(() => inp.focus && inp.focus(), 30);
  }, { cls: 'palette' });
}

// dialog: close when the backdrop is clicked
(() => { const d = $('#sheet'); if (d) d.addEventListener('click', (e) => { if (e.target === d) { try { d.close(); } catch { /* closed */ } } }); })();
boot().catch((e) => { $('#app').replaceChildren(h('div', { class: 'content' }, h('div', { class: 'panel' }, h('h2', {}, 'OmniPay can\'t reach its server'), h('p', { class: 'mute' }, e.message)))); });
