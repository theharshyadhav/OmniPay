import unittest
from decimal import Decimal as D

from omnipay import Kind, Ledger, Oracle, PaymentError, State, Switch
from promo import PromoMarket

Switch.PIN_ROUNDS = 1_000  # fast KDF for tests


class PromoTests(unittest.TestCase):
    def setUp(self):
        self.t = 1_700_000_000.0
        clock = lambda: self.t
        self.ledger, self.oracle = Ledger(clock), Oracle(clock)
        self.sw = Switch(self.ledger, self.oracle, clock)
        self.market = PromoMarket(self.sw)
        for code, price in {"EUR": "1.08", "USDC": 1, "BTC": 100_000, "ETH": 3_500, "AAPL": 230}.items():
            self.oracle.publish(code, price)
        self.sw.register("alice@omni", "1234", region="US")
        self.sw.register("hans@omni", "1234", region="DE")
        self.sw.register("bob@omni", region="US")                                    # wants plain USD
        self.sw.register("dana@omni", region="DE", settle_in="EUR", accepts=["BTC"])  # takes BTC in kind
        for promoter in ("circle@omni", "kraken@omni", "whale@omni"):
            self.sw.register(promoter, region="US")
        self.sw.fund_treasury("USD", "1000000")
        self.sw.fund_treasury("EUR", "1000000")
        for who in ("alice@omni", "hans@omni"):
            for code, amount in (("USD", "1000"), ("EUR", "500"), ("USDC", "1000"), ("BTC", "0.05"), ("ETH", "1")):
                self.sw.deposit(who, code, amount)
        self.sw.deposit("circle@omni", "USD", "1000")
        self.sw.deposit("kraken@omni", "EUR", "1000")
        self.sw.deposit("whale@omni", "BTC", "0.1")
        self.sw.deposit("whale@omni", "AAPL", "10")

    def listing(self, payer="alice@omni", payee="bob@omni", invoice="100", market=None):
        return (market or self.market).options(payer, payee, invoice, "USD")

    @staticmethod
    def option(listing, asset):
        return next(o for o in listing.options if o.asset == asset)

    @staticmethod
    def order(listing):
        return [o.asset for o in listing.options]

    def bal(self, who, code):
        return self.ledger.balance(f"user:{who}" if "@" in who and ":" not in who else who, code)

    def assertSound(self):
        self.assertTrue(all(v == 0 for v in self.ledger.trial_balance().values()))

    def test_the_list_only_shows_what_the_payer_can_pay_with(self):
        l = self.listing()
        self.assertEqual(self.order(l), ["USD", "EUR", "BTC", "ETH", "USDC"])        # in kind, then fiat, then crypto
        self.assertFalse(any(o.sponsored for o in l.options))
        self.assertEqual(l.default, "USD")

    def test_more_bps_means_a_higher_position_and_you_pay_one_step_above_the_next_bid(self):
        self.market.bid("circle@omni", "USDC", 20, pay_with="USD", budget="100")
        self.market.bid("kraken@omni", "BTC", 30, pay_with="EUR", budget="100")
        self.market.bid("whale@omni", "ETH", 15, pay_with="BTC", budget="0.01")
        l = self.listing()
        self.assertEqual(self.order(l), ["BTC", "USDC", "ETH", "USD", "EUR"])
        self.assertEqual([o.sponsored for o in l.options], [True, True, True, False, False])
        self.assertEqual([o.price_bps for o in l.options[:3]], [D("20.1"), D("15.1"), D("10")])
        self.market.bid("whale@omni", "ETH", 45, pay_with="BTC", budget="0.001")     # outbid everyone
        l = self.listing()
        self.assertEqual(self.order(l)[:3], ["ETH", "BTC", "USDC"])
        self.assertEqual([o.price_bps for o in l.options[:3]], [D("30.1"), D("20.1"), D("10")])

    def test_slots_cap_the_sponsored_positions(self):
        m = PromoMarket(self.sw, slots=1)
        m.bid("circle@omni", "USDC", 20, pay_with="USD", budget="100")
        m.bid("kraken@omni", "BTC", 30, pay_with="EUR", budget="100")
        l = self.listing(market=m)
        self.assertEqual(self.order(l), ["BTC", "USD", "EUR", "ETH", "USDC"])        # USDC lost the only slot
        self.assertEqual(self.option(l, "BTC").price_bps, D("20.1"))                 # still priced against the runner-up

    def test_reserve_price_floors_conversions_higher_than_in_kind(self):
        self.market.bid("kraken@omni", "BTC", 5, pay_with="EUR", budget="100")
        self.assertFalse(self.option(self.listing(), "BTC").sponsored)               # Bob wants USD: a conversion, floor 10 bps
        o = self.option(self.listing(payee="dana@omni"), "BTC")                      # Dana takes BTC in kind: floor 1 bp
        self.assertTrue(o.sponsored)
        self.assertEqual((o.quote.route, o.price_bps), ("direct", D(1)))
        with self.assertRaises(PaymentError) as c:
            self.market.bid("kraken@omni", "ETH", "0.5", pay_with="EUR", budget="1")
        self.assertEqual(c.exception.code, "BID_BELOW_RESERVE")

    def test_a_promoter_pays_only_when_a_sponsored_tap_settles(self):
        self.market.bid("kraken@omni", "BTC", 30, pay_with="EUR", budget="100")
        pay = lambda q: self.sw.pay(q.id, "1234")
        pay(self.sw.quote("alice@omni", "bob@omni", "100", "USD", "BTC"))            # organic quote, no listing
        self.sw.execute = lambda p: False
        self.assertIs(pay(self.market.quote(self.listing().id, "BTC")).state, State.FAILED)   # sponsored, but it fails
        self.sw.execute = lambda p: True
        pay(self.market.quote(self.listing().id, "USD"))                             # sponsored row ignored, paid in USD
        self.assertEqual(self.bal("sys:revenue", "EUR"), 0)
        q = self.market.quote(self.listing().id, "BTC")                              # a sponsored tap that settles
        p, again = pay(q), pay(q)
        self.assertIs(p.state, State.SETTLED)
        self.assertIs(p, again)                                                       # a retry never bills twice
        self.assertEqual(self.bal("sys:revenue", "EUR"), D("0.10"))                  # 10 bps of USD 100, in EUR plus haircut
        self.assertEqual(self.bal("promo:kraken@omni/BTC", "EUR"), D("99.90"))
        self.assertEqual(self.bal("alice@omni", "BTC"), D("0.05") - 2 * D("0.001"))  # she paid mid-market, twice
        self.assertSound()

    def test_promoters_can_pay_in_any_asset_and_risky_assets_cost_a_little_more(self):
        self.market.bid("whale@omni", "USDC", 30, pay_with="AAPL", budget="1")       # paying in shares
        l = self.listing()
        self.assertEqual(self.order(l)[0], "USDC")
        self.sw.pay(self.market.quote(l.id, "USDC").id, "1234")
        self.assertEqual(self.bal("sys:revenue", "AAPL"), D("0.000435"))             # USD 0.10 / 230, rounded up
        self.market.cancel_bid("whale@omni", "USDC")
        self.market.bid("whale@omni", "ETH", 30, pay_with="BTC", budget="0.001")     # paying in BTC (150 bps haircut)
        l = self.listing()
        self.sw.pay(self.market.quote(l.id, "ETH").id, "1234")
        self.assertEqual(self.bal("sys:revenue", "BTC"), D("0.00000102"))
        self.assertGreater(self.bal("sys:revenue", "BTC"), D("0.10") / 100_000)      # more than the mid-market amount
        self.assertSound()

    def test_a_bid_drops_out_when_its_budget_cannot_cover_another_payment(self):
        m = PromoMarket(self.sw, pricing="first")                                    # first price: you pay what you bid
        m.bid("kraken@omni", "BTC", 30, pay_with="EUR", budget="0.50")
        l = self.listing(market=m)
        self.assertTrue(self.option(l, "BTC").sponsored)
        self.sw.pay(m.quote(l.id, "BTC").id, "1234")
        self.assertEqual(self.bal("sys:revenue", "EUR"), D("0.28"))                  # 30 bps of USD 100 plus haircut
        self.assertFalse(self.option(self.listing(market=m), "BTC").sponsored)       # 0.22 EUR left < 0.28

    def test_region_targeting_ad_rules_and_risk_notices(self):
        m = PromoMarket(self.sw, no_ads={"DE": {Kind.CRYPTO}}, warnings={Kind.CRYPTO: "Crypto prices move."})
        m.bid("kraken@omni", "BTC", 30, pay_with="EUR", budget="100", regions=["US", "DE"])
        m.bid("circle@omni", "EUR", 20, pay_with="USD", budget="100", regions=["DE"])
        us = self.listing("alice@omni", market=m)
        self.assertEqual(self.order(us)[0], "BTC")                                   # US: BTC ads allowed...
        self.assertEqual(self.option(us, "BTC").notice, "Crypto prices move.")       # ...with a risk notice
        self.assertFalse(self.option(us, "EUR").sponsored)                           # the EUR bid targets DE only
        de = self.listing("hans@omni", market=m)
        self.assertFalse(self.option(de, "BTC").sponsored)                           # DE bans crypto ads; BTC is still payable
        self.assertTrue(self.option(de, "EUR").sponsored)

    def test_cancel_refunds_the_unspent_escrow(self):
        self.market.bid("whale@omni", "ETH", 30, pay_with="BTC", budget="0.01")
        self.assertEqual(self.bal("whale@omni", "BTC"), D("0.09"))
        self.sw.pay(self.market.quote(self.listing().id, "ETH").id, "1234")
        self.market.cancel_bid("whale@omni", "ETH")
        self.assertEqual(self.bal("promo:whale@omni/ETH", "BTC"), 0)
        self.assertEqual(self.bal("whale@omni", "BTC"), D("0.1") - self.bal("sys:revenue", "BTC"))
        self.assertFalse(self.option(self.listing(), "ETH").sponsored)
        self.assertSound()

    def test_a_sponsored_row_is_never_preselected(self):
        self.market.bid("kraken@omni", "BTC", 30, pay_with="EUR", budget="100")
        l = self.listing()
        self.assertEqual((self.order(l)[0], l.default), ("BTC", "USD"))              # BTC on top, USD preselected
        self.sw.register("zed@omni", "1234", region="US")
        self.sw.deposit("zed@omni", "BTC", "0.05")                                   # zed holds only BTC
        only = self.listing("zed@omni")
        self.assertEqual((self.order(only), only.default), (["BTC"], ""))            # nothing organic: no default

    def test_dust_payments_carry_no_ad(self):
        self.market.bid("kraken@omni", "BTC", 30, pay_with="EUR", budget="100")
        self.assertFalse(self.option(self.listing(invoice="0.50"), "BTC").sponsored)
        self.assertTrue(self.option(self.listing(invoice="5"), "BTC").sponsored)

    def test_economics_promoters_must_cover_the_cost_of_free_conversions(self):
        self.market.bid("kraken@omni", "BTC", 30, pay_with="EUR", budget="100")
        pay = lambda q: self.sw.pay(q.id, "1234")
        pay(self.market.quote(self.listing().id, "BTC"))                             # sponsored conversion
        pay(self.sw.quote("alice@omni", "bob@omni", "100", "USD", "ETH"))            # unsponsored conversion
        e = self.market.economics()
        self.assertEqual(e["revenue_usd"], D("0.10") * D("1.08"))                    # EUR 0.10 at 1.08
        self.assertEqual(e["conversion_cost_usd"], D("0.2"))                         # USD 200 converted at 10 bps
        self.assertEqual(e["net_usd"], e["revenue_usd"] - e["conversion_cost_usd"])
        self.assertLess(e["net_usd"], 0)                                              # the unsponsored one isn't covered

    def test_bids_need_a_registered_promoter_an_accepted_funding_asset_and_money(self):
        m = PromoMarket(self.sw, fund_assets={"USD", "EUR"})
        with self.assertRaises(PaymentError) as c:
            m.bid("nobody@omni", "BTC", 30, pay_with="USD", budget="1")
        self.assertEqual(c.exception.code, "UNKNOWN_VPA")
        with self.assertRaises(PaymentError) as c:
            m.bid("kraken@omni", "BTC", 30, pay_with="BTC", budget="1")
        self.assertEqual(c.exception.code, "FUNDING_NOT_ACCEPTED")
        with self.assertRaises(PaymentError) as c:
            m.bid("kraken@omni", "BTC", 30, pay_with="EUR", budget="5000")
        self.assertEqual(c.exception.code, "INSUFFICIENT_FUNDS")
        self.assertSound()


if __name__ == "__main__":
    unittest.main()
