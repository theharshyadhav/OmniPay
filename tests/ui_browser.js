// Real-browser walkthrough (Playwright + Chromium): signs in, pays, visits every view on desktop and phone, saves screenshots,
// fails on any console error or horizontal overflow on the phone. Run with the demo server on :8123:  node tests/ui_browser.js [outdir]
const { chromium, devices } = require('playwright');
const B = process.env.BASE || 'http://127.0.0.1:8123', O = process.argv[2] || 'screenshots';
require('fs').mkdirSync(O, { recursive: true });
(async () => {
  const br = await chromium.launch();
  const errs = [];
  async function session(name, ctxOpts) {
    const ctx = await br.newContext(ctxOpts); const p = await ctx.newPage();
    p.on('console', (m) => { if (m.type() === 'error') errs.push(name + ': ' + m.text()); });
    p.on('pageerror', (e) => errs.push(name + ' pageerror: ' + e.message));
    return p;
  }
  const p = await session('desk', { viewport: { width: 1440, height: 960 }, deviceScaleFactor: 1 });
  await fetch(B + '/v1/demo/reset', { method: 'POST', body: '{}' });
  await p.goto(B + '/'); await p.waitForSelector('.auth'); await p.waitForTimeout(400);
  await p.screenshot({ path: O + '/01-auth.png' });
  await p.click('text=Alice'); await p.waitForSelector('.holdings'); await p.waitForTimeout(300);
  await p.screenshot({ path: O + '/02-home.png', fullPage: true });
  await p.goto(B + '/#/pay?to=dana@omni&amount=100&currency=USD'); await p.waitForSelector('.opt'); await p.waitForTimeout(400);
  await p.screenshot({ path: O + '/03-pay.png', fullPage: true });
  await p.click('.opt[data-asset="BTC"]'); await p.click('#p_review'); await p.waitForSelector('#p_pin'); await p.waitForTimeout(300);
  await p.screenshot({ path: O + '/04-review.png' });
  await p.fill('#p_pin', '1234'); await p.click('#p_confirm'); await p.waitForSelector('#p_done'); await p.waitForTimeout(600);
  await p.screenshot({ path: O + '/05-paid.png' });
  await p.click('#p_done');
  for (const v of ['wallet', 'receive', 'assistant']) { await p.goto(B + '/#/' + v); await p.waitForTimeout(900); await p.screenshot({ path: `${O}/06-${v}.png`, fullPage: true }); }
  await p.fill('#chat_in', 'pay invoice inv-demo000001'); await p.click('#concierge_send'); await p.waitForTimeout(1200);
  await p.screenshot({ path: O + '/07-assistant-chat.png' });
  await p.keyboard.press('Control+k'); await p.waitForSelector('#pal_in'); await p.fill('#pal_in', 'bob@omni'); await p.waitForTimeout(200); await p.screenshot({ path: O + '/08-palette.png' });
  await p.keyboard.press('Escape');
  await p.evaluate(() => { localStorage.setItem('omnipay_theme', 'dark'); }); await p.goto(B + '/#/home'); await p.reload(); await p.waitForSelector('.holdings'); await p.waitForTimeout(400);
  await p.screenshot({ path: O + '/09-home-dark.png' });
  await p.goto(B + '/#/pay?invoice=inv-demo000001'); await p.waitForSelector('.opt'); await p.waitForTimeout(400); await p.screenshot({ path: O + '/10-pay-invoice-dark.png', fullPage: true });
  await p.evaluate(() => localStorage.removeItem('omnipay_theme'));
  // promoter + admin
  await p.click('#logout'); await p.waitForSelector('.auth'); await p.click('text=Coinco'); await p.waitForSelector('.holdings');
  await p.goto(B + '/#/promote'); await p.waitForTimeout(900); await p.screenshot({ path: O + '/11-promote.png', fullPage: true });
  await p.click('#logout'); await p.waitForSelector('.auth'); await p.click('.persona >> text=Admin'); await p.waitForSelector('.holdings');
  await p.goto(B + '/#/admin'); await p.waitForTimeout(900); await p.screenshot({ path: O + '/12-admin.png', fullPage: true });
  // phone
  const m = await session('phone', { ...devices['iPhone 13'] });
  await m.goto(B + '/'); await m.waitForSelector('.auth'); await m.waitForTimeout(300); await m.screenshot({ path: O + '/20-m-auth.png', fullPage: true });
  await m.click('text=Alice'); await m.waitForSelector('.holdings'); await m.waitForTimeout(300); await m.screenshot({ path: O + '/21-m-home.png', fullPage: true });
  await m.goto(B + '/#/pay?to=dana@omni&amount=40&currency=EUR'); await m.waitForSelector('.opt'); await m.waitForTimeout(300); await m.screenshot({ path: O + '/22-m-pay.png', fullPage: true });
  await m.click('#p_review'); await m.waitForSelector('#p_pin'); await m.waitForTimeout(300); await m.screenshot({ path: O + '/23-m-review.png' });
  await m.keyboard.press('Escape'); await m.goto(B + '/#/receive'); await m.waitForTimeout(800); await m.screenshot({ path: O + '/24-m-receive.png', fullPage: true });
  const sw = await m.evaluate(() => document.documentElement.scrollWidth); console.log('phone scrollWidth', sw);
  console.log('ERRORS', JSON.stringify(errs, null, 1));
  await br.close();
  if (errs.length || sw > 390) { console.error('FAIL'); process.exit(1); }
  console.log('browser walkthrough passed');
})().catch((e) => { console.error(e); process.exit(1); });
