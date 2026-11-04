"""In-memory PayPal stand-in with the same interface as PayPalClient. Used by tests and by demo mode
(`simulated = True` -> the UI shows a 'Simulated PayPal' badge). Failure injection mirrors real failure modes."""
from __future__ import annotations

import itertools
import time
from decimal import Decimal as D

from app.paypal import PayPalError, fmt_amount


class FakePayPal:
    simulated = True

    def __init__(self, clock=time.time, fee_pct: str = "0.029", fee_fixed: str = "0.30"):
        self.clock, self.fee_pct, self.fee_fixed = clock, D(fee_pct), D(fee_fixed)
        self.orders, self.captures, self.payouts, self.txns = {}, {}, {}, []
        self.ids = itertools.count(1)
        self.request_ids: dict[str, dict] = {}          # idempotency: same PayPal-Request-Id -> same answer
        self.webhook_ok = True
        self.fail_next: list[PayPalError | Exception] = []   # popped by the next create_payout
        self.payout_item_status = "SUCCESS"              # what get_payout reports

    def _idem(self, key, make):
        if key and key in self.request_ids:
            return self.request_ids[key]
        r = self.request_ids[key] = make() if key else make()
        return r

    # ---- orders -------------------------------------------------------------
    def create_order(self, order_id, currency, amount, description=""):
        def make():
            oid = f"PPORD{next(self.ids):06d}"
            self.orders[oid] = {"custom_id": order_id, "currency": currency, "value": fmt_amount(currency, amount), "status": "CREATED"}
            return oid
        return self._idem(f"{order_id}-create", make)

    def approve(self, oid):                              # what the buyer does in the PayPal popup
        self.orders[oid]["status"] = "APPROVED"

    def capture_order(self, paypal_order_id, order_id):
        o = self.orders[paypal_order_id]
        if o["status"] not in ("APPROVED", "COMPLETED"):
            raise PayPalError(422, {"name": "ORDER_NOT_APPROVED"})

        def make():
            cid = f"CAP{next(self.ids):07d}"
            gross = D(o["value"])
            fee = (gross * self.fee_pct + D(self.fee_fixed)).quantize(D("0.01"))
            self.captures[cid] = {"id": cid, "status": "COMPLETED", "amount": {"currency_code": o["currency"], "value": o["value"]},
                                  "custom_id": o["custom_id"], "fee": str(fee), "order": paypal_order_id}
            self.txns.append({"transaction_info": {"transaction_id": cid, "transaction_amount": {"currency_code": o["currency"], "value": o["value"]},
                                                   "transaction_status": "S", "transaction_event_code": "T0006", "custom_field": o["custom_id"],
                                                   "transaction_initiation_date": self._iso()}})
            o["status"] = "COMPLETED"
            return {"id": paypal_order_id, "status": "COMPLETED",
                    "purchase_units": [{"payments": {"captures": [{"id": cid, "status": "COMPLETED"}]}}]}
        return self._idem(f"{order_id}-capture", make)

    def get_capture(self, capture_id):
        c = self.captures[capture_id]
        gross = D(c["amount"]["value"]); fee = D(c["fee"])
        return {"id": capture_id, "status": c["status"], "amount": c["amount"], "custom_id": c["custom_id"],
                "seller_receivable_breakdown": {"gross_amount": c["amount"], "paypal_fee": {"currency_code": c["amount"]["currency_code"], "value": c["fee"]},
                                                "net_amount": {"currency_code": c["amount"]["currency_code"], "value": f"{gross - fee:.2f}"}},
                "supplementary_data": {"related_ids": {"order_id": c["order"]}}}

    # ---- webhooks -----------------------------------------------------------
    def verify_webhook(self, headers, event):
        return self.webhook_ok and bool({k.lower() for k in headers} & {"paypal-transmission-sig"})

    # ---- payouts ------------------------------------------------------------
    def create_payout(self, payout_id, email, currency, amount, note=""):
        if self.fail_next:
            raise self.fail_next.pop(0)

        def make():
            bid = f"BATCH{next(self.ids):06d}"
            self.payouts[bid] = {"sender_batch_id": payout_id, "email": email, "currency": currency, "value": fmt_amount(currency, amount),
                                 "status": "PENDING", "txn": f"PTX{next(self.ids):07d}"}
            return {"batch_header": {"payout_batch_id": bid, "batch_status": "PENDING", "sender_batch_header": {"sender_batch_id": payout_id}}}
        return self._idem(payout_id, make)

    def settle_payout(self, bid, status="SUCCESS"):
        p = self.payouts[bid]; p["status"] = status
        if status == "SUCCESS":
            self.txns.append({"transaction_info": {"transaction_id": p["txn"], "transaction_amount": {"currency_code": p["currency"], "value": "-" + p["value"]},
                                                    "transaction_status": "S", "transaction_event_code": "T0400",
                                                    "custom_field": p["sender_batch_id"], "transaction_initiation_date": self._iso()}})

    def get_payout(self, batch_id):
        p = self.payouts[batch_id]
        st = p["status"]
        return {"batch_header": {"payout_batch_id": batch_id, "batch_status": "SUCCESS" if st == "SUCCESS" else "PENDING",
                                 "sender_batch_header": {"sender_batch_id": p["sender_batch_id"]}},
                "items": [{"payout_item_id": "ITEM" + batch_id, "transaction_id": p["txn"], "transaction_status": st,
                           "payout_item": {"sender_item_id": p["sender_batch_id"], "receiver": p["email"],
                                           "amount": {"currency": p["currency"], "value": p["value"]}},
                           "payout_item_fee": {"currency": p["currency"], "value": "0.25" if p["currency"] != "JPY" else "0"}}]}

    # ---- reporting ----------------------------------------------------------
    def search_transactions(self, start_iso, end_iso):
        return list(self.txns)

    def _iso(self):
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.clock()))
