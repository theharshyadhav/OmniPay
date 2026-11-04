// Films OmniPay on a phone-sized page, one screenshot per video frame (30 fps). CSS animations are frozen and stepped
// by exactly one frame each shot, so motion is smooth regardless of capture speed. Writes shots/*.jpg + timeline.json.
const { chromium } = require('playwright');
const fs = require('fs');
const DIR = __dirname, B = 'http://127.0.0.1:8124', FPS = 30, DT = 1000 / FPS;
const BASE = Number(fs.readFileSync(DIR + '/base.txt', 'utf8')), CLOCK = DIR + '/filmclock.txt'; let filmMs = 0;
const OUT = DIR + '/shots'; fs.rmSync(OUT, { recursive: true, force: true }); fs.mkdirSync(OUT);
const timeline = { fps: FPS, viewport: [390, 800], scale: 3, scenes: [] };
let scene = null, n = 0, page, cdp;

function startScene(id) { scene = { id, shots: [], taps: [], zooms: [], marks: {} }; timeline.scenes.push(scene); }
const frames = () => scene.shots.reduce((s, x) => s + x.dur, 0);
async function adv(ms) {                                  // advance film time: browser timers and the server clock together
  filmMs += ms; fs.writeFileSync(CLOCK, String(filmMs / 1000)); await page.clock.runFor(Math.round(ms));
}
async function step() {                                   // advance every animation and timer by one frame
  await adv(DT);
  await page.evaluate((dt) => document.getAnimations().forEach((a) => { try { a.currentTime = (a.currentTime || 0) + dt; } catch (e) {} }), DT);
}
async function shot(dur = 1) {                            // one captured image shown for `dur` frames
  const file = `s${String(n++).padStart(5, '0')}.jpg`;
  await page.screenshot({ path: `${OUT}/${file}`, type: 'jpeg', quality: 92 });
  scene.shots.push({ file, dur });
}
async function play(frames_) { for (let i = 0; i < frames_; i++) { await step(); await shot(1); } }   // live motion
async function hold(frames_) { await step(); await shot(frames_); }
const mark = (name) => { scene.marks[name] = frames(); };
const zoom = (at, to, cx, cy, dur = 18) => scene.zooms.push({ at: frames() + (at || 0), to, cx, cy, dur });   // cx,cy in CSS px of the viewport
async function center(sel) { const b = await page.locator(sel).first().boundingBox(); return [b.x + b.width / 2, b.y + b.height / 2]; }
async function tap(sel, opts = {}) {
  const [x, y] = await center(sel);
  scene.taps.push({ at: frames(), x, y });
  await play(opts.lead ?? 7);                               // the ripple shows before the screen reacts
  await page.locator(sel).first().click({ force: true });
  await settle(opts.wait ?? 250);
}
async function typeSlow(sel, text, per = 3) {
  await page.locator(sel).first().focus();
  for (const ch of text) { await page.keyboard.type(ch); await play(per); }
}
async function scrollTo(y, frames_ = 24) {                // eased scroll of the page
  const y0 = await page.evaluate(() => scrollY);
  for (let i = 1; i <= frames_; i++) { const t = i / frames_, e = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2; await page.evaluate((v) => scrollTo(0, v), y0 + (y - y0) * e); await step(); await shot(1); }
}
async function scrollInto(sel, offset = 120, frames_ = 24) { const y = await page.evaluate(([s, o]) => { const e = document.querySelector(s); return e.getBoundingClientRect().top + scrollY - o; }, [sel, offset]); await scrollTo(Math.max(0, y), frames_); }
async function settle(ms = 400) { for (let t = 0; t < ms; t += 50) { await adv(50); await page.waitForTimeout(25); } }
async function waitFor(fn, max = 20000) {                 // hidden wait: lets fetches finish and timers run
  for (let t = 0; t < max; t += 50) { if (await fn()) return; await adv(50); await page.waitForTimeout(40); }
  throw new Error('waitFor timed out: ' + fn);
}
async function playUntil(fn, max = 240) { for (let i = 0; i < max; i++) { if (await fn()) return; await step(); await shot(1); } throw new Error('playUntil timed out: ' + fn); }
const sheetTop = () => page.evaluate(() => { document.activeElement && document.activeElement.blur(); document.querySelector('#sheet .sheet').scrollTop = 0; });
const has = (sel) => async () => (await page.locator(sel).count()) > 0;
async function sheetToBottom(frames_ = 18) {
  const [y0, y1] = await page.evaluate(() => { const s = document.querySelector('#sheet .sheet'); return [s.scrollTop, s.scrollHeight - s.clientHeight]; });
  for (let i = 1; i <= frames_; i++) { const t = i / frames_, e = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2; await page.evaluate((v) => { document.querySelector('#sheet .sheet').scrollTop = v; }, y0 + (y1 - y0) * e); await step(); await shot(1); }
}
async function goto(hash) { await page.evaluate((h) => { location.hash = h; }, hash); await settle(300); await page.evaluate(() => scrollTo(0, 0)); }

(async () => {
  await fetch(B + '/v1/demo/reset', { method: 'POST', body: '{}' });
  const br = await chromium.launch({ args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', `--use-file-for-fake-video-capture=${DIR}/cam.y4m`, '--hide-scrollbars'] });
  const ctx = await br.newContext({ viewport: { width: 390, height: 800 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true, permissions: ['camera'],
    userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1' });
  page = await ctx.newPage();
  fs.writeFileSync(CLOCK, '0');
  await page.clock.install({ time: BASE * 1000 - 2000 }); await page.clock.pauseAt(BASE * 1000);
  cdp = await ctx.newCDPSession(page);
  await page.goto(B + '/');
  await page.addStyleTag({ content: '::-webkit-scrollbar{display:none} *{caret-color:transparent}' });
  await waitFor(has('.auth')); await settle(600);
  await cdp.send('Animation.enable'); await cdp.send('Animation.setPlaybackRate', { playbackRate: 0 });   // freeze; we step time ourselves

  // ---- sign in
  startScene('signin');
  await play(45);                                            // story card: one request priced many ways
  await scrollInto('.personas', 260, 30); await hold(24);
  await tap('.persona >> text=Alice', { wait: 900 });
  await page.evaluate(() => scrollTo(0, 0)); await waitFor(has('.holdings')); await settle(300);
  await play(30);

  // ---- home
  startScene('home');
  await hold(20);
  zoom(0, 1.55, 195, 160); await hold(54);                   // balance
  zoom(0, 1, 195, 400); await play(18);
  await scrollTo(330, 30); await hold(30);
  await scrollTo(0, 24);

  // ---- scan a UPI code with the camera
  startScene('scan');
  await page.addScriptTag({ url: '/vendor/jsQR.js' });
  await page.evaluate(() => { const real = window.jsQR; window.__scanOK = false; window.jsQR = (...a) => (window.__scanOK ? real(...a) : null); });
  await tap('#scan_tab', { lead: 8, wait: 0 }); await waitFor(async () => page.evaluate(() => { const v = document.querySelector('#scan_video'); return v && v.readyState >= 2; }));
  await play(14);                                            // sheet rises, camera on
  mark('viewfinder');
  await play(70);                                            // laser sweeping over the code
  await page.evaluate(() => { window.__scanOK = true; });
  await playUntil(async () => /via=upi/.test(page.url()));
  await playUntil(has("text=Ravi's Chai Stall")); await playUntil(has('.opt'));
  await page.evaluate(() => scrollTo(0, 0));
  mark('opened');
  await play(20); await hold(30);
  zoom(0, 1.6, 195, 330); await hold(60);                    // payee card + note
  zoom(0, 1, 195, 400); await play(18);

  // ---- choose what to pay with
  startScene('choose');
  await scrollInto('.opt', 150, 36); await hold(30);
  await tap('.opt[data-asset="USDC"]', { wait: 200 }); await play(10); await hold(30);
  await scrollInto('.fee-strip', 360, 24);
  zoom(0, 1.7, 195, await page.evaluate(() => document.querySelector('.fee-strip').getBoundingClientRect().top + 20)); await hold(54);
  zoom(0, 1, 195, 400); await play(18);

  // ---- review, PIN, paid
  startScene('pay');
  await tap('#p_review', { wait: 0 }); await waitFor(has('#p_pin')); await settle(150); await sheetTop();
  await play(16); await hold(30);
  zoom(0, 1.5, 195, await page.evaluate(() => document.querySelector('.route-map').getBoundingClientRect().top + 60)); await hold(54);
  zoom(0, 1, 195, 400); await play(18);
  await sheetToBottom(24); await hold(10);
  await typeSlow('#p_pin', '1234', 5); await hold(12);
  await tap('#p_confirm', { wait: 0 });
  await playUntil(has('#p_done'));
  mark('paid');
  await play(40); await hold(60);
  await tap('#p_done', { wait: 300 }); await waitFor(has('.holdings')); await page.evaluate(() => scrollTo(0, 0)); await play(12);

  // ---- pay Dana in Apple shares
  startScene('shares');
  await tap('.tabbar a[href="#/pay"]', { wait: 300 }); await waitFor(has('#p_payee')); await play(10);
  await page.fill('#p_payee', ''); await typeSlow('#p_payee', 'dana@omni', 2); await settle(500); await play(20);
  zoom(0, 1.55, 195, 260); await hold(48); zoom(0, 1, 195, 400); await play(18);
  await typeSlow('#p_amt', '100', 4); await hold(10);
  await tap('#p_find', { wait: 0 }); await playUntil(has('.opt'));
  await scrollInto('.opt', 130, 30); await hold(20);
  await scrollInto('.opt[data-asset="AAPL"]', 300, 24);
  await tap('.opt[data-asset="AAPL"]', { wait: 200 }); await play(8);
  zoom(0, 1.6, 195, await page.evaluate(() => document.querySelector('.opt[data-asset="AAPL"]').getBoundingClientRect().top + 40)); await hold(48);
  zoom(0, 1, 195, 400); await play(18);
  await scrollInto('#p_review', 500, 18);
  await tap('#p_review', { wait: 0 }); await waitFor(has('#p_pin')); await settle(150); await sheetTop(); await play(16); await hold(40);
  await sheetToBottom(20); await page.fill('#p_pin', '1234'); await play(4);
  await tap('#p_confirm', { wait: 0 }); await playUntil(has('#p_done'));
  mark('paid');
  await play(36); await hold(40);
  await page.click('#p_done'); await waitFor(has('.holdings'));

  // ---- your own QR
  startScene('qr');
  await page.evaluate(() => scrollTo(0, 0));
  await tap('.tabbar a[href="#/profile"]', { wait: 300 }); await waitFor(has('#my_qr')); await play(12); await hold(40);
  await scrollInto('#my_amt', 330, 24);
  await typeSlow('#my_amt', '42', 6); await settle(350); await play(10); await hold(45);
  await scrollInto('#my_qr', 70, 24); await hold(30);

  // ---- assistant vs. prompt injection
  startScene('assistant');
  await goto('/pay?invoice=inv-demo000001'); await waitFor(has('.memo')); await waitFor(has('.opt')); await settle(300); await page.evaluate(() => scrollTo(0, 0));
  await play(10); zoom(0, 1.6, 195, await page.evaluate(() => document.querySelector('.memo').getBoundingClientRect().top + 50)); await hold(66);
  zoom(0, 1, 195, 400); await play(18);
  await goto('/assistant'); await waitFor(has('#chat_in')); await settle(400); await play(10); await hold(20);
  await tap('.suggest button >> text=Pay invoice inv-demo000001', { wait: 0 });
  await playUntil(has('text=Paid 120 USD'));
  await play(10);
  zoom(0, 1.45, 195, await page.evaluate(() => { const b = [...document.querySelectorAll('.bubble')].pop(); return b.getBoundingClientRect().top + 30; })); await hold(84);
  zoom(0, 1, 195, 400); await play(18);

  // ---- dark mode
  startScene('dark');
  await goto('/profile'); await waitFor(has('#my_qr')); await settle(300);
  await scrollInto('.li.setting:nth-child(5)', 380, 30); await hold(12);
  await tap('.li.setting:nth-child(5)', { wait: 700 });
  await page.evaluate(() => scrollTo(0, 0)); await settle(200);
  await goto('/home'); await waitFor(has('.holdings')); await settle(300);
  await play(10); await hold(50);

  fs.writeFileSync(DIR + '/timeline.json', JSON.stringify(timeline, null, 1));
  for (const s of timeline.scenes) console.log(s.id, (s.shots.reduce((a, x) => a + x.dur, 0) / FPS).toFixed(1) + 's', s.shots.length, 'shots');
  await br.close();
})().catch((e) => { console.error(e); process.exit(1); });
