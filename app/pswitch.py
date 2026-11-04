"""PersistentSwitch: omnipay.Switch with every piece of state in the database.

Reused unchanged from the engine: price() (routing, policy, mid-market maths), the asset table, the oracle.
Rewritten for persistence: register/accept/decline, quote storage, PIN lockout, and pay().

pay() lifecycle (each arrow is a durable step):
  auth -> [tx A: idempotency check, expiry, risk, reserve daily spend, HOLD] -> execute (outside any DB tx)
       -> [tx B: CAPTURE + SETTLED]   or   release + FAILED   or   execute raised -> UNKNOWN (stays held)
An UNKNOWN payment is never auto-released: resolve_held() is the reconciliation entry point.
Improvements over the reference: daily spend is reserved at hold time (no concurrent bypass); retries of a
quote require authentication; PIN lockout is persisted and time-limited; handles cannot be re-registered.
"""
from __future__ import annotations

import hmac
import json
import os
import time
import uuid
from decimal import Decimal as D
from typing import Callable, Iterable, Optional

from omnipay import (ASSETS, CUSTODY, DAY, TREASURY, VPA_RE, Ledger, Oracle, Payment, PaymentError, Profile,
                     Quote, State, Switch)
from app.dbledger import DbLedger


class PersistentSwitch(Switch):
    PIN_LOCK_SECONDS = 900

    def __init__(self, ledger: DbLedger, oracle: Oracle, clock: Callable[[], float] = time.time, *,
                 switches=None, **kw):
        super().__init__(ledger, oracle, clock, **kw)   # type: ignore[arg-type]
        self.switches = switches
        self.on_failed: Optional[Callable[[Payment], None]] = None

    # ---- kill switches / feature flags (global, per asset, per asset kind, per region) -----------------
    def _check_switches(self, payer: str, payee: str, *codes: str) -> None:
        sx = self.switches
        if sx is None:
            return
        sx.require("global")
        for h in (payer, payee):
            sx.require("region", self._user(h).region)
        for code in {c for c in codes if c in ASSETS}:
            if not sx.on("asset", code) or not sx.on("kind", ASSETS[code].kind.value):
                raise PaymentError("ASSET_DISABLED", f"{code} is switched off")

    def price(self, payer, payee, invoice, invoice_asset, pay_asset):
        self._check_switches(payer, payee, pay_asset, invoice_asset)
        return super().price(payer, payee, invoice, invoice_asset, pay_asset)

    # the base class assigns converted_usd; here it is derived from durable payments instead
    @property
    def converted_usd(self) -> D:
        rows = self.ledger.conn().execute(
            "SELECT q.usd FROM payments p JOIN quotes q ON q.id=p.quote_id WHERE p.state='SETTLED' AND q.route='convert'")
        return sum((D(r[0]) for r in rows), D(0))

    @converted_usd.setter
    def converted_usd(self, _v) -> None:
        pass

    # ---- directory ----------------------------------------------------------
    def register(self, handle: str, pin: Optional[str] = None, *, region: str = "US",
                 settle_in: str = "USD", accepts: Iterable[str] = ()) -> None:
        if not VPA_RE.match(handle):
            raise PaymentError("BAD_VPA", handle)
        if not self._asset(settle_in).convertible:
            raise PaymentError("BAD_SETTLEMENT_ASSET", settle_in)
        taken = sorted({self._asset(c).code for c in accepts} | {settle_in})
        salt = os.urandom(16)
        pin_hash = self._kdf(pin, salt) if pin else None
        with self.ledger.tx() as c:
            if c.execute("SELECT 1 FROM users WHERE handle=?", (handle,)).fetchone():
                raise PaymentError("HANDLE_TAKEN", handle)
            c.execute("INSERT INTO users(handle,region,settle_in,accepts,salt,pin_hash,created_at) VALUES(?,?,?,?,?,?,?)",
                      (handle, region, settle_in, json.dumps(taken), salt, pin_hash, self.clock()))

    def _user(self, handle: str) -> Profile:
        r = self.ledger.conn().execute("SELECT * FROM users WHERE handle=?", (handle,)).fetchone()
        if r is None:
            raise PaymentError("UNKNOWN_VPA", handle)
        return Profile(r["region"], r["settle_in"], set(json.loads(r["accepts"])), r["salt"], r["pin_hash"])

    def _set_accepts(self, handle: str, fn) -> None:
        with self.ledger.tx() as c:
            u = self._user(handle)
            u.accepts = fn(u)
            c.execute("UPDATE users SET accepts=? WHERE handle=?", (json.dumps(sorted(u.accepts)), handle))

    def set_settle_in(self, handle: str, code: str) -> None:
        if not self._asset(code).convertible:
            raise PaymentError("BAD_SETTLEMENT_ASSET", code)
        with self.ledger.tx() as c:
            u = self._user(handle)
            c.execute("UPDATE users SET settle_in=?, accepts=? WHERE handle=?", (code, json.dumps(sorted(u.accepts | {code})), handle))

    def accept(self, handle: str, *codes: str) -> None:
        new = {self._asset(c).code for c in codes}
        self._set_accepts(handle, lambda u: u.accepts | new)

    def decline(self, handle: str, *codes: str) -> None:
        self._set_accepts(handle, lambda u: u.accepts - (set(codes) - {u.settle_in}))

    # ---- quotes -------------------------------------------------------------
    def quote(self, payer: str, payee: str, invoice: str, invoice_asset: str, pay_asset: str) -> Quote:
        q = self.price(payer, payee, invoice, invoice_asset, pay_asset)
        self.ledger.conn().execute(
            "INSERT INTO quotes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (q.id, q.payer, q.payee, str(q.invoice), q.invoice_asset, q.route, q.pay_asset, str(q.amount),
             q.receive_asset, str(q.receive), str(q.usd), q.expires_at, self.clock()))
        return q

    def load_quote(self, quote_id: str) -> Quote:
        r = self.ledger.conn().execute("SELECT * FROM quotes WHERE id=?", (quote_id,)).fetchone()
        if r is None:
            raise PaymentError("UNKNOWN_QUOTE", quote_id)
        return Quote(r["id"], r["payer"], r["payee"], D(r["invoice"]), r["invoice_asset"], r["route"], r["pay_asset"],
                     D(r["amount"]), r["receive_asset"], D(r["receive"]), D(r["usd"]), r["expires_at"])

    # ---- authentication (stop-gap: real passkeys/sessions come with the web app) ------------------------
    def _authenticate(self, handle: str, pin: str) -> None:
        c = self.ledger.conn()
        r = c.execute("SELECT salt, pin_hash, pin_locked_until FROM users WHERE handle=?", (handle,)).fetchone()
        if r is None:
            raise PaymentError("UNKNOWN_VPA", handle)
        if r["pin_locked_until"] > self.clock():
            raise PaymentError("PIN_LOCKED", handle)
        ok = r["pin_hash"] is not None and hmac.compare_digest(r["pin_hash"], self._kdf(pin, r["salt"]))  # KDF outside any DB tx
        with self.ledger.tx() as c:
            if ok:
                c.execute("UPDATE users SET pin_fails=0 WHERE handle=?", (handle,))
                return
            c.execute("UPDATE users SET pin_fails=pin_fails+1 WHERE handle=?", (handle,))
            fails = c.execute("SELECT pin_fails FROM users WHERE handle=?", (handle,)).fetchone()[0]
            if fails >= self.MAX_PIN_TRIES:
                c.execute("UPDATE users SET pin_locked_until=?, pin_fails=0 WHERE handle=?",
                          (self.clock() + self.PIN_LOCK_SECONDS, handle))
        raise PaymentError("BAD_PIN", f"{max(self.MAX_PIN_TRIES - fails, 0)} tries left")

    # ---- spend & risk -------------------------------------------------------
    def _spent_today(self, c, payer: str) -> D:
        r = c.execute("SELECT usd FROM daily_spend WHERE payer=? AND day=?", (payer, int(self.clock() // DAY))).fetchone()
        return D(r[0]) if r else D(0)

    def _add_spend(self, c, payer: str, day: int, usd: D) -> None:
        r = c.execute("SELECT usd FROM daily_spend WHERE payer=? AND day=?", (payer, day)).fetchone()
        c.execute("INSERT OR REPLACE INTO daily_spend VALUES(?,?,?)", (payer, day, str((D(r[0]) if r else D(0)) + usd)))

    def _check_risk(self, q: Quote) -> None:
        c = self.ledger.conn()
        if self._spent_today(c, q.payer) + q.usd > self.daily:
            raise PaymentError("LIMIT_DAILY", q.payer)
        if q.route == "convert" and q.pay_asset != "USD":
            added = q.amount * self.oracle.usd(q.pay_asset)
            if self.exposure_usd() + added > self.exposure_cap:
                raise PaymentError("EXPOSURE_CAP", "platform inventory limit reached")

    # ---- payment lifecycle --------------------------------------------------
    def pay(self, quote_id: str, pin: str) -> Payment:
        q = self.load_quote(quote_id)
        self._authenticate(q.payer, pin)
        return self.pay_authorised(quote_id)

    def pay_authorised(self, quote_id: str) -> Payment:
        """Authorisation already established by the caller (PIN, or a user-set mandate checked server-side)."""
        q = self.load_quote(quote_id)
        L = self.ledger
        with L.tx() as c:                                        # tx A
            row = c.execute("SELECT id FROM payments WHERE quote_id=?", (quote_id,)).fetchone()
            if row:
                return self.get_payment(row["id"])               # idempotent retry: same payment, no second charge
            if self.clock() > q.expires_at:
                raise PaymentError("QUOTE_EXPIRED", quote_id)
            self._check_switches(q.payer, q.payee, q.pay_asset, q.receive_asset)
            fz = c.execute("SELECT frozen FROM credentials WHERE handle=?", (q.payer,)).fetchone()
            if fz and fz[0]:
                raise PaymentError("ACCOUNT_FROZEN", q.payer)
            self._check_risk(q)
            pid, now, day = uuid.uuid4().hex[:12], self.clock(), int(self.clock() // DAY)
            c.execute("INSERT INTO payments VALUES(?,?,?,?,?,?)", (pid, quote_id, "HELD", "", now, now))
            self._add_spend(c, q.payer, day, q.usd)
            try:
                L.post(f"hold:{pid}", [(f"user:{q.payer}", q.pay_asset, -q.amount), (f"hold:{pid}", q.pay_asset, q.amount)])
            except PaymentError as e:                            # savepoint rolled back the hold; keep a FAILED record
                self._add_spend(c, q.payer, day, -q.usd)
                self._set_state(c, pid, "FAILED", e.code)
                return self.get_payment(pid)
        p = Payment(pid, q)
        try:
            ok = self.execute(p)                                 # external step, deliberately outside any DB transaction
        except BaseException:
            with L.tx() as c:
                self._set_state(c, pid, "UNKNOWN", "EXECUTE_OUTCOME_UNKNOWN")
            raise                                                # stays HELD until resolve_held()
        return self._finish(pid, q, ok, "EXECUTE_FAILED")

    def _finish(self, pid: str, q: Quote, executed: bool, fail_reason: str) -> Payment:
        L, X = self.ledger, q.pay_asset
        hold = f"hold:{pid}"
        with L.tx() as c:                                        # tx B
            if executed and not ASSETS[q.receive_asset].convertible and q.receive_asset not in self._user(q.payee).accepts:
                executed, fail_reason = False, "PAYEE_NO_LONGER_ACCEPTS"     # shares only ever land with an opted-in payee
            if executed:
                legs = ([(hold, X, -q.amount), (f"user:{q.payee}", X, q.receive)] if q.route == "direct" else
                        [(hold, X, -q.amount), (CUSTODY, X, q.amount),
                         (TREASURY, q.receive_asset, -q.receive), (f"user:{q.payee}", q.receive_asset, q.receive)])
                try:
                    L.post(f"capture:{pid}", legs)
                    self._set_state(c, pid, "SETTLED", "")
                except PaymentError as e:
                    fail_reason = "NO_LIQUIDITY" if e.code == "INSUFFICIENT_FUNDS" else e.code
                    executed = False
            if not executed:
                L.post(f"release:{pid}", [(hold, X, -q.amount), (f"user:{q.payer}", X, q.amount)])
                self._add_spend(c, q.payer, self._payment_day(c, pid), -q.usd)
                self._set_state(c, pid, "FAILED", fail_reason)
        p = self.get_payment(pid)
        if p.state is State.FAILED and self.on_failed is not None:
            try:
                self.on_failed(p)
            except Exception as e:                               # noqa: BLE001
                self.hook_errors.append((pid, repr(e)))
        if p.state is State.SETTLED and self.on_settled is not None:
            try:                                                 # committed: a failing hook must never undo the payment
                self.on_settled(p)
            except Exception as e:                               # noqa: BLE001
                self.hook_errors.append((pid, repr(e)))
        return p

    def resolve_held(self, payment_id: str, executed: bool) -> Payment:
        """Reconciliation: the external system told us what really happened to a HELD/UNKNOWN payment."""
        with self.ledger.tx() as c:
            r = c.execute("SELECT quote_id, state FROM payments WHERE id=?", (payment_id,)).fetchone()
            if r is None:
                raise PaymentError("UNKNOWN_PAYMENT", payment_id)
            if r["state"] not in ("HELD", "UNKNOWN"):
                raise PaymentError("NOT_HELD", f"{payment_id} is {r['state']}")
            q = self.load_quote(r["quote_id"])
            return self._finish(payment_id, q, executed, "RECONCILED_NOT_EXECUTED")

    def _payment_day(self, c, pid: str) -> int:
        return int(c.execute("SELECT created_at FROM payments WHERE id=?", (pid,)).fetchone()[0] // DAY)

    def _set_state(self, c, pid: str, state: str, reason: str) -> None:
        c.execute("UPDATE payments SET state=?, reason=?, updated_at=? WHERE id=?", (state, reason, self.clock(), pid))

    def get_payment(self, payment_id: str) -> Payment:
        r = self.ledger.conn().execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
        if r is None:
            raise PaymentError("UNKNOWN_PAYMENT", payment_id)
        s = r["state"]
        state = {"SETTLED": State.SETTLED, "FAILED": State.FAILED}.get(s, State.PENDING)
        return Payment(r["id"], self.load_quote(r["quote_id"]), state, r["reason"] or (s if state is State.PENDING else ""))

    def raw_state(self, payment_id: str) -> str:
        return self.ledger.conn().execute("SELECT state FROM payments WHERE id=?", (payment_id,)).fetchone()[0]

    def held_payments(self) -> list[str]:
        return [r[0] for r in self.ledger.conn().execute("SELECT id FROM payments WHERE state IN ('HELD','UNKNOWN')")]
