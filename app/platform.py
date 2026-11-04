"""Wires the engine, persistence, PayPal edge, promo market and agents into one object (also used by tests/CLI)."""
from __future__ import annotations

import time
from typing import Callable, Optional

from omnipay import Oracle, Policy, Switch
from app.config import Settings
from app.core import Audit, Auth, RateLimiter, Switches
from app.dbledger import DbLedger
from app.pswitch import PersistentSwitch
from app.wallet import Wallet

DEMO_PRICES = {"EUR": "1.08", "GBP": "1.27", "INR": "0.012", "JPY": "0.0067", "USDC": "1", "BTC": "100000",
               "ETH": "3500", "AAPL": "230", "TSLA": "250"}
DEFAULT_POLICY = dict(allowed={"IN": {"INR", "USD", "EUR", "USDC"}}, blocked={"KP", "IR"})


class Platform:
    def __init__(self, settings: Settings, *, clock: Callable[[], float] = time.time, paypal=None, prices: Optional[dict] = None,
                 scrypt_n: Optional[int] = None, groq=None, **switch_kw):
        self.cfg, self.clock = settings, clock
        self.ledger = DbLedger(settings.db_path, clock)
        self.audit = Audit(self.ledger)
        self.switches = Switches(self.ledger, self.audit)
        self.auth = Auth(self.ledger, self.audit, settings.session_ttl, settings.stepup_window, scrypt_n)
        self.limiter = RateLimiter(clock)
        self.oracle = Oracle(clock, max_age=10 ** 9 if prices is not None else 30)
        for c, p in (prices if prices is not None else {}).items():
            self.oracle.publish(c, p)
        self.sw = PersistentSwitch(self.ledger, self.oracle, clock, switches=self.switches, policy=Policy(**DEFAULT_POLICY), **switch_kw)
        self.paypal = paypal if paypal is not None else self._make_paypal()
        self.wallet = Wallet(self.ledger, self.sw, self.paypal, self.audit, self.switches, settings)
        self.groq = groq
        from app.market import PersistentPromo          # noqa: E402  (after the core objects exist)
        self.promo = PersistentPromo(self.sw, self.audit, self.switches)
        from app.agents import Agents                   # noqa: E402
        self.agents = Agents(self)

    def _make_paypal(self):
        from app.paypal import PayPalClient
        from app.paypal_fake import FakePayPal
        if self.cfg.paypal_client_id and self.cfg.paypal_secret:
            return PayPalClient(self.cfg)
        return FakePayPal(self.clock)                   # no keys: simulated PayPal, UI shows a badge

    # ---- user lifecycle -----------------------------------------------------
    def signup(self, handle: str, password: str, pin: str, *, region="US", settle_in="USD", accepts=(), email="") -> None:
        self.sw.register(handle, pin, region=region, settle_in=settle_in, accepts=accepts)
        self.auth.set_password(handle, password, email=email)
        self.audit.log(handle, "user.signup", handle, region=region)

    def run_jobs(self) -> dict:
        """One tick of the background jobs (call every minute from a scheduler)."""
        out = {"payouts_polled": self.wallet.poll_open_payouts(), "payouts_retried": self.wallet.retry_unknown_payouts()}
        out.update(self.promo.sweep())
        out["recon"] = self.wallet.reconcile()
        out["autobid"] = self.promo.run_autobidders()
        return out
