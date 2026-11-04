"""SQLite-backed double-entry ledger with the same interface as omnipay.Ledger.

Correctness lives in the database (see migrations/sqlite/0001_init.sql): entries are append-only, a
transaction cannot be sealed unless every asset nets to zero, and balances (which only move on seal)
carry a CHECK so only ext:* accounts can go negative. Writers serialise with BEGIN IMMEDIATE, so this is
safe across threads and processes. Nested tx() calls become SAVEPOINTs.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from decimal import Decimal as D
from pathlib import Path
from typing import Callable

from omnipay import PaymentError

SCALE = 8
MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations" / "sqlite"


def to_atoms(x: D) -> int:
    v = D(x).scaleb(SCALE)
    if v != v.to_integral_value():
        raise PaymentError("BAD_AMOUNT", f"{x} has more than {SCALE} decimals")
    return int(v)


def from_atoms(n: int) -> D:
    return D(n).scaleb(-SCALE)


class DbLedger:
    def __init__(self, path: str, clock: Callable[[], float] = time.time):
        self.path, self.clock = str(path), clock
        self.lock = threading.RLock()        # in-process convenience only; the DB transaction is the real guard
        self._local = threading.local()
        self._migrate()

    # ---- connections & transactions ------------------------------------------
    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, isolation_level=None, timeout=60)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("PRAGMA busy_timeout=60000")
            self._local.conn, self._local.depth = c, 0
        return c

    @contextmanager
    def tx(self):
        c = self.conn()
        depth = self._local.depth
        if depth == 0:
            c.execute("BEGIN IMMEDIATE")
        else:
            c.execute(f"SAVEPOINT sp{depth}")
        self._local.depth = depth + 1
        try:
            yield c
        except BaseException:
            self._local.depth = depth
            c.execute("ROLLBACK" if depth == 0 else f"ROLLBACK TO sp{depth}")
            if depth:
                c.execute(f"RELEASE sp{depth}")
            raise
        else:
            self._local.depth = depth
            c.execute("COMMIT" if depth == 0 else f"RELEASE sp{depth}")

    def _migrate(self) -> None:
        c = self.conn()
        c.execute("CREATE TABLE IF NOT EXISTS schema_migrations(version TEXT PRIMARY KEY, applied_at REAL NOT NULL)")
        done = {r[0] for r in c.execute("SELECT version FROM schema_migrations")}
        for f in sorted(MIGRATIONS.glob("*.sql")):
            if f.name not in done:
                c.executescript(f.read_text())
                c.execute("INSERT INTO schema_migrations VALUES(?,?)", (f.name, self.clock()))

    # ---- ledger API (matches omnipay.Ledger) ----------------------------------
    def balance(self, account: str, asset: str) -> D:
        r = self.conn().execute("SELECT atoms FROM balances WHERE account=? AND asset=?", (account, asset)).fetchone()
        return from_atoms(r[0]) if r else D(0)

    def post(self, ref: str, legs: list[tuple[str, str, D]]) -> None:
        """All-or-nothing. Raises LEDGER_UNBALANCED / INSUFFICIENT_FUNDS like the in-memory ledger."""
        legs = [(a, c, D(x)) for a, c, x in legs if x]
        per_asset: dict[str, D] = defaultdict(D)
        delta: dict[tuple[str, str], int] = defaultdict(int)
        for acct, code, x in legs:
            per_asset[code] += x
            delta[(acct, code)] += to_atoms(x)
        if any(per_asset.values()) or len(legs) < 2:
            raise PaymentError("LEDGER_UNBALANCED", ref)
        with self.tx() as c:
            for (acct, code), d in delta.items():           # friendly error; the CHECK constraint is the backstop
                if not acct.startswith("ext:"):
                    r = c.execute("SELECT atoms FROM balances WHERE account=? AND asset=?", (acct, code)).fetchone()
                    if (r[0] if r else 0) + d < 0:
                        raise PaymentError("INSUFFICIENT_FUNDS", f"{acct} {code}")
            tx_id = c.execute("INSERT INTO ledger_tx(ref, ts) VALUES(?,?)", (ref, self.clock())).lastrowid
            c.executemany("INSERT INTO ledger_entry(tx_id, account, asset, amount) VALUES(?,?,?,?)",
                          [(tx_id, a, code, to_atoms(x)) for a, code, x in legs])
            try:
                c.execute("UPDATE ledger_tx SET sealed=1 WHERE id=?", (tx_id,))
            except sqlite3.IntegrityError as e:
                msg = str(e)
                raise PaymentError("INSUFFICIENT_FUNDS" if "balances" in msg else "LEDGER_UNBALANCED", msg) from e

    def trial_balance(self) -> dict[str, D]:
        rows = self.conn().execute("SELECT asset, SUM(atoms) FROM balances GROUP BY asset").fetchall()
        return {a: from_atoms(s) for a, s in rows}

    @property
    def journal(self) -> list[dict]:
        out: dict[int, dict] = {}
        for r in self.conn().execute(
                "SELECT t.id, t.ref, t.ts, e.account, e.asset, e.amount FROM ledger_tx t "
                "JOIN ledger_entry e ON e.tx_id=t.id WHERE t.sealed=1 ORDER BY t.id, e.id"):
            j = out.setdefault(r["id"], {"n": r["id"], "ref": r["ref"], "ts": r["ts"], "legs": []})
            j["legs"].append((r["account"], r["asset"], from_atoms(r["amount"])))
        return list(out.values())

    def audit(self) -> list[str]:
        """Independent re-check: balances == sum of sealed entries, every tx nets to zero, nothing left unsealed.
        Run it from the reconciliation job; any output is an alert."""
        c, problems = self.conn(), []
        derived = {(a, s): n for a, s, n in c.execute(
            "SELECT e.account, e.asset, SUM(e.amount) FROM ledger_entry e JOIN ledger_tx t ON t.id=e.tx_id "
            "WHERE t.sealed=1 GROUP BY e.account, e.asset")}
        stored = {(a, s): n for a, s, n in c.execute("SELECT account, asset, atoms FROM balances")}
        for key in derived.keys() | stored.keys():
            if derived.get(key, 0) != stored.get(key, 0):
                problems.append(f"balance mismatch {key}: stored={stored.get(key, 0)} derived={derived.get(key, 0)}")
        for (tid,) in c.execute("SELECT t.id FROM ledger_tx t JOIN ledger_entry e ON e.tx_id=t.id "
                                "GROUP BY t.id, e.asset HAVING SUM(e.amount)<>0"):
            problems.append(f"unbalanced tx {tid}")
        for (tid,) in c.execute("SELECT id FROM ledger_tx WHERE sealed=0"):
            problems.append(f"unsealed tx {tid}")
        for asset, total in self.trial_balance().items():
            if total != 0:
                problems.append(f"trial balance {asset} = {total}")
        return problems
