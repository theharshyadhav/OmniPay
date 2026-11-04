"""Promotion market: how OmniPay earns while staying free for users.

Anyone can pay to push an asset up the "pay with" list. The more they bid, the higher it ranks.

  bid      bid `bps` (basis points of each completed payment's value) and prepay a budget in ANY asset
  options  the list for one invoice: only assets the payer can actually pay with. Sponsored ones first,
           ranked by bid; everything else after, in a neutral order. Sponsored rows are labelled and
           never preselected.
  quote    the payer taps an asset; a sponsored tap is remembered
  bill     when (and only when) that payment settles, the promoter is charged, in the asset it prepaid with

Pricing is a generalised second-price auction: you bid your maximum and pay one step above the next bid
(pricing="first" makes you pay what you bid). A reserve price per route keeps sponsored conversions at
least cost-covering. Paying in a riskier asset costs a little more (that asset's haircut).
Nothing here touches the user's price: users never pay a fee or a spread.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_UP, Decimal as D
from typing import Iterable, Optional

from omnipay import ASSETS, REVENUE, Kind, Payment, PaymentError, Quote, Switch

CONVERT_COST_BPS = D("10")                                      # assumed cost of one conversion (hedging, liquidity)
RESERVE_BPS = {"direct": D("1"), "convert": CONVERT_COST_BPS}   # floor price: a sponsored conversion must pay for itself
INCREMENT_BPS = D("0.1")                                        # second-price step
MIN_BILLABLE_USD = D("1")                                       # dust payments carry no ad (anti click-fraud)
ASSET_SPECIFIC = {"ASSET_DISABLED", "PAYEE_NOT_ACCEPTING", "NO_PRICE", "TOO_SMALL"}
KIND_ORDER = {Kind.FIAT: 0, Kind.CRYPTO: 1, Kind.EQUITY: 2}


@dataclass(frozen=True)
class Bid:
    promoter: str
    asset: str                   # the asset being promoted
    bps: D                       # max price per completed payment, in basis points of its value
    fund_asset: str              # what the promoter pays with: any asset the platform accepts
    regions: frozenset           # payer regions targeted; empty = everywhere
    placed_at: float             # earlier wins a tie

    @property
    def escrow(self) -> str:
        return f"promo:{self.promoter}/{self.asset}"


@dataclass(frozen=True)
class Option:
    quote: Quote                 # priced, not locked: what the payer would pay and the payee would get
    sponsored: bool = False
    promoter: str = ""
    price_bps: D = D(0)          # what the promoter pays if this option completes
    notice: str = ""             # disclosure or risk warning to show next to a sponsored row

    @property
    def asset(self) -> str:
        return self.quote.pay_asset

    @property
    def label(self) -> str:
        return "Sponsored" if self.sponsored else ""


@dataclass(frozen=True)
class Listing:
    id: str
    payer: str
    payee: str
    invoice: str
    invoice_asset: str
    options: tuple[Option, ...]
    default: str                 # first organic option: a sponsored row is never preselected
    expires_at: float


class PromoMarket:
    def __init__(self, switch: Switch, *, slots: int = 3, pricing: str = "second",
                 no_ads: Optional[dict[str, set[Kind]]] = None, warnings: Optional[dict[Kind, str]] = None,
                 fund_assets: Optional[Iterable[str]] = None):
        self.sw, self.slots, self.pricing = switch, slots, pricing
        self.no_ads = no_ads or {}              # region -> asset kinds that may not be sponsored there
        self.warnings = warnings or {}          # asset kind -> notice shown next to a sponsored row
        self.fund_assets = set(fund_assets) if fund_assets is not None else None   # None = any asset
        self.bids: dict[tuple[str, str], Bid] = {}
        self.unbilled: list[str] = []           # payments whose promoter couldn't be priced yet: retry later
        self._listings: dict[str, Listing] = {}
        self._attrib: dict[str, tuple[Bid, D]] = {}   # quote id -> (bid, price in bps locked when it was tapped)
        switch.on_settled = self.bill

    # ---- promoters ----------------------------------------------------------
    def bid(self, promoter: str, asset: str, bps, *, pay_with: str, budget,
            regions: Iterable[str] = ()) -> Bid:
        """Promote `asset`. `bps` is your max price per completed payment (basis points of its value).
        `budget` is prepaid into escrow in `pay_with`, which can be any asset the platform accepts.
        Bidding again on the same asset updates the bid and tops up the escrow."""
        sw = self.sw
        sw.region(promoter)                                           # must be a registered handle
        for code in (asset, pay_with):
            if code not in ASSETS:
                raise PaymentError("UNKNOWN_ASSET", code)
        if self.fund_assets is not None and pay_with not in self.fund_assets:
            raise PaymentError("FUNDING_NOT_ACCEPTED", pay_with)
        bps, budget = D(str(bps)), D(str(budget))
        if bps < RESERVE_BPS["direct"]:
            raise PaymentError("BID_BELOW_RESERVE", f"minimum {RESERVE_BPS['direct']} bps")
        if budget <= 0 or budget != ASSETS[pay_with].q(budget, ROUND_DOWN):
            raise PaymentError("BAD_AMOUNT", f"{budget} {pay_with}")
        with sw.ledger.lock:
            old = self.bids.get((promoter, asset))
            if old is not None and old.fund_asset != pay_with:
                raise PaymentError("CANCEL_FIRST", "a bid keeps the asset it was funded with")
            new = Bid(promoter, asset, bps, pay_with, frozenset(regions), old.placed_at if old else sw.clock())
            sw.ledger.post(f"bid:{promoter}/{asset}",
                           [(f"user:{promoter}", pay_with, -budget), (new.escrow, pay_with, budget)])
            self.bids[(promoter, asset)] = new
            return new

    def cancel_bid(self, promoter: str, asset: str) -> None:
        """Withdraw a bid and get the unspent escrow back."""
        with self.sw.ledger.lock:
            b = self.bids.pop((promoter, asset), None)
            if b is None:
                raise PaymentError("NO_BID", f"{promoter} / {asset}")
            left = self.sw.ledger.balance(b.escrow, b.fund_asset)
            self.sw.ledger.post(f"unbid:{promoter}/{asset}",
                                [(b.escrow, b.fund_asset, -left), (f"user:{promoter}", b.fund_asset, left)])

    # ---- payers -------------------------------------------------------------
    def options(self, payer: str, payee: str, invoice: str, invoice_asset: str) -> Listing:
        """The "pay with" list for one invoice."""
        sw = self.sw
        with sw.ledger.lock:
            region = sw.region(payer)
            payable: dict[str, Quote] = {}
            for code in ASSETS:
                try:
                    q = sw.price(payer, payee, invoice, invoice_asset, code)
                except PaymentError as e:
                    if e.code in ASSET_SPECIFIC:
                        continue                    # this asset can't pay this invoice (policy, opt-in, no price)
                    raise                           # the payer, payee or invoice itself is the problem
                if sw.balance(payer, code) >= q.amount:      # only what the payer can actually pay with
                    payable[code] = q

            ranked = sorted((b for b in self.bids.values()
                             if b.asset in payable and self._eligible(b, payable[b.asset], region)),
                            key=lambda b: (-b.bps, b.placed_at))
            sponsored: list[Option] = []
            seen: set[str] = set()
            for i, b in enumerate(ranked):
                if b.asset in seen:
                    continue                        # an asset appears once: its highest bid wins
                if len(sponsored) == self.slots:
                    break
                seen.add(b.asset)
                q = payable[b.asset]
                floor = RESERVE_BPS[q.route]
                if self.pricing == "first":
                    price = b.bps
                elif i + 1 < len(ranked):           # one step above the next bid, never above your own
                    price = min(b.bps, max(floor, ranked[i + 1].bps + INCREMENT_BPS))
                else:
                    price = floor
                sponsored.append(Option(q, True, b.promoter, price, self.warnings.get(ASSETS[b.asset].kind, "")))
            rest = sorted((q for c, q in payable.items() if c not in seen),
                          key=lambda q: (q.route != "direct", KIND_ORDER[ASSETS[q.pay_asset].kind], q.pay_asset))
            organic = [Option(q) for q in rest]
            listing = Listing(uuid.uuid4().hex[:12], payer, payee, str(invoice), invoice_asset,
                              tuple(sponsored + organic), organic[0].asset if organic else "",
                              sw.clock() + sw.quote_ttl)
            self._listings[listing.id] = listing
            return listing

    def quote(self, listing_id: str, asset: str) -> Quote:
        """The payer tapped `asset`. Locks the price, and remembers a sponsored tap so the promoter is
        billed if (and only if) that payment settles."""
        sw = self.sw
        with sw.ledger.lock:
            listing = self._listings.get(listing_id)
            if listing is None or sw.clock() > listing.expires_at:
                raise PaymentError("LISTING_EXPIRED", listing_id)
            option = next((o for o in listing.options if o.asset == asset), None)
            if option is None:
                raise PaymentError("NOT_IN_LISTING", asset)
            q = sw.quote(listing.payer, listing.payee, listing.invoice, listing.invoice_asset, asset)
            bid = self.bids.get((option.promoter, asset)) if option.sponsored else None
            if bid is not None:
                self._attrib[q.id] = (bid, option.price_bps)
            return q

    # ---- billing & economics ------------------------------------------------
    def bill(self, p: Payment) -> None:
        """Switch hook, called after a payment settles: charge the promoter whose sponsored row was tapped."""
        with self.sw.ledger.lock:
            att = self._attrib.pop(p.quote.id, None)
            if att is None:
                return                                              # organic payment: nobody to bill
            b, price = att
            try:
                owed = self._charge(b, p.quote.usd, price)
            except PaymentError:
                self.unbilled.append(p.id)                          # no fresh price for the funding asset
                return
            owed = min(owed, self.sw.ledger.balance(b.escrow, b.fund_asset))   # never more than the escrow holds
            self.sw.ledger.post(f"ad:{p.id}", [(b.escrow, b.fund_asset, -owed), (REVENUE, b.fund_asset, owed)])

    def economics(self) -> dict[str, D]:
        """Free for users means promoters pay for everything: revenue vs the assumed cost of conversions."""
        sw = self.sw
        revenue = sum((sw.ledger.balance(REVENUE, c) * sw.oracle.usd(c)
                       for c in ASSETS if sw.ledger.balance(REVENUE, c) > 0), D(0))
        cost = sw.converted_usd * CONVERT_COST_BPS / D(10_000)
        return {"revenue_usd": revenue, "conversion_cost_usd": cost, "net_usd": revenue - cost}

    # ---- internals ----------------------------------------------------------
    def _eligible(self, b: Bid, q: Quote, region: str) -> bool:
        """Can this bid be shown, for this payer and this invoice, right now?"""
        if b.regions and region not in b.regions:
            return False                                            # targets other regions
        if ASSETS[b.asset].kind in self.no_ads.get(region, ()):
            return False                                            # ads for this kind of asset are banned there
        if b.bps < RESERVE_BPS[q.route] or q.usd < MIN_BILLABLE_USD:
            return False                                            # below the floor, or a dust payment
        try:
            worst = self._charge(b, q.usd, b.bps)                   # the most this bid could be charged
        except PaymentError:
            return False                                            # can't price the funding asset right now
        return self.sw.ledger.balance(b.escrow, b.fund_asset) >= worst

    def _charge(self, b: Bid, invoice_usd: D, price_bps: D) -> D:
        """What the promoter pays for one completed payment, in the asset it prepaid with: the USD price,
        grossed up by that asset's haircut (we carry its risk until we sweep it to USD)."""
        fa = ASSETS[b.fund_asset]
        usd_cost = invoice_usd * price_bps / D(10_000)
        px = self.sw.oracle.usd(b.fund_asset) * (1 - D(fa.haircut_bps) / D(10_000))
        return fa.q(usd_cost / px, ROUND_UP)
