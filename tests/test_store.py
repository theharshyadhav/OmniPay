import sqlite3
import unittest
from decimal import Decimal as D

from omnipay import PaymentError, State
from tests.helpers import Env


class ConstraintTests(unittest.TestCase):
    """The database itself refuses to break the ledger, whatever the application does."""

    def setUp(self):
        self.e = Env()
        self.c = self.e.ledger.conn()

    def raw_tx(self, legs, seal=True):
        self.c.execute("BEGIN IMMEDIATE")
        tid = self.c.execute("INSERT INTO ledger_tx(ref, ts) VALUES('raw', 0)").lastrowid
        for a, s, n in legs:
            self.c.execute("INSERT INTO ledger_entry(tx_id, account, asset, amount) VALUES(?,?,?,?)", (tid, a, s, n))
        if seal:
            self.c.execute("UPDATE ledger_tx SET sealed=1 WHERE id=?", (tid,))
        return tid

    def test_unbalanced_transaction_cannot_be_sealed(self):
        with self.assertRaisesRegex(sqlite3.IntegrityError, "unbalanced"):
            self.raw_tx([("ext:fiat", "USD", -100), ("user:a", "USD", 99)])
        self.c.execute("ROLLBACK")
        self.assertEqual(self.e.ledger.audit(), [])

    def test_single_entry_transaction_rejected(self):
        with self.assertRaisesRegex(sqlite3.IntegrityError, "two entries"):
            self.raw_tx([("ext:fiat", "USD", 0 + 1)])
        self.c.execute("ROLLBACK")

    def test_balances_cannot_go_negative_for_non_external_accounts(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.raw_tx([("user:a", "USD", -5), ("user:b", "USD", 5)])
        self.c.execute("ROLLBACK")
        self.e.ledger.post("dep", [("ext:fiat", "USD", D(-5)), ("user:a", "USD", D(5))])   # ext may go negative
        self.assertEqual(self.e.ledger.balance("ext:fiat", "USD"), D(-5))

    def test_entries_and_transactions_are_append_only(self):
        self.e.ledger.post("dep", [("ext:fiat", "USD", D(-5)), ("user:a", "USD", D(5))])
        for sql in ("UPDATE ledger_entry SET amount=1", "DELETE FROM ledger_entry",
                    "DELETE FROM ledger_tx", "UPDATE ledger_tx SET ref='x'", "UPDATE ledger_tx SET sealed=0"):
            with self.assertRaises(sqlite3.IntegrityError, msg=sql):
                self.c.execute(sql)

    def test_cannot_add_entries_to_a_sealed_transaction(self):
        tid = self.raw_tx([("ext:fiat", "USD", -1), ("user:a", "USD", 1)])
        with self.assertRaisesRegex(sqlite3.IntegrityError, "sealed"):
            self.c.execute("INSERT INTO ledger_entry(tx_id, account, asset, amount) VALUES(?,?,?,?)", (tid, "x", "USD", 1))
        self.c.execute("ROLLBACK")

    def test_audit_detects_tampered_balances(self):
        self.e.ledger.post("dep", [("ext:fiat", "USD", D(-5)), ("user:a", "USD", D(5))])
        self.c.execute("UPDATE balances SET atoms=atoms+1 WHERE account='user:a'")   # someone edits the derived table
        self.assertTrue(any("mismatch" in p for p in self.e.ledger.audit()))

    def test_failed_post_leaves_nothing_behind(self):
        with self.assertRaises(PaymentError):
            self.e.ledger.post("bad", [("user:a", "USD", D(-5)), ("user:b", "USD", D(5))])
        self.assertEqual(self.e.ledger.journal, [])
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM ledger_tx").fetchone()[0], 0)


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        self.sw = self.e.seed()
        self.sw.deposit("alice@omni", "USD", "1000")
        self.sw.deposit("alice@omni", "BTC", "0.05")

    def pay(self, payee, asset, invoice="100"):
        q = self.sw.quote("alice@omni", payee, invoice, "USD", asset)
        return q, self.sw.pay(q.id, "1234")

    def test_state_survives_restart(self):
        self.sw.accept("bob@omni", "AAPL")
        q, p = self.pay("dana@omni", "USD")
        self.assertEqual((p.state, q.route), (State.SETTLED, "convert"))
        sw2 = self.e.reopen()
        self.assertEqual(sw2.balance("alice@omni", "USD"), D(900))
        self.assertEqual(sw2.balance("dana@omni", "EUR"), D("92.60"))
        self.assertIn("AAPL", sw2._user("bob@omni").accepts)
        self.assertEqual(sw2.get_payment(p.id).state, State.SETTLED)
        self.assertEqual(sw2.converted_usd, D(100))
        self.assertEqual(self.e.ledger.audit(), [])

    def test_retry_is_idempotent_even_after_restart(self):
        q, p = self.pay("bob@omni", "USD")
        sw2 = self.e.reopen()
        again = sw2.pay(q.id, "1234")
        self.assertEqual(again.id, p.id)
        self.assertEqual(sw2.balance("alice@omni", "USD"), D(900))
        self.assertEqual(sw2.balance("bob@omni", "USD"), D(100))

    def test_retry_requires_the_pin(self):
        q, _ = self.pay("bob@omni", "USD")
        with self.assertRaises(PaymentError) as cm:
            self.sw.pay(q.id, "0000")
        self.assertEqual(cm.exception.code, "BAD_PIN")

    def test_pin_lockout_persists_and_expires(self):
        q = self.sw.quote("alice@omni", "bob@omni", "10", "USD", "USD")
        for _ in range(3):
            with self.assertRaises(PaymentError):
                self.sw.pay(q.id, "9999")
        sw2 = self.e.reopen()
        with self.assertRaises(PaymentError) as cm:
            sw2.pay(q.id, "1234")
        self.assertEqual(cm.exception.code, "PIN_LOCKED")
        self.e.t += 901
        q2 = sw2.quote("alice@omni", "bob@omni", "10", "USD", "USD")
        self.assertEqual(sw2.pay(q2.id, "1234").state, State.SETTLED)

    def test_unknown_outcome_stays_held_until_reconciled(self):
        def boom(p):
            raise TimeoutError("rail timed out")
        self.sw.execute = boom
        q = self.sw.quote("alice@omni", "bob@omni", "100", "USD", "USD")
        with self.assertRaises(TimeoutError):
            self.sw.pay(q.id, "1234")
        pid = self.sw.held_payments()[0]
        self.assertEqual(self.sw.raw_state(pid), "UNKNOWN")
        self.assertEqual(self.sw.balance("alice@omni", "USD"), D(900))
        self.assertEqual(self.e.ledger.balance(f"hold:{pid}", "USD"), D(100))
        sw2 = self.e.reopen()                                    # restart: still held, never auto-released
        self.assertEqual(sw2.held_payments(), [pid])
        self.assertEqual(sw2.pay(q.id, "1234").state, State.PENDING)   # retry does NOT charge again
        self.assertEqual(sw2.balance("alice@omni", "USD"), D(900))
        done = sw2.resolve_held(pid, executed=True)
        self.assertEqual((done.state, sw2.balance("bob@omni", "USD")), (State.SETTLED, D(100)))
        self.assertEqual(sw2.held_payments(), [])
        with self.assertRaises(PaymentError):
            sw2.resolve_held(pid, executed=False)                # cannot resolve twice
        self.assertEqual(self.e.ledger.audit(), [])

    def test_reconciled_as_not_executed_refunds_the_payer(self):
        self.sw.execute = lambda p: (_ for _ in ()).throw(TimeoutError())
        q = self.sw.quote("alice@omni", "bob@omni", "100", "USD", "USD")
        with self.assertRaises(TimeoutError):
            self.sw.pay(q.id, "1234")
        p = self.sw.resolve_held(self.sw.held_payments()[0], executed=False)
        self.assertEqual((p.state, self.sw.balance("alice@omni", "USD")), (State.FAILED, D(1000)))
        self.assertEqual(self.e.ledger.audit(), [])

    def test_execute_false_releases_the_hold(self):
        self.sw.execute = lambda p: False
        _, p = self.pay("bob@omni", "USD")
        self.assertEqual((p.state, p.reason, self.sw.balance("alice@omni", "USD")), (State.FAILED, "EXECUTE_FAILED", D(1000)))

    def test_no_liquidity_releases_and_is_recorded(self):
        self.sw.accept("bob@omni")                               # no-op, bob settles USD; treasury EUR is plenty
        e = Env(); sw = e.seed()
        sw.deposit("alice@omni", "USD", "100")
        e.ledger.post("drain", [("sys:treasury", "EUR", D(-1_000_000)), ("ext:fiat", "EUR", D(1_000_000))])
        q = sw.quote("alice@omni", "dana@omni", "100", "USD", "USD")
        p = sw.pay(q.id, "1234")
        self.assertEqual((p.state, p.reason, sw.balance("alice@omni", "USD")), (State.FAILED, "NO_LIQUIDITY", D(100)))
        self.assertEqual(e.ledger.audit(), [])

    def test_insufficient_funds_is_a_recorded_failure_and_frees_the_daily_limit(self):
        q = self.sw.quote("alice@omni", "bob@omni", "5000", "USD", "USD")
        p = self.sw.pay(q.id, "1234")
        self.assertEqual((p.state, p.reason), (State.FAILED, "INSUFFICIENT_FUNDS"))
        r = self.e.ledger.conn().execute("SELECT usd FROM daily_spend").fetchone()
        self.assertEqual(D(r[0]), D(0))

    def test_expired_quote_and_unknown_quote(self):
        q = self.sw.quote("alice@omni", "bob@omni", "10", "USD", "USD")
        self.e.t += 31
        with self.assertRaises(PaymentError) as cm:
            self.sw.pay(q.id, "1234")
        self.assertEqual(cm.exception.code, "QUOTE_EXPIRED")
        with self.assertRaises(PaymentError) as cm:
            self.sw.pay("nope", "1234")
        self.assertEqual(cm.exception.code, "UNKNOWN_QUOTE")

    def test_shares_only_to_opted_in_payee(self):
        e = Env(); sw = e.seed()
        sw.deposit("alice@omni", "AAPL", "2")
        with self.assertRaises(PaymentError) as cm:
            sw.quote("alice@omni", "bob@omni", "230", "USD", "AAPL")
        self.assertEqual(cm.exception.code, "PAYEE_NOT_ACCEPTING")
        q = sw.quote("alice@omni", "dana@omni", "230", "USD", "AAPL")
        self.assertEqual((sw.pay(q.id, "1234").state, q.route, sw.balance("dana@omni", "AAPL")), (State.SETTLED, "direct", D(1)))

    def test_handle_cannot_be_reregistered(self):
        with self.assertRaises(PaymentError) as cm:
            self.sw.register("alice@omni", "0000")
        self.assertEqual(cm.exception.code, "HANDLE_TAKEN")

    def test_on_settled_hook_runs_after_commit_and_cannot_undo(self):
        seen = []
        self.sw.on_settled = lambda p: seen.append(self.sw.balance("bob@omni", "USD"))
        self.pay("bob@omni", "USD")
        self.assertEqual(seen, [D(100)])
        self.sw.on_settled = lambda p: 1 / 0
        self.pay("bob@omni", "USD")
        self.assertEqual(self.sw.balance("bob@omni", "USD"), D(200))
        self.assertEqual(len(self.sw.hook_errors), 1)


if __name__ == "__main__":
    unittest.main()


class OptOutRaceTests(unittest.TestCase):
    def test_payee_who_opts_out_mid_flight_never_receives_shares(self):
        e = Env(); sw = e.seed()
        sw.deposit("alice@omni", "AAPL", "2")
        q = sw.quote("alice@omni", "dana@omni", "230", "USD", "AAPL")
        sw.execute = lambda p: (sw.decline("dana@omni", "AAPL"), True)[1]     # dana opts out while it is in flight
        p = sw.pay(q.id, "1234")
        self.assertEqual((p.state, p.reason), (State.FAILED, "PAYEE_NO_LONGER_ACCEPTS"))
        self.assertEqual((sw.balance("alice@omni", "AAPL"), sw.balance("dana@omni", "AAPL")), (D(2), D(0)))
        self.assertEqual(e.ledger.audit(), [])
