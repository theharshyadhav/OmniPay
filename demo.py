"""Demo: free for users, paid for by promoters.   Run: python demo.py"""
from omnipay import ASSETS, REVENUE, Kind, Ledger, Oracle, Switch
from promo import PromoMarket


def main() -> None:
    now = [1_700_000_000.0]
    clock = lambda: now[0]
    ledger, oracle = Ledger(clock), Oracle(clock)
    sw = Switch(ledger, oracle, clock)
    market = PromoMarket(sw, warnings={Kind.CRYPTO: "crypto prices move"})
    for code, price in {"EUR": "1.08", "USDC": 1, "BTC": 100_000, "ETH": 3_500}.items():
        oracle.publish(code, price)                  # USD price of one unit (illustrative)

    sw.register("alice@omni", "1234", region="US")
    sw.register("shop@omni", region="US")            # the merchant wants plain USD
    for promoter in ("circle@omni", "kraken@omni", "whale@omni"):
        sw.register(promoter, region="US")
    sw.fund_treasury("USD", "100000")
    for code, amount in (("USD", "1000"), ("EUR", "500"), ("USDC", "500"), ("BTC", "0.05"), ("ETH", "1")):
        sw.deposit("alice@omni", code, amount)
    sw.deposit("circle@omni", "USD", "1000")
    sw.deposit("kraken@omni", "EUR", "1000")
    sw.deposit("whale@omni", "BTC", "0.1")

    def show(title: str):
        listing = market.options("alice@omni", "shop@omni", "100", "USD")
        print(f"\n{title}")
        for i, o in enumerate(listing.options, 1):
            ad = f"SPONSORED, promoter pays {o.price_bps} bps" if o.sponsored else ""
            print(f"  {i}. {o.asset:<5} {o.quote.route:<8} you pay {o.quote.amount:>12f}   {ad}")
        print(f"  preselected: {listing.default}")
        return listing

    show("Alice pays USD 100 to shop@omni. No bids yet: neutral order, in kind first")

    market.bid("circle@omni", "USDC", 20, pay_with="USD", budget="100")     # paying in dollars
    market.bid("kraken@omni", "BTC", 30, pay_with="EUR", budget="100")      # paying in euros
    market.bid("whale@omni", "ETH", 15, pay_with="BTC", budget="0.01")      # paying in bitcoin
    listing = show("Three promoters bid, each paying in a different asset: more bps, higher position")

    q = market.quote(listing.id, "BTC")
    payment = sw.pay(q.id, "1234")
    print(f"\nAlice taps the sponsored BTC row: {payment.state.value}. She paid {q.amount:f} BTC for a USD 100 bill,"
          f" no fee. BTC left: {sw.balance('alice@omni', 'BTC'):f}")

    market.bid("whale@omni", "ETH", 45, pay_with="BTC", budget="0.001")
    show("The whale raises its ETH bid to 45 bps and takes the top spot")

    revenue = ", ".join(f"{ledger.balance(REVENUE, c):f} {c}" for c in ASSETS if ledger.balance(REVENUE, c))
    e = market.economics()
    print(f"\nPromoter revenue so far : {revenue}")
    print(f"In USD                  : {e['revenue_usd']:.4f}  vs assumed conversion cost {e['conversion_cost_usd']:.4f}")
    print(f"Ledger balanced         : {all(v == 0 for v in ledger.trial_balance().values())}")


if __name__ == "__main__":
    main()
