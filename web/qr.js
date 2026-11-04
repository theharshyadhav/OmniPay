'use strict';
// Minimal QR Code encoder (byte mode, error correction level M, versions 1-10). No dependencies, no DOM.
// Returns a matrix; app.js draws it with SVG elements. Validated against OpenCV's decoder (tests/qr_check.py).
(function (root) {
  // [total codewords, ecc per block, [[blockCount, dataPerBlock], ...]] for level M
  const SPEC = [null,
    [26, 10, [[1, 16]]], [44, 16, [[1, 28]]], [70, 26, [[1, 44]]], [100, 18, [[2, 32]]], [134, 24, [[2, 43]]],
    [172, 16, [[4, 27]]], [196, 18, [[4, 31]]], [242, 22, [[2, 38], [2, 39]]], [292, 22, [[3, 36], [2, 37]]], [346, 26, [[4, 43], [1, 44]]]];
  const ALIGN = [null, [], [6, 18], [6, 22], [6, 26], [6, 30], [6, 34], [6, 22, 38], [6, 24, 42], [6, 26, 46], [6, 28, 50]];

  // GF(256) arithmetic for Reed-Solomon
  const EXP = new Array(512), LOG = new Array(256);
  for (let i = 0, x = 1; i < 255; i++) { EXP[i] = x; LOG[x] = i; x <<= 1; if (x & 0x100) x ^= 0x11d; }
  for (let i = 255; i < 512; i++) EXP[i] = EXP[i - 255];
  const mul = (a, b) => (a && b ? EXP[LOG[a] + LOG[b]] : 0);
  function rsGenerator(deg) {
    let g = [1];
    for (let i = 0; i < deg; i++) {
      const n = new Array(g.length + 1).fill(0);
      for (let j = 0; j < g.length; j++) { n[j] ^= g[j]; n[j + 1] ^= mul(g[j], EXP[i]); }
      g = n;
    }
    return g;
  }
  function rsRemainder(data, deg) {
    const gen = rsGenerator(deg), res = data.concat(new Array(deg).fill(0));
    for (let i = 0; i < data.length; i++) {
      const c = res[i];
      if (c) for (let j = 0; j < gen.length; j++) res[i + j] ^= mul(gen[j], c);
    }
    return res.slice(data.length);
  }
  function utf8(s) { return Array.from(new TextEncoder().encode(s)); }

  function encode(text) {
    const bytes = utf8(text);
    let ver = 0;
    for (let v = 1; v <= 10; v++) {
      const dataCap = SPEC[v][2].reduce((s, [n, d]) => s + n * d, 0);
      const bits = 4 + (v < 10 ? 8 : 16) + 8 * bytes.length;
      if (bits <= dataCap * 8) { ver = v; break; }
    }
    if (!ver) throw new Error('text too long for QR');
    const [total, eccLen, groups] = SPEC[ver];
    const dataCap = groups.reduce((s, [n, d]) => s + n * d, 0);
    // bit stream
    const bb = [];
    const put = (val, len) => { for (let i = len - 1; i >= 0; i--) bb.push((val >>> i) & 1); };
    put(4, 4); put(bytes.length, ver < 10 ? 8 : 16); bytes.forEach((b) => put(b, 8));
    put(0, Math.min(4, dataCap * 8 - bb.length));
    while (bb.length % 8) bb.push(0);
    const data = [];
    for (let i = 0; i < bb.length; i += 8) data.push(parseInt(bb.slice(i, i + 8).join(''), 2));
    for (let p = 0; data.length < dataCap; p ^= 1) data.push(p ? 0x11 : 0xec);
    // blocks + interleave
    const blocks = []; let k = 0;
    for (const [n, d] of groups) for (let i = 0; i < n; i++) { const blk = data.slice(k, k + d); k += d; blocks.push({ d: blk, e: rsRemainder(blk, eccLen) }); }
    const out = [], maxD = Math.max(...blocks.map((b) => b.d.length));
    for (let i = 0; i < maxD; i++) for (const b of blocks) if (i < b.d.length) out.push(b.d[i]);
    for (let i = 0; i < eccLen; i++) for (const b of blocks) out.push(b.e[i]);
    if (out.length !== total) throw new Error('qr: codeword count mismatch');

    const size = ver * 4 + 17;
    const m = Array.from({ length: size }, () => new Array(size).fill(false));
    const fn = Array.from({ length: size }, () => new Array(size).fill(false));
    const set = (x, y, dark) => { m[y][x] = dark; fn[y][x] = true; };
    const finder = (cx, cy) => {
      for (let dy = -4; dy <= 4; dy++) for (let dx = -4; dx <= 4; dx++) {
        const x = cx + dx, y = cy + dy; if (x < 0 || y < 0 || x >= size || y >= size) continue;
        const d = Math.max(Math.abs(dx), Math.abs(dy)); set(x, y, d !== 2 && d !== 4);
      }
    };
    finder(3, 3); finder(size - 4, 3); finder(3, size - 4);
    for (let i = 0; i < size; i++) { if (!fn[6][i]) set(i, 6, i % 2 === 0); if (!fn[i][6]) set(6, i, i % 2 === 0); }
    const al = ALIGN[ver];
    for (const ay of al) for (const ax of al) {
      if ((ax === 6 && ay === 6) || (ax === 6 && ay === al[al.length - 1]) || (ax === al[al.length - 1] && ay === 6)) continue;
      for (let dy = -2; dy <= 2; dy++) for (let dx = -2; dx <= 2; dx++) set(ax + dx, ay + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
    }
    // reserve format areas
    const drawFormat = (mask) => {
      const fdata = (0 << 3) | mask;              // level M = 00
      let rem = fdata; for (let i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >>> 9) * 0x537);
      const bits = ((fdata << 10) | rem) ^ 0x5412;
      const b = (i) => ((bits >>> i) & 1) === 1;
      for (let i = 0; i <= 5; i++) set(8, i, b(i));
      set(8, 7, b(6)); set(8, 8, b(7)); set(7, 8, b(8));
      for (let i = 9; i < 15; i++) set(14 - i, 8, b(i));
      for (let i = 0; i < 8; i++) set(size - 1 - i, 8, b(i));
      for (let i = 8; i < 15; i++) set(8, size - 15 + i, b(i));
      set(8, size - 8, true);
    };
    drawFormat(0);
    if (ver >= 7) {
      let rem = ver; for (let i = 0; i < 12; i++) rem = (rem << 1) ^ ((rem >>> 11) * 0x1f25);
      const bits = (ver << 12) | rem;
      for (let i = 0; i < 18; i++) {
        const bit = ((bits >>> i) & 1) === 1, a = size - 11 + (i % 3), b = Math.floor(i / 3);
        set(a, b, bit); set(b, a, bit);
      }
    }
    // data placement (zigzag)
    let bi = 0; const allBits = out.length * 8;
    for (let right = size - 1; right >= 1; right -= 2) {
      if (right === 6) right = 5;
      for (let vert = 0; vert < size; vert++) {
        for (let j = 0; j < 2; j++) {
          const x = right - j, upward = ((right + 1) & 2) === 0, y = upward ? size - 1 - vert : vert;
          if (!fn[y][x] && bi < allBits) { m[y][x] = ((out[bi >>> 3] >>> (7 - (bi & 7))) & 1) === 1; bi++; }
        }
      }
    }
    const MASKS = [(x, y) => (x + y) % 2 === 0, (x, y) => y % 2 === 0, (x) => x % 3 === 0, (x, y) => (x + y) % 3 === 0,
      (x, y) => (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0, (x, y) => ((x * y) % 2) + ((x * y) % 3) === 0,
      (x, y) => (((x * y) % 2) + ((x * y) % 3)) % 2 === 0, (x, y) => (((x + y) % 2) + ((x * y) % 3)) % 2 === 0];
    const applyMask = (k) => { for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) if (!fn[y][x] && MASKS[k](x, y)) m[y][x] = !m[y][x]; };
    const penalty = () => {
      let p = 0;
      const runs = (get) => {
        for (let a = 0; a < size; a++) {
          let run = 1;
          for (let b = 1; b < size; b++) { if (get(a, b) === get(a, b - 1)) { run++; if (b === size - 1 && run >= 5) p += run - 2; } else { if (run >= 5) p += run - 2; run = 1; } }
          for (let b = 0; b + 6 < size; b++) {
            const s = [0, 1, 2, 3, 4, 5, 6].map((i) => get(a, b + i));
            if (s[0] && !s[1] && s[2] && s[3] && s[4] && !s[5] && s[6]) p += 40;
          }
        }
      };
      runs((a, b) => m[a][b]); runs((a, b) => m[b][a]);
      for (let y = 0; y < size - 1; y++) for (let x = 0; x < size - 1; x++) { const c = m[y][x]; if (c === m[y][x + 1] && c === m[y + 1][x] && c === m[y + 1][x + 1]) p += 3; }
      let dark = 0; m.forEach((r) => r.forEach((c) => { if (c) dark++; }));
      p += Math.floor(Math.abs(dark * 20 - size * size * 10) / (size * size)) * 10;
      return p;
    };
    let best = 0, bestP = Infinity;
    for (let k = 0; k < 8; k++) { applyMask(k); drawFormat(k); const p = penalty(); if (p < bestP) { bestP = p; best = k; } applyMask(k); }
    applyMask(best); drawFormat(best);
    return { size, dark: (x, y) => m[y][x] };
  }
  root.OmniQR = { encode };
  if (typeof module !== 'undefined') module.exports = { encode };
})(typeof window !== 'undefined' ? window : globalThis);
