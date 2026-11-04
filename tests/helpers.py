import os
import tempfile
from decimal import Decimal as D

from omnipay import Oracle, Policy, Switch
from app.dbledger import DbLedger
from app.pswitch import PersistentSwitch

Switch.PIN_ROUNDS = 1_000  # fast KDF for tests

PRICES = {"EUR": "1.08", "GBP": "1.27", "INR": "0.012", "JPY": "0.0067", "USDC": 1, "BTC": 100_000, "ETH": 3_500, "AAPL": 230, "TSLA": 250}


class Env:
    """A fresh database file plus a PersistentSwitch. reopen() simulates a process restart."""

    def __init__(self, **kw):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "omnipay.db")
        self.t = 1_700_000_000.0
        self.kw = kw
        self.open()

    def clock(self):
        return self.t

    def open(self):
        self.ledger = DbLedger(self.path, self.clock)
        self.oracle = Oracle(self.clock, max_age=10**9)
        for c, p in PRICES.items():
            self.oracle.publish(c, p)
        policy = Policy(allowed={"IN": {"INR", "USD", "EUR"}}, blocked={"KP"})
        self.sw = PersistentSwitch(self.ledger, self.oracle, self.clock, policy=policy, **self.kw)
        return self.sw

    def reopen(self):
        import threading
        self.ledger._local = threading.local()  # drop this thread's connection, as a restart would
        return self.open()

    def seed(self):
        sw = self.sw
        sw.register("alice@omni", "1234", region="US")
        sw.register("bob@omni", region="US")
        sw.register("dana@omni", region="DE", settle_in="EUR", accepts=["BTC", "AAPL"])
        sw.register("ravi@omni", region="IN", settle_in="INR")
        for c, a in (("USD", "1000000"), ("EUR", "1000000"), ("INR", "50000000")):
            sw.fund_treasury(c, a)
        return sw

    def bal(self, who, code):
        return self.ledger.balance(who if ":" in who and not who.endswith("@omni") else f"user:{who}", code)
