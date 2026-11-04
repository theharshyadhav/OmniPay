'use strict';
// Turns the text inside a scanned QR code into something OmniPay can act on. Pure function, no DOM, no network.
// Never follows or opens a scanned URL: it only extracts payee/amount fields, and every payment still goes through
// the normal review screen (payee name shown, PIN required). Unknown content is reported as 'unknown'.
(function (root) {
  const HANDLE = /^[a-z0-9][\w.-]{0,63}@[a-z0-9][\w.-]{0,63}$/i;     // OmniPay handles and UPI IDs share this shape
  const INVOICE = /^inv-[a-z0-9]{6,20}$/i;
  const AMOUNT = /^\d{1,12}(\.\d{1,8})?$/;
  const CUR = /^[A-Z]{3,5}$/;

  function clean(v, re) { v = (v == null ? '' : String(v)).trim(); return v && re.test(v) ? v : ''; }
  function text(v, max) { return (v == null ? '' : String(v)).replace(/[\u0000-\u001f\u007f]/g, ' ').trim().slice(0, max || 140); }
  function params(qs) {                                   // case-insensitive keys, first value wins
    const out = {};
    for (const [k, v] of new URLSearchParams(qs)) { const key = k.toLowerCase(); if (!(key in out)) out[key] = v; }
    return out;
  }
  function payFields(p) {
    const r = {};
    const to = clean(p.to || p.pa, HANDLE), amount = clean(p.amount || p.am, AMOUNT), currency = clean((p.currency || p.cu || '').toUpperCase(), CUR);
    const invoice = clean(p.invoice, INVOICE), note = text(p.note || p.tn);
    if (to) r.to = to.toLowerCase();
    if (amount && Number(amount) > 0) r.amount = amount;
    if (currency) r.currency = currency;
    if (invoice) r.invoice = invoice.toLowerCase();
    if (note) r.note = note;
    return r;
  }

  function parse(raw, opts) {
    const s = text(raw, 2000);
    if (!s) return { kind: 'empty' };
    if (INVOICE.test(s)) return { kind: 'omnipay', invoice: s.toLowerCase() };
    if (HANDLE.test(s)) return { kind: 'omnipay', to: s.toLowerCase() };

    // UPI (NPCI) deep link: upi://pay?pa=<vpa>&pn=<name>&am=<amount>&cu=INR&tn=<note>
    const upi = /^upi:\/\/pay\?(.*)$/i.exec(s);
    if (upi) {
      const p = params(upi[1]), f = payFields(p);
      if (!f.to) return { kind: 'unknown', text: s, reason: 'This UPI code has no valid payee ID.' };
      return { kind: 'upi', to: f.to, name: text(p.pn, 80), amount: f.amount, currency: f.currency || 'INR', note: f.note };
    }

    // omnipay:<handle>  or  omnipay://pay?to=..&amount=..
    const op = /^omnipay:(?:\/\/pay\?(.*)|([^?]+))$/i.exec(s);
    if (op) {
      const f = op[1] != null ? payFields(params(op[1])) : payFields({ to: decodeURIComponent(op[2]) });
      return f.to || f.invoice ? { kind: 'omnipay', ...f } : { kind: 'unknown', text: s };
    }

    // an OmniPay web link: https://host/#/pay?to=...  (also /pay?to=... without the hash)
    let u = null;
    try { u = new URL(s); } catch { /* not a URL */ }
    if (u && (u.protocol === 'https:' || u.protocol === 'http:')) {
      const hash = u.hash.replace(/^#\/?/, '');
      const [path, qs] = hash ? hash.split('?') : [u.pathname.replace(/^\//, ''), u.search.slice(1)];
      if (/^pay\/?$/.test(path || '') && qs) {
        const f = payFields(params(qs));
        if (f.to || f.invoice) {
          const here = opts && opts.origin ? opts.origin : '';
          return { kind: 'omnipay', ...f, foreign: !!here && u.origin !== here, host: u.host };
        }
      }
      return { kind: 'unknown', text: s, reason: 'This QR code holds a web link, not a payment code. OmniPay doesn\'t open links from codes.' };
    }
    return { kind: 'unknown', text: s, reason: 'This QR code isn\'t a payment code.' };
  }

  // Build the in-app route for a parsed code (used after the payee has been checked).
  function payRoute(r, extra) {
    const q = new URLSearchParams();
    if (r.invoice) q.set('invoice', r.invoice);
    else {
      if (r.to) q.set('to', r.to);
      if (r.amount) q.set('amount', r.amount);
      if (r.currency) q.set('currency', r.currency);
      if (r.note) q.set('note', r.note);
    }
    for (const [k, v] of Object.entries(extra || {})) q.set(k, v);
    return 'pay?' + q.toString();
  }

  root.OmniScan = { parse, payRoute };
  if (typeof module !== 'undefined') module.exports = { parse, payRoute };
})(typeof window !== 'undefined' ? window : globalThis);
