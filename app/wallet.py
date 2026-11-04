"""Money in and out through PayPal; everything between users stays on our own ledger.

Ledger accounts used here (all double-entry, one transaction per movement):
  ext:paypal        counterpart of our PayPal balance (net of PayPal's fees)
  ext:paypal_fees   PayPal's own fees. They are a third-party cost, outside OmniPay's pricing, and are never charged to users.
  hold:payout:<id>  a payout in flight; resolved by SUCCESS (-> ext:paypal) or FAILED (-> back to the user)
Rules: never trust amounts from the client or from a webhook body: the credit is computed from PayPal's own capture
record fetched server-to-server, compared with the amount we stored when we created the order."""
from __future__ import annotations

import json
import logging
import time
import uuid
from decimal import Decimal as D
from typing import Callable, Optional

from omnipay import ASSETS, Kind, PaymentError
from app.paypal import PayPalError

log = logging.getLogger("omnipay.wallet")
PAYOUT_FINAL = ("SUCCESS", "FAILED", "RETURNED")
FAIL_STATES = {"FAILED", "DENIED", "BLOCKED", "CANCELED", "CANCELLED", "REFUNDED", "REVERSED"}
PENDING_STATES = {"PENDING", "PROCESSING", "ONHOLD", "HELD", "UNCLAIMED", "NEW"}


class Wallet:
    MAX_ORDER_USD = D("5000")
    RECON_LAG = 4 * 3600                      # PayPal reports can trail by ~3h: never judge anything newer than this

    def __init__(self, ledger, sw, paypal, audit, switches, settings, alert: Optional[Callable[[str, str, str], None]] = None):
        self.L, self.sw, self.pp, self.audit, self.switches, self.cfg = ledger, sw, paypal, audit, switches, settings
        self.alert = alert or (lambda kind, ref, detail: log.error("RECON BREAK %s %s %s", kind, ref, detail))
        self.metrics: dict[str, int] = {}

    def _m(self, k):
        self.metrics[k] = self.metrics.get(k, 0) + 1

    # ---- helpers ------------------------------------------------------------
    def _currency(self, cur: str) -> None:
        if cur not in self.cfg.paypal_currencies or cur not in ASSETS or ASSETS[cur].kind is not Kind.FIAT:
            raise PaymentError("CURRENCY_NOT_SUPPORTED", f"{cur} is not available through PayPal here")

    def _amount(self, cur: str, v) -> D:
        try:
            a = D(str(v))
        except Exception:
            raise PaymentError("BAD_AMOUNT", str(v))
        dp = 0 if cur in ("JPY", "HUF", "TWD") else 2
        if a <= 0 or a != a.quantize(D(1).scaleb(-dp)):
            raise PaymentError("BAD_AMOUNT", f"{v} {cur}")
        return a

    def _brk(self, kind: str, ref: str, detail: str) -> None:
        c = self.L.conn()
        cur = c.execute("INSERT OR IGNORE INTO recon_breaks(ts, kind, ref, detail) VALUES(?,?,?,?)", (self.L.clock(), kind, ref, detail))
        if cur.rowcount:
            self._m("recon_breaks")
            self.audit.log("system", "recon.break", ref, kind=kind, detail=detail)
            self.alert(kind, ref, detail)
        else:
            c.execute("UPDATE recon_breaks SET resolved=0, detail=? WHERE kind=? AND ref=?", (detail, kind, ref))

    # ================= money in =================
    def create_funding_order(self, handle: str, currency: str, amount, purpose: str = "fund") -> dict:
        self.switches.require("paypal_in", code="PAYPAL_IN_OFF")
        self.switches.require("global")
        self.sw._user(handle)
        self._currency(currency)
        a = self._amount(currency, amount)
        try:
            if a * self.sw.oracle.usd(currency) > self.MAX_ORDER_USD:
                raise PaymentError("ORDER_TOO_LARGE", f"max about USD {self.MAX_ORDER_USD} per order")
        except PaymentError as e:
            if e.code == "ORDER_TOO_LARGE":
                raise
        oid, now = "omp-" + uuid.uuid4().hex[:20], self.L.clock()
        c = self.L.conn()
        c.execute("INSERT INTO paypal_orders VALUES(?,?,?,?,?,?,?,?,?)", (oid, None, handle, purpose, currency, str(a), "NEW", now, now))
        try:
            ppid = self.pp.create_order(oid, currency, a)
        except PayPalError as e:
            c.execute("UPDATE paypal_orders SET status='FAILED', updated_at=? WHERE id=?", (self.L.clock(), oid))
            raise PaymentError("PAYPAL_UNAVAILABLE", str(e)[:120])
        c.execute("UPDATE paypal_orders SET paypal_order_id=?, status='CREATED', updated_at=? WHERE id=?", (ppid, self.L.clock(), oid))
        self.audit.log(handle, "fund.order_created", oid, currency=currency, amount=str(a), paypal_order=ppid)
        return {"order_id": oid, "paypal_order_id": ppid, "currency": currency, "amount": str(a), "simulated": bool(getattr(self.pp, "simulated", False))}

    def capture_order(self, handle: str, order_id: str) -> dict:
        r = self.L.conn().execute("SELECT * FROM paypal_orders WHERE id=? AND handle=?", (order_id, handle)).fetchone()
        if r is None or not r["paypal_order_id"]:
            raise PaymentError("UNKNOWN_ORDER", order_id)
        try:
            res = self.pp.capture_order(r["paypal_order_id"], order_id)
        except PayPalError as e:
            raise PaymentError("PAYPAL_CAPTURE_FAILED", str(e)[:120])
        try:
            cid = res["purchase_units"][0]["payments"]["captures"][0]["id"]
        except (KeyError, IndexError):
            raise PaymentError("PAYPAL_BAD_RESPONSE", "no capture in response")
        return self.settle_capture(cid)

    def settle_capture(self, capture_id: str) -> dict:
        """Credit the wallet for a capture, once. Safe to call from the browser return, the webhook and the recon job."""
        c = self.L.conn()
        info = self.pp.get_capture(capture_id)                      # authoritative: fetched with our credentials
        if info.get("status") != "COMPLETED":
            return {"capture_id": capture_id, "state": info.get("status", "UNKNOWN"), "credited": False}
        o = c.execute("SELECT * FROM paypal_orders WHERE id=?", (info.get("custom_id", ""),)).fetchone()
        if o is None:
            self._brk("UNKNOWN_CAPTURE", capture_id, "capture does not belong to any order of ours")
            return {"capture_id": capture_id, "state": "REJECTED", "credited": False}
        cur, gross = info["amount"]["currency_code"], D(info["amount"]["value"])
        if cur != o["currency"] or gross != D(o["amount"]):
            self._brk("AMOUNT_MISMATCH", capture_id, f"expected {o['amount']} {o['currency']} got {gross} {cur}")
            self.audit.log("system", "fund.rejected_mismatch", capture_id)
            return {"capture_id": capture_id, "state": "REJECTED", "credited": False}
        fee = D((info.get("seller_receivable_breakdown", {}).get("paypal_fee") or {}).get("value", "0"))
        net = gross - fee
        with self.L.tx() as t:
            if t.execute("SELECT 1 FROM paypal_captures WHERE capture_id=?", (capture_id,)).fetchone():
                return {"capture_id": capture_id, "state": "COMPLETED", "credited": False, "duplicate": True}
            if t.execute("SELECT 1 FROM paypal_captures WHERE order_id=?", (o["id"],)).fetchone():
                self._brk("DUPLICATE_CAPTURE", capture_id, f"order {o['id']} already credited by another capture")
                return {"capture_id": capture_id, "state": "REJECTED", "credited": False}
            t.execute("INSERT INTO paypal_captures VALUES(?,?,?,?,?,?,?,?)",
                      (capture_id, o["id"], str(gross), str(fee), str(net), cur, "CREDITED", self.L.clock()))
            legs = [("ext:paypal", cur, -net), (f"user:{o['handle']}", cur, gross)]
            if fee:
                legs.append(("ext:paypal_fees", cur, -fee))
            self.L.post(f"paypal-in:{capture_id}", legs)
            t.execute("UPDATE paypal_orders SET status='CAPTURED', updated_at=? WHERE id=?", (self.L.clock(), o["id"]))
        self.audit.log(o["handle"], "fund.credited", capture_id, currency=cur, gross=str(gross), fee=str(fee), order=o["id"])
        self._m("fund_credited")
        return {"capture_id": capture_id, "state": "COMPLETED", "credited": True, "amount": str(gross), "currency": cur}

    def reverse_capture(self, capture_id: str, why: str) -> dict:
        """Refund / dispute reversal / denial after we credited: take back what the wallet still has, flag the rest."""
        c = self.L.conn()
        with self.L.tx() as t:
            cap = t.execute("SELECT c.*, o.handle FROM paypal_captures c JOIN paypal_orders o ON o.id=c.order_id WHERE capture_id=?", (capture_id,)).fetchone()
            if cap is None or cap["state"] != "CREDITED":
                return {"reversed": False}
            gross, cur, h = D(cap["gross"]), cap["currency"], cap["handle"]
            have = self.L.balance(f"user:{h}", cur)
            take = min(have, gross)
            if take > 0:
                self.L.post(f"paypal-reverse:{capture_id}", [(f"user:{h}", cur, -take), ("ext:paypal", cur, take)])
            short = gross - take
            t.execute("UPDATE paypal_captures SET state='REVERSED' WHERE capture_id=?", (capture_id,))
            t.execute("INSERT INTO chargebacks VALUES(?,?,?,?,?,?,?)", (capture_id, h, cur, str(gross), str(take), str(short), self.L.clock()))
            if short > 0:
                t.execute("UPDATE credentials SET frozen=1 WHERE handle=?", (h,))     # spent what PayPal took back: freeze
        self.audit.log("system", "fund.reversed", capture_id, why=why, recovered=str(take), shortfall=str(short))
        if short > 0:
            self._brk("CHARGEBACK_SHORTFALL", capture_id, f"{h} owes {short} {cur}; account frozen")
        return {"reversed": True, "recovered": str(take), "shortfall": str(short)}

    # ================= money out =================
    def request_payout(self, handle: str, currency: str, amount) -> dict:
        self.switches.require("paypal_out", code="PAYPAL_OUT_OFF")
        self.switches.require("global")
        self._currency(currency)
        a = self._amount(currency, amount)
        c = self.L.conn()
        cred = c.execute("SELECT payout_email, frozen FROM credentials WHERE handle=?", (handle,)).fetchone()
        if cred is None or not cred["payout_email"]:
            raise PaymentError("NO_PAYOUT_EMAIL", "add your PayPal email first")
        if cred["frozen"]:
            raise PaymentError("ACCOUNT_FROZEN", handle)
        pid, now = "omp-pay-" + uuid.uuid4().hex[:16], self.L.clock()
        with self.L.tx() as t:
            self.L.post(f"payout-hold:{pid}", [(f"user:{handle}", currency, -a), (f"hold:payout:{pid}", currency, a)])
            t.execute("INSERT INTO payouts(id,handle,currency,amount,receiver_email,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                      (pid, handle, currency, str(a), cred["payout_email"], "HELD", now, now))
        self.audit.log(handle, "payout.requested", pid, currency=currency, amount=str(a))
        return self._submit(pid)

    def _submit(self, pid: str) -> dict:
        c = self.L.conn()
        p = c.execute("SELECT * FROM payouts WHERE id=?", (pid,)).fetchone()
        try:
            res = self.pp.create_payout(pid, p["receiver_email"], p["currency"], D(p["amount"]))
        except PayPalError as e:
            if e.definitive:
                self.apply_item_status(pid, "FAILED")                   # compensates: held money goes back to the user
                self._set_payout(pid, "FAILED", reason=f"paypal rejected: {str(e.body)[:150]}")
            else:                                              # timeout / 5xx / network: PayPal may or may not have it
                self._set_payout(pid, "UNKNOWN", reason=str(e)[:150])
                self._m("payout_unknown")
            return self.get_payout(pid)
        except Exception as e:                                 # noqa: BLE001 - any other failure is also "unknown outcome"
            self._set_payout(pid, "UNKNOWN", reason=repr(e)[:150])
            return self.get_payout(pid)
        bid = res["batch_header"]["payout_batch_id"]
        c.execute("UPDATE payouts SET state='SUBMITTED', paypal_batch_id=?, updated_at=? WHERE id=?", (bid, self.L.clock(), pid))
        self.audit.log("system", "payout.submitted", pid, batch=bid)
        return self.get_payout(pid)

    def get_payout(self, pid: str) -> dict:
        r = self.L.conn().execute("SELECT * FROM payouts WHERE id=?", (pid,)).fetchone()
        if r is None:
            raise PaymentError("UNKNOWN_PAYOUT", pid)
        return {k: r[k] for k in r.keys()}

    def _set_payout(self, pid, state, **kw):
        sets = ", ".join(f"{k}=?" for k in kw)
        self.L.conn().execute(f"UPDATE payouts SET state=?, {sets + ',' if sets else ''} updated_at=? WHERE id=?",
                              (state, *kw.values(), self.L.clock(), pid))

    def apply_item_status(self, pid: str, status: str, *, txn_id: str = "", fee: D = D(0)) -> dict:
        """Idempotent state machine. HELD/SUBMITTED/PENDING/UNKNOWN -> SUCCESS | FAILED ; SUCCESS -> RETURNED."""
        status = status.upper()
        with self.L.tx() as t:
            p = t.execute("SELECT * FROM payouts WHERE id=?", (pid,)).fetchone()
            cur, amt, h, state = p["currency"], D(p["amount"]), p["handle"], p["state"]
            hold = f"hold:payout:{pid}"
            if status == "SUCCESS" and state in ("HELD", "SUBMITTED", "PENDING", "UNKNOWN"):
                legs = [(hold, cur, -amt), ("ext:paypal", cur, amt)]
                if fee:
                    legs += [("ext:paypal", cur, fee), ("ext:paypal_fees", cur, -fee)]
                self.L.post(f"payout-success:{pid}", legs)
                t.execute("UPDATE payouts SET state='SUCCESS', fee=?, paypal_item_id=?, updated_at=? WHERE id=?",
                          (str(fee), txn_id or p["paypal_item_id"], self.L.clock(), pid))
                self.audit.log("system", "payout.success", pid, amount=str(amt), fee=str(fee))
            elif status in FAIL_STATES and state in ("HELD", "SUBMITTED", "PENDING", "UNKNOWN"):
                self.L.post(f"payout-compensate:{pid}", [(hold, cur, -amt), (f"user:{h}", cur, amt)])   # compensation: money goes back
                t.execute("UPDATE payouts SET state='FAILED', reason=?, updated_at=? WHERE id=?", (status, self.L.clock(), pid))
                self.audit.log("system", "payout.compensated", pid, status=status)
            elif status == "RETURNED" and state == "SUCCESS":
                self.L.post(f"payout-returned:{pid}", [("ext:paypal", cur, -amt), (f"user:{h}", cur, amt)])
                t.execute("UPDATE payouts SET state='RETURNED', updated_at=? WHERE id=?", (self.L.clock(), pid))
                self.audit.log("system", "payout.returned", pid, amount=str(amt))
            elif status in PENDING_STATES and state in ("HELD", "SUBMITTED", "UNKNOWN"):
                t.execute("UPDATE payouts SET state='PENDING', updated_at=? WHERE id=?", (self.L.clock(), pid))
            elif status == "SUCCESS" and state in ("FAILED",):
                self._brk("PAYOUT_SUCCESS_AFTER_FAIL", pid, "PayPal says SUCCESS but we already compensated the user")
        return self.get_payout(pid)

    def sync_payout(self, pid: str) -> dict:
        p = self.get_payout(pid)
        if not p["paypal_batch_id"]:
            return p
        d = self.pp.get_payout(p["paypal_batch_id"])
        for it in d.get("items", []):
            if it.get("payout_item", {}).get("sender_item_id") in (pid, None):
                fee = D((it.get("payout_item_fee") or {}).get("value", "0"))
                return self.apply_item_status(pid, it.get("transaction_status", "PENDING"), txn_id=it.get("transaction_id", ""), fee=fee)
        return p

    def poll_open_payouts(self) -> int:
        n = 0
        for r in self.L.conn().execute("SELECT id FROM payouts WHERE state IN ('SUBMITTED','PENDING','SUCCESS') AND paypal_batch_id IS NOT NULL").fetchall():
            before = self.get_payout(r[0])["state"]
            if self.sync_payout(r[0])["state"] != before:
                n += 1
        return n

    def retry_unknown_payouts(self) -> int:
        """UNKNOWN stays held. Re-submit with the SAME sender_batch_id / PayPal-Request-Id: PayPal does not process
        duplicates, so this is safe; a duplicate-style refusal is escalated to a human, never auto-compensated."""
        n = 0
        for (pid,) in self.L.conn().execute("SELECT id FROM payouts WHERE state='UNKNOWN'").fetchall():
            before = self.get_payout(pid)
            try:
                res = self.pp.create_payout(pid, before["receiver_email"], before["currency"], D(before["amount"]))
            except PayPalError as e:
                if e.definitive:
                    self._brk("UNKNOWN_PAYOUT_NEEDS_HUMAN", pid, f"retry refused ({e.status}); check PayPal activity manually before resolving")
                continue
            except Exception:                                  # noqa: BLE001
                continue
            self.L.conn().execute("UPDATE payouts SET state='SUBMITTED', paypal_batch_id=?, updated_at=? WHERE id=?",
                                  (res["batch_header"]["payout_batch_id"], self.L.clock(), pid))
            n += 1
        return n

    def resolve_unknown_payout(self, pid: str, *, delivered: bool, actor: str) -> dict:
        """Admin decision after checking PayPal activity by hand."""
        self.audit.log(actor, "payout.manual_resolve", pid, delivered=delivered)
        return self.apply_item_status(pid, "SUCCESS" if delivered else "FAILED")

    # ================= webhooks =================
    def handle_webhook(self, headers: dict, raw: bytes) -> dict:
        try:
            event = json.loads(raw)
        except ValueError:
            raise PaymentError("BAD_WEBHOOK", "not json")
        if not self.pp.verify_webhook(headers, event):
            self._m("webhook_bad_signature")
            self.audit.log("system", "webhook.bad_signature", str(event.get("id", "")))
            raise PaymentError("BAD_SIGNATURE", "webhook signature not verified")
        eid, etype, res = event.get("id", ""), event.get("event_type", ""), event.get("resource", {}) or {}
        c = self.L.conn()
        if not eid or c.execute("SELECT status FROM webhook_events WHERE event_id=? AND status='PROCESSED'", (eid,)).fetchone():
            return {"status": "duplicate"}
        status, detail = "PROCESSED", ""
        try:
            if etype == "PAYMENT.CAPTURE.COMPLETED":
                detail = json.dumps(self.settle_capture(res["id"]))
            elif etype in ("PAYMENT.CAPTURE.REFUNDED", "PAYMENT.CAPTURE.REVERSED", "PAYMENT.CAPTURE.DENIED"):
                cid = res.get("id") if etype != "PAYMENT.CAPTURE.REFUNDED" else (
                    next((l["href"].rsplit("/", 1)[-1] for l in res.get("links", []) if l.get("rel") == "up"), res.get("id")))
                detail = json.dumps(self.reverse_capture(cid, etype))
            elif etype.startswith("PAYMENT.PAYOUTS"):
                keys = {res.get(k) for k in ("sender_batch_id", "payout_batch_id", "sender_item_id")} - {None, ""}
                keys |= {(res.get("batch_header") or {}).get("payout_batch_id"), ((res.get("batch_header") or {}).get("sender_batch_header") or {}).get("sender_batch_id")} - {None}
                row = None
                for k in keys:
                    row = c.execute("SELECT id FROM payouts WHERE id=? OR paypal_batch_id=?", (k, k)).fetchone() or row
                if row is None:
                    status, detail = "IGNORED", "payout not ours"
                else:
                    detail = self.sync_payout(row[0])["state"]       # re-fetch the truth, don't trust the body
            else:
                status, detail = "IGNORED", etype
        except Exception as e:                                        # noqa: BLE001
            c.execute("INSERT OR REPLACE INTO webhook_events VALUES(?,?,?,?,?)", (eid, etype, self.L.clock(), "FAILED", repr(e)[:200]))
            raise                                                      # 5xx -> PayPal retries delivery
        c.execute("INSERT OR REPLACE INTO webhook_events VALUES(?,?,?,?,?)", (eid, etype, self.L.clock(), status, detail[:300]))
        return {"status": status.lower(), "detail": detail}

    # ================= reconciliation =================
    def reconcile(self, now: float | None = None, lookback: float = 2 * 86400) -> dict:
        now = now or self.L.clock()
        end, start = now - self.RECON_LAG, now - lookback
        iso = lambda ts: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))
        txns, s = [], start
        while s < end:                                              # Transaction Search allows 31-day windows at most
            e = min(end, s + 30 * 86400)
            txns += self.pp.search_transactions(iso(s), iso(e))
            s = e
        idx: dict[str, list[dict]] = {}
        for t in txns:
            ti = t.get("transaction_info", {})
            idx.setdefault(ti.get("transaction_id", ""), []).append(ti)   # ids are NOT unique in PayPal reporting
        c, seen = self.L.conn(), set()

        def brk(kind, ref, detail):
            seen.add((kind, ref)); self._brk(kind, ref, detail)

        def amount_matches(entries, cur, value):
            return any(x["transaction_amount"]["currency_code"] == cur and D(x["transaction_amount"]["value"]) == value for x in entries)

        for r in c.execute("SELECT * FROM paypal_captures WHERE state IN ('CREDITED','REVERSED') AND created_at BETWEEN ? AND ?", (start, end)):
            e = idx.get(r["capture_id"])
            if not e:
                brk("MISSING_AT_PAYPAL", r["capture_id"], "credited capture not in PayPal's report")
            elif not amount_matches(e, r["currency"], D(r["gross"])):
                brk("AMOUNT_MISMATCH", r["capture_id"], f"ledger {r['gross']} vs report {[x['transaction_amount'] for x in e]}")
        known = {r[0] for r in c.execute("SELECT capture_id FROM paypal_captures")}
        for r in c.execute("SELECT * FROM payouts WHERE state IN ('SUCCESS','RETURNED') AND updated_at BETWEEN ? AND ?", (start, end)):
            e = idx.get(r["paypal_item_id"] or "")
            known.add(r["paypal_item_id"] or "")
            if not e:
                brk("MISSING_AT_PAYPAL", r["id"], "successful payout not in PayPal's report")
            elif not amount_matches(e, r["currency"], -D(r["amount"])):
                brk("AMOUNT_MISMATCH", r["id"], f"ledger -{r['amount']} vs report {[x['transaction_amount'] for x in e]}")
        for tid, entries in idx.items():
            for ti in entries:
                if str(ti.get("custom_field", "")).startswith("omp-") and tid not in known:
                    brk("UNKNOWN_PAYPAL_TXN", tid, f"PayPal shows our reference {ti.get('custom_field')} but the ledger has no record")
        for r in c.execute("SELECT id, state, updated_at FROM payouts WHERE state IN ('HELD','SUBMITTED','PENDING','UNKNOWN') AND updated_at < ?", (now - 6 * 3600,)):
            brk("STUCK_PAYOUT", r["id"], f"{r['state']} for over 6h")
        for p in self.L.audit():
            brk("LEDGER_INTEGRITY", p[:60], p)
        derived = {"MISSING_AT_PAYPAL", "AMOUNT_MISMATCH", "UNKNOWN_PAYPAL_TXN", "STUCK_PAYOUT", "LEDGER_INTEGRITY"}
        for r in c.execute("SELECT id, kind, ref FROM recon_breaks WHERE resolved=0").fetchall():
            if r["kind"] in derived and (r["kind"], r["ref"]) not in seen:
                c.execute("UPDATE recon_breaks SET resolved=1 WHERE id=?", (r["id"],))
        open_breaks = c.execute("SELECT COUNT(*) FROM recon_breaks WHERE resolved=0").fetchone()[0]
        return {"checked_transactions": len(txns), "open_breaks": open_breaks, "window": [iso(start), iso(end)]}

    def breaks(self, include_resolved=False) -> list[dict]:
        q = "SELECT * FROM recon_breaks" + ("" if include_resolved else " WHERE resolved=0") + " ORDER BY id DESC"
        return [dict(r) for r in self.L.conn().execute(q)]
