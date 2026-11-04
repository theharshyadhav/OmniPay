"""PersistentPromo: promo.PromoMarket with durable state, tap-time reservation, fraud caps and a retry job.

Reused from the engine: options() ranking/second-price logic, _eligible(), _charge() (haircuts), reserve prices.
Changed: bids/listings/attributions live in the database; at tap time the promoter's MAXIMUM charge is moved from
escrow into a per-quote reservation (so concurrent taps cannot overspend a budget); a settled payment bills
min(price, reservation) and returns the rest; a failed/expired tap releases the reservation; sweep() retries
anything that settled but was never billed. Sponsored rows stay labelled, never preselected, and never bypass
region policy, payee acceptance or kill switches (that is all enforced upstream in Switch.price)."""
from __future__ import annotations

import json
from decimal import ROUND_DOWN, Decimal as D
from typing import Iterable, Optional

from omnipay import ASSETS, REVENUE, DAY, Payment, PaymentError, Quote, State
from promo import INCREMENT_BPS, RESERVE_BPS, Bid, Listing, Option, PromoMarket
from app.dbledger import DbLedger


def _quote_to_json(q: Quote) -> dict:
    return {"id": q.id, "payer": q.payer, "payee": q.payee, "invoice": str(q.invoice), "invoice_asset": q.invoice_asset,
            "route": q.route, "pay_asset": q.pay_asset, "amount": str(q.amount), "receive_asset": q.receive_asset,
            "receive": str(q.receive), "usd": str(q.usd), "expires_at": q.expires_at}


def _quote_from_json(d: dict) -> Quote:
    return Quote(d["id"], d["payer"], d["payee"], D(d["invoice"]), d["invoice_asset"], d["route"], d["pay_asset"], D(d["amount"]),
                 d["receive_asset"], D(d["receive"]), D(d["usd"]), d["expires_at"])


class BidStore:
    def __init__(self, L: DbLedger):
        self.L = L

    @staticmethod
    def _row(r) -> Bid:
        return Bid(r["promoter"], r["asset"], D(r["bps"]), r["fund_asset"], frozenset(json.loads(r["regions"])), r["placed_at"])

    def get(self, key, default=None):
        r = self.L.conn().execute("SELECT * FROM bids WHERE promoter=? AND asset=?", key).fetchone()
        return self._row(r) if r else default

    def __setitem__(self, key, b: Bid):
        self.L.conn().execute("INSERT OR REPLACE INTO bids VALUES(?,?,?,?,?,?)",
                              (b.promoter, b.asset, str(b.bps), b.fund_asset, json.dumps(sorted(b.regions)), b.placed_at))

    def pop(self, key, default=None):
        b = self.get(key)
        if b is None:
            return default
        self.L.conn().execute("DELETE FROM bids WHERE promoter=? AND asset=?", key)
        return b

    def values(self):
        return [self._row(r) for r in self.L.conn().execute("SELECT * FROM bids")]


class ListingStore:
    def __init__(self, L: DbLedger):
        self.L = L

    def __setitem__(self, lid: str, l: Listing):
        body = {"id": l.id, "payer": l.payer, "payee": l.payee, "invoice": l.invoice, "invoice_asset": l.invoice_asset, "default": l.default,
                "expires_at": l.expires_at,
                "options": [{"quote": _quote_to_json(o.quote), "sponsored": o.sponsored, "promoter": o.promoter,
                             "price_bps": str(o.price_bps), "notice": o.notice} for o in l.options]}
        self.L.conn().execute("INSERT OR REPLACE INTO listings VALUES(?,?,?)", (lid, json.dumps(body), l.expires_at))

    def get(self, lid: str, default=None):
        r = self.L.conn().execute("SELECT body FROM listings WHERE id=?", (lid,)).fetchone()
        if not r:
            return default
        d = json.loads(r[0])
        opts = tuple(Option(_quote_from_json(o["quote"]), o["sponsored"], o["promoter"], D(o["price_bps"]), o["notice"]) for o in d["options"])
        return Listing(d["id"], d["payer"], d["payee"], d["invoice"], d["invoice_asset"], opts, d["default"], d["expires_at"])


class PersistentPromo(PromoMarket):
    PAYER_DAILY_CAP = 5        # sponsored conversions one payer can generate for one promoter per day
    PAYEE_DAILY_CAP = 50       # ... and one payee

    def __init__(self, switch, audit, switches, **kw):
        super().__init__(switch, **kw)
        self.L, self.audit, self.switches = switch.ledger, audit, switches
        self.bids, self._listings = BidStore(self.L), ListingStore(self.L)     # type: ignore[assignment]
        switch.on_failed = self._on_failed

    # unbilled list of the reference becomes a query
    @property
    def unbilled(self) -> list[str]:                                  # type: ignore[override]
        return [r[0] for r in self.L.conn().execute(
            "SELECT p.id FROM attributions a JOIN payments p ON p.quote_id=a.quote_id WHERE a.state='UNBILLED'")]

    @unbilled.setter
    def unbilled(self, _v) -> None:
        pass

    def _reserve_acct(self, quote_id: str) -> str:
        return f"promo-hold:{quote_id}"

    # ---- promoters ----------------------------------------------------------
    def bid(self, promoter, asset, bps, *, pay_with, budget, regions: Iterable[str] = ()) -> Bid:
        sw = self.sw
        sw.region(promoter)
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
        with self.L.tx():
            old = self.bids.get((promoter, asset))
            if old is not None and old.fund_asset != pay_with:
                raise PaymentError("CANCEL_FIRST", "a bid keeps the asset it was funded with")
            new = Bid(promoter, asset, bps, pay_with, frozenset(regions), old.placed_at if old else sw.clock())
            self.L.post(f"bid:{promoter}/{asset}", [(f"user:{promoter}", pay_with, -budget), (new.escrow, pay_with, budget)])
            self.bids[(promoter, asset)] = new
        self.audit.log(promoter, "promo.bid", asset, bps=str(bps), fund=pay_with, budget=str(budget))
        return new

    def update_bps(self, promoter: str, asset: str, bps) -> Bid:
        b = self.bids.get((promoter, asset))
        if b is None:
            raise PaymentError("NO_BID", f"{promoter} / {asset}")
        bps = D(str(bps))
        if bps < RESERVE_BPS["direct"]:
            raise PaymentError("BID_BELOW_RESERVE", f"minimum {RESERVE_BPS['direct']} bps")
        nb = Bid(b.promoter, b.asset, bps, b.fund_asset, b.regions, b.placed_at)
        self.bids[(promoter, asset)] = nb
        self.audit.log(promoter, "promo.update_bps", asset, bps=str(bps))
        return nb

    def cancel_bid(self, promoter: str, asset: str) -> None:
        with self.L.tx():
            b = self.bids.pop((promoter, asset), None)
            if b is None:
                raise PaymentError("NO_BID", f"{promoter} / {asset}")
            left = self.L.balance(b.escrow, b.fund_asset)
            if left:
                self.L.post(f"unbid:{promoter}/{asset}", [(b.escrow, b.fund_asset, -left), (f"user:{promoter}", b.fund_asset, left)])
        self.audit.log(promoter, "promo.cancel", asset)

    # ---- payers -------------------------------------------------------------
    def _capped(self, c, promoter: str, payer: str, payee: str, day: int) -> bool:
        n = lambda col, v: c.execute(f"SELECT COUNT(*) FROM attributions WHERE promoter=? AND {col}=? AND day=? AND state<>'RELEASED'",
                                     (promoter, v, day)).fetchone()[0]
        return n("payer", payer) >= self.PAYER_DAILY_CAP or n("payee", payee) >= self.PAYEE_DAILY_CAP

    def quote(self, listing_id: str, asset: str) -> Quote:
        sw = self.sw
        with self.L.tx() as c:
            listing = self._listings.get(listing_id)
            if listing is None or sw.clock() > listing.expires_at:
                raise PaymentError("LISTING_EXPIRED", listing_id)
            option = next((o for o in listing.options if o.asset == asset), None)
            if option is None:
                raise PaymentError("NOT_IN_LISTING", asset)
            q = sw.quote(listing.payer, listing.payee, listing.invoice, listing.invoice_asset, asset)
            bid = self.bids.get((option.promoter, asset)) if option.sponsored else None
            day = int(sw.clock() // DAY)
            if bid is None or self._capped(c, bid.promoter, q.payer, q.payee, day):
                return q                                              # organic, or capped: payer unaffected, promoter not billed
            worst = self._charge(bid, q.usd, option.price_bps)        # maximum this tap can cost, reserved NOW
            if self.L.balance(bid.escrow, bid.fund_asset) < worst:
                return q                                              # budget exhausted meanwhile: serve unsponsored
            self.L.post(f"promo-reserve:{q.id}", [(bid.escrow, bid.fund_asset, -worst), (self._reserve_acct(q.id), bid.fund_asset, worst)])
            c.execute("INSERT INTO attributions(quote_id,promoter,asset,fund_asset,price_bps,reserved,state,payer,payee,day,created_at,updated_at) "
                      "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (q.id, bid.promoter, asset, bid.fund_asset, str(option.price_bps), str(worst),
                                                         "RESERVED", q.payer, q.payee, day, sw.clock(), sw.clock()))
            return q

    # ---- billing ------------------------------------------------------------
    def _return_reserve(self, row, ref: str, amount: D) -> None:
        if amount <= 0:
            return
        live = self.bids.get((row["promoter"], row["asset"]))
        dest = live.escrow if live and live.fund_asset == row["fund_asset"] else f"user:{row['promoter']}"
        self.L.post(ref, [(self._reserve_acct(row["quote_id"]), row["fund_asset"], -amount), (dest, row["fund_asset"], amount)])

    def bill(self, p: Payment) -> None:
        with self.L.tx() as c:
            row = c.execute("SELECT * FROM attributions WHERE quote_id=? AND state IN ('RESERVED','UNBILLED')", (p.quote.id,)).fetchone()
            if row is None:
                return                                                # organic payment, or already billed: nothing to do (idempotent)
            reserved = D(row["reserved"])
            pseudo = Bid(row["promoter"], row["asset"], D(0), row["fund_asset"], frozenset(), 0)
            try:
                owed = min(self._charge(pseudo, p.quote.usd, D(row["price_bps"])), reserved)
            except PaymentError:
                c.execute("UPDATE attributions SET state='UNBILLED', attempts=attempts+1, reason='no fresh price', updated_at=? WHERE quote_id=?",
                          (self.L.clock(), p.quote.id))
                return                                                # money stays reserved; sweep() retries
            if owed > 0:
                self.L.post(f"ad:{p.id}", [(self._reserve_acct(p.quote.id), row["fund_asset"], -owed), (REVENUE, row["fund_asset"], owed)])
            self._return_reserve(row, f"promo-refund:{p.quote.id}", reserved - owed)
            c.execute("UPDATE attributions SET state='BILLED', billed=?, updated_at=? WHERE quote_id=?", (str(owed), self.L.clock(), p.quote.id))
        self.audit.log("system", "promo.billed", p.id, promoter=row["promoter"], owed=str(owed), asset=row["fund_asset"])

    def _on_failed(self, p: Payment) -> None:
        self._release(p.quote.id, "payment failed")

    def _release(self, quote_id: str, why: str) -> bool:
        with self.L.tx() as c:
            row = c.execute("SELECT * FROM attributions WHERE quote_id=? AND state IN ('RESERVED','UNBILLED')", (quote_id,)).fetchone()
            if row is None:
                return False
            self._return_reserve(row, f"promo-release:{quote_id}", D(row["reserved"]))
            c.execute("UPDATE attributions SET state='RELEASED', reason=?, updated_at=? WHERE quote_id=?", (why, self.L.clock(), quote_id))
            return True

    def sweep(self) -> dict:
        """Retry job: bill settled-but-unbilled payments; release reservations whose tap expired or whose payment failed."""
        c, now, out = self.L.conn(), self.L.clock(), {"billed": 0, "released": 0}
        for r in c.execute("SELECT a.quote_id, a.state, p.id AS pid, p.state AS pstate, q.expires_at FROM attributions a "
                           "JOIN quotes q ON q.id=a.quote_id LEFT JOIN payments p ON p.quote_id=a.quote_id "
                           "WHERE a.state IN ('RESERVED','UNBILLED')").fetchall():
            if r["pstate"] == "SETTLED":
                self.bill(self.sw.get_payment(r["pid"]))
                out["billed"] += 1
            elif r["pstate"] == "FAILED" or (r["pid"] is None and now > r["expires_at"]):
                out["released"] += int(self._release(r["quote_id"], "failed" if r["pid"] else "tap expired"))
        return out

    # ---- advisor support ------------------------------------------------------
    def landscape(self, asset: str) -> dict:
        """Anonymised view of the competition for one asset (no promoter identities)."""
        bps = sorted((b.bps for b in self.bids.values() if b.asset == asset), reverse=True)
        allb = sorted((b.bps for b in self.bids.values()), reverse=True)
        return {"asset": asset, "bids_on_asset": len(bps), "top_bps": str(bps[0]) if bps else None,
                "median_bps": str(bps[len(bps) // 2]) if bps else None, "all_bids_top_slots": [str(x) for x in allb[: self.slots]],
                "slots": self.slots, "reserve_bps": {k: str(v) for k, v in RESERVE_BPS.items()}, "pricing": self.pricing}

    def simulate(self, promoter: str, asset: str, bps, route: str = "convert") -> dict:
        """What would happen if `promoter` bid `bps` on `asset` right now (against the live field, other bids unchanged)."""
        bps = D(str(bps))
        field = sorted([(b.bps, b.placed_at, b.promoter, b.asset) for b in self.bids.values() if (b.promoter, b.asset) != (promoter, asset)],
                       key=lambda t: (-t[0], t[1]))
        mine = (bps, self.sw.clock(), promoter, asset)
        ranked = sorted(field + [mine], key=lambda t: (-t[0], t[1]))
        i = ranked.index(mine)
        floor = RESERVE_BPS[route]
        shown = i < self.slots and bps >= floor
        if self.pricing == "first":
            price = bps
        elif i + 1 < len(ranked):
            price = min(bps, max(floor, ranked[i + 1][0] + INCREMENT_BPS))
        else:
            price = floor
        return {"bid_bps": str(bps), "rank": i + 1, "shown": shown, "would_pay_bps": str(price) if shown else None,
                "next_lower_bid_bps": str(ranked[i + 1][0]) if i + 1 < len(ranked) else None,
                "explain": ("second-price: you pay one step above the next bid, never more than you bid" if self.pricing == "second" else "first-price")}

    def history(self, promoter: str, limit: int = 50) -> list[dict]:
        return [dict(r) for r in self.L.conn().execute(
            "SELECT quote_id, asset, state, price_bps, reserved, billed, fund_asset, day FROM attributions WHERE promoter=? ORDER BY created_at DESC LIMIT ?",
            (promoter, limit))]

    def stats(self, promoter: str) -> dict:
        c = self.L.conn()
        rows = c.execute("SELECT state, COUNT(*), COALESCE(SUM(CAST(billed AS REAL)),0) FROM attributions WHERE promoter=? GROUP BY state", (promoter,)).fetchall()
        bids = [{"asset": b.asset, "bps": str(b.bps), "fund_asset": b.fund_asset, "escrow": str(self.L.balance(b.escrow, b.fund_asset))}
                for b in self.bids.values() if b.promoter == promoter]
        return {"by_state": {r[0]: {"n": r[1], "billed": r[2]} for r in rows}, "bids": bids}

    # ---- auto-bidder (budget + pacing limited) -----------------------------------
    def set_autobid(self, promoter: str, asset: str, *, max_bps, daily_cap_usd, total_cap_usd) -> None:
        if self.bids.get((promoter, asset)) is None:
            raise PaymentError("NO_BID", "place a funded bid first; the auto-bidder only adjusts bps inside that escrow")
        self.L.conn().execute("INSERT OR REPLACE INTO autobids VALUES(?,?,?,?,?,1,?)",
                              (promoter, asset, str(D(str(max_bps))), str(D(str(daily_cap_usd))), str(D(str(total_cap_usd))), self.sw.clock()))
        self.audit.log(promoter, "promo.autobid_set", asset, max_bps=str(max_bps), daily=str(daily_cap_usd), total=str(total_cap_usd))

    def _spent_usd(self, promoter: str, asset: str, day: Optional[int]) -> D:
        q, a = "SELECT fund_asset, billed FROM attributions WHERE promoter=? AND asset=? AND state='BILLED'", [promoter, asset]
        if day is not None:
            q, a = q + " AND day=?", a + [day]
        return sum((D(b) * self.sw.oracle.usd(f) for f, b in self.L.conn().execute(q, a)), D(0))

    def run_autobidders(self) -> int:
        n, day = 0, int(self.sw.clock() // DAY)
        for r in self.L.conn().execute("SELECT * FROM autobids WHERE enabled=1").fetchall():
            p, a = r["promoter"], r["asset"]
            if self.bids.get((p, a)) is None:
                continue
            try:
                over = self._spent_usd(p, a, day) >= D(r["daily_cap_usd"]) or self._spent_usd(p, a, None) >= D(r["total_cap_usd"])
            except PaymentError:
                continue
            if over:                                                  # pacing: stop for the day by dropping to the floor
                self.update_bps(p, a, RESERVE_BPS["direct"])
                self.audit.log("system", "promo.autobid_paused", a, promoter=p)
                continue
            rivals = sorted((b.bps for b in self.bids.values() if (b.promoter, b.asset) != (p, a)), reverse=True)
            cutoff = rivals[self.slots - 1] if len(rivals) >= self.slots else RESERVE_BPS["convert"]   # need to beat the last visible slot
            target = min(D(r["max_bps"]), max(RESERVE_BPS["convert"], cutoff + INCREMENT_BPS))
            if target != self.bids.get((p, a)).bps:
                self.update_bps(p, a, target)
                n += 1
        return n
