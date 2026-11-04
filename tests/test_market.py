import unittest
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal as D

from omnipay import PaymentError, State
from app.platform import DEMO_PRICES, Platform
from tests.pf import mk, seed_users


def setup():
    pf = seed_users(mk())
    for h in ("pro1@omni", "pro2@omni", "pro3@omni"):
        pf.signup(h, "correct-horse-1", "1234", region="US")
        pf.sw.deposit(h, "USD", "1000")
    pf.sw.accept("bob@omni", "USDC")
    pf.sw.deposit("alice@omni", "USD", "5000"); pf.sw.deposit("alice@omni", "USDC", "5000"); pf.sw.deposit("alice@omni", "EUR", "5000")
    return pf


class PersistenceTests(unittest.TestCase):
    def test_bids_listings_and_reservations_survive_a_restart(self):
        pf = setup()
        pf.promo.bid("pro1@omni", "USDC", 40, pay_with="USD", budget="100")
        lst = pf.promo.options("alice@omni", "bob@omni", "100", "USD")
        q = pf.promo.quote(lst.id, "USDC")
        pf2 = Platform(pf.cfg, clock=pf.clock_obj, paypal=pf.paypal, prices=DEMO_PRICES, scrypt_n=16)     # "restart"
        self.assertEqual([b.asset for b in pf2.promo.bids.values()], ["USDC"])
        self.assertEqual(pf2.promo._listings.get(lst.id).options[0].promoter, "pro1@omni")
        self.assertEqual(pf2.sw.pay(q.id, "1234").state, State.SETTLED)
        self.assertEqual(pf2.ledger.audit(), [])


class AuctionTests(unittest.TestCase):
    def setUp(self):
        self.pf = setup()

    def test_sponsored_rows_are_labelled_never_preselected_and_only_payable(self):
        self.pf.promo.bid("pro1@omni", "USDC", 40, pay_with="USD", budget="100")
        self.pf.promo.bid("pro2@omni", "BTC", 90, pay_with="USD", budget="100")      # alice owns no BTC -> not listed
        lst = self.pf.promo.options("alice@omni", "bob@omni", "100", "USD")
        self.assertEqual([o.asset for o in lst.options if o.sponsored], ["USDC"])
        self.assertEqual([o.label for o in lst.options][:1], ["Sponsored"])
        self.assertFalse(next(o for o in lst.options if o.asset == lst.default).sponsored)
        self.assertNotIn("BTC", [o.asset for o in lst.options])

    def test_generalised_second_price(self):
        self.pf.promo.bid("pro1@omni", "USDC", 40, pay_with="USD", budget="100")
        self.pf.promo.bid("pro2@omni", "EUR", 25, pay_with="USD", budget="100")
        lst = self.pf.promo.options("alice@omni", "bob@omni", "100", "USD")
        top = lst.options[0]
        self.assertEqual((top.asset, top.price_bps), ("USDC", D("25.1")))            # one step above the next bid
        self.assertEqual(lst.options[1].price_bps, D("10"))                          # last bidder pays the reserve

    def test_sponsorship_never_changes_the_payers_price(self):
        organic = self.pf.sw.price("alice@omni", "bob@omni", "100", "USD", "USDC")
        self.pf.promo.bid("pro1@omni", "USDC", 90, pay_with="USD", budget="100")
        lst = self.pf.promo.options("alice@omni", "bob@omni", "100", "USD")
        self.assertEqual(next(o for o in lst.options if o.asset == "USDC").quote.amount, organic.amount)

    def test_kill_switch_and_region_policy_beat_money(self):
        self.pf.promo.bid("pro1@omni", "USDC", 90, pay_with="USD", budget="100")
        self.pf.switches.set("asset", "USDC", False, "admin")
        lst = self.pf.promo.options("alice@omni", "bob@omni", "100", "USD")
        self.assertNotIn("USDC", [o.asset for o in lst.options])
        self.pf.switches.set("asset", "USDC", True, "admin")
        self.pf.switches.set("kind", "crypto", False, "admin")
        self.assertNotIn("USDC", [o.asset for o in self.pf.promo.options("alice@omni", "bob@omni", "100", "USD").options])
        self.pf.switches.set("kind", "crypto", True, "admin"); self.pf.switches.set("global", "*", False, "admin")
        with self.assertRaises(PaymentError) as cm:
            self.pf.promo.options("alice@omni", "bob@omni", "100", "USD")
        self.assertEqual(cm.exception.code, "KILL_SWITCH")


class BillingTests(unittest.TestCase):
    def setUp(self):
        self.pf = setup()
        self.L = self.pf.ledger

    def tap(self, asset="USDC", invoice="100", payer="alice@omni"):
        lst = self.pf.promo.options(payer, "bob@omni", invoice, "USD")
        return self.pf.promo.quote(lst.id, asset)

    def test_max_charge_is_reserved_at_tap_and_the_remainder_returned_at_settlement(self):
        self.pf.promo.bid("pro1@omni", "USDC", 40, pay_with="USD", budget="100")
        self.pf.promo.bid("pro2@omni", "EUR", 25, pay_with="USD", budget="100")
        escrow = lambda: self.L.balance("promo:pro1@omni/USDC", "USD")
        q = self.tap()
        reserve = self.L.balance(f"promo-hold:{q.id}", "USD")
        self.assertGreater(reserve, 0)
        self.assertEqual(escrow(), D(100) - reserve)                                   # out of escrow immediately
        p = self.pf.sw.pay(q.id, "1234")
        self.assertEqual(p.state, State.SETTLED)
        billed = D(self.L.conn().execute("SELECT billed FROM attributions").fetchone()[0])
        self.assertEqual(self.L.balance("sys:revenue", "USD"), billed)
        self.assertLessEqual(billed, reserve)
        self.assertEqual(escrow(), D(100) - billed)                                    # unspent reservation came back
        self.assertEqual(self.L.balance(f"promo-hold:{q.id}", "USD"), 0)
        self.assertEqual(self.L.audit(), [])

    def test_budget_cannot_be_overspent_by_simultaneous_taps(self):
        self.pf.promo.bid("pro1@omni", "USDC", 100, pay_with="USD", budget="1.00")     # 1%: a USD 20 tap reserves 0.20, room for 5
        self.pf.promo.PAYER_DAILY_CAP = 100
        self.pf.promo.pricing = "first"                                                # pay what you bid, so each tap really costs 0.20
        ids = [self.pf.promo.options("alice@omni", "bob@omni", "20", "USD").id for _ in range(12)]
        def tap(i):
            try:
                return self.pf.promo.quote(i, "USDC").id
            except PaymentError:
                return None
        with ThreadPoolExecutor(12) as ex:
            qs = [q for q in ex.map(tap, ids) if q]
        reserved = self.L.conn().execute("SELECT COUNT(*) FROM attributions").fetchone()[0]
        self.assertTrue(1 <= reserved <= 5, reserved)                                  # the 6th+ taps were served unsponsored
        for q in qs:
            self.pf.sw.pay(q, "1234")
        paid = self.L.balance("sys:revenue", "USD")
        self.assertLessEqual(paid, D("1.00"))                                          # never more than the prepaid escrow
        self.assertGreaterEqual(self.L.balance("promo:pro1@omni/USDC", "USD"), 0)
        self.assertEqual(self.L.audit(), [])

    def test_failed_payment_releases_the_reservation_and_bills_nothing(self):
        self.pf.promo.bid("pro1@omni", "USDC", 40, pay_with="USD", budget="100")
        q = self.tap()
        self.pf.sw.execute = lambda p: False
        self.assertEqual(self.pf.sw.pay(q.id, "1234").state, State.FAILED)
        self.assertEqual(self.L.balance("promo:pro1@omni/USDC", "USD"), D(100))
        self.assertEqual(self.L.balance("sys:revenue", "USD"), 0)
        self.assertEqual(self.L.conn().execute("SELECT state FROM attributions").fetchone()[0], "RELEASED")

    def test_abandoned_tap_is_released_by_the_sweep(self):
        self.pf.promo.bid("pro1@omni", "USDC", 40, pay_with="USD", budget="100")
        q = self.tap()
        self.assertEqual(self.pf.promo.sweep()["released"], 0)                        # still inside the quote window
        self.pf.clock_obj.t += 60
        self.assertEqual(self.pf.promo.sweep()["released"], 1)
        self.assertEqual(self.L.balance("promo:pro1@omni/USDC", "USD"), D(100))

    def test_unknown_payment_keeps_the_reservation_until_reconciled(self):
        self.pf.promo.bid("pro1@omni", "USDC", 40, pay_with="USD", budget="100")
        q = self.tap()
        self.pf.sw.execute = lambda p: (_ for _ in ()).throw(TimeoutError())
        with self.assertRaises(TimeoutError):
            self.pf.sw.pay(q.id, "1234")
        self.pf.clock_obj.t += 600
        self.pf.promo.sweep()
        self.assertGreater(self.L.balance(f"promo-hold:{q.id}", "USD"), 0)            # still reserved
        self.pf.sw.resolve_held(self.pf.sw.held_payments()[0], executed=True)
        self.assertEqual(self.L.balance(f"promo-hold:{q.id}", "USD"), 0)
        self.assertGreater(self.L.balance("sys:revenue", "USD"), 0)

    def test_unbilled_payment_is_retried_when_the_price_returns(self):
        self.pf.promo.bid("pro1@omni", "EUR", 40, pay_with="USD", budget="100")       # funded in USD...
        self.pf.promo.bid("pro3@omni", "USDC", 40, pay_with="USD", budget="100")
        q = self.tap("USDC")
        real = self.pf.promo._charge
        self.pf.promo._charge = lambda *a, **k: (_ for _ in ()).throw(PaymentError("NO_PRICE", "feed down"))
        self.assertEqual(self.pf.sw.pay(q.id, "1234").state, State.SETTLED)           # payer is never held hostage by billing
        self.assertEqual(self.L.conn().execute("SELECT state FROM attributions").fetchone()[0], "UNBILLED")
        self.assertEqual(len(self.pf.promo.unbilled), 1)
        self.pf.promo._charge = real
        self.assertEqual(self.pf.promo.sweep()["billed"], 1)
        self.assertEqual(self.L.conn().execute("SELECT state FROM attributions").fetchone()[0], "BILLED")
        self.assertGreater(self.L.balance("sys:revenue", "USD"), 0)
        self.assertEqual(self.pf.promo.sweep()["billed"], 0)                          # and only once

    def test_a_settled_payment_whose_billing_hook_never_ran_is_still_billed(self):
        self.pf.promo.bid("pro1@omni", "USDC", 40, pay_with="USD", budget="100")
        q = self.tap()
        self.pf.sw.on_settled = None                                                  # crash between commit and hook
        self.pf.sw.pay(q.id, "1234")
        self.assertEqual(self.L.balance("sys:revenue", "USD"), 0)
        self.pf.promo.sweep()
        self.assertGreater(self.L.balance("sys:revenue", "USD"), 0)

    def test_click_fraud_caps_stop_a_payer_draining_one_promoters_budget(self):
        self.pf.promo.PAYER_DAILY_CAP = 2
        self.pf.promo.bid("pro1@omni", "USDC", 40, pay_with="USD", budget="100")
        results = []
        for _ in range(5):
            q = self.tap(invoice="20")
            self.pf.sw.pay(q.id, "1234")
            results.append(self.L.conn().execute("SELECT COUNT(*) FROM attributions").fetchone()[0])
        self.assertEqual(results[-1], 2)                                              # taps 3-5 were organic: nobody billed
        self.assertEqual(self.L.audit(), [])

    def test_dust_payments_carry_no_ad(self):
        self.pf.promo.bid("pro1@omni", "USDC", 40, pay_with="USD", budget="100")
        lst = self.pf.promo.options("alice@omni", "bob@omni", "0.50", "USD")
        self.assertFalse(any(o.sponsored for o in lst.options))

    def test_haircut_makes_risky_funding_cost_more(self):
        self.pf.sw.deposit("pro2@omni", "BTC", "1")
        self.pf.promo.bid("pro1@omni", "USDC", 40, pay_with="USD", budget="100")
        self.pf.promo.bid("pro2@omni", "EUR", 40, pay_with="BTC", budget="0.5")
        b1 = self.pf.promo._charge(self.pf.promo.bids.get(("pro1@omni", "USDC")), D(1000), D(10)) * 1
        b2 = self.pf.promo._charge(self.pf.promo.bids.get(("pro2@omni", "EUR")), D(1000), D(10)) * self.pf.oracle.usd("BTC")
        self.assertGreater(b2, b1)


class AdvisorSupportTests(unittest.TestCase):
    def setUp(self):
        self.pf = setup()
        self.pf.promo.bid("pro2@omni", "EUR", 25, pay_with="USD", budget="100")
        self.pf.promo.bid("pro3@omni", "USDC", 60, pay_with="USD", budget="100")

    def test_landscape_is_anonymous(self):
        d = self.pf.promo.landscape("USDC")
        self.assertEqual((d["bids_on_asset"], d["top_bps"]), (1, "60"))
        self.assertNotIn("pro3", str(d))

    def test_simulation_matches_the_real_auction(self):
        s = self.pf.promo.simulate("pro1@omni", "ETH", 40, "convert")
        self.assertEqual((s["rank"], s["shown"], s["would_pay_bps"]), (2, True, "25.1"))
        self.pf.sw.accept("bob@omni", "ETH"); self.pf.sw.deposit("alice@omni", "ETH", "5")
        self.pf.promo.bid("pro1@omni", "ETH", 40, pay_with="USD", budget="100")
        lst = self.pf.promo.options("alice@omni", "bob@omni", "100", "USD")
        eth = next(o for o in lst.options if o.asset == "ETH")
        self.assertEqual(str(eth.price_bps), s["would_pay_bps"])                       # the simulation told the truth
        low = self.pf.promo.simulate("pro1@omni", "ETH", 5, "convert")
        self.assertEqual((low["shown"], low["rank"]), (False, 3))                      # below the 10 bps conversion reserve

    def test_autobidder_stays_inside_max_and_pauses_at_its_daily_cap(self):
        P = self.pf.promo
        self.pf.sw.accept("bob@omni", "ETH"); self.pf.sw.deposit("alice@omni", "ETH", "5")
        P.bid("pro1@omni", "ETH", 12, pay_with="USD", budget="500")
        P.set_autobid("pro1@omni", "ETH", max_bps=100, daily_cap_usd="1000", total_cap_usd="5000")
        P.run_autobidders()
        self.assertEqual(P.bids.get(("pro1@omni", "ETH")).bps, D("10.1"))               # two rivals, third slot open: floor + one step
        P.bid("pro3@omni", "BTC", 15, pay_with="USD", budget="50")                     # a third rival appears
        P.run_autobidders()
        self.assertEqual(P.bids.get(("pro1@omni", "ETH")).bps, D("15.1"))               # now it must beat the last visible slot
        P.set_autobid("pro1@omni", "ETH", max_bps=12, daily_cap_usd="1000", total_cap_usd="5000")
        P.run_autobidders()
        self.assertEqual(P.bids.get(("pro1@omni", "ETH")).bps, D("12"))                 # never above its own max
        P.set_autobid("pro1@omni", "ETH", max_bps=100, daily_cap_usd="0.001", total_cap_usd="5000")
        lst = P.options("alice@omni", "bob@omni", "1000", "USD")
        q = P.quote(lst.id, "ETH"); self.pf.sw.pay(q.id, "1234")
        P.run_autobidders()
        self.assertEqual(P.bids.get(("pro1@omni", "ETH")).bps, D("1"))                  # spent its day: dropped to the minimum
        with self.assertRaises(PaymentError):
            P.set_autobid("pro2@omni", "BTC", max_bps=10, daily_cap_usd="1", total_cap_usd="1")   # needs a funded bid first


if __name__ == "__main__":
    unittest.main()
