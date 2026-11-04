"""OmniPay core - a global "pay with anything" switch (reference implementation).

The payer picks WHAT to pay with: any currency, crypto, or shares. The payee decides what it ACCEPTS:

  direct   the payee accepts that asset -> it moves in kind: no conversion, no platform market risk
  convert  it doesn't -> the platform converts into the payee's settlement asset (needs treasury liquidity)

Shares are direct-only: they move only to a payee who has opted in.

Users pay NOTHING: no fee and no spread. The payer's cost is the invoice's mid-market value in the chosen
asset, rounded to that asset's smallest unit. Revenue comes from promoters (see promo.py).

Every movement is a double-entry ledger transaction, so value cannot be created, lost or double-spent.

  quote -> PIN -> policy + risk -> HOLD -> EXECUTE -> CAPTURE -> on_settled hook

Pure stdlib. Money is Decimal, never float.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import threading
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, ROUND_HALF_UP, ROUND_UP, Decimal as D
from enum import Enum
from statistics import median
from typing import Callable, Iterable, Optional


class PaymentError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


# ------------------------------------------------------------------ assets --
class Kind(Enum):
    FIAT = "fiat"
    CRYPTO = "crypto"
    EQUITY = "equity"


@dataclass(frozen=True)
class Asset:
    code: str
    kind: Kind
    dp: int                    # decimal places: JPY 0, USD 2, BTC 8, fractional / tokenized shares 6
    haircut_bps: int = 0       # risk discount when the platform is PAID in this asset (by promoters); never charged to users
    convertible: bool = True   # False for shares: they move in kind only, never sold on the payer's behalf

    def q(self, x: D, rounding=ROUND_HALF_UP) -> D:
        return x.quantize(D(1).scaleb(-self.dp), rounding=rounding)


ASSETS = {a.code: a for a in (
    Asset("USD", Kind.FIAT, 2, 20), Asset("EUR", Kind.FIAT, 2, 30), Asset("GBP", Kind.FIAT, 2, 30),
    Asset("INR", Kind.FIAT, 2, 40), Asset("JPY", Kind.FIAT, 0, 40),
    Asset("USDC", Kind.CRYPTO, 6, 25), Asset("BTC", Kind.CRYPTO, 8, 150), Asset("ETH", Kind.CRYPTO, 8, 200),
    Asset("AAPL", Kind.EQUITY, 6, convertible=False), Asset("TSLA", Kind.EQUITY, 6, convertible=False),
)}


# ------------------------------------------------------------------ prices --
class Oracle:
    """USD price of one unit of every asset (USD is the numeraire). Median of fresh sources;
    refuses stale data (never trade blind)."""

    def __init__(self, clock: Callable[[], float] = time.time, max_age: float = 5.0):
        self.clock, self.max_age = clock, max_age
        self._feeds: dict[str, dict[str, tuple[D, float]]] = defaultdict(dict)

    def publish(self, code: str, usd_price, source: str = "primary") -> None:
        self._feeds[code][source] = (D(str(usd_price)), self.clock())

    def usd(self, code: str) -> D:
        if code == "USD":
            return D(1)
        now = self.clock()
        fresh = [p for p, ts in self._feeds[code].values() if now - ts <= self.max_age]
        if not fresh:
            raise PaymentError("NO_PRICE", f"no fresh price for {code}")
        return median(fresh)

    def cross(self, a: str, b: str) -> D:
        """How many units of b one unit of a is worth."""
        return D(1) if a == b else self.usd(a) / self.usd(b)


# ------------------------------------------------------------------ ledger --
class Ledger:
    """Append-only, double-entry, multi-asset. Every transaction nets to zero per asset.
    Accounts starting with 'ext:' mirror the outside world (banks, chains, brokers) and may go negative."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self.clock = clock
        self.lock = threading.RLock()
        self._bal: dict[tuple[str, str], D] = defaultdict(D)
        self.journal: list[dict] = []

    def balance(self, account: str, asset: str) -> D:
        return self._bal[(account, asset)]

    def post(self, ref: str, legs: list[tuple[str, str, D]]) -> None:
        """legs = (account, asset, signed amount). All-or-nothing."""
        legs = [(acct, code, D(x)) for acct, code, x in legs if x]
        delta: dict[tuple[str, str], D] = defaultdict(D)
        per_asset: dict[str, D] = defaultdict(D)
        for acct, code, x in legs:
            delta[(acct, code)] += x
            per_asset[code] += x
        if any(per_asset.values()):
            raise PaymentError("LEDGER_UNBALANCED", ref)
        with self.lock:
            for (acct, code), x in delta.items():
                if not acct.startswith("ext:") and self._bal[(acct, code)] + x < 0:
                    raise PaymentError("INSUFFICIENT_FUNDS", f"{acct} {code}")
            for key, x in delta.items():
                self._bal[key] += x
            self.journal.append({"n": len(self.journal) + 1, "ref": ref, "ts": self.clock(), "legs": legs})

    def trial_balance(self) -> dict[str, D]:
        """Sum of every account per asset. Must be exactly zero."""
        tb: dict[str, D] = defaultdict(D)
        for (_, code), v in self._bal.items():
            tb[code] += v
        return dict(tb)


# ------------------------------------------------------------------ policy --
@dataclass
class Policy:
    """Per-jurisdiction switches. allowed: region -> asset codes usable there ('*' = default for other
    regions). blocked: regions that cannot transact at all (sanctions)."""
    allowed: dict[str, set[str]] = field(default_factory=dict)
    blocked: set[str] = field(default_factory=set)

    def allows(self, region: str, code: str) -> bool:
        return code in self.allowed.get(region, self.allowed.get("*", set(ASSETS)))


@dataclass
class Profile:
    region: str
    settle_in: str             # what the payee gets when the payer's asset isn't accepted in kind
    accepts: set[str]          # assets the payee takes in kind (always includes settle_in)
    salt: bytes = b""
    pin_hash: Optional[bytes] = None


# ------------------------------------------------------------------ switch --
class State(Enum):
    PENDING = "PENDING"
    SETTLED = "SETTLED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class Quote:
    id: str
    payer: str
    payee: str
    invoice: D
    invoice_asset: str
    route: str            # "direct" (in kind) | "convert"
    pay_asset: str
    amount: D             # debited from the payer, in pay_asset: the invoice's mid-market value, nothing more
    receive_asset: str
    receive: D            # credited to the payee
    usd: D                # USD value of the invoice (for limits and billing)
    expires_at: float


@dataclass
class Payment:
    id: str
    quote: Quote
    state: State = State.PENDING
    reason: str = ""


TREASURY, CUSTODY, REVENUE = "sys:treasury", "sys:custody", "sys:revenue"
VPA_RE = re.compile(r"^[a-z0-9._-]{2,}@[a-z]{2,}$")
DAY = 86_400


class Switch:
    PIN_ROUNDS = 200_000   # stand-in: real rails verify the PIN inside the issuing bank's HSM
    MAX_PIN_TRIES = 3

    def __init__(self, ledger: Ledger, oracle: Oracle, clock: Callable[[], float] = time.time, *,
                 policy: Optional[Policy] = None, quote_ttl: float = 30, per_txn_usd: str = "50000",
                 daily_usd: str = "100000", exposure_cap_usd: str = "2000000",
                 execute: Callable[[Payment], bool] = lambda p: True):
        self.ledger, self.oracle, self.clock = ledger, oracle, clock
        self.policy, self.quote_ttl = policy or Policy(), quote_ttl
        self.per_txn, self.daily, self.exposure_cap = D(per_txn_usd), D(daily_usd), D(exposure_cap_usd)
        self.execute = execute   # external step that must succeed before we commit (local-rail push, sanctions...)
        self.on_settled: Optional[Callable[[Payment], None]] = None   # post-commit hook: billing, notifications
        self.hook_errors: list[tuple[str, str]] = []
        self.converted_usd = D(0)   # USD value of invoices settled through the convert route: that is what costs us
        self._users: dict[str, Profile] = {}
        self._quotes: dict[str, Quote] = {}
        self._payments: dict[str, Payment] = {}
        self._pin_fails: dict[str, int] = defaultdict(int)
        self._spent: dict[tuple[str, int], D] = defaultdict(D)

    # ---- directory & funding ------------------------------------------------
    def register(self, handle: str, pin: Optional[str] = None, *, region: str = "US",
                 settle_in: str = "USD", accepts: Iterable[str] = ()) -> None:
        """Create an account. pin=None -> receive-only (a merchant). settle_in is what the payee wants when
        the payer's asset isn't accepted in kind; accepts lists the extra assets it takes in kind."""
        if not VPA_RE.match(handle):
            raise PaymentError("BAD_VPA", handle)
        if not self._asset(settle_in).convertible:
            raise PaymentError("BAD_SETTLEMENT_ASSET", settle_in)
        taken = {self._asset(c).code for c in accepts} | {settle_in}
        salt = os.urandom(16)
        self._users[handle] = Profile(region, settle_in, taken, salt, self._kdf(pin, salt) if pin else None)

    def accept(self, handle: str, *codes: str) -> None:
        """Payee opt-in: take these assets in kind. Shares only ever move to a payee who did this."""
        self._user(handle).accepts.update(self._asset(c).code for c in codes)

    def decline(self, handle: str, *codes: str) -> None:
        u = self._user(handle)
        u.accepts.difference_update(set(codes) - {u.settle_in})   # the settlement asset always stays accepted

    def region(self, handle: str) -> str:
        return self._user(handle).region

    def _kdf(self, pin: str, salt: bytes) -> bytes:
        return hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, self.PIN_ROUNDS)

    def _user(self, handle: str) -> Profile:
        if handle not in self._users:
            raise PaymentError("UNKNOWN_VPA", handle)
        return self._users[handle]

    @staticmethod
    def _asset(code: str) -> Asset:
        if code not in ASSETS:
            raise PaymentError("UNKNOWN_ASSET", code)
        return ASSETS[code]

    def balance(self, handle: str, code: str) -> D:
        return self.ledger.balance(f"user:{handle}", code)

    def deposit(self, handle: str, code: str, amount: str) -> None:
        """On-ramp: bank credit / on-chain deposit / brokerage transfer lands in the user's account."""
        self._user(handle)
        self._mint(f"user:{handle}", code, amount, f"deposit:{handle}")

    def fund_treasury(self, code: str, amount: str) -> None:
        """Platform liquidity, one pool per currency: what a conversion pays the payee from."""
        self._mint(TREASURY, code, amount, f"fund:{code}")

    def _mint(self, account: str, code: str, amount: str, ref: str) -> None:
        a, x = self._asset(code), D(str(amount))
        if x <= 0 or x != a.q(x, ROUND_DOWN):
            raise PaymentError("BAD_AMOUNT", f"{amount} {code}")
        self.ledger.post(ref, [(f"ext:{a.kind.value}", code, -x), (account, code, x)])

    # ---- payment lifecycle --------------------------------------------------
    def price(self, payer: str, payee: str, invoice: str, invoice_asset: str, pay_asset: str) -> Quote:
        """Price an invoice in the asset the payer chose and pick the route. No side effects, nothing locked."""
        pu, ru = self._user(payer), self._user(payee)
        if payer == payee:
            raise PaymentError("SELF_PAYMENT")
        inv_a, pay_a = self._asset(invoice_asset), self._asset(pay_asset)
        inv = D(str(invoice))
        if inv <= 0 or inv != inv_a.q(inv, ROUND_DOWN):
            raise PaymentError("BAD_AMOUNT", f"{invoice} {invoice_asset}")
        if {pu.region, ru.region} & self.policy.blocked:
            raise PaymentError("REGION_BLOCKED")

        route = "direct" if pay_asset in ru.accepts else "convert"
        if route == "convert" and not pay_a.convertible:
            raise PaymentError("PAYEE_NOT_ACCEPTING", f"{payee} has not opted in to receive {pay_asset}")
        recv_a = pay_a if route == "direct" else ASSETS[ru.settle_in]
        # an asset has to be usable in BOTH countries, and the payee's region must allow what it receives
        for region, code in ((pu.region, pay_a.code), (ru.region, pay_a.code), (ru.region, recv_a.code)):
            if not self.policy.allows(region, code):
                raise PaymentError("ASSET_DISABLED", f"{code} is not available in {region}")

        usd = inv * self.oracle.usd(invoice_asset)
        if usd > self.per_txn:
            raise PaymentError("LIMIT_TXN", f"max USD {self.per_txn}")
        amount = pay_a.q(inv * self.oracle.cross(invoice_asset, pay_asset))      # mid-market: nothing added
        if route == "direct":
            receive = amount
        else:                                                                     # payee's side rounds up: the dust is ours
            receive = recv_a.q(inv * self.oracle.cross(invoice_asset, recv_a.code), ROUND_UP)
        if amount <= 0 or receive <= 0:
            raise PaymentError("TOO_SMALL", f"{invoice} {invoice_asset} in {pay_asset}")
        return Quote(uuid.uuid4().hex[:12], payer, payee, inv, invoice_asset, route, pay_asset, amount,
                     recv_a.code, receive, usd, self.clock() + self.quote_ttl)

    def quote(self, payer: str, payee: str, invoice: str, invoice_asset: str, pay_asset: str) -> Quote:
        """Price it and lock the price for quote_ttl seconds."""
        q = self.price(payer, payee, invoice, invoice_asset, pay_asset)
        with self.ledger.lock:
            self._quotes[q.id] = q
        return q

    def pay(self, quote_id: str, pin: str) -> Payment:
        """Authorise with the PIN and execute. A quote is a single-use payment intent, so retrying the
        same quote returns the same Payment instead of charging twice (idempotent).
        Pre-flight problems raise PaymentError; failures after the hold return a FAILED Payment."""
        with self.ledger.lock:
            if quote_id in self._payments:
                return self._payments[quote_id]
            q = self._quotes.get(quote_id)
            if q is None:
                raise PaymentError("UNKNOWN_QUOTE", quote_id)
            if self.clock() > q.expires_at:
                raise PaymentError("QUOTE_EXPIRED", quote_id)
            self._check_pin(q.payer, pin)
            self._check_risk(q)

            p = self._payments[quote_id] = Payment(uuid.uuid4().hex[:12], q)
            X, payer, payee, hold = q.pay_asset, f"user:{q.payer}", f"user:{q.payee}", f"hold:{p.id}"
            try:                                               # 1. HOLD: earmark the payer's funds
                self.ledger.post(hold, [(payer, X, -q.amount), (hold, X, q.amount)])
            except PaymentError as e:
                return self._fail(p, e.code)
            # 2. EXECUTE: the external step. Exceptions propagate on purpose: an unknown outcome
            #    (timeout) stays HELD until reconciled, it is never auto-released.
            if not self.execute(p):
                return self._release(p, "EXECUTE_FAILED")
            if q.route == "direct":                            # 3. CAPTURE: in kind ...
                legs = [(hold, X, -q.amount), (payee, X, q.receive)]
            else:                                              # ... or convert through the treasury
                legs = [(hold, X, -q.amount), (CUSTODY, X, q.amount),
                        (TREASURY, q.receive_asset, -q.receive), (payee, q.receive_asset, q.receive)]
            try:
                self.ledger.post(f"capture:{p.id}", legs)
            except PaymentError as e:
                return self._release(p, "NO_LIQUIDITY" if e.code == "INSUFFICIENT_FUNDS" else e.code)
            self._spent[(q.payer, int(self.clock() // DAY))] += q.usd
            if q.route == "convert":
                self.converted_usd += q.usd
            p.state = State.SETTLED
            if self.on_settled is not None:
                try:                       # the payment is committed: a failing hook must never undo or fail it
                    self.on_settled(p)
                except Exception as e:     # noqa: BLE001
                    self.hook_errors.append((p.id, repr(e)))
            return p

    def exposure_usd(self) -> D:
        """USD value of the non-USD assets the platform holds after conversions: its market risk.
        In-kind payments never add to it."""
        held = [(c, self.ledger.balance(CUSTODY, c)) for c in ASSETS if c != "USD"]
        return sum((b * self.oracle.usd(c) for c, b in held if b > 0), D(0))

    # ---- internals ----------------------------------------------------------
    def _check_pin(self, handle: str, pin: str) -> None:
        u = self._users[handle]
        if self._pin_fails[handle] >= self.MAX_PIN_TRIES:
            raise PaymentError("PIN_LOCKED", handle)
        if u.pin_hash is None or not hmac.compare_digest(u.pin_hash, self._kdf(pin, u.salt)):
            self._pin_fails[handle] += 1
            raise PaymentError("BAD_PIN", f"{self.MAX_PIN_TRIES - self._pin_fails[handle]} tries left")
        self._pin_fails[handle] = 0

    def _check_risk(self, q: Quote) -> None:
        if self._spent[(q.payer, int(self.clock() // DAY))] + q.usd > self.daily:
            raise PaymentError("LIMIT_DAILY", q.payer)
        if q.route == "convert" and q.pay_asset != "USD":
            added = q.amount * self.oracle.usd(q.pay_asset)
            if self.exposure_usd() + added > self.exposure_cap:
                raise PaymentError("EXPOSURE_CAP", "platform inventory limit reached")

    def _release(self, p: Payment, reason: str) -> Payment:
        """Compensate: give the held funds back to the payer."""
        q = p.quote
        self.ledger.post(f"release:{p.id}", [(f"hold:{p.id}", q.pay_asset, -q.amount),
                                             (f"user:{q.payer}", q.pay_asset, q.amount)])
        return self._fail(p, reason)

    @staticmethod
    def _fail(p: Payment, reason: str) -> Payment:
        p.state, p.reason = State.FAILED, reason
        return p
