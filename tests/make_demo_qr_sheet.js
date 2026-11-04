// Renders docs/demo-qr-codes.png: QR codes to try the scanner with during a demo (scan them from a screen or print).
const { chromium } = require('playwright'); const { encode } = require('../web/qr.js');
const codes = [
  ['upi://pay?pa=ravi@omni&pn=Ravi%27s%20Chai%20Stall&am=250&cu=INR&tn=Masala%20chai%20x5', "Ravi's Chai Stall", 'A UPI-style merchant code for ₹250. Pay it with USDC, BTC or shares; Ravi receives rupees.'],
  ['dana@omni', 'Dana Rivera', 'A plain OmniPay handle. Opens Pay for Dana; you choose the amount.'],
  ['inv-demo000001', 'Request inv-demo000001', 'A payment request for $120. Its note is a prompt-injection test.'],
  ['upi://pay?pa=cornershop@okicici&pn=Corner%20Shop&am=99&cu=INR', 'Corner Shop (UPI, not on OmniPay)', 'Shows how OmniPay explains a UPI ID it can\'t pay yet. Nothing is paid.'],
];
const svg = (t) => { const q = encode(t), n = q.size; let d = ''; for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) if (q.dark(x, y)) d += `M${x} ${y}h1v1h-1z`; return `<svg viewBox="-3 -3 ${n + 6} ${n + 6}" shape-rendering="crispEdges"><rect x="-3" y="-3" width="${n + 6}" height="${n + 6}" fill="#fff"/><path d="${d}" fill="#132034"/></svg>`; };
const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;');
const html = `<style>body{margin:0;font:15px/1.45 Inter,system-ui,sans-serif;color:#132034;background:#fff}main{padding:44px;width:1100px}h1{font-size:30px;margin:0 0 6px}p.l{color:#56627A;margin:0 0 28px}
.g{display:grid;grid-template-columns:1fr 1fr;gap:28px}.c{border:1px solid #DCE2EB;border-radius:20px;padding:22px;display:flex;gap:20px;align-items:center}svg{width:200px;height:200px;flex:none}b{font-size:18px;display:block;margin-bottom:6px}span{color:#56627A}</style>
<main><h1>OmniPay demo QR codes</h1><p class="l">Open OmniPay, tap Scan, and point the camera at any of these.</p><div class="g">${codes.map(([t, a, b]) => `<div class="c">${svg(t)}<div><b>${esc(a)}</b><span>${esc(b)}</span></div></div>`).join('')}</div></main>`;
(async () => { const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 1188, height: 900 }, deviceScaleFactor: 2 }); await p.setContent(html); await p.locator('main').screenshot({ path: process.argv[2] || 'docs/demo-qr-codes.png' }); await b.close(); })();
