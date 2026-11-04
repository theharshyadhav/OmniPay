// Headless UI smoke test: minimal DOM shim + real server. Verifies app.js runs end to end and renders every view.
// It is NOT a visual/browser test (no layout, no CSS, no real PayPal SDK). For the real-browser pass see tests/ui_browser.js.
const BASE = process.env.BASE || 'http://127.0.0.1:8123';
const reg = new Map();
class Node_ {
  constructor(tag) { this.tag = tag; this.kids = []; this.attrs = {}; this.listeners = {}; this.className = ''; this._v = ''; this.checked = false; this.disabled = false; this.parent = null; this._text = ''; this.open = false; }
  get nodeType() { return 1; }
  get tagName() { return this.tag.toUpperCase(); }
  get value() {                                   // like a browser: a <select> reports its selected (else first) <option>
    if (this.tag === 'select') { const os = this.all((n) => n.tag === 'option'); const sel = os.find((o) => 'selected' in o.attrs) || os[0]; return this._v || (sel ? (sel.attrs.value ?? sel.textContent) : ''); }
    return this._v;
  }
  set value(v) { this._v = v; }
  setAttribute(k, v) { this.attrs[k] = v; if (k === 'id') reg.set(v, this); if (k === 'checked') this.checked = true; if (k === 'disabled') this.disabled = true; if (k === 'value' && this.tag !== 'option') this._v = v; }
  removeAttribute(k) { delete this.attrs[k]; }
  append(...k) { for (const c of k) { if (c && typeof c === 'object' && 'parent' in c) { if (c.parent && c.parent !== this) c.parent.kids = c.parent.kids.filter((x) => x !== c); c.parent = this; } this.kids.push(c); } }
  replaceChildren(...k) { this.kids = []; this.append(...k); }
  remove() { if (this.parent) this.parent.kids = this.parent.kids.filter((x) => x !== this); }
  addEventListener(e, f) { (this.listeners[e] ||= []).push(f); }
  removeEventListener(e, f) { this.listeners[e] = (this.listeners[e] || []).filter((x) => x !== f); }
  get firstChild() { return this.kids[0] || null; }
  async fire(e, ev) { for (const f of this.listeners[e] || []) await f({ target: this, currentTarget: this, preventDefault() {}, ...(ev || {}) }); }
  async click() { await this.fire('click'); }
  focus() {}
  get textContent() { return this._text + this.kids.map((k) => (k == null ? '' : k.textContent ?? k.data ?? String(k))).join(' '); }
  set textContent(t) { this._text = t; this.kids = []; }
  closest(sel) { let n = this; while (n) { if (n.className.split(' ').includes(sel.slice(1))) return n; n = n.parent; } return null; }
  all(pred, out = []) { if (pred(this)) out.push(this); for (const k of this.kids) if (k && k.all) k.all(pred, out); return out; }
  querySelector(sel) { return this.all((n) => sel[0] === '.' ? n.className.split(' ').includes(sel.slice(1)) : n.tag === sel)[0] || null; }
  querySelectorAll(sel) { return this.all((n) => n.className.split(' ').includes(sel.slice(1))); }
  showModal() { this.open = true; } close() { if (this.open) { this.open = false; this.fire('close'); } }
  get returnValue() { return 'cancel'; }
}
const app = new Node_('div'), sheet = new Node_('dialog'), toasts = new Node_('div'), html = new Node_('html');
const els = { '#app': app, '#sheet': sheet, '#toasts': toasts };
const winL = {};
global.window = { addEventListener: (e, f) => { (winL[e] ||= []).push(f); }, matchMedia: () => ({ matches: false }), OmniQR: require('../web/qr.js'), OmniScan: require('../web/scan.js') };
global.document = {
  createElement: (t) => new Node_(t), createElementNS: (_, t) => new Node_(t), createTextNode: (t) => ({ data: String(t), nodeType: 3, textContent: String(t) }), head: new Node_('head'), documentElement: html,
  querySelector: (s) => els[s] || (s[0] === '#' ? reg.get(s.slice(1)) : app.querySelector(s)) || null,
  querySelectorAll: (s) => app.querySelectorAll(s),
};
let hash = '';
global.location = { get hash() { return hash; }, set hash(v) { hash = v.startsWith('#') ? v : '#' + v; (winL.hashchange || []).forEach((f) => f()); }, origin: BASE };
const mem = () => { const store = {}; return { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } }; };
global.sessionStorage = mem(); global.localStorage = mem();
const realFetch = global.fetch; global.fetch = (p, o) => realFetch(BASE + p, o);
const fs = require('fs');
const src = fs.readFileSync(__dirname + '/../web/app.js', 'utf8');
if (/innerHTML|outerHTML|insertAdjacentHTML|document\.write|eval\(/.test(src.replace(/^\s*\/\/.*$/mg, ''))) { console.error('FAIL: unsafe DOM API in app.js'); process.exit(1); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let failures = 0; const ok = (c, m) => { if (!c) { failures++; console.error('FAIL', m); } else console.log('ok  ', m); };
const text = () => app.textContent + ' ' + sheet.textContent;
const byId = (id) => reg.get(id);
const btn = (label, root = app) => root.all((n) => n.tag === 'button' && n.textContent.trim() === label)[0];
async function until(pred, what, ms = 5000) { const t0 = Date.now(); while (Date.now() - t0 < ms) { if (pred()) return true; await sleep(40); } ok(false, 'timeout: ' + what); return false; }
const go = (p) => { reg.clear(); location.hash = '/' + p; };
(async () => {
  await fetch('/v1/demo/reset', { method: 'POST', body: '{}' });
  (0, eval)(src);                                                             // defines S, h, render... and calls boot()
  await until(() => text().includes('Welcome to OmniPay'), 'auth view');
  ok(text().includes('no fee, no conversion fee and no spread') && text().includes('outside OmniPay'), 'fee statement and third-party-cost disclaimer on sign-in');
  ok(text().includes('Pay with whatever you hold') && text().includes('BTC'), 'sign-in story prices one request in many assets');
  byId('a_h').value = 'alice@omni'; byId('a_p').value = 'demo-password-1';
  await byId('a_go').click();
  await until(() => (byId('nav') || { textContent: '' }).textContent.includes('Assistant'), 'logged in');
  ok(byId('who').textContent.includes('alice@omni') && !byId('nav').textContent.includes('Admin'), 'non-admin has no Admin tab');
  ok(text().includes('Simulated PayPal') && text().includes('Demo data'), 'environment tags show Simulated PayPal + Demo data');
  ok(byId('foot').textContent.includes('outside OmniPay'), 'footer carries the fee statement');
  await until(() => text().includes('Total balance'), 'home view');
  // pay flow
  go('pay?to=dana@omni&amount=100&currency=USD');
  await until(() => app.all((n) => (n.className || '').startsWith('opt')).length >= 3, 'options listed');
  ok(text().includes('Sponsored') && text().includes('Simulated'), 'options carry Sponsored and Simulated labels');
  ok(text().includes('no OmniPay fee, no conversion fee, no spread') && text().includes('OmniPay fee'), 'zero-fee statement next to the options');
  const sel = app.all((n) => n.className === 'opt sel');
  ok(sel.length === 1 && !sel[0].textContent.includes('Sponsored'), 'exactly one preselected row and it is not sponsored');
  await app.all((n) => n.attrs && n.attrs['data-asset'] === 'BTC')[0].click();
  ok(app.all((n) => n.className === 'opt sel')[0].attrs['data-asset'] === 'BTC', 'payer can choose BTC');
  await byId('p_review').click();
  await until(() => sheet.open && byId('p_pin'), 'review sheet with PIN');
  ok(sheet.textContent.includes('Review payment') && sheet.textContent.includes('0.001 BTC') && !/\bnull\b/.test(sheet.textContent), 'review shows the locked BTC amount, no stray text');
  byId('p_pin').value = '0000'; await byId('p_confirm').click();
  await until(() => sheet.textContent.includes('PIN didn'), 'wrong PIN is explained');
  byId('p_pin').value = '1234'; await byId('p_confirm').click();
  await until(() => sheet.textContent.includes('Paid 0.001 BTC'), 'payment receipt');
  await byId('p_done').click();
  for (const v of ['wallet', 'receive', 'promote', 'assistant']) {
    go(v); await until(() => !app.all((n) => n.className === 'skeleton').length, v + ' view loaded');
    ok(!/undefined|\[object|NaN/.test(app.textContent), v + ' view renders without errors');
  }
  ok(text().includes('Limits') && text().includes('What the assistant did'), 'assistant view shows chat, limits and log');
  // assistant: run the prompt-injection invoice through the real concierge
  byId('chat_in').value = 'pay invoice inv-demo000001'; await byId('concierge_send').click();
  await until(() => text().includes('Paid 120 USD to dana@omni'), 'assistant paid the real invoice, ignoring the memo');
  ok(!text().includes('attacker@omni 5000 USD with'), 'memo not acted on');
  // the request is closed now
  go('pay?invoice=inv-demo000001'); await until(() => text().includes('is paid'), 'paid request is shown as paid');
  // profile: the personal QR code, and the scanner's typed-code path
  go('profile'); await until(() => byId('my_qr'), 'profile view');
  ok(text().includes('alice@omni') && app.all((n) => n.tag === 'path' && (n.attrs.d || '').length > 200).length >= 1, 'profile shows a personal QR code');
  ok(text().includes('This code is yours alone'), 'personal QR explains what it does');
  byId('my_amt').value = '42'; await byId('my_amt').fire('input'); await sleep(300);
  ok(text().includes('asks for $42.00'), 'personal QR can ask for a set amount');
  await byId('scan_top').click(); await until(() => byId('scan_text'), 'scanner opened');
  ok(sheet.textContent.includes('Upload a photo') && sheet.textContent.includes('Links to other websites are never opened'), 'scanner offers photo upload and states the link rule');
  byId('scan_text').value = 'https://evil.example/login'; await byId('scan_go').click();
  await until(() => sheet.textContent.includes('Not a payment code'), 'ordinary links are refused');
  sheet.close(); await byId('scan_top').click(); await until(() => byId('scan_text'), 'scanner reopened');
  byId('scan_text').value = 'upi://pay?pa=ravi@omni&pn=Chai&am=250&cu=INR&tn=Masala%20chai'; await byId('scan_go').click();
  await until(() => location.hash.includes('via=upi') && text().includes('From a scanned UPI code'), 'UPI code opens a pre-filled payment');
  ok(text().includes('Masala chai') && reg.get('p_amt').value === '250', 'scanned amount and note are shown');
  // receive: create a request with its QR code
  go('receive'); await until(() => byId('i_go'), 'receive view');
  await byId('i_go').click(); await until(() => text().includes('Request inv-'), 'request created');
  if (!text().includes('Request inv-')) console.error('DEBUG', app.all((n) => (n.className || '').includes('msg-line')).map((n) => n.textContent).join(' | '));
  ok(app.all((n) => n.tag === 'path' && (n.attrs.d || '').length > 200).length >= 1, 'QR code drawn');
  // wallet: simulated funding end to end
  go('wallet'); await until(() => byId('f_go'), 'wallet');
  await byId('f_go').click();
  await until(() => btn('Approve simulated checkout'), 'simulated checkout offered');
  await btn('Approve simulated checkout').click();
  await until(() => text().includes('Added 50'), 'wallet credited after capture');
  // admin
  const lo = byId('logout'); reg.clear(); await lo.click();
  await until(() => text().includes('Welcome to OmniPay'), 'logged out');
  byId('a_h').value = 'admin@omni'; byId('a_p').value = 'demo-admin-password-1';
  await byId('a_go').click();
  await until(() => (byId('nav') || { textContent: '' }).textContent.includes('Admin'), 'admin logged in');
  go('admin');
  await until(() => text().includes('Kill switches'), 'admin console');
  ok(text().includes('Ledger balanced') && text().includes('Reconciliation'), 'admin console shows integrity + reconciliation');
  ok(text().includes('simulated') && text().includes('Trial balance'), 'admin shows assets and trial balance');
  console.log(failures ? `\n${failures} FAILED` : '\nUI smoke test passed'); process.exit(failures ? 1 : 0);
})().catch((e) => { console.error('CRASH', e); process.exit(1); });
