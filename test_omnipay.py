import unittest
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal as D

from omnipay import Ledger, Oracle, PaymentError, Policy, State, Switch

Switch.PIN_ROUNDS = 1_000  # fast KDF for tests


class OmniPayTests(unittest.TestCase):
    def setUp(self):
        self.t = 1_700_000_000.0
        clock = lambda: self.t
        self.ledger, self.oracle = Ledger(clock), Oracle(clock)
        policy = Policy(allowed={"IN": {"INR", "USD", "EUR"}}, blocked={"KP"})
        self.sw = Switch(self.ledger, self.oracle, clock, policy=policy)
        self.prices = {"EUR": "1.08", "INR": "0.012", "JPY": "0.0067", "BTC": 100_000, "AAPL": 230, "TSLA": 250}
        self.feed()
        self.sw.register("alice@omni", "1234", region="US")
        self.sw.register("bob@omni", region="US")                                    # wants USD
        self.sw.register("dana@omni", region="DE", settle_in="EUR", accepts=["BTC", "AAPL"])
        self.sw.register("ravi@omni", region="IN", settle_in="INR")
        for code, amount in (("USD", "100000"), ("EUR", "100000"), ("INR", "5000000")):
            self.sw.fund_treasury(code, amount)

    def feed(self):
        for code, price in self.prices.items():
            self.oracle.publish(code, price)

    def bal(self, who, code):
        return self.ledger.balance(who if who.startswith("sys:") else f"user:{who}", code)

    def pay(self, payee, asset, invoice="100", invoice_asset="USD"):
        q = self.sw.quote("alice@omni", payee, invoice, invoice_asset, asset)
        return q, self.sw.pay(q.id, "1234")

    def assertSound(self):  # the ledger invariant: every asset nets to zero
        self.assertTrue(all(v == 0 for v in self.ledger.trial_balance().values()))

    def test_same_currency_is_direct(self):
        self.sw.deposit("alice@omni", "USD", "1000")
        q, p = self.pay("bob@omni", "USD", "250")
        self.assertEqual((q.route, q.amount, q.receive, p.state), ("direct", D(250), D(250), State.SETTLED))
        self.assertEqual((self.bal("alice@omni", "USD"), self.bal("bob@omni", "USD")), (D(750), D(250)))
        self.assertSound()

    def test_convert_when_the_payee_wants_something_else(self):
        self.sw.deposit("alice@omni", "USD", "1000")
        q, p = self.pay("dana@omni", "USD")                                          # Dana settles in EUR
        self.assertEqual((q.route, q.amount, q.receive_asset, q.receive), ("convert", D(100), "EUR", D("92.60")))
        self.assertEqual(self.bal("dana@omni", "EUR"), D("92.60"))
        self.assertEqual(self.bal("alice@omni", "USD"), D(900))                      # she paid exactly the invoice
        self.assertEqual(self.bal("sys:custody", "USD"), D(100))
        self.assertEqual(self.bal("sys:treasury", "EUR"), 100_000 - D("92.60"))
        self.assertEqual(self.sw.converted_usd, D(100))
        self.assertSound()

    def test_in_kind_crypto_creates_no_platform_risk(self):
        self.sw.deposit("alice@omni", "BTC", "0.05")
        q, p = self.pay("dana@omni", "BTC")                                          # Dana takes BTC in kind
        self.assertEqual((q.route, q.amount, q.receive_asset, q.receive), ("direct", D("0.001"), "BTC", D("0.001")))
        self.assertEqual(self.bal("dana@omni", "BTC"), D("0.001"))
        self.assertEqual(self.bal("sys:custody", "BTC"), 0)                          # platform holds nothing
        self.assertEqual(self.bal("sys:treasury", "EUR"), 100_000)                   # no conversion happened
        self.assertEqual(self.sw.exposure_usd(), 0)
        self.assertSound()

    def test_users_never_pay_a_fee_or_a_spread(self):
        self.sw.deposit("alice@omni", "USD", "1000")
        self.sw.deposit("alice@omni", "BTC", "0.05")
        q1, _ = self.pay("bob@omni", "USD")                                          # same currency
        q2, _ = self.pay("bob@omni", "BTC")                                          # converted: Bob wants USD
        q3, _ = self.pay("dana@omni", "BTC")                                         # in kind
        self.assertEqual((q1.amount, q2.amount, q3.amount), (D(100), D("0.001"), D("0.001")))  # mid-market
        self.assertEqual(self.bal("bob@omni", "USD"), D(200))                        # Bob got both invoices in full
        self.assertEqual(self.bal("alice@omni", "USD"), D(900))
        self.assertTrue(all(self.bal("sys:revenue", c) == 0 for c in ("USD", "EUR", "BTC")))
        self.assertSound()

    def test_shares_only_move_to_a_payee_who_opted_in(self):
        self.sw.deposit("alice@omni", "AAPL", "5")
        self.sw.deposit("alice@omni", "TSLA", "5")
        with self.assertRaises(PaymentError) as c:
            self.pay("dana@omni", "TSLA")                                            # she never opted in
        self.assertEqual(c.exception.code, "PAYEE_NOT_ACCEPTING")
        q, p = self.pay("dana@omni", "AAPL")                                         # she did for AAPL
        self.assertEqual((q.route, p.state, self.bal("dana@omni", "AAPL")), ("direct", State.SETTLED, q.receive))
        self.sw.accept("dana@omni", "TSLA")
        self.assertIs(self.pay("dana@omni", "TSLA")[1].state, State.SETTLED)
        self.sw.decline("dana@omni", "TSLA")                                         # and she can opt out again
        with self.assertRaises(PaymentError):
            self.pay("dana@omni", "TSLA")
        self.assertEqual(self.bal("sys:custody", "AAPL"), 0)                         # platform never owns shares
        self.assertSound()

    def test_policy_gates_assets_and_regions(self):
        self.sw.deposit("alice@omni", "BTC", "0.05")
        self.sw.deposit("alice@omni", "USD", "1000")
        with self.assertRaises(PaymentError) as c:
            self.pay("ravi@omni", "BTC")                                             # crypto is off in IN
        self.assertEqual(c.exception.code, "ASSET_DISABLED")
        self.pay("ravi@omni", "USD")                                                 # fiat is fine: converted to INR
        self.assertEqual(self.bal("ravi@omni", "INR"), D("8333.34"))
        self.sw.register("kim@omni", region="KP")
        with self.assertRaises(PaymentError) as c:
            self.pay("kim@omni", "USD")
        self.assertEqual(c.exception.code, "REGION_BLOCKED")
        self.assertSound()

    def test_quote_expires_and_stale_prices_are_refused(self):
        self.sw.deposit("alice@omni", "BTC", "0.05")
        q = self.sw.quote("alice@omni", "dana@omni", "100", "USD", "BTC")
        self.t += 31
        with self.assertRaises(PaymentError) as c:
            self.sw.pay(q.id, "1234")
        self.assertEqual(c.exception.code, "QUOTE_EXPIRED")
        with self.assertRaises(PaymentError) as c:
            self.sw.quote("alice@omni", "dana@omni", "100", "USD", "BTC")
        self.assertEqual(c.exception.code, "NO_PRICE")                               # feed is 31 s old

    def test_retry_is_idempotent(self):
        self.sw.deposit("alice@omni", "USD", "1000")
        q = self.sw.quote("alice@omni", "bob@omni", "100", "USD", "USD")
        a, b = self.sw.pay(q.id, "1234"), self.sw.pay(q.id, "1234")
        self.assertIs(a, b)
        self.assertEqual(self.bal("bob@omni", "USD"), D(100))

    def test_pin_lockout(self):
        self.sw.deposit("alice@omni", "USD", "1000")
        q = self.sw.quote("alice@omni", "bob@omni", "100", "USD", "USD")
        for _ in range(3):
            with self.assertRaises(PaymentError):
                self.sw.pay(q.id, "0000")
        with self.assertRaises(PaymentError) as c:                                   # even the right PIN is refused
            self.sw.pay(q.id, "1234")
        self.assertEqual(c.exception.code, "PIN_LOCKED")

    def test_failed_execute_and_no_liquidity_release_the_hold(self):
        self.sw.deposit("alice@omni", "USD", "1000")
        self.sw.execute = lambda p: False                                            # local-rail push fails
        _, p = self.pay("dana@omni", "USD")
        self.assertEqual((p.state, p.reason), (State.FAILED, "EXECUTE_FAILED"))
        self.assertEqual(self.bal("alice@omni", "USD"), D(1000))                     # funds back, nothing leaked
        self.sw.execute = lambda p: True
        self.ledger.post("drain", [("sys:treasury", "EUR", -D(100_000)), ("ext:fiat", "EUR", D(100_000))])
        _, p = self.pay("dana@omni", "USD")                                          # treasury has no EUR left
        self.assertEqual(p.reason, "NO_LIQUIDITY")
        self.assertEqual(self.bal("alice@omni", "USD"), D(1000))
        self.assertSound()

    def test_no_double_spend_under_concurrency(self):
        self.sw.deposit("alice@omni", "USD", "1000")
        qs = [self.sw.quote("alice@omni", "bob@omni", "100", "USD", "USD") for _ in range(20)]
        with ThreadPoolExecutor(8) as ex:
            res = list(ex.map(lambda q: self.sw.pay(q.id, "1234"), qs))
        self.assertEqual(sum(r.state is State.SETTLED for r in res), 10)             # 1000 / 100, no more
        self.assertEqual(self.bal("alice@omni", "USD"), 0)
        self.assertEqual(self.bal("bob@omni", "USD"), 1000)
        self.assertSound()

    def test_exposure_cap_limits_inventory_from_conversions(self):
        self.sw.deposit("alice@omni", "BTC", "0.05")
        _, p = self.pay("bob@omni", "BTC")                                           # Bob wants USD: platform converts
        self.assertIs(p.state, State.SETTLED)
        self.assertEqual(self.sw.exposure_usd(), D(100))                             # and now carries 0.001 BTC
        self.sw.exposure_cap = self.sw.exposure_usd() + 50                           # room for less than one more
        q = self.sw.quote("alice@omni", "bob@omni", "100", "USD", "BTC")
        with self.assertRaises(PaymentError) as c:
            self.sw.pay(q.id, "1234")
        self.assertEqual(c.exception.code, "EXPOSURE_CAP")

    def test_whole_unit_currencies_and_bad_amounts(self):
        self.sw.deposit("alice@omni", "BTC", "0.05")
        with self.assertRaises(PaymentError) as c:
            self.pay("dana@omni", "BTC", "1500.5", "JPY")                            # yen has no decimals
        self.assertEqual(c.exception.code, "BAD_AMOUNT")
        q, _ = self.pay("dana@omni", "BTC", "15000", "JPY")                          # JPY invoice, paid in BTC, in kind
        self.assertEqual((q.route, q.amount, q.receive), ("direct", D("0.001005"), D("0.001005")))
        self.assertSound()


if __name__ == "__main__":
    unittest.main()
