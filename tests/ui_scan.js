// Real-browser test of QR scanning (Playwright + Chromium). Uses Chromium's fake camera fed with a .y4m video of a
// UPI QR code (tests/make_fake_camera.py), then photo upload and pasted codes. Run against the demo server:
//   python tests/make_fake_camera.py /tmp/cam.y4m 'upi://pay?pa=ravi@omni&pn=Ravi%27s%20Chai%20Stall&am=250&cu=INR&tn=Masala%20chai%20x5'
//   python tests/make_fake_camera.py /tmp/blank.y4m --blank
//   BASE=https://127.0.0.1:8443 node tests/ui_scan.js /tmp/cam.y4m /tmp/blank.y4m qr.png [screenshot dir]
const { chromium, devices } = require('playwright');
const B = process.env.BASE || 'https://127.0.0.1:8443';
const [cam, blank, photo, O = 'screenshots'] = process.argv.slice(2);
require('fs').mkdirSync(O, { recursive: true });
let failures = 0; const ok = (c, m) => { if (!c) { failures++; console.error('FAIL', m); } else console.log('ok  ', m); };
const errs = [];
async function browser(video) {
  return chromium.launch({ args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', `--use-file-for-fake-video-capture=${video}`] });
}
async function page(br, opts) {
  const ctx = await br.newContext({ ignoreHTTPSErrors: true, permissions: ['camera'], ...opts }); const p = await ctx.newPage();
  p.on('pageerror', (e) => errs.push(e.message)); p.on('console', (m) => { if (m.type() === 'error' && !/favicon|sw\.js|ServiceWorker|fetching the script|status of 404/i.test(m.text())) errs.push(m.text()); });
  return p;
}
async function signIn(p, who) { await p.goto(B + '/'); await p.waitForSelector('.auth'); await p.click(`.persona >> text=${who}`); await p.waitForSelector('.holdings'); }
(async () => {
  await fetch(B + '/v1/demo/reset', { method: 'POST', body: '{}' }).catch(() => {});
  // 1. live camera reads a UPI merchant code and opens a pre-filled payment
  let br = await browser(cam), p = await page(br, { viewport: { width: 1280, height: 860 } });
  await signIn(p, 'Alice');
  await p.click('#scan_top');
  await p.waitForURL(/#\/pay\?/, { timeout: 15000 });
  ok(/to=ravi%40omni/.test(p.url()) && /amount=250/.test(p.url()) && /currency=INR/.test(p.url()) && /via=upi/.test(p.url()), 'camera scan of a UPI code opens Pay pre-filled: ' + p.url().split('#')[1]);
  await p.waitForSelector('.opt'); await p.waitForSelector("text=Ravi's Chai Stall", { timeout: 3000 }).catch(() => {});
  const body = await p.textContent('#main');
  ok(body.includes("Ravi's Chai Stall") && body.includes('From a scanned UPI code') && body.includes('Masala chai'), 'payee card shows the registered name, scanned tag and the note');
  ok(await p.evaluate(() => !document.querySelector('#sheet').open), 'scanner closed and camera released');
  await p.screenshot({ path: O + '/30-scanned-upi.png', fullPage: true });
  await p.click('.opt[data-asset="USDC"]'); await p.click('#p_review'); await p.fill('#p_pin', '1234'); await p.click('#p_confirm');
  await p.waitForSelector('#p_done');
  ok((await p.textContent('#sheet')).includes('Ravi'), 'paid the chai stall in USDC; it received INR');
  await br.close();

  // 2. scanner UI, photo upload, pasted codes (camera shows no code)
  br = await browser(blank); p = await page(br, { viewport: { width: 1280, height: 860 } });
  await signIn(p, 'Alice');
  await p.click('#scan_top'); await p.waitForSelector('.scan-view'); await p.waitForTimeout(1200);
  ok((await p.textContent('.scan-status')).includes('Point your camera'), 'camera started, waiting for a code');
  await p.screenshot({ path: O + '/31-scanner.png' });
  await p.setInputFiles('#scan_file', photo);
  await p.waitForURL(/to=dana%40omni/, { timeout: 10000 });
  ok(true, 'photo of a QR code opens a payment to dana@omni');
  await p.click('#scan_top'); await p.click('.scan-manual summary'); await p.fill('#scan_text', 'https://evil.example/login'); await p.click('#scan_go');
  await p.waitForSelector('text=Not a payment code'); ok(true, 'ordinary web links are refused, not opened');
  await p.screenshot({ path: O + '/32-not-payment.png' });
  await p.click('text=Scan another'); await p.waitForSelector('#scan_text', { state: 'attached' });
  await p.click('.scan-manual summary'); await p.fill('#scan_text', 'upi://pay?pa=shop@okicici&pn=Corner%20Shop&am=99'); await p.click('#scan_go');
  await p.waitForSelector('text=Corner Shop uses UPI'); ok((await p.textContent('#sheet')).includes('Nothing was paid'), 'UPI IDs outside OmniPay are explained, nothing is paid');
  await p.keyboard.press('Escape');
  await p.click('#scan_top'); await p.click('.scan-manual summary'); await p.fill('#scan_text', 'alice@omni'); await p.click('#scan_go');
  await p.waitForSelector("text=That's your own code"); ok(true, 'own code is recognised'); await p.keyboard.press('Escape');
  // profile with the personal QR
  await p.goto(B + '/#/profile'); await p.waitForSelector('#my_qr svg');
  const qrPath = await p.$eval('#my_qr svg path', (e) => e.getAttribute('d').length);
  ok(qrPath > 500, 'profile shows the personal QR code');
  await p.fill('#my_amt', '42'); await p.waitForTimeout(400);
  ok((await p.textContent('#my_qr')).includes('asks for $42.00'), 'personal QR can ask for a set amount');
  await p.fill('#my_amt', '');
  await p.waitForTimeout(300);
  await p.screenshot({ path: O + '/33-profile.png', fullPage: true });
  const [dl] = await Promise.all([p.waitForEvent('download'), p.click('text=Save image')]);
  ok(/omnipay-alice_omni\.png$/.test(dl.suggestedFilename()), 'QR saved as an image: ' + dl.suggestedFilename());
  await br.close();

  // 3. phone: profile, scanner, installability
  br = await browser(blank); p = await page(br, { ...devices['iPhone 13'] });
  await signIn(p, 'Dana');
  await p.goto(B + '/#/profile'); await p.waitForSelector('#my_qr svg'); await p.waitForTimeout(300);
  await p.screenshot({ path: O + '/40-m-profile.png' });
  ok(await p.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'phone profile has no sideways scroll');
  await p.click('#scan_tab'); await p.waitForSelector('.scan-view'); await p.waitForTimeout(1200);
  await p.screenshot({ path: O + '/41-m-scanner.png' });
  await p.keyboard.press('Escape');
  await p.goto(B + '/#/home'); await p.waitForSelector('.holdings'); await p.waitForTimeout(200); await p.screenshot({ path: O + '/42-m-home.png' });
  const man = await (await p.request.get(B + '/manifest.webmanifest')).json();
  ok(man.display === 'standalone' && man.icons.some((i) => i.sizes === '512x512'), 'web app manifest is served (installable)');
  const swr = await p.request.get(B + '/sw.js');
  ok(swr.ok() && /javascript/.test(swr.headers()['content-type']), 'service worker script is served (registers on trusted https; self-signed demo certs skip it)');
  await br.close();
  ok(!errs.length, 'no console errors ' + JSON.stringify(errs));
  console.log(failures ? `\n${failures} FAILED` : '\nscan tests passed'); process.exit(failures ? 1 : 0);
})().catch((e) => { console.error('CRASH', e); process.exit(1); });
