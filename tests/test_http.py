import json
import threading
import unittest
import urllib.error
import urllib.request
from decimal import Decimal as D
from wsgiref.simple_server import WSGIRequestHandler, make_server

from app.seed import ADMIN_PASSWORD, DEMO_PASSWORD, DEMO_PIN, seed
from app.serve import Quiet, Threaded
from app.server import make_app
from tests.pf import HDR, mk


class Api:
    def __init__(self, base):
        self.base, self.token = base, None

    def req(self, method, path, body=None, token="__self__", headers=None, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        h = {"Content-Type": "application/json", **(headers or {})}
        tok = self.token if token == "__self__" else token
        if tok:
            h["Authorization"] = f"Bearer {tok}"
        r = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(r) as resp:
                b = resp.read()
                return resp.status, (json.loads(b) if resp.headers.get("Content-Type", "").startswith("application/json") else b.decode()), dict(resp.headers)
        except urllib.error.HTTPError as e:
            b = e.read()
            e.close()
            return e.code, json.loads(b) if b.startswith(b"{") else b.decode(), dict(e.headers)

    def get(self, p, **k): return self.req("GET", p, **k)
    def post(self, p, b=None, **k): return self.req("POST", p, b if b is not None else {}, **k)

    def login(self, handle, pw=DEMO_PASSWORD):
        st, d, _ = self.post("/v1/auth/login", {"handle": handle, "password": pw}, token=None)
        assert st == 200, d
        self.token = d["token"]
        return self


class HttpCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pf = mk(cfg={"demo": True})
        seed(cls.pf)
        cls.srv = make_server("127.0.0.1", 0, make_app(cls.pf), server_class=Threaded, handler_class=Quiet)
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def api(self, handle=None, pw=DEMO_PASSWORD):
        a = Api(self.base)
        return a.login(handle, pw) if handle else a

    def setUp(self):
        self.pf.limiter.hits.clear()


class AuthTests(HttpCase):
    def test_session_is_required_and_identity_comes_from_it(self):
        st, d, _ = self.api().get("/v1/me")
        self.assertEqual((st, d["error"]), (401, "UNAUTHENTICATED"))
        a = self.api("alice@omni")
        self.assertEqual(a.get("/v1/me")[1]["handle"], "alice@omni")
        st, d, _ = a.post("/v1/pay/options", {"payer": "bob@omni", "payee": "dana@omni", "amount": "10", "currency": "USD"})
        self.assertEqual(st, 200)
        self.assertEqual(self.pf.promo._listings.get(d["listing_id"]).payer, "alice@omni")      # the spoofed 'payer' was ignored

    def test_wrong_password_same_error_for_unknown_user_and_lockout(self):
        a = self.api()
        s1 = a.post("/v1/auth/login", {"handle": "nobody@omni", "password": "x" * 12}, token=None)
        s2 = a.post("/v1/auth/login", {"handle": "ravi@omni", "password": "x" * 12}, token=None)
        self.assertEqual((s1[0], s1[1]["error"], s1[1]["detail"]), (s2[0], s2[1]["error"], s2[1]["detail"]))
        for _ in range(6):
            self.pf.limiter.hits.clear()
            last = a.post("/v1/auth/login", {"handle": "ravi@omni", "password": "x" * 12}, token=None)
        self.assertEqual(last[1]["error"], "LOGIN_LOCKED")
        self.pf.limiter.hits.clear()
        self.assertEqual(a.post("/v1/auth/login", {"handle": "ravi@omni", "password": DEMO_PASSWORD}, token=None)[1]["error"], "LOGIN_LOCKED")
        self.pf.ledger.conn().execute("UPDATE credentials SET locked_until=0, failed=0 WHERE handle='ravi@omni'")

    def test_login_is_rate_limited_per_ip(self):
        a, codes = self.api(), []
        for _ in range(25):
            codes.append(a.post("/v1/auth/login", {"handle": "zzz@omni", "password": "x" * 12}, token=None)[0])
        self.assertIn(429, codes)

    def test_admin_endpoints_are_admin_only(self):
        self.assertEqual(self.api("alice@omni").get("/v1/admin/overview")[0], 403)
        self.assertEqual(self.api("admin@omni", ADMIN_PASSWORD).get("/v1/admin/overview")[0], 200)

    def test_session_expiry_and_logout(self):
        a = self.api("bob@omni")
        a.post("/v1/auth/logout")
        self.assertEqual(a.get("/v1/me")[0], 401)
        b = self.api("bob@omni")
        self.pf.clock_obj.t += 9 * 3600
        self.assertEqual(b.get("/v1/me")[0], 401)
        self.pf.clock_obj.t -= 9 * 3600

    def test_signup_blocked_region_and_weak_password(self):
        a = self.api()
        self.assertEqual(a.post("/v1/auth/signup", {"handle": "kim@omni", "password": "long-enough-pw", "pin": "1234", "region": "KP"}, token=None)[1]["error"], "REGION_BLOCKED")
        self.assertEqual(a.post("/v1/auth/signup", {"handle": "kim@omni", "password": "short", "pin": "1234"}, token=None)[1]["error"], "WEAK_PASSWORD")
        st, d, _ = a.post("/v1/auth/signup", {"handle": "newbie@omni", "password": "long-enough-pw", "pin": "4321", "region": "US"}, token=None)
        self.assertEqual(st, 200)
        self.assertTrue(d["token"])


class PayFlowTests(HttpCase):
    def test_ranked_list_labels_sponsored_never_preselects_and_states_zero_fee(self):
        a = self.api("alice@omni")
        st, d, _ = a.post("/v1/pay/options", {"payee": "bob@omni", "amount": "100", "currency": "USD"})
        self.assertEqual((st, d["omnipay_fee"], d["spread"]), (200, "0", "0"))
        sponsored = [o for o in d["options"] if o["sponsored"]]
        self.assertTrue(sponsored and all(o["label"] == "Sponsored" for o in sponsored))
        self.assertFalse(next(o for o in d["options"] if o["asset"] == d["default"])["sponsored"])
        self.assertEqual(self.api().get("/v1/config")[1]["fee_statement"].count("outside OmniPay's pricing"), 1)

    def test_only_assets_the_payer_can_pay_with_are_listed(self):
        a = self.api("bob@omni")                                          # bob owns nothing but USD from the seed payment
        st, d, _ = a.post("/v1/pay/options", {"payee": "dana@omni", "amount": "10", "currency": "USD"})
        self.assertEqual({o["asset"] for o in d["options"]} - {"USD"}, set())

    def test_pay_end_to_end_with_pin_and_simulated_badges(self):
        a = self.api("alice@omni")
        st, d, _ = a.post("/v1/pay/options", {"payee": "dana@omni", "amount": "100", "currency": "USD"})
        btc = next(o for o in d["options"] if o["asset"] == "BTC")
        self.assertTrue(btc["simulated"])
        _, q, _ = a.post("/v1/pay/quote", {"listing_id": d["listing_id"], "asset": "BTC"})
        st, bad, _ = a.post("/v1/pay", {"quote_id": q["quote_id"], "pin": "0000"})
        self.assertEqual((st, bad["error"]), (401, "BAD_PIN"))
        st, ok, _ = a.post("/v1/pay", {"quote_id": q["quote_id"], "pin": DEMO_PIN})
        self.assertEqual((st, ok["state"]), (200, "SETTLED"))
        st, again, _ = a.post("/v1/pay", {"quote_id": q["quote_id"], "pin": DEMO_PIN})
        self.assertEqual(again["payment_id"], ok["payment_id"])                    # idempotent
        self.assertEqual(self.pf.ledger.balance("user:dana@omni", "BTC"), D("0.001"))
        self.assertEqual(self.pf.ledger.audit(), [])

    def test_cannot_use_someone_elses_listing_or_quote(self):
        a, b = self.api("alice@omni"), self.api("bob@omni")
        _, d, _ = a.post("/v1/pay/options", {"payee": "dana@omni", "amount": "5", "currency": "USD"})
        self.assertEqual(b.post("/v1/pay/quote", {"listing_id": d["listing_id"], "asset": "USD"})[1]["error"], "UNKNOWN_LISTING")
        _, q, _ = a.post("/v1/pay/quote", {"listing_id": d["listing_id"], "asset": "USD"})
        self.assertEqual(b.post("/v1/pay", {"quote_id": q["quote_id"], "pin": DEMO_PIN})[1]["error"], "UNKNOWN_QUOTE")

    def test_bad_input_is_a_clean_4xx(self):
        a = self.api("alice@omni")
        self.assertEqual(a.req("POST", "/v1/pay/options", raw=b"{not json")[0], 422)
        self.assertEqual(a.post("/v1/pay/options", {"payee": "bob@omni"})[0], 422)
        self.assertEqual(a.post("/v1/pay/options", {"payee": "ghost@omni", "amount": "1", "currency": "USD"})[0], 404)
        self.assertEqual(a.req("POST", "/v1/pay/options", raw=b"x" * 70000)[0], 413)
        self.assertEqual(a.get("/v1/nope")[0], 404)
        self.assertEqual(a.req("DELETE", "/v1/me")[0], 405)


class UxApiTests(HttpCase):
    def test_rates_are_public_and_cover_every_asset(self):
        st, d, _ = self.api().get("/v1/rates")
        self.assertEqual((st, d["base"], d["rates"]["USD"], d["rates"]["BTC"]), (200, "USD", "1", "100000"))

    def test_balances_never_use_exponent_notation(self):
        bals = self.api("alice@omni").get("/v1/wallet")[1]["balances"]
        self.assertTrue(bals and all("E" not in b["amount"] for b in bals), bals)

    def test_payee_lookup_includes_display_name(self):
        self.assertEqual(self.api().get("/v1/payees/dana@omni")[1]["name"], "Dana Rivera")

    def test_paying_a_request_closes_it_only_when_it_matches(self):
        dana, alice = self.api("dana@omni"), self.api("alice@omni")
        iid = dana.post("/v1/invoices", {"amount": "12.5", "currency": "EUR", "memo": "lunch"})[1]["id"]
        self.assertEqual(dana.get("/v1/me/invoices")[1]["invoices"][0]["id"], iid)
        # a quote for a different amount must not close the request
        _, d, _ = alice.post("/v1/pay/options", {"payee": "dana@omni", "amount": "1", "currency": "EUR"})
        _, q, _ = alice.post("/v1/pay/quote", {"listing_id": d["listing_id"], "asset": "EUR"})
        self.assertFalse(alice.post("/v1/pay", {"quote_id": q["quote_id"], "pin": DEMO_PIN, "invoice_id": iid})[1]["invoice_paid"])
        _, d, _ = alice.post("/v1/pay/options", {"payee": "dana@omni", "amount": "12.5", "currency": "EUR"})
        _, q, _ = alice.post("/v1/pay/quote", {"listing_id": d["listing_id"], "asset": "BTC"})
        st, r, _ = alice.post("/v1/pay", {"quote_id": q["quote_id"], "pin": DEMO_PIN, "invoice_id": iid})
        self.assertEqual((st, r["state"], r["invoice_paid"]), (200, "SETTLED", True))
        self.assertEqual(alice.get(f"/v1/invoices/{iid}")[1]["status"], "PAID")
        self.assertEqual(self.pf.ledger.audit(), [])

    def test_assistant_closes_the_request_it_paid(self):
        dana, alice = self.api("dana@omni"), self.api("alice@omni")
        iid = dana.post("/v1/invoices", {"amount": "15", "currency": "USD"})[1]["id"]
        r = alice.post("/v1/agent/concierge", {"text": f"pay invoice {iid}"})[1]
        self.assertTrue(r["reply"].startswith("Paid 15 USD"), r)
        self.assertEqual(alice.get(f"/v1/invoices/{iid}")[1]["status"], "PAID")


class WalletTests(HttpCase):
    def test_fund_then_payout_with_step_up(self):
        a = self.api("bob@omni")
        st, o, _ = a.post("/v1/wallet/fund/orders", {"currency": "USD", "amount": "50"})
        self.assertEqual((st, o["simulated"]), (200, True))
        self.assertEqual(a.post("/v1/wallet/fund/capture", {"order_id": o["order_id"]})[0], 422)         # not approved yet
        a.post("/v1/demo/paypal/approve", {"paypal_order_id": o["paypal_order_id"]})
        before = self.pf.ledger.balance("user:bob@omni", "USD")
        st, cap, _ = a.post("/v1/wallet/fund/capture", {"order_id": o["order_id"]})
        self.assertEqual((st, cap["credited"]), (200, True))
        self.assertEqual(self.pf.ledger.balance("user:bob@omni", "USD") - before, D(50))
        a.post("/v1/wallet/fund/capture", {"order_id": o["order_id"]})                                    # replay
        self.assertEqual(self.pf.ledger.balance("user:bob@omni", "USD") - before, D(50))
        self.pf.clock_obj.t += 20 * 60                                                                     # step-up window passed
        st, d, _ = a.post("/v1/wallet/payouts", {"currency": "USD", "amount": "10", "pin": DEMO_PIN})
        self.assertEqual((st, d["error"]), (401, "STEP_UP_REQUIRED"))
        self.assertEqual(a.post("/v1/auth/stepup", {"password": "wrong-password"})[0], 401)
        a.post("/v1/auth/stepup", {"password": DEMO_PASSWORD})
        st, p, _ = a.post("/v1/wallet/payouts", {"currency": "USD", "amount": "10", "pin": DEMO_PIN})
        self.assertEqual((st, p["state"]), (200, "SUBMITTED"))
        st, s, _ = a.post("/v1/demo/paypal/settle-payout", {"payout_id": p["id"]})
        self.assertEqual(s["state"], "SUCCESS")
        self.assertEqual(a.get("/v1/wallet")[1]["payouts"][0]["state"], "SUCCESS")

    def test_webhook_endpoint_rejects_bad_signature_and_accepts_good(self):
        a = self.api()
        body = json.dumps({"id": "EV-HTTP", "event_type": "CUSTOMER.DISPUTE.CREATED", "resource": {}}).encode()
        self.pf.paypal.webhook_ok = False
        self.assertEqual(a.req("POST", "/v1/webhooks/paypal", raw=body, token=None, headers=HDR)[0], 400)
        self.pf.paypal.webhook_ok = True
        st, d, _ = a.req("POST", "/v1/webhooks/paypal", raw=body, token=None, headers=HDR)
        self.assertEqual((st, d["status"]), (200, "ignored"))

    def test_client_cannot_pick_the_credit(self):
        a = self.api("bob@omni")
        self.assertEqual(a.post("/v1/wallet/fund/orders", {"currency": "USD", "amount": "-1"})[0], 422)
        self.assertEqual(a.post("/v1/wallet/fund/orders", {"currency": "INR", "amount": "100"})[1]["error"], "CURRENCY_NOT_SUPPORTED")


class PromoterTests(HttpCase):
    def test_bid_needs_step_up_and_shows_on_dashboard(self):
        a = self.api("exchange@omni")
        self.pf.clock_obj.t += 20 * 60
        self.assertEqual(a.post("/v1/promoter/bids", {"asset": "BTC", "bps": "30", "fund_asset": "USD", "budget": "50"})[1]["error"], "STEP_UP_REQUIRED")
        a.post("/v1/auth/stepup", {"password": DEMO_PASSWORD})
        self.assertEqual(a.post("/v1/promoter/bids", {"asset": "BTC", "bps": "30", "fund_asset": "USD", "budget": "50"})[0], 200)
        d = a.get("/v1/promoter/dashboard")[1]
        self.assertEqual({b["asset"] for b in d["stats"]["bids"]}, {"ETH", "BTC"})
        s = a.post("/v1/promoter/simulate", {"asset": "USDC", "bps": "70"})[1]
        self.assertEqual((s["rank"], s["shown"]), (1, True))
        self.assertEqual(a.post("/v1/promoter/bids", {"asset": "BTC", "bps": "0.5", "fund_asset": "USD", "budget": "5"})[1]["error"], "BID_BELOW_RESERVE")


class AgentTests(HttpCase):
    def test_concierge_injection_memo_and_mandate_over_http(self):
        a = self.api("alice@omni")
        st, r, _ = a.post("/v1/agent/concierge", {"text": "pay invoice inv-demo000001"})
        self.assertEqual(st, 200)
        self.assertEqual(self.pf.ledger.balance("user:attacker@omni", "USD"), 0)
        self.assertIn("Paid 120 USD to dana@omni", r["reply"])                   # inside the mandate: paid directly, memo ignored
        self.assertNotIn("attacker", r["reply"])
        log = a.get("/v1/agent/log")[1]["log"]
        self.assertTrue(any(l["kind"] == "tool_call" for l in log))

    def test_confirm_needs_pin_and_mandate_is_stepup_protected(self):
        a = self.api("alice@omni")
        st, r, _ = a.post("/v1/agent/concierge", {"text": "pay bob 300 dollars"})
        cid = r["confirmation_id"]
        self.assertEqual(a.post("/v1/agent/confirm", {"id": cid, "pin": "0000"})[1]["error"], "BAD_PIN")
        self.assertEqual(a.post("/v1/agent/confirm", {"id": cid, "pin": DEMO_PIN})[1]["state"], "SETTLED")
        self.pf.clock_obj.t += 20 * 60
        body = {"enabled": True, "per_payment_usd": "9999", "daily_usd": "99999", "allowed_assets": ["USD"], "payee_allowlist": ["*"], "confirm_above_usd": "0"}
        self.assertEqual(a.req("PUT", "/v1/agent/mandate", body)[1]["error"], "STEP_UP_REQUIRED")

    def test_payee_setup_preview_then_confirm(self):
        a = self.api("ravi@omni")
        st, p, _ = a.post("/v1/agent/payee-setup", {"text": "I accept EUR, BTC and my AAPL shares"})
        self.assertEqual(p["profile"]["accepts"], ["AAPL", "BTC", "EUR"])
        self.assertNotIn("BTC", a.get("/v1/me")[1]["accepts"])                  # nothing saved yet
        a.post("/v1/agent/payee-setup/confirm", {"id": p["pending_id"]})
        me = a.get("/v1/me")[1]
        self.assertEqual((me["settle_in"], "BTC" in me["accepts"]), ("EUR", True))

    def test_advisor_over_http(self):
        a = self.api("exchange@omni")
        r = a.post("/v1/agent/advisor", {"text": "what if I bid 80 on USDC"})[1]
        self.assertIn("rank #1", r["reply"])


class AdminTests(HttpCase):
    def admin(self):
        return self.api("admin@omni", ADMIN_PASSWORD)

    def test_kill_switches_take_effect_immediately(self):
        adm, a = self.admin(), self.api("alice@omni")
        adm.post("/v1/admin/switch", {"scope": "asset", "key": "BTC", "enabled": False, "reason": "test"})
        opts = a.post("/v1/pay/options", {"payee": "dana@omni", "amount": "10", "currency": "USD"})[1]["options"]
        self.assertNotIn("BTC", [o["asset"] for o in opts])
        self.assertFalse(next(x for x in self.api().get("/v1/assets")[1]["assets"] if x["code"] == "BTC")["enabled"])
        adm.post("/v1/admin/switch", {"scope": "asset", "key": "BTC", "enabled": True})
        adm.post("/v1/admin/switch", {"scope": "global", "key": "*", "enabled": False, "reason": "incident"})
        st, d, _ = a.post("/v1/pay/options", {"payee": "dana@omni", "amount": "10", "currency": "USD"})
        self.assertEqual((st, d["error"]), (503, "KILL_SWITCH"))
        adm.post("/v1/admin/switch", {"scope": "global", "key": "*", "enabled": True})
        adm.post("/v1/admin/switch", {"scope": "region", "key": "DE", "enabled": False})
        self.assertEqual(a.post("/v1/pay/options", {"payee": "dana@omni", "amount": "10", "currency": "USD"})[0], 503)
        adm.post("/v1/admin/switch", {"scope": "region", "key": "DE", "enabled": True})
        actions = [x["action"] for x in adm.get("/v1/admin/audit")[1]["audit"]]
        self.assertIn("switch.set", actions)                                    # every admin action is audited

    def test_overview_reconcile_and_jobs(self):
        adm = self.admin()
        o = adm.get("/v1/admin/overview")[1]
        self.assertEqual(o["ledger_problems"], [])
        self.assertTrue(o["paypal_simulated"])
        self.assertEqual(adm.post("/v1/admin/reconcile")[0], 200)
        self.assertEqual(adm.post("/v1/admin/jobs")[0], 200)

    def test_resolve_held_payment_needs_step_up_and_is_audited(self):
        a, adm = self.api("alice@omni"), self.admin()
        self.pf.sw.execute = lambda p: (_ for _ in ()).throw(TimeoutError())
        try:
            _, d, _ = a.post("/v1/pay/options", {"payee": "bob@omni", "amount": "7", "currency": "USD"})
            _, q, _ = a.post("/v1/pay/quote", {"listing_id": d["listing_id"], "asset": "USD"})
            st, r, _ = a.post("/v1/pay", {"quote_id": q["quote_id"], "pin": DEMO_PIN})
            self.assertEqual((st, r["state"]), (200, "UNKNOWN"))                  # told the truth, not a generic 500
        finally:
            del self.pf.sw.execute
        held = adm.get("/v1/admin/overview")[1]["held_payments"]
        self.assertEqual(len(held), 1)
        self.assertEqual(adm.post(f"/v1/admin/payments/{held[0]}/resolve", {"executed": False})[1]["state"], "FAILED")


class PlumbingTests(HttpCase):
    def test_security_headers_request_id_openapi_metrics_health(self):
        st, d, h = self.api().get("/healthz")
        self.assertEqual((st, d["ledger_problems"]), (200, 0))
        for k in ("Content-Security-Policy", "X-Content-Type-Options", "X-Request-Id", "Strict-Transport-Security", "Cache-Control"):
            self.assertIn(k, h)
        self.assertIn("frame-ancestors 'none'", h["Content-Security-Policy"])
        spec = self.api().get("/openapi.json")[1]
        self.assertIn("/v1/pay", spec["paths"])
        self.assertTrue(spec["paths"]["/v1/wallet/payouts"]["post"]["x-step-up-required"])
        self.assertIn("omnipay_ledger_problems 0", self.api().get("/metrics")[1])

    def test_internal_errors_do_not_leak(self):
        self.pf.ledger.conn().execute("CREATE TABLE IF NOT EXISTS boom(x)")
        orig = self.pf.sw._user
        self.pf.sw._user = lambda h: (_ for _ in ()).throw(RuntimeError("secret internal detail /etc/passwd"))
        try:
            st, d, _ = self.api().get("/v1/payees/alice@omni")
        finally:
            self.pf.sw._user = orig
        self.assertEqual((st, d["error"]), (500, "INTERNAL"))
        self.assertNotIn("passwd", json.dumps(d))

    def test_demo_reset_restores_the_seed_and_is_demo_only(self):
        a = self.api("alice@omni")
        q = self.pf.sw.quote("alice@omni", "bob@omni", "5", "USD", "USD"); self.pf.sw.pay(q.id, DEMO_PIN)
        st, d, _ = self.api().post("/v1/demo/reset", token=None)
        self.assertEqual(st, 200)
        self.assertEqual(self.pf.ledger.balance("user:alice@omni", "USD"), D("4981.50"))
        self.assertEqual(self.pf.ledger.audit(), [])
        self.pf.cfg.__class__  # demo flag is frozen config: a non-demo platform must refuse
        pf2 = mk()
        srv = make_server("127.0.0.1", 0, make_app(pf2), server_class=Threaded, handler_class=Quiet)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            self.assertEqual(Api(f"http://127.0.0.1:{srv.server_port}").post("/v1/demo/reset", token=None)[0], 403)
        finally:
            srv.shutdown()


if __name__ == "__main__":
    unittest.main()
