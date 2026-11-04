"""Product rule 2: OmniPay charges nothing. No fee, no conversion fee, no spread."""
import random
import unittest
from decimal import ROUND_DOWN, Decimal as D

from omnipay import ASSETS, REVENUE, State
from tests.helpers import Env


class ZeroFeeTests(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        self.sw = self.e.seed()
        self.sw.register("sam@omni", "1234", region="US")
        for c, a in (("USD", "100000"), ("EUR", "100000"), ("USDC", "100000"), ("BTC", "5"), ("ETH", "50"),
                     ("JPY", "10000000"), ("AAPL", "100"), ("TSLA", "100")):
            self.sw.deposit("sam@omni", c, a)
        self.sw.accept("bob@omni", "USDC")

    def test_payer_pays_exactly_mid_market_rounded_to_smallest_unit(self):
        rnd = random.Random(7)
        n = 0
        for payee in ("bob@omni", "dana@omni"):
            for code, a in ASSETS.items():
                for _ in range(15):
                    inv = D(rnd.randint(100, 900_000)) / D(100)
                    try:
                        q = self.sw.quote("sam@omni", payee, str(inv), "USD", code)
                    except Exception:
                        continue
                    exact = inv * self.e.oracle.cross("USD", code)
                    # nothing added: the amount is the mid-market value rounded half-up to the asset's smallest unit
                    self.assertEqual(q.amount, a.q(exact))
                    self.assertLessEqual(abs(q.amount - exact), D(1).scaleb(-a.dp) / 2)
                    # the payee is never shorted: they get at least the invoice value (rounded in their favour)
                    recv_exact = inv * self.e.oracle.cross("USD", q.receive_asset)
                    if q.route == "convert":
                        self.assertGreaterEqual(q.receive, recv_exact)
                        self.assertLess(q.receive - recv_exact, D(1).scaleb(-ASSETS[q.receive_asset].dp))
                    else:
                        self.assertEqual(q.receive, q.amount)
                    n += 1
        self.assertGreater(n, 80)

    def test_no_ledger_leg_ever_goes_to_a_fee_account(self):
        for payee in ("bob@omni", "dana@omni", "ravi@omni"):
            for code in ("USD", "EUR", "BTC", "USDC"):
                try:
                    q = self.sw.quote("sam@omni", payee, "123.45", "USD", code)
                except Exception:
                    continue
                self.assertEqual(self.sw.pay(q.id, "1234").state, State.SETTLED)
        accounts = {a for j in self.e.ledger.journal for a, _, _ in j["legs"]}
        self.assertFalse(any(a == REVENUE or "fee" in a or "spread" in a for a in accounts), accounts)
        for code in ASSETS:
            self.assertEqual(self.e.ledger.balance(REVENUE, code), 0)
        self.assertEqual(self.e.ledger.audit(), [])

    def test_direct_route_is_value_neutral_and_convert_moves_exactly_what_was_quoted(self):
        q = self.sw.quote("sam@omni", "dana@omni", "100", "USD", "BTC")      # dana takes BTC in kind
        before = {c: self.e.ledger.balance("user:sam@omni", c) for c in ("BTC",)}
        self.sw.pay(q.id, "1234")
        self.assertEqual(before["BTC"] - self.e.ledger.balance("user:sam@omni", "BTC"), q.amount)
        self.assertEqual(self.e.ledger.balance("user:dana@omni", "BTC"), q.amount)
        self.assertEqual(self.e.ledger.balance("sys:custody", "BTC"), 0)       # nothing kept by the platform
        q = self.sw.quote("sam@omni", "dana@omni", "100", "USD", "USD")      # convert: payer USD -> payee EUR
        self.sw.pay(q.id, "1234")
        self.assertEqual(self.e.ledger.balance("sys:custody", "USD"), q.amount)   # exactly the quoted amount, no skim
        self.assertEqual(q.amount, D("100.00"))                                  # a USD 100 bill costs USD 100.00

    def test_promoter_charges_never_touch_the_payer_price(self):
        from promo import PromoMarket
        m = PromoMarket(self.sw)
        self.sw.register("pro@omni", "1234", region="US")
        self.sw.deposit("pro@omni", "USD", "1000")
        base = self.sw.price("sam@omni", "bob@omni", "100", "USD", "USDC").amount
        m.bid("pro@omni", "USDC", 50, pay_with="USD", budget="100")
        self.assertEqual(self.sw.price("sam@omni", "bob@omni", "100", "USD", "USDC").amount, base)


if __name__ == "__main__":
    unittest.main()
