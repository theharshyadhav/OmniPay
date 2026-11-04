"""Real threads, one SQLite connection each. Correctness must come from the database, not from luck."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal as D

from omnipay import PaymentError, State
from tests.helpers import Env


def run_all(fns, workers=16):
    with ThreadPoolExecutor(workers) as ex:
        return [f.result() for f in [ex.submit(fn) for fn in fns]]


class ConcurrencyTests(unittest.TestCase):
    def test_concurrent_overdraft_never_goes_negative(self):
        e = Env()
        e.ledger.post("dep", [("ext:fiat", "USD", D(-100)), ("user:a", "USD", D(100))])

        def spend():
            try:
                e.ledger.post("s", [("user:a", "USD", D(-10)), ("user:b", "USD", D(10))])
                return True
            except PaymentError as err:
                assert err.code == "INSUFFICIENT_FUNDS", err
                return False
        ok = sum(run_all([spend] * 40))
        self.assertEqual(ok, 10)
        self.assertEqual((e.ledger.balance("user:a", "USD"), e.ledger.balance("user:b", "USD")), (D(0), D(100)))
        self.assertEqual(e.ledger.audit(), [])

    def test_same_quote_paid_by_many_threads_charges_once(self):
        e = Env(); sw = e.seed()
        sw.deposit("alice@omni", "USD", "1000")
        q = sw.quote("alice@omni", "bob@omni", "100", "USD", "USD")
        calls = []
        sw.execute = lambda p: (calls.append(p.id), True)[1]
        res = run_all([lambda: sw.pay(q.id, "1234")] * 24)
        self.assertEqual(len({p.id for p in res}), 1)
        self.assertEqual(len(calls), 1)                                   # the external rail was hit exactly once
        self.assertEqual((sw.balance("alice@omni", "USD"), sw.balance("bob@omni", "USD")), (D(900), D(100)))
        self.assertEqual(e.ledger.audit(), [])

    def test_many_payers_one_payee(self):
        e = Env(); sw = e.seed()
        payers = [f"p{i}@omni" for i in range(12)]
        for p in payers:
            sw.register(p, "1234", region="US")
            sw.deposit(p, "USD", "500")
        qs = [sw.quote(p, "dana@omni", "25", "USD", "USD") for p in payers for _ in range(4)]   # convert route
        res = run_all([lambda q=q: sw.pay(q.id, "1234") for q in qs])
        self.assertTrue(all(p.state is State.SETTLED for p in res))
        self.assertEqual(sw.balance("dana@omni", "EUR"), D("23.15") * len(qs))
        self.assertEqual(sw.ledger.balance("sys:custody", "USD"), D(25) * len(qs))
        self.assertEqual(e.ledger.audit(), [])

    def test_daily_limit_cannot_be_beaten_by_racing_payments(self):
        e = Env(daily_usd="300", per_txn_usd="300"); sw = e.seed()
        sw.deposit("alice@omni", "USD", "5000")
        qs = [sw.quote("alice@omni", "bob@omni", "100", "USD", "USD") for _ in range(20)]

        def go(q):
            try:
                return sw.pay(q.id, "1234").state
            except PaymentError as err:
                return err.code
        res = run_all([lambda q=q: go(q) for q in qs])
        self.assertEqual(res.count(State.SETTLED), 3)
        self.assertEqual(res.count("LIMIT_DAILY"), 17)
        self.assertEqual(sw.balance("bob@omni", "USD"), D(300))
        self.assertEqual(e.ledger.audit(), [])

    def test_racing_pin_guesses_still_lock_the_account(self):
        e = Env(); sw = e.seed()
        sw.deposit("alice@omni", "USD", "100")
        q = sw.quote("alice@omni", "bob@omni", "10", "USD", "USD")

        def guess(_):
            try:
                sw.pay(q.id, "0000")
            except PaymentError as err:
                return err.code
        codes = run_all([lambda i=i: guess(i) for i in range(30)])
        self.assertIn("PIN_LOCKED", codes)
        self.assertLessEqual(codes.count("BAD_PIN"), 3 + 16)             # bounded by workers already past the lock check
        with self.assertRaises(PaymentError) as cm:
            sw.pay(q.id, "1234")
        self.assertEqual(cm.exception.code, "PIN_LOCKED")


if __name__ == "__main__":
    unittest.main()
