import json
import unittest
from decimal import Decimal as D

from omnipay import PaymentError
from app.config import Settings
from app.paypal import PayPalClient, PayPalError, fmt_amount
from app.paypal_fake import FakePayPal
from tests.pf import HDR, Clock, fund, mk, seed_users, webhook


class ConfigTests(unittest.TestCase):
    def test_live_is_refused_without_the_second_lock(self):
        with self.assertRaises(ValueError):
            Settings.from_env({"PAYPAL_ENV": "live"})
        with self.assertRaises(ValueError):
            Settings.from_env({"PAYPAL_ENV": "prod"})
        s = Settings.from_env({"PAYPAL_ENV": "sandbox"})
        self.assertEqual(s.paypal_base, "https://api-m.sandbox.paypal.com")

    def test_amount_formatting_follows_minor_units(self):
        self.assertEqual(fmt_amount("USD", D("10")), "10.00")
        self.assertEqual(fmt_amount("JPY", D("1500")), "1500")
        with self.assertRaises(ValueError):
            fmt_amount("USD", D("10.005"))
        with self.assertRaises(ValueError):
            fmt_amount("JPY", D("1.5"))


class ClientTests(unittest.TestCase):
    """Request shapes and retry behaviour against a scripted HTTP layer (no network)."""

    def setUp(self):
        self.calls, self.script, self.sleeps = [], [], []
        self.clock = Clock()

        def http(method, url, headers, body, timeout):
            self.calls.append((method, url, headers, json.loads(body) if body and headers.get("Content-Type") == "application/json" else body))
            if url.endswith("/v1/oauth2/token"):
                return 200, {}, json.dumps({"access_token": f"tok{len(self.calls)}", "expires_in": 3000}).encode()
            r = self.script.pop(0)
            if isinstance(r, Exception):
                raise r
            return r[0], r[2] if len(r) > 2 else {}, json.dumps(r[1]).encode()
        cfg = Settings(paypal_client_id="id", paypal_secret="sec", paypal_webhook_id="WH1")
        self.c = PayPalClient(cfg, http=http, clock=self.clock, sleep=self.sleeps.append)

    def api_calls(self):
        return [c for c in self.calls if not c[1].endswith("/oauth2/token")]

    def test_create_order_sets_request_id_custom_id_and_server_amount(self):
        self.script.append((201, {"id": "ORD1"}))
        self.assertEqual(self.c.create_order("omp-1", "USD", D("25")), "ORD1")
        m, url, h, body = self.api_calls()[0]
        self.assertEqual((m, url), ("POST", "https://api-m.sandbox.paypal.com/v2/checkout/orders"))
        self.assertEqual(h["PayPal-Request-Id"], "omp-1-create")
        pu = body["purchase_units"][0]
        self.assertEqual((body["intent"], pu["custom_id"], pu["amount"]), ("CAPTURE", "omp-1", {"currency_code": "USD", "value": "25.00"}))

    def test_token_is_cached_then_refreshed_on_401(self):
        self.script += [(200, {"a": 1}), (200, {"a": 2})]
        self.c.get_capture("C1"); self.c.get_capture("C2")
        self.assertEqual(sum(1 for c in self.calls if c[1].endswith("/oauth2/token")), 1)
        self.script += [(401, {}), (200, {"ok": 1})]
        self.c.get_capture("C3")
        self.assertEqual(sum(1 for c in self.calls if c[1].endswith("/oauth2/token")), 2)

    def test_429_and_5xx_back_off_and_reuse_the_same_request_id(self):
        self.script += [(429, {}, {"Retry-After": "3"}), (503, {}), (201, {"batch_header": {"payout_batch_id": "B1"}})]
        self.c.create_payout("omp-pay-1", "a@b.com", "USD", D("10"))
        ids = {c[2]["PayPal-Request-Id"] for c in self.api_calls()}
        self.assertEqual(ids, {"omp-pay-1"})
        self.assertEqual(self.sleeps[0], 3.0)
        self.assertEqual(len(self.sleeps), 2)

    def test_gives_up_after_retries_and_is_not_definitive(self):
        self.script += [(500, {})] * 3
        with self.assertRaises(PayPalError) as cm:
            self.c.create_payout("omp-pay-2", "a@b.com", "USD", D("10"))
        self.assertFalse(cm.exception.definitive)

    def test_network_timeout_is_an_unknown_outcome_not_a_rejection(self):
        self.script += [TimeoutError()] * 3
        with self.assertRaises(PayPalError) as cm:
            self.c.create_payout("omp-pay-3", "a@b.com", "USD", D("10"))
        self.assertEqual(cm.exception.status, 0)
        self.assertFalse(cm.exception.definitive)

    def test_4xx_is_definitive(self):
        self.script.append((422, {"name": "INVALID_RESOURCE_ID"}))
        with self.assertRaises(PayPalError) as cm:
            self.c.capture_order("X", "omp-1")
        self.assertTrue(cm.exception.definitive)

    def test_verify_webhook_sends_the_documented_fields(self):
        self.script.append((200, {"verification_status": "SUCCESS"}))
        ok = self.c.verify_webhook({"Paypal-Transmission-Id": "T", "PAYPAL-TRANSMISSION-TIME": "TM", "PAYPAL-TRANSMISSION-SIG": "S",
                                    "PAYPAL-CERT-URL": "https://c", "PAYPAL-AUTH-ALGO": "SHA256withRSA"}, {"id": "EV"})
        self.assertTrue(ok)
        body = self.api_calls()[0][3]
        self.assertEqual(set(body), {"auth_algo", "cert_url", "transmission_id", "transmission_sig", "transmission_time", "webhook_id", "webhook_event"})
        self.assertEqual((body["webhook_id"], body["transmission_id"]), ("WH1", "T"))
        self.script.append((200, {"verification_status": "FAILURE"}))
        self.assertFalse(self.c.verify_webhook({}, {"id": "EV"}))

    def test_payout_payload_and_search_pagination(self):
        self.script.append((201, {"batch_header": {"payout_batch_id": "B1"}}))
        self.c.create_payout("omp-pay-9", "r@x.com", "EUR", D("12.5"))
        body = self.api_calls()[0][3]
        self.assertEqual(body["sender_batch_header"]["sender_batch_id"], "omp-pay-9")
        it = body["items"][0]
        self.assertEqual((it["recipient_type"], it["receiver"], it["sender_item_id"], it["amount"]),
                         ("EMAIL", "r@x.com", "omp-pay-9", {"currency": "EUR", "value": "12.50"}))
        self.script += [(200, {"transaction_details": [{"a": 1}], "total_pages": 2}), (200, {"transaction_details": [{"a": 2}], "total_pages": 2})]
        self.assertEqual(len(self.c.search_transactions("2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z")), 2)


class FundingTests(unittest.TestCase):
    def setUp(self):
        self.pf = seed_users(mk())

    def test_credit_happens_once_after_a_verified_capture_with_fee_on_a_third_party_account(self):
        o, res = fund(self.pf, "alice@omni", "USD", "100")
        self.assertTrue(res["credited"])
        L = self.pf.ledger
        self.assertEqual(L.balance("user:alice@omni", "USD"), D(100))           # user gets the full amount
        fee = D(self.pf.paypal.captures[res["capture_id"]]["fee"])
        self.assertEqual(L.balance("ext:paypal_fees", "USD"), -fee)             # PayPal's fee sits outside OmniPay pricing
        self.assertEqual(L.balance("ext:paypal", "USD"), -(D(100) - fee))
        self.assertEqual(L.audit(), [])
        again = self.pf.wallet.settle_capture(res["capture_id"])
        self.assertEqual((again["credited"], again.get("duplicate")), (False, True))
        self.assertEqual(L.balance("user:alice@omni", "USD"), D(100))

    def test_double_capture_call_is_idempotent_end_to_end(self):
        o, _ = fund(self.pf, "alice@omni", "USD", "40")
        self.pf.wallet.capture_order("alice@omni", o["order_id"])
        self.assertEqual(self.pf.ledger.balance("user:alice@omni", "USD"), D(40))

    def test_unapproved_order_cannot_be_captured_or_credited(self):
        o = self.pf.wallet.create_funding_order("alice@omni", "USD", "40")
        with self.assertRaises(PaymentError):
            self.pf.wallet.capture_order("alice@omni", o["order_id"])
        self.assertEqual(self.pf.ledger.balance("user:alice@omni", "USD"), D(0))

    def test_someone_elses_order_cannot_be_captured(self):
        o = self.pf.wallet.create_funding_order("alice@omni", "USD", "40")
        with self.assertRaises(PaymentError) as cm:
            self.pf.wallet.capture_order("bob@omni", o["order_id"])
        self.assertEqual(cm.exception.code, "UNKNOWN_ORDER")

    def test_amount_mismatch_is_refused_and_flagged(self):
        o = self.pf.wallet.create_funding_order("alice@omni", "USD", "10")
        self.pf.paypal.approve(o["paypal_order_id"])
        self.pf.paypal.orders[o["paypal_order_id"]]["value"] = "1000.00"        # tampered/mismatched at PayPal's side
        res = self.pf.wallet.capture_order("alice@omni", o["order_id"])
        self.assertEqual((res["credited"], res["state"]), (False, "REJECTED"))
        self.assertEqual(self.pf.ledger.balance("user:alice@omni", "USD"), D(0))
        self.assertEqual([b["kind"] for b in self.pf.wallet.breaks()], ["AMOUNT_MISMATCH"])

    def test_pending_capture_is_not_credited_until_completed(self):
        o = self.pf.wallet.create_funding_order("alice@omni", "USD", "10")
        self.pf.paypal.approve(o["paypal_order_id"])
        self.pf.paypal.capture_order(o["paypal_order_id"], o["order_id"])
        cid = next(iter(self.pf.paypal.captures)); self.pf.paypal.captures[cid]["status"] = "PENDING"
        self.assertFalse(self.pf.wallet.settle_capture(cid)["credited"])
        self.assertEqual(self.pf.ledger.balance("user:alice@omni", "USD"), D(0))
        self.pf.paypal.captures[cid]["status"] = "COMPLETED"
        webhook(self.pf, "PAYMENT.CAPTURE.COMPLETED", {"id": cid}, "EV-1")
        self.assertEqual(self.pf.ledger.balance("user:alice@omni", "USD"), D(10))

    def test_client_cannot_choose_the_credited_amount(self):
        with self.assertRaises(PaymentError):
            self.pf.wallet.create_funding_order("alice@omni", "USD", "-5")
        with self.assertRaises(PaymentError):
            self.pf.wallet.create_funding_order("alice@omni", "USD", "1.999")
        with self.assertRaises(PaymentError) as cm:
            self.pf.wallet.create_funding_order("alice@omni", "USD", "999999")
        self.assertEqual(cm.exception.code, "ORDER_TOO_LARGE")
        with self.assertRaises(PaymentError) as cm:
            self.pf.wallet.create_funding_order("alice@omni", "INR", "100")        # not a PayPal currency here
        self.assertEqual(cm.exception.code, "CURRENCY_NOT_SUPPORTED")

    def test_paypal_outage_while_creating_an_order_fails_cleanly(self):
        self.pf.paypal.create_order = lambda *a, **k: (_ for _ in ()).throw(PayPalError(503, "down", retryable=True))
        with self.assertRaises(PaymentError) as cm:
            self.pf.wallet.create_funding_order("alice@omni", "USD", "10")
        self.assertEqual(cm.exception.code, "PAYPAL_UNAVAILABLE")

    def test_kill_switch_for_money_in(self):
        self.pf.switches.set("paypal_in", "*", False, "admin", "incident")
        with self.assertRaises(PaymentError) as cm:
            self.pf.wallet.create_funding_order("alice@omni", "USD", "10")
        self.assertEqual(cm.exception.code, "PAYPAL_IN_OFF")


class WebhookTests(unittest.TestCase):
    def setUp(self):
        self.pf = seed_users(mk())

    def test_bad_signature_is_rejected_and_credits_nothing(self):
        o = self.pf.wallet.create_funding_order("alice@omni", "USD", "10")
        self.pf.paypal.approve(o["paypal_order_id"]); self.pf.paypal.capture_order(o["paypal_order_id"], o["order_id"])
        cid = next(iter(self.pf.paypal.captures))
        self.pf.paypal.webhook_ok = False
        with self.assertRaises(PaymentError) as cm:
            webhook(self.pf, "PAYMENT.CAPTURE.COMPLETED", {"id": cid}, "EV-X")
        self.assertEqual(cm.exception.code, "BAD_SIGNATURE")
        self.assertEqual(self.pf.ledger.balance("user:alice@omni", "USD"), D(0))

    def test_webhook_credit_is_idempotent_across_redelivery_and_browser_capture(self):
        o = self.pf.wallet.create_funding_order("alice@omni", "USD", "10")
        self.pf.paypal.approve(o["paypal_order_id"]); self.pf.paypal.capture_order(o["paypal_order_id"], o["order_id"])
        cid = next(iter(self.pf.paypal.captures))
        r1 = webhook(self.pf, "PAYMENT.CAPTURE.COMPLETED", {"id": cid}, "EV-1")
        r2 = webhook(self.pf, "PAYMENT.CAPTURE.COMPLETED", {"id": cid}, "EV-1")          # same event again
        r3 = webhook(self.pf, "PAYMENT.CAPTURE.COMPLETED", {"id": cid}, "EV-2")          # new event id, same capture
        self.pf.wallet.capture_order("alice@omni", o["order_id"])                         # and the browser return
        self.assertEqual((r1["status"], r2["status"]), ("processed", "duplicate"))
        self.assertEqual(self.pf.ledger.balance("user:alice@omni", "USD"), D(10))

    def test_webhook_body_amount_is_ignored(self):
        o = self.pf.wallet.create_funding_order("alice@omni", "USD", "10")
        self.pf.paypal.approve(o["paypal_order_id"]); self.pf.paypal.capture_order(o["paypal_order_id"], o["order_id"])
        cid = next(iter(self.pf.paypal.captures))
        webhook(self.pf, "PAYMENT.CAPTURE.COMPLETED", {"id": cid, "amount": {"value": "9999.00", "currency_code": "USD"}}, "EV-9")
        self.assertEqual(self.pf.ledger.balance("user:alice@omni", "USD"), D(10))

    def test_unknown_event_types_are_ignored_not_failed(self):
        self.assertEqual(webhook(self.pf, "CUSTOMER.DISPUTE.CREATED", {}, "EV-Z")["status"], "ignored")

    def test_a_failure_inside_processing_is_recorded_and_surfaces_for_retry(self):
        with self.assertRaises(Exception):
            webhook(self.pf, "PAYMENT.CAPTURE.COMPLETED", {"id": "NOPE"}, "EV-F")
        st = self.pf.ledger.conn().execute("SELECT status FROM webhook_events WHERE event_id='EV-F'").fetchone()[0]
        self.assertEqual(st, "FAILED")

    def test_refund_after_spending_takes_back_what_is_left_and_freezes(self):
        o, res = fund(self.pf, "alice@omni", "USD", "100")
        self.pf.sw.deposit  # noqa
        q = self.pf.sw.quote("alice@omni", "bob@omni", "70", "USD", "USD"); self.pf.sw.pay(q.id, "1234")
        out = webhook(self.pf, "PAYMENT.CAPTURE.REVERSED", {"id": res["capture_id"]}, "EV-R")
        self.assertEqual(self.pf.ledger.balance("user:alice@omni", "USD"), D(0))
        row = self.pf.ledger.conn().execute("SELECT shortfall FROM chargebacks").fetchone()
        self.assertEqual(D(row[0]), D(70))
        self.assertEqual(self.pf.ledger.conn().execute("SELECT frozen FROM credentials WHERE handle='alice@omni'").fetchone()[0], 1)
        self.assertIn("CHARGEBACK_SHORTFALL", [b["kind"] for b in self.pf.wallet.breaks()])
        q2 = self.pf.sw.quote("alice@omni", "bob@omni", "1", "USD", "USD")
        with self.assertRaises(PaymentError) as cm:
            self.pf.sw.pay(q2.id, "1234")
        self.assertEqual(cm.exception.code, "ACCOUNT_FROZEN")
        self.assertEqual(self.pf.ledger.audit(), [])


class PayoutTests(unittest.TestCase):
    def setUp(self):
        self.pf = seed_users(mk())
        fund(self.pf, "alice@omni", "USD", "200")

    def bal(self):
        return self.pf.ledger.balance("user:alice@omni", "USD")

    def test_happy_path_hold_then_success_with_fee_on_third_party_account(self):
        p = self.pf.wallet.request_payout("alice@omni", "USD", "80")
        self.assertEqual((p["state"], self.bal()), ("SUBMITTED", D(120)))
        self.assertEqual(self.pf.ledger.balance(f"hold:payout:{p['id']}", "USD"), D(80))
        self.pf.paypal.settle_payout(p["paypal_batch_id"])
        webhook(self.pf, "PAYMENT.PAYOUTS-ITEM.SUCCEEDED", {"payout_batch_id": p["paypal_batch_id"], "sender_item_id": p["id"]}, "EV-P1")
        self.assertEqual(self.pf.wallet.get_payout(p["id"])["state"], "SUCCESS")
        self.assertEqual(self.pf.ledger.balance(f"hold:payout:{p['id']}", "USD"), 0)
        self.assertEqual(self.bal(), D(120))
        self.assertEqual(self.pf.ledger.audit(), [])
        webhook(self.pf, "PAYMENT.PAYOUTS-ITEM.SUCCEEDED", {"payout_batch_id": p["paypal_batch_id"]}, "EV-P2")   # redelivery
        self.assertEqual(self.bal(), D(120))

    def test_failed_payout_is_compensated(self):
        p = self.pf.wallet.request_payout("alice@omni", "USD", "80")
        self.pf.paypal.settle_payout(p["paypal_batch_id"], "FAILED")
        webhook(self.pf, "PAYMENT.PAYOUTS-ITEM.FAILED", {"payout_batch_id": p["paypal_batch_id"]}, "EV-P3")
        self.assertEqual((self.pf.wallet.get_payout(p["id"])["state"], self.bal()), ("FAILED", D(200)))

    def test_blocked_and_returned(self):
        p = self.pf.wallet.request_payout("alice@omni", "USD", "50")
        self.pf.paypal.settle_payout(p["paypal_batch_id"], "SUCCESS"); self.pf.wallet.sync_payout(p["id"])
        self.assertEqual(self.bal(), D(150))
        self.pf.paypal.settle_payout(p["paypal_batch_id"], "RETURNED")
        self.pf.wallet.sync_payout(p["id"])
        self.assertEqual((self.pf.wallet.get_payout(p["id"])["state"], self.bal()), ("RETURNED", D(200)))
        self.assertEqual(self.pf.ledger.audit(), [])
        p2 = self.pf.wallet.request_payout("alice@omni", "USD", "10")
        self.pf.paypal.settle_payout(p2["paypal_batch_id"], "BLOCKED"); self.pf.wallet.sync_payout(p2["id"])
        self.assertEqual(self.bal(), D(200))

    def test_unclaimed_stays_pending_and_funds_stay_held(self):
        p = self.pf.wallet.request_payout("alice@omni", "USD", "30")
        self.pf.paypal.settle_payout(p["paypal_batch_id"], "UNCLAIMED"); self.pf.wallet.sync_payout(p["id"])
        self.assertEqual((self.pf.wallet.get_payout(p["id"])["state"], self.bal()), ("PENDING", D(170)))

    def test_timeout_leaves_it_held_as_unknown_then_retry_is_idempotent(self):
        self.pf.paypal.fail_next = [PayPalError(0, "timeout", retryable=True)]
        p = self.pf.wallet.request_payout("alice@omni", "USD", "60")
        self.assertEqual((p["state"], self.bal()), ("UNKNOWN", D(140)))                  # NOT refunded: PayPal may have it
        self.assertEqual(self.pf.ledger.balance(f"hold:payout:{p['id']}", "USD"), D(60))
        self.assertEqual(self.pf.wallet.retry_unknown_payouts(), 1)
        p = self.pf.wallet.get_payout(p["id"])
        self.assertEqual(p["state"], "SUBMITTED")
        self.assertEqual(len(self.pf.paypal.payouts), 1)                                 # one payout at PayPal, not two

    def test_definitive_rejection_compensates_immediately(self):
        self.pf.paypal.fail_next = [PayPalError(422, {"name": "RECEIVER_UNREGISTERED"})]
        p = self.pf.wallet.request_payout("alice@omni", "USD", "60")
        self.assertEqual((p["state"], self.bal()), ("FAILED", D(200)))

    def test_refused_retry_escalates_to_a_human_and_never_auto_compensates(self):
        self.pf.paypal.fail_next = [PayPalError(0, "t"), PayPalError(400, {"name": "DUPLICATE"})]
        p = self.pf.wallet.request_payout("alice@omni", "USD", "60")
        self.pf.wallet.retry_unknown_payouts()
        self.assertEqual(self.pf.wallet.get_payout(p["id"])["state"], "UNKNOWN")
        self.assertIn("UNKNOWN_PAYOUT_NEEDS_HUMAN", [b["kind"] for b in self.pf.wallet.breaks()])
        done = self.pf.wallet.resolve_unknown_payout(p["id"], delivered=False, actor="admin@omni")
        self.assertEqual((done["state"], self.bal()), ("FAILED", D(200)))

    def test_cannot_pay_out_more_than_the_wallet_holds_or_without_an_email(self):
        with self.assertRaises(PaymentError) as cm:
            self.pf.wallet.request_payout("alice@omni", "USD", "5000")
        self.assertEqual(cm.exception.code, "INSUFFICIENT_FUNDS")
        with self.assertRaises(PaymentError) as cm:
            self.pf.wallet.request_payout("bob@omni", "USD", "1")
        self.assertEqual(cm.exception.code, "NO_PAYOUT_EMAIL")
        self.assertEqual(self.bal(), D(200))

    def test_payout_kill_switch(self):
        self.pf.switches.set("paypal_out", "*", False, "admin")
        with self.assertRaises(PaymentError) as cm:
            self.pf.wallet.request_payout("alice@omni", "USD", "1")
        self.assertEqual(cm.exception.code, "PAYPAL_OUT_OFF")


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.pf = seed_users(mk())
        self.clock = self.pf.clock_obj

    def run_recon(self):
        self.clock.t += 6 * 3600                                  # let the reporting lag pass
        return self.pf.wallet.reconcile()

    def test_clean_books_have_no_breaks(self):
        fund(self.pf, "alice@omni", "USD", "100")
        p = self.pf.wallet.request_payout("alice@omni", "USD", "40")
        self.pf.paypal.settle_payout(p["paypal_batch_id"]); self.pf.wallet.sync_payout(p["id"])
        self.assertEqual(self.run_recon()["open_breaks"], 0)

    def test_detects_a_credit_that_paypal_does_not_show(self):
        _, res = fund(self.pf, "alice@omni", "USD", "100")
        self.pf.paypal.txns.clear()
        out = self.run_recon()
        self.assertEqual(out["open_breaks"], 1)
        self.assertEqual(self.pf.wallet.breaks()[0]["kind"], "MISSING_AT_PAYPAL")

    def test_detects_amount_difference_and_unknown_paypal_transactions(self):
        _, res = fund(self.pf, "alice@omni", "USD", "100")
        self.pf.paypal.txns[0]["transaction_info"]["transaction_amount"]["value"] = "99.00"
        self.pf.paypal.txns.append({"transaction_info": {"transaction_id": "GHOST1", "custom_field": "omp-ghost", "transaction_status": "S",
                                                         "transaction_amount": {"currency_code": "USD", "value": "5.00"}}})
        self.run_recon()
        self.assertEqual(sorted(b["kind"] for b in self.pf.wallet.breaks()), ["AMOUNT_MISMATCH", "UNKNOWN_PAYPAL_TXN"])

    def test_breaks_alert_once_and_resolve_when_fixed(self):
        alerts = []
        self.pf.wallet.alert = lambda *a: alerts.append(a)
        _, res = fund(self.pf, "alice@omni", "USD", "100")
        saved = list(self.pf.paypal.txns); self.pf.paypal.txns.clear()
        self.run_recon(); self.run_recon()
        self.assertEqual(len(alerts), 1)
        self.pf.paypal.txns.extend(saved)
        self.assertEqual(self.run_recon()["open_breaks"], 0)

    def test_stuck_payouts_and_ledger_tampering_are_flagged(self):
        fund(self.pf, "alice@omni", "USD", "100")
        self.pf.paypal.fail_next = [PayPalError(0, "t")]
        self.pf.wallet.request_payout("alice@omni", "USD", "10")
        self.pf.ledger.conn().execute("UPDATE balances SET atoms=atoms+1 WHERE account='user:bob@omni' OR account='user:alice@omni'")
        self.clock.t += 7 * 3600
        self.pf.wallet.reconcile()
        kinds = {b["kind"] for b in self.pf.wallet.breaks()}
        self.assertTrue({"STUCK_PAYOUT", "LEDGER_INTEGRITY"} <= kinds)

    def test_recent_activity_inside_the_reporting_lag_is_not_judged(self):
        fund(self.pf, "alice@omni", "USD", "100")
        self.pf.paypal.txns.clear()
        self.assertEqual(self.pf.wallet.reconcile()["open_breaks"], 0)


if __name__ == "__main__":
    unittest.main()
