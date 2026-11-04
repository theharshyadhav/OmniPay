"""Randomised ledger/switch invariants. Hypothesis is not installable in this sandbox (no network), so this is a
seeded stdlib fuzzer: each seed is a reproducible random operation sequence. Failures print the seed."""
import random
import unittest
from decimal import ROUND_DOWN, Decimal as D

from omnipay import ASSETS, PaymentError
from tests.helpers import Env

SEEDS = 60
OPS = 40


class LedgerProperties(unittest.TestCase):
    def run_seed(self, seed):
        rnd = random.Random(seed)
        e = Env(per_txn_usd="5000", daily_usd="20000", exposure_cap_usd="30000")
        sw = e.seed()
        sw.accept("bob@omni", "USDC")
        names = ["alice@omni", "bob@omni", "dana@omni", "ravi@omni", "sam@omni", "zed@omni"]
        sw.register("sam@omni", "1234", region="US", settle_in="JPY", accepts=["ETH"])
        sw.register("zed@omni", "1234", region="IN", settle_in="INR")
        codes = list(ASSETS)
        for who in ("alice@omni", "sam@omni", "zed@omni"):          # fund every payer in every asset (~USD 4000 each)
            for c in codes:
                sw.deposit(who, c, str(ASSETS[c].q(D(4000) / e.oracle.usd(c), ROUND_DOWN)))
        e.violations = []

        def on_settled(p):   # shares may only ever land with a payee who has opted in, at the moment of settlement
            q = p.quote
            if ASSETS[q.receive_asset].kind.value == "equity" and q.receive_asset not in sw._user(q.payee).accepts:
                e.violations.append((p.id, q.payee, q.receive_asset))
        sw.on_settled = on_settled
        for step in range(OPS):
            op = rnd.choice(["dep", "dep", "pay", "pay", "pay", "accept", "decline", "stale", "boom"])
            try:
                if op == "dep":
                    c = rnd.choice(codes)
                    sw.deposit(rnd.choice(names), c, str(ASSETS[c].q(D(rnd.randint(1, 10**6)) / D(100))))
                elif op in ("pay", "boom"):
                    payer = rnd.choice(["alice@omni", "sam@omni", "zed@omni"])
                    payee = rnd.choice([n for n in names if n != payer])
                    inv = str(D(rnd.randint(1, 400_000)) / D(100))
                    q = sw.quote(payer, payee, inv, "USD", rnd.choice(codes))
                    sw.execute = (lambda p: (_ for _ in ()).throw(TimeoutError())) if op == "boom" else \
                        (lambda p: rnd.random() > 0.15)
                    try:
                        sw.pay(q.id, "1234" if payer != "bob@omni" else "x")
                    except TimeoutError:
                        pass
                    for pid in sw.held_payments():
                        if rnd.random() < 0.5:
                            sw.resolve_held(pid, rnd.random() < 0.5)
                elif op == "accept":
                    sw.accept(rnd.choice(names), rnd.choice(codes))
                elif op == "decline":
                    sw.decline(rnd.choice(names), rnd.choice(codes))
                else:
                    e.t += rnd.choice([1, 31, 3600])
            except PaymentError:
                pass
            self.check(e, seed, step)
        return e

    def check(self, e, seed, step):
        where = f"seed={seed} step={step}"
        self.assertEqual(e.ledger.audit(), [], where)                          # balances == journal, all tx balanced
        self.assertTrue(all(v == 0 for v in e.ledger.trial_balance().values()), where)
        rows = e.ledger.conn().execute("SELECT account, asset, atoms FROM balances").fetchall()
        for r in rows:
            if not r["account"].startswith("ext:"):
                self.assertGreaterEqual(r["atoms"], 0, f"{where} negative {r['account']}")
        # a hold exists exactly for payments still in flight
        held = {f"hold:{p}" for p in e.sw.held_payments()}
        for r in rows:
            if r["account"].startswith("hold:") and r["atoms"] != 0:
                self.assertIn(r["account"], held, f"{where} orphan hold {r['account']}")
        for acct in held:
            self.assertTrue(any(r["account"] == acct and r["atoms"] > 0 for r in rows), f"{where} empty hold {acct}")

    def test_random_sequences_keep_every_invariant(self):
        for seed in range(SEEDS):
            self.run_seed(seed)

    def test_shares_never_move_to_a_payee_who_has_not_opted_in(self):
        for seed in range(SEEDS):
            e = self.run_seed(1000 + seed)
            self.assertEqual(e.violations, [], f"seed {1000 + seed}")
            for code in ("AAPL", "TSLA"):          # shares are never sold: platform accounts never hold them
                self.assertEqual(e.ledger.balance("sys:custody", code), 0)
                self.assertEqual(e.ledger.balance("sys:treasury", code), 0)


if __name__ == "__main__":
    unittest.main()
