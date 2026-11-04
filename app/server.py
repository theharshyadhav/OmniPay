"""HTTP layer: a small WSGI app (stdlib only, so it is runnable and testable offline). Thin handlers over Platform.
Identity ALWAYS comes from the session, never from the request body. Money-moving and credential-changing calls need a
fresh password step-up or the PIN. OpenAPI is generated from the route table at /openapi.json.
Deviation from the brief: FastAPI/Pydantic could not be installed in the build sandbox (no network); handlers are
framework-free so porting them to FastAPI is mechanical."""
from __future__ import annotations

import json
import logging
import mimetypes
import re
import time
import uuid
from collections import defaultdict
from decimal import Decimal as D
from pathlib import Path
from typing import Callable

from omnipay import ASSETS, PaymentError, State
from app.agents import close_invoice_if_matching
from app.paypal import PayPalError
from app.platform import Platform

log = logging.getLogger("omnipay.http")
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("text/javascript", ".js")
LAN_URL = {"url": None}                       # set by app.serve when it knows the LAN address (demo only: lets a phone open the app)
WEB = Path(__file__).resolve().parent.parent / "web"
STATUS = {"BAD_LOGIN": 401, "UNAUTHENTICATED": 401, "STEP_UP_REQUIRED": 401, "BAD_PIN": 401, "LOGIN_LOCKED": 429, "PIN_LOCKED": 429,
          "RATE_LIMITED": 429, "HANDLE_TAKEN": 409, "FORBIDDEN": 403, "ACCOUNT_FROZEN": 403, "BAD_SIGNATURE": 400, "BAD_WEBHOOK": 400,
          "KILL_SWITCH": 503, "PAYPAL_IN_OFF": 503, "PAYPAL_OUT_OFF": 503, "PAYPAL_UNAVAILABLE": 502, "QUOTE_EXPIRED": 410, "REGION_BLOCKED": 403}
CSP = ("default-src 'self'; script-src 'self' https://www.paypal.com https://www.sandbox.paypal.com; frame-src https://www.paypal.com "
       "https://www.sandbox.paypal.com; connect-src 'self' https://www.paypal.com https://www.sandbox.paypal.com; img-src 'self' data: https://www.paypalobjects.com; "
       "style-src 'self' 'unsafe-inline'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


class Ctx:
    def __init__(self, pf, body, sess, params, query, headers, raw, token, ip):
        self.pf, self.body, self.sess, self.params, self.query, self.headers, self.raw, self.token, self.ip = pf, body, sess, params, query, headers, raw, token, ip

    @property
    def me(self) -> str:
        return self.sess["handle"]

    def need(self, *keys):
        miss = [k for k in keys if k not in self.body]
        if miss:
            raise PaymentError("BAD_REQUEST", f"missing {miss}")
        return [self.body[k] for k in keys]


ROUTES: list[tuple] = []


def route(method: str, path: str, *, auth: str = "user", summary: str = "", rl: str = "api", stepup: bool = False):
    """auth: none | user | admin"""
    rx = re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", path) + "$")

    def deco(fn):
        ROUTES.append((method, path, rx, auth, rl, stepup, fn, summary or (fn.__doc__ or "").strip()))
        return fn
    return deco


def dec(v) -> str:
    return format(v.normalize(), "f") if isinstance(v, D) and v != 0 else str(v)


# ============================ public ============================
@route("GET", "/healthz", auth="none", summary="Liveness + ledger integrity")
def healthz(c):
    return {"ok": True, "ledger_problems": len(c.pf.ledger.audit())}


@route("GET", "/v1/config", auth="none", summary="Public client configuration")
def config(c):
    cfg = c.pf.cfg
    return {"paypal_env": cfg.paypal_env, "paypal_simulated": bool(getattr(c.pf.paypal, "simulated", False)),
            "paypal_client_id": cfg.paypal_client_id, "paypal_currencies": sorted(cfg.paypal_currencies), "demo": cfg.demo,
            "fee_statement": "OmniPay charges no fee, no conversion fee and no spread: you pay the invoice's mid-market value in the asset you choose. "
                             "Third-party costs, for example PayPal's own fees when you add or withdraw money, are outside OmniPay's pricing.",
            "groq": c.pf.groq is not None, "lan_url": LAN_URL["url"] if cfg.demo else None}


@route("GET", "/v1/assets", auth="none", summary="Assets, kinds, availability and Simulated badges")
def assets(c):
    sx, cfg = c.pf.switches, c.pf.cfg
    return {"assets": [{"code": k, "kind": a.kind.value, "dp": a.dp, "convertible": a.convertible, "simulated": a.kind.value in cfg.simulated_kinds,
                        "enabled": sx.on("asset", k) and sx.on("kind", a.kind.value)} for k, a in ASSETS.items()]}


@route("GET", "/v1/payees/{handle}", auth="none", summary="What a payee accepts (public)")
def payee(c):
    u = c.pf.sw._user(c.params["handle"])
    n = c.pf.ledger.conn().execute("SELECT name FROM display_names WHERE handle=?", (c.params["handle"],)).fetchone()
    return {"handle": c.params["handle"], "settle_in": u.settle_in, "accepts": sorted(u.accepts), "name": n["name"] if n else None}


@route("GET", "/v1/rates", auth="none", summary="USD value of one unit of each asset (demo prices are simulated)")
def rates(c):
    out = {}
    for k in ASSETS:
        try:
            out[k] = dec(c.pf.oracle.usd(k))
        except PaymentError:
            out[k] = None
    return {"base": "USD", "rates": out, "simulated": True}


# ============================ auth ============================
@route("POST", "/v1/auth/signup", auth="none", rl="auth")
def signup(c):
    h, pw, pin = c.need("handle", "password", "pin")
    region = c.body.get("region", "US")
    if region in c.pf.sw.policy.blocked:
        raise PaymentError("REGION_BLOCKED", f"OmniPay is not available in {region}")
    if not isinstance(pin, str) or not re.fullmatch(r"\d{4,8}", pin):
        raise PaymentError("BAD_PIN_FORMAT", "PIN must be 4-8 digits")
    c.pf.signup(h, pw, pin, region=region, settle_in=c.body.get("settle_in", "USD"), email=c.body.get("email", ""))
    return {"token": c.pf.auth.login(h, pw), "handle": h}


@route("POST", "/v1/auth/login", auth="none", rl="auth")
def login(c):
    h, pw = c.need("handle", "password")
    return {"token": c.pf.auth.login(h, pw), "handle": h}


@route("POST", "/v1/auth/logout")
def logout(c):
    c.pf.auth.logout(c.token)
    return {"ok": True}


@route("POST", "/v1/auth/stepup", rl="auth", summary="Re-enter the password to unlock sensitive actions for a short time")
def stepup(c):
    c.pf.auth.step_up(c.token, c.need("password")[0])
    return {"ok": True}


@route("GET", "/v1/me")
def me(c):
    u = c.pf.sw._user(c.me)
    cred = c.pf.ledger.conn().execute("SELECT payout_email FROM credentials WHERE handle=?", (c.me,)).fetchone()
    return {"handle": c.me, "admin": c.sess["admin"], "frozen": c.sess["frozen"], "region": u.region, "settle_in": u.settle_in, "accepts": sorted(u.accepts),
            "payout_email": cred["payout_email"] if cred else None}


@route("PUT", "/v1/me/profile", summary="Payee settings (also produced by the natural-language setup)")
def set_profile(c):
    settle, accepts = c.need("settle_in", "accepts")
    sw = c.pf.sw
    sw.set_settle_in(c.me, settle)
    cur = sw._user(c.me)
    sw.decline(c.me, *(cur.accepts - set(accepts) - {settle}))
    sw.accept(c.me, *accepts)
    c.pf.audit.log(c.me, "payee.profile_saved", c.me, settle_in=settle, accepts=sorted(accepts))
    return me(c)


@route("POST", "/v1/me/payout-email", stepup=True, summary="Where PayPal payouts go (needs fresh step-up)")
def payout_email(c):
    e = c.need("email")[0]
    if not re.fullmatch(r"[^@\s]{1,64}@[^@\s]{1,120}\.[^@\s]{2,20}", e):
        raise PaymentError("BAD_EMAIL", e)
    c.pf.ledger.conn().execute("UPDATE credentials SET payout_email=? WHERE handle=?", (e, c.me))
    c.pf.audit.log(c.me, "payout.email_set", c.me)
    return {"ok": True}


@route("POST", "/v1/invoices", summary="Create a payment request")
def invoice(c):
    amount, cur = c.need("amount", "currency")
    try:
        ok = D(str(amount)) > 0
    except Exception:
        ok = False
    if not ok or cur not in ASSETS:
        raise PaymentError("BAD_AMOUNT", f"{amount} {cur}")
    iid = "inv-" + uuid.uuid4().hex[:10]
    c.pf.ledger.conn().execute("INSERT INTO invoices VALUES(?,?,?,?,?,'OPEN',?)", (iid, c.me, str(amount), cur, str(c.body.get("memo", ""))[:300], c.pf.clock()))
    return {"id": iid}


@route("GET", "/v1/invoices/{id}", summary="Look up a payment request (memo is untrusted text)")
def get_invoice(c):
    r = c.pf.ledger.conn().execute("SELECT * FROM invoices WHERE id=?", (c.params["id"],)).fetchone()
    if r is None:
        raise PaymentError("UNKNOWN_INVOICE", c.params["id"])
    return {k: r[k] for k in ("id", "payee", "amount", "currency", "memo", "status")}


@route("GET", "/v1/me/invoices", summary="Payment requests you created")
def my_invoices(c):
    rows = c.pf.ledger.conn().execute("SELECT * FROM invoices WHERE payee=? ORDER BY created_at DESC, rowid DESC LIMIT 50", (c.me,)).fetchall()
    return {"invoices": [{k: r[k] for k in ("id", "payee", "amount", "currency", "memo", "status", "created_at")} for r in rows]}


def _close_invoice(c, invoice_id, qid):
    return close_invoice_if_matching(c.pf, c.me, invoice_id, c.pf.sw.load_quote(qid))


# ============================ paying ============================
def _opt(c, o):
    a = ASSETS[o.asset]
    return {"asset": o.asset, "kind": a.kind.value, "simulated": a.kind.value in c.pf.cfg.simulated_kinds, "amount": dec(o.quote.amount), "route": o.quote.route,
            "receive_asset": o.quote.receive_asset, "receive": dec(o.quote.receive), "sponsored": o.sponsored, "label": o.label, "notice": o.notice}


@route("POST", "/v1/pay/options", summary="Ranked 'pay with' list; only assets the payer can pay with; sponsored rows labelled, never preselected")
def pay_options(c):
    payee, amount, cur = c.need("payee", "amount", "currency")
    lst = c.pf.promo.options(c.me, payee, str(amount), cur)
    return {"listing_id": lst.id, "default": lst.default, "expires_at": lst.expires_at, "options": [_opt(c, o) for o in lst.options],
            "omnipay_fee": "0", "spread": "0"}


@route("POST", "/v1/pay/quote")
def pay_quote(c):
    lid, asset = c.need("listing_id", "asset")
    l = c.pf.promo._listings.get(lid)
    if l is None or l.payer != c.me:
        raise PaymentError("UNKNOWN_LISTING", lid)
    q = c.pf.promo.quote(lid, asset)
    return {"quote_id": q.id, "pay_asset": q.pay_asset, "amount": dec(q.amount), "receive_asset": q.receive_asset, "receive": dec(q.receive), "route": q.route,
            "usd": dec(q.usd), "expires_at": q.expires_at}


@route("POST", "/v1/pay", summary="Pay a quote (PIN required)")
def pay(c):
    qid, pin = c.need("quote_id", "pin")
    if c.pf.sw.load_quote(qid).payer != c.me:
        raise PaymentError("UNKNOWN_QUOTE", qid)
    try:
        p = c.pf.sw.pay(qid, str(pin))
    except PaymentError:
        raise
    except Exception:                                   # the rail failed mid-flight: the payment exists and stays held
        row = c.pf.ledger.conn().execute("SELECT id, state FROM payments WHERE quote_id=?", (qid,)).fetchone()
        if row is None:
            raise
        log.exception("payment %s outcome unknown", row["id"])
        return {"payment_id": row["id"], "state": row["state"], "reason": "outcome unknown: funds are held until the payment is confirmed"}
    state = c.pf.sw.raw_state(p.id)
    out = {"payment_id": p.id, "state": state, "reason": p.reason}
    if c.body.get("invoice_id") and state == "SETTLED":
        out["invoice_paid"] = _close_invoice(c, str(c.body["invoice_id"]), qid)
    return out


def _pay_row(r):
    return {"id": r["id"], "state": r["state"], "reason": r["reason"], "payer": r["payer"], "payee": r["payee"], "pay_asset": r["pay_asset"],
            "amount": dec(D(r["amount"])), "receive_asset": r["receive_asset"], "receive": dec(D(r["receive"])), "route": r["route"], "at": r["created_at"]}


@route("GET", "/v1/me/payments")
def my_payments(c):
    rows = c.pf.ledger.conn().execute(
        "SELECT p.id, p.state, p.reason, p.created_at, q.* FROM payments p JOIN quotes q ON q.id=p.quote_id WHERE q.payer=? OR q.payee=? ORDER BY p.created_at DESC LIMIT 50",
        (c.me, c.me)).fetchall()
    return {"payments": [_pay_row(r) for r in rows]}


# ============================ wallet ============================
@route("GET", "/v1/wallet")
def wallet(c):
    out = c.pf.agents.toolbox(c.me, "ui", "concierge").t_get_balances()["balances"]
    return {"balances": out, "payouts": [dict(r) for r in c.pf.ledger.conn().execute(
        "SELECT id, currency, amount, state, reason, created_at FROM payouts WHERE handle=? ORDER BY created_at DESC LIMIT 20", (c.me,))]}


@route("POST", "/v1/wallet/fund/orders", summary="Create a PayPal order for a wallet top-up (amount validated server-side)")
def fund_order(c):
    cur, amount = c.need("currency", "amount")
    return c.pf.wallet.create_funding_order(c.me, cur, amount, c.body.get("purpose", "fund") if c.body.get("purpose") in ("fund", "promoter_topup") else "fund")


@route("POST", "/v1/wallet/fund/capture", summary="Capture an approved PayPal order; the wallet is credited only after PayPal confirms")
def fund_capture(c):
    return c.pf.wallet.capture_order(c.me, c.need("order_id")[0])


@route("POST", "/v1/wallet/payouts", stepup=True, summary="Withdraw to PayPal (fresh step-up + PIN)")
def payout(c):
    cur, amount, pin = c.need("currency", "amount", "pin")
    c.pf.sw._authenticate(c.me, str(pin))
    return c.pf.wallet.request_payout(c.me, cur, amount)


@route("POST", "/v1/webhooks/paypal", auth="none", rl="webhook", summary="PayPal webhooks (signature verified; idempotent)")
def paypal_webhook(c):
    return c.pf.wallet.handle_webhook(c.headers, c.raw)


# ============================ promoter ============================
@route("GET", "/v1/promoter/dashboard")
def dash(c):
    P = c.pf.promo
    return {"stats": P.stats(c.me), "history": P.history(c.me, 30), "pending": [p for p in c.pf.agents.pending(c.me) if p["quote_id"].startswith("advisor:")],
            "autobids": [dict(r) for r in c.pf.ledger.conn().execute("SELECT asset,max_bps,daily_cap_usd,total_cap_usd,enabled FROM autobids WHERE promoter=?", (c.me,))],
            "rules": "Bids are basis points of each completed payment, prepaid in escrow, generalised second price, with a reserve per route and a haircut for risky funding assets."}


@route("POST", "/v1/promoter/bids", stepup=True, summary="Place or top up a bid (moves money into escrow: step-up required)")
def place_bid(c):
    asset, bps, fund, budget = c.need("asset", "bps", "fund_asset", "budget")
    b = c.pf.promo.bid(c.me, asset, bps, pay_with=fund, budget=budget, regions=c.body.get("regions", []))
    return {"asset": b.asset, "bps": dec(b.bps), "fund_asset": b.fund_asset}


@route("DELETE", "/v1/promoter/bids/{asset}", stepup=True)
def drop_bid(c):
    c.pf.promo.cancel_bid(c.me, c.params["asset"])
    return {"ok": True}


@route("POST", "/v1/promoter/autobid", stepup=True)
def autobid(c):
    a, mx, d, t = c.need("asset", "max_bps", "daily_cap_usd", "total_cap_usd")
    c.pf.promo.set_autobid(c.me, a, max_bps=mx, daily_cap_usd=d, total_cap_usd=t)
    return {"ok": True}


@route("GET", "/v1/promoter/landscape")
def landscape(c):
    return c.pf.promo.landscape(c.query.get("asset", "USDC"))


@route("POST", "/v1/promoter/simulate", summary="What if I bid X?")
def simulate(c):
    a, bps = c.need("asset", "bps")
    return c.pf.promo.simulate(c.me, a, bps, c.body.get("route", "convert"))


# ============================ agents ============================
@route("GET", "/v1/agent/mandate")
def get_mandate(c):
    m = c.pf.agents.get_mandate(c.me)
    return {"mandate": None if m is None else {k: (str(v) if isinstance(v, D) else v) for k, v in m.items()}}


@route("PUT", "/v1/agent/mandate", stepup=True, summary="Set the limits the assistant may spend within (step-up required)")
def set_mandate(c):
    b = c.body
    c.pf.agents.set_mandate(c.me, enabled=bool(b.get("enabled")), per_payment_usd=b["per_payment_usd"], daily_usd=b["daily_usd"],
                            allowed_assets=b.get("allowed_assets", []), payee_allowlist=b.get("payee_allowlist", []), confirm_above_usd=b["confirm_above_usd"])
    return get_mandate(c)


@route("POST", "/v1/agent/concierge", rl="agent")
def concierge(c):
    return c.pf.agents.concierge(c.me, str(c.need("text")[0]), c.body.get("session"))


@route("GET", "/v1/agent/pending")
def pending(c):
    return {"pending": [{"id": p["id"], "kind": p["quote_id"].split(":")[0] + ":" + p["quote_id"].split(":")[1] if ":" in p["quote_id"] else p["quote_id"], "usd": p["usd"],
                         "summary": json.loads(p["summary"]), "expires_at": p["expires_at"]} for p in c.pf.agents.pending(c.me)]}


@route("POST", "/v1/agent/confirm", summary="A human approves a payment the assistant prepared (PIN required)")
def confirm(c):
    cid, pin = c.need("id", "pin")
    return c.pf.agents.confirm_payment(c.me, cid, str(pin))


@route("POST", "/v1/agent/reject")
def reject(c):
    c.pf.agents.reject_confirmation(c.me, c.need("id")[0])
    return {"ok": True}


@route("GET", "/v1/agent/log", summary="Everything the assistant did on your behalf")
def agent_log(c):
    return {"log": c.pf.agents.audit_trail(c.me, c.query.get("session"), 200)}


@route("POST", "/v1/agent/advisor", rl="agent")
def advisor(c):
    return c.pf.agents.advisor(c.me, str(c.need("text")[0]), c.body.get("session"))


@route("POST", "/v1/agent/advisor/confirm", stepup=True)
def advisor_confirm(c):
    return c.pf.agents.confirm_advisor(c.me, c.need("id")[0])


@route("POST", "/v1/agent/payee-setup", rl="agent", summary="Natural language -> structured profile preview (nothing saved yet)")
def payee_setup(c):
    return c.pf.agents.payee_setup(c.me, str(c.need("text")[0]))


@route("POST", "/v1/agent/payee-setup/confirm")
def payee_setup_confirm(c):
    return c.pf.agents.confirm_payee_profile(c.me, c.need("id")[0])


# ============================ admin ============================
@route("GET", "/v1/admin/overview", auth="admin")
def a_overview(c):
    pf, L = c.pf, c.pf.ledger
    return {"switches": pf.switches.all(), "breaks": pf.wallet.breaks(), "ledger_problems": L.audit(), "held_payments": pf.sw.held_payments(),
            "stuck_payouts": [dict(r) for r in L.conn().execute("SELECT id,handle,currency,amount,state,reason FROM payouts WHERE state IN ('HELD','SUBMITTED','PENDING','UNKNOWN')")],
            "assets": assets(c)["assets"], "policy": {"allowed": {k: sorted(v) for k, v in pf.sw.policy.allowed.items()}, "blocked": sorted(pf.sw.policy.blocked)},
            "trial_balance": {k: dec(v) for k, v in L.trial_balance().items()}, "revenue": {k: dec(L.balance("sys:revenue", k)) for k in ASSETS if L.balance("sys:revenue", k)},
            "metrics": METRICS.snapshot(), "paypal_simulated": bool(getattr(pf.paypal, "simulated", False))}


@route("POST", "/v1/admin/switch", auth="admin", stepup=True, summary="Kill switches / feature flags per asset, kind, region, PayPal in/out, agents, global")
def a_switch(c):
    scope, key, enabled = c.need("scope", "key", "enabled")
    c.pf.switches.set(scope, key, bool(enabled), c.me, str(c.body.get("reason", "")))
    return {"switches": c.pf.switches.all()}


@route("POST", "/v1/admin/reconcile", auth="admin")
def a_recon(c):
    return c.pf.wallet.reconcile()


@route("POST", "/v1/admin/jobs", auth="admin", summary="Run one tick of the background jobs now")
def a_jobs(c):
    return c.pf.run_jobs()


@route("GET", "/v1/admin/audit", auth="admin")
def a_audit(c):
    return {"audit": c.pf.audit.recent(int(c.query.get("limit", 100)), c.query.get("actor"))}


@route("POST", "/v1/admin/payments/{id}/resolve", auth="admin", stepup=True, summary="Settle a held/unknown payment after checking the rail")
def a_resolve_payment(c):
    c.pf.audit.log(c.me, "payment.manual_resolve", c.params["id"], executed=bool(c.body.get("executed")))
    p = c.pf.sw.resolve_held(c.params["id"], bool(c.need("executed")[0]))
    return {"state": p.state.value, "reason": p.reason}


@route("POST", "/v1/admin/payouts/{id}/resolve", auth="admin", stepup=True)
def a_resolve_payout(c):
    return c.pf.wallet.resolve_unknown_payout(c.params["id"], delivered=bool(c.need("delivered")[0]), actor=c.me)


@route("POST", "/v1/demo/reset", auth="none", rl="auth", summary="Demo only: wipe and reseed")
def demo_reset(c):
    if not c.pf.cfg.demo:
        raise PaymentError("FORBIDDEN", "demo mode is off")
    from app.seed import reset_demo
    return reset_demo(c.pf)


@route("POST", "/v1/demo/paypal/approve", auth="user", summary="Demo only: act as the buyer approving the simulated PayPal order")
def demo_approve(c):
    if not (c.pf.cfg.demo and getattr(c.pf.paypal, "simulated", False)):
        raise PaymentError("FORBIDDEN", "only in simulated demo mode")
    c.pf.paypal.approve(c.need("paypal_order_id")[0])
    return {"ok": True}


@route("POST", "/v1/demo/paypal/settle-payout", auth="user")
def demo_settle(c):
    if not (c.pf.cfg.demo and getattr(c.pf.paypal, "simulated", False)):
        raise PaymentError("FORBIDDEN", "only in simulated demo mode")
    p = c.pf.wallet.get_payout(c.need("payout_id")[0])
    if p["handle"] != c.me:
        raise PaymentError("FORBIDDEN", "not yours")
    c.pf.paypal.settle_payout(p["paypal_batch_id"], c.body.get("status", "SUCCESS"))
    return c.pf.wallet.sync_payout(p["id"])


# ============================ plumbing ============================
class Metrics:
    def __init__(self):
        self.c = defaultdict(int); self.t = defaultdict(float)

    def inc(self, k, n=1):
        self.c[k] += n

    def snapshot(self):
        return dict(self.c)

    def text(self, pf):
        out = [f'omnipay_http_requests_total{{route="{k}"}} {v}' for k, v in sorted(self.c.items()) if not k.startswith("_")]
        out += [f'omnipay_http_seconds_total{{route="{k}"}} {v:.4f}' for k, v in sorted(self.t.items())]
        w = pf.wallet.metrics
        out += [f"omnipay_wallet_{k} {v}" for k, v in sorted(w.items())]
        out += [f"omnipay_recon_open_breaks {len(pf.wallet.breaks())}", f"omnipay_ledger_problems {len(pf.ledger.audit())}",
                f"omnipay_held_payments {len(pf.sw.held_payments())}", f"omnipay_unbilled_promos {len(pf.promo.unbilled)}"]
        if pf.groq is not None:
            out += [f"omnipay_groq_calls_total {pf.groq.calls}", f"omnipay_groq_retries_total {pf.groq.retries}", f"omnipay_groq_cache_hits_total {pf.groq.cache_hits}"]
        return "\n".join(out) + "\n"


METRICS = Metrics()


@route("GET", "/metrics", auth="none", summary="Prometheus metrics (protect at the proxy in production)")
def metrics(c):
    return ("text", METRICS.text(c.pf))


@route("GET", "/openapi.json", auth="none")
def openapi(c):
    paths = {}
    for method, path, _, auth, rl, stepup, fn, summary in ROUTES:
        sec = [] if auth == "none" else [{"bearer": []}]
        paths.setdefault(path, {})[method.lower()] = {"summary": summary or fn.__name__, "security": sec, "operationId": fn.__name__,
                                                       "x-step-up-required": stepup, "x-rate-limit-class": rl, "responses": {"200": {"description": "OK"}, "4XX": {"description": "PaymentError {error, detail}"}}}
    return {"openapi": "3.0.3", "info": {"title": "OmniPay API", "version": "1.0.0"}, "paths": paths,
            "components": {"securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}}}}


def make_app(pf: Platform) -> Callable:
    def app(environ, start_response):
        t0, rid = time.time(), uuid.uuid4().hex[:12]
        pf.audit.request_id.v = rid
        method, path = environ["REQUEST_METHOD"], environ.get("PATH_INFO", "/")
        ip = (environ.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() or environ.get("REMOTE_ADDR", "?"))
        status, ctype, payload, name = 200, "application/json", b"", f"{method} ?"

        def finish(st, body, ctype="application/json"):
            hdrs = [("Content-Type", ctype), ("X-Request-Id", rid), ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer"),
                    ("Content-Security-Policy", CSP), ("Cache-Control", "no-store"), ("Strict-Transport-Security", "max-age=31536000")]
            start_response(f"{st} {'OK' if st < 400 else 'ERR'}", hdrs)
            return [body]
        try:
            if method == "GET" and not path.startswith(("/v1", "/healthz", "/metrics", "/openapi")):
                f = (WEB / ("index.html" if path in ("/", "") else path.lstrip("/"))).resolve()
                if WEB.resolve() not in f.parents or not f.is_file():
                    f = WEB / "index.html"
                return finish(200, f.read_bytes(), mimetypes.guess_type(str(f))[0] or "text/html")
            match = None
            for m, p, rx, auth, rl, stepup, fn, _ in ROUTES:
                mm = rx.match(path)
                if mm:
                    if m == method:
                        match = (p, auth, rl, stepup, fn, mm.groupdict()); break
                    status = 405
            if match is None:
                raise PaymentError("NOT_FOUND" if status != 405 else "METHOD_NOT_ALLOWED", path)
            p, auth, rl, stepup, fn, params = match
            name = f"{method} {p}"
            n = int(environ.get("CONTENT_LENGTH") or 0)
            if n > 65536:
                raise PaymentError("TOO_LARGE", "body over 64KB")
            raw = environ["wsgi.input"].read(n) if n else b""
            body = {}
            if raw and rl != "webhook":
                try:
                    body = json.loads(raw)
                    assert isinstance(body, dict)
                except (ValueError, AssertionError):
                    raise PaymentError("BAD_REQUEST", "body must be a JSON object")
            q = dict(x.split("=", 1) for x in environ.get("QUERY_STRING", "").split("&") if "=" in x)
            headers = {k[5:].replace("_", "-"): v for k, v in environ.items() if k.startswith("HTTP_")}
            token = headers.get("AUTHORIZATION", "")[7:] if headers.get("AUTHORIZATION", "").startswith("Bearer ") else None
            pf.limiter.check(f"ip:{ip}:{rl}", {"auth": 20, "webhook": 600, "agent": 30}.get(rl, pf.cfg.rate_per_min_ip))
            sess = None
            if auth != "none":
                sess = pf.auth.session(token)
                pf.limiter.check(f"u:{sess['handle']}:{rl}", {"agent": 20}.get(rl, pf.cfg.rate_per_min_user))
                if auth == "admin" and not sess["admin"]:
                    raise PaymentError("FORBIDDEN", "admin only")
                if stepup:
                    pf.auth.require_fresh(sess)
            out = fn(Ctx(pf, body, sess, params, q, headers, raw, token, ip))
            if isinstance(out, tuple) and out[0] == "text":
                return finish(200, out[1].encode(), "text/plain; version=0.0.4")
            payload = json.dumps(out, default=str).encode()
        except PaymentError as e:
            status = STATUS.get(e.code, 404 if e.code.startswith("UNKNOWN") or e.code == "NOT_FOUND" else 405 if e.code == "METHOD_NOT_ALLOWED" else 413 if e.code == "TOO_LARGE" else 422)
            payload = json.dumps({"error": e.code, "detail": str(e).split(": ", 1)[-1][:200], "request_id": rid}).encode()
        except PayPalError as e:
            status, payload = 502, json.dumps({"error": "PAYPAL_ERROR", "detail": "payment provider error", "request_id": rid}).encode()
        except KeyError as e:
            status, payload = 422, json.dumps({"error": "BAD_REQUEST", "detail": f"missing {e}", "request_id": rid}).encode()
        except Exception:                                                      # noqa: BLE001 - never leak internals
            log.exception("unhandled error rid=%s", rid)
            status, payload = 500, json.dumps({"error": "INTERNAL", "detail": "something went wrong", "request_id": rid}).encode()
        dur = time.time() - t0
        METRICS.inc(name); METRICS.inc(f"_status_{status // 100}xx"); METRICS.t[name] += dur
        log.info(json.dumps({"rid": rid, "method": method, "path": path, "status": status, "ms": round(dur * 1000, 1), "ip": ip}))
        return finish(status, payload)
    return app
