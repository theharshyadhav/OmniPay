# OmniPay - pay with whatever you hold

A payment switch where the **payer picks what to pay with** (any currency, stablecoin, crypto or shares) and the **payee picks what to receive**.
One request, every way to pay it: Dana asks for $120, Alice can settle it in euros, USDC, bitcoin or Apple shares, and Dana still gets
euros (or the asset itself, if she accepts it). OmniPay converts at mid-market and charges **no fee and no spread**; it earns only from
clearly labelled sponsored placements that never change the payer's price. PayPal is the on-ramp and off-ramp.

## Demo in two minutes (for judges)
```bash
OMNIPAY_DEMO=1 python3 -m app.serve        # then open http://localhost:8000
```
1. On the sign-in page, click **Alice** (no typing needed).
2. **Pay** dana@omni $100. Every asset Alice holds is priced side by side; pick **Bitcoin**, review the route (BTC goes to Dana as BTC because she accepts it),
   enter PIN `1234`. Try **US dollar** too: it's converted to euros for Dana at mid-market, fee $0.
3. **Scan**: tap Scan, choose *Enter a code instead* and paste `upi://pay?pa=ravi@omni&pn=Chai&am=250&cu=INR` (or scan the same code with the camera):
   Alice pays Ravi's chai stall ₹250 with USDC; he receives rupees. Your own QR code is in **Profile**.
4. **Receive**: a QR code for your handle, or create a request with its own QR and link. Paying via that link closes the request automatically.
5. **Assistant**: tap *Pay invoice inv-demo000001*. The request's memo is a prompt-injection attack; the assistant pays the real invoice and ignores it.
6. **Wallet**: *Add money* through (simulated) PayPal checkout. Sign in as **Admin** for kill switches, reconciliation and a live trial balance.
Press **⌘K / Ctrl+K** anywhere for the command palette (type `bob@omni` or a request ID to jump straight to paying it). Light and dark themes.

## Scan to pay, your own QR, and running it on a phone
* **Scan and pay** (Scan button on every page; the big centre button on phones): reads QR codes with the camera, from a **photo**, or **pasted text**.
  It understands OmniPay codes and links, **UPI codes** (`upi://pay?pa=…&am=…&cu=INR`), handles like `dana@omni` and request IDs, then opens Pay
  pre-filled. You still choose the asset, review and enter your PIN. Uses the browser's built-in QR reader where there is one (Chrome on Android, macOS)
  and the bundled jsQR (Apache-2.0, `web/vendor/`) everywhere else, including iPhone. Links to other websites are **never opened**; a UPI ID that isn't on
  OmniPay is explained ("needs a connection to the UPI network") and nothing is paid. The parser is `web/scan.js` (unit tests: `node tests/scan_parse.js`).
* **Your own QR code** (Profile, and Receive): unique to your handle. Anyone who scans it with OmniPay or their phone's normal camera lands on
  "Pay <you>". You can set an amount, **Share** it (the phone's share sheet), **Save image** (a PNG with your name), or copy the link.
  Demo merchant: `ravi@omni` is *Ravi's Chai Stall* (settles in INR), so a UPI-style code for him can be paid with USDC, BTC or shares.
* **On a phone**:
  ```bash
  OMNIPAY_DEMO=1 OMNIPAY_HTTPS=1 python3 -m app.serve     # prints  On your phone (same Wi-Fi): https://192.168.x.y:8000
  ```
  Phones only allow the camera on **https** pages, so `OMNIPAY_HTTPS=1` makes a self-signed certificate (needs `openssl`; or pass your own with
  `OMNIPAY_TLS_CERT` / `OMNIPAY_TLS_KEY`). Open the printed address on the phone, or scan the **Open OmniPay on your phone** QR shown on the sign-in
  page and in Profile. Accept the one-time certificate warning. Use **Add to Home Screen** to install it like an app (web manifest + service worker; the
  service worker only registers on trusted https or localhost). Deployed on Render (`render.yaml`) you get real https and none of this is needed.

## What's in the web app
* **Home**: total balance across cash, crypto and shares (split by kind), quick actions, activity, open-a-request box.
* **Pay**: live payee lookup (name, main currency, what they accept), big amount entry, a "Pay with" list showing the exact amount in every asset you hold,
  its USD value, what the payee receives and what you'd have left; sponsored rows labelled and never preselected; prices held with a visible countdown;
  a review sheet with a route diagram, zero-fee breakdown and PIN; a receipt. Deep links: `/#/pay?to=dana@omni&amount=40&currency=EUR`, `/#/pay?invoice=inv-…`.
* **Receive**: pay-me QR code and link, payment requests with their own QR codes and status, accept/decline each asset with switches, or set it in plain words.
* **Wallet**: balances filtered by kind, PayPal top-up and withdrawal (step-up + PIN), payout history.
* **Assistant**: chat with suggestion chips, inline approvals for payments above your limit, spending limits, full audit log.
* **Promote**: stats, bid form with rank preview and competition, bid advisor chat.  **Operations** (admin): kill-switch toggles, integrity, reconciliation, audit.
* Built framework-free with no build step; all text goes through text nodes (no `innerHTML`), SVG via `createElementNS`. Fonts are self-hosted
  (Inter, SIL OFL; TeX Gyre Adventor, GUST font license, see `web/fonts/`) because the CSP allows only `'self'`. QR codes come from a dependency-free encoder
  (`web/qr.js`) verified by decoding with OpenCV (`tests/qr_check.*`). Responsive to phone width with a bottom tab bar; keyboard focus visible; reduced motion respected.

A payment switch where the **payer picks what to pay with** (any currency, crypto or shares) and the **payee picks what to accept**.
Built on the reference engine (`omnipay.py`, `promo.py`, kept unmodified and with its 26 tests still green), now with a persisted
double-entry ledger, PayPal at the edges, a persisted promo market, Groq agents, a web app and an admin console.

> **Read `LAUNCH_READINESS.md` first.** This is a build for the PayPal **Sandbox / simulated** rails. It must not go live.

## Product rules, and where they are enforced
| Rule | Where | Proof |
|---|---|---|
| Payer chooses asset; payee chooses what to accept; direct if accepted, else convert; shares in kind only, opt-in only, never sold | `omnipay.py` price(), `app/pswitch.py` capture re-check | `tests/test_store.py::OptOutRaceTests`, `tests/test_property.py` |
| OmniPay charges nothing: no fee, no conversion fee, no spread | no ledger leg ever reaches a fee account | `tests/test_zero_fee.py`; API and UI state it; PayPal's fees are named as outside OmniPay's pricing |
| Revenue only from promoters: bps bids, prepaid escrow, second price, reserves, haircut; Sponsored labelled, never preselected, never overriding policy | `app/market.py` | `tests/test_market.py`, `tests/test_http.py`, `tests/ui_smoke.js` |
| Regional policy, kill switches (global / asset / kind / region / PayPal in / out / agents) | `app/core.py` Switches, `app/pswitch.py` | `tests/test_http.py::AdminTests` |
| PayPal only at the edges; our ledger between users | `app/wallet.py`, `app/paypal.py` | `tests/test_paypal.py` (40 tests) |
| Agents neutral, mandate-bound, server-enforced, audited | `app/agents.py` | `evals/` (35 scripted conversations) + mutation tests |

## Run it
```bash
python3 -m unittest discover -s . -p 'test_*.py' -t .   # reference engine tests (26)
python3 -m unittest discover -s tests -t .              # platform tests (138)
python3 -m evals.run                                    # 35 scripted agent conversations, offline
OMNIPAY_DEMO=1 python3 -m app.serve                     # http://localhost:8000  (seeded demo, simulated PayPal)
node tests/ui_smoke.js                                  # with the server on :8123 (see CI for the exact command)
node tests/ui_browser.js screenshots                    # real Chromium walkthrough, desktop + phone (needs Playwright)
node tests/scan_parse.js                                # what scanned codes turn into (18 cases)
# camera scanning in real Chromium with a fake camera showing a UPI QR (server started with OMNIPAY_HTTPS=1 on :8443):
python3 tests/make_fake_camera.py /tmp/cam.y4m 'upi://pay?pa=ravi@omni&pn=Ravi%27s%20Chai%20Stall&am=250&cu=INR&tn=Masala%20chai%20x5'
python3 tests/make_fake_camera.py /tmp/blank.y4m --blank   # plus any PNG of a QR code for the photo-upload test
BASE=https://127.0.0.1:8443 NODE_TLS_REJECT_UNAUTHORIZED=0 node tests/ui_scan.js /tmp/cam.y4m /tmp/blank.y4m qr.png screenshots
```
Demo logins (demo mode only): `alice@omni` (payer), `bob@omni`, `dana@omni`, `ravi@omni`, `coinco@omni`/`exchange@omni` (promoters), `admin@omni`.
Password `demo-password-1` (admin: `demo-admin-password-1`), PIN `1234`. Try the assistant with `pay invoice inv-demo000001`: its memo is a prompt-injection attempt.
Real admin: `OMNIPAY_ADMIN_PASSWORD=... python -m app.cli create-admin you@domain`.

## Configuration (environment variables only)
See `.env.example`. `PAYPAL_ENV=sandbox|live` (live refused without `OMNIPAY_ALLOW_LIVE=1`), `PAYPAL_CLIENT_ID/SECRET/WEBHOOK_ID`,
`GROQ_API_KEY`, `GROQ_MODEL` (concierge), `GROQ_ADVISOR_MODEL`, `GROQ_STRUCT_MODEL`. With no PayPal keys the app uses a **simulated PayPal**
(badge in the UI); with no Groq key the agents run on the **deterministic fallback**.

## Architecture
```
web/ (vanilla JS)  ->  app/server.py (WSGI, sessions, step-up, rate limits, OpenAPI, metrics)
                           |
                      app/platform.py
   +---------------+-------+----------------+------------------+-----------------+
   app/pswitch.py   app/wallet.py            app/market.py      app/agents.py     app/core.py
   persisted Switch PayPal edge:              persisted promo:   Groq agents:      audit, switches,
   hold/execute/    orders, capture, webhooks reserve at tap,    concierge, bid    auth, rate limit
   capture, UNKNOWN payouts, reconciliation   caps, sweep,       advisor, payee
   stays held       (app/paypal.py + fake)    autobidder        setup (app/groq.py)
                           |
                   app/dbledger.py + migrations/  (SQLite; append-only entries; balanced-or-rejected; balances CHECK >= 0)
```
Key invariants (all tested): Decimal money stored as exact integer atoms; double entry enforced by DB triggers (an unbalanced transaction cannot be sealed);
user/hold/escrow balances can never go negative; one payment per quote (idempotent); holds with compensation; **unknown outcomes stay on hold until reconciled**;
the PayPal credit is computed from PayPal's own capture record fetched server-to-server, never from client or webhook-body amounts; webhooks are signature-verified and deduplicated.