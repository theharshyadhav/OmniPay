// Writes QR matrices for several payloads as PGM images; tests/qr_check.py decodes them with OpenCV.
const { encode } = require('../web/qr.js'); const fs = require('fs'); const dir = process.argv[2];
const payloads = ['omnipay:dana@omni', 'https://omnipay.example/#/pay/inv-demo000001', 'x'.repeat(60), 'https://omnipay.example/#/pay?to=alice@omni&amount=123.45&currency=EUR&memo=dinner%20split', 'y'.repeat(150), 'z'.repeat(200)];
payloads.forEach((p, i) => {
  const q = encode(p), s = 8, pad = 4, n = (q.size + pad * 2) * s; const px = Buffer.alloc(n * n, 255);
  for (let y = 0; y < q.size; y++) for (let x = 0; x < q.size; x++) if (q.dark(x, y)) for (let dy = 0; dy < s; dy++) for (let dx = 0; dx < s; dx++) px[((y + pad) * s + dy) * n + (x + pad) * s + dx] = 0;
  fs.writeFileSync(`${dir}/q${i}.pgm`, Buffer.concat([Buffer.from(`P5\n${n} ${n}\n255\n`), px])); fs.writeFileSync(`${dir}/q${i}.txt`, p);
});
