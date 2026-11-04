-- OmniPay M1 schema (SQLite). Money is stored as INTEGER "atoms" = amount * 1e8 (exact; max asset dp is 8).
CREATE TABLE users(
  handle TEXT PRIMARY KEY CHECK (handle GLOB '*@*'),
  region TEXT NOT NULL, settle_in TEXT NOT NULL, accepts TEXT NOT NULL,
  salt BLOB, pin_hash BLOB,
  pin_fails INTEGER NOT NULL DEFAULT 0, pin_locked_until REAL NOT NULL DEFAULT 0,
  created_at REAL NOT NULL);

CREATE TABLE quotes(
  id TEXT PRIMARY KEY,
  payer TEXT NOT NULL REFERENCES users(handle), payee TEXT NOT NULL REFERENCES users(handle),
  invoice TEXT NOT NULL, invoice_asset TEXT NOT NULL,
  route TEXT NOT NULL CHECK (route IN ('direct','convert')),
  pay_asset TEXT NOT NULL, amount TEXT NOT NULL, receive_asset TEXT NOT NULL, receive TEXT NOT NULL,
  usd TEXT NOT NULL, expires_at REAL NOT NULL, created_at REAL NOT NULL);

-- one payment per quote: the quote is the idempotency key
CREATE TABLE payments(
  id TEXT PRIMARY KEY,
  quote_id TEXT NOT NULL UNIQUE REFERENCES quotes(id),
  state TEXT NOT NULL CHECK (state IN ('HELD','UNKNOWN','SETTLED','FAILED')),
  reason TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL, updated_at REAL NOT NULL);

-- USD reserved at hold time (released on failure): closes the concurrent daily-limit race
CREATE TABLE daily_spend(payer TEXT NOT NULL, day INTEGER NOT NULL, usd TEXT NOT NULL, PRIMARY KEY (payer, day));

CREATE TABLE ledger_tx(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ref TEXT NOT NULL, ts REAL NOT NULL,
  sealed INTEGER NOT NULL DEFAULT 0 CHECK (sealed IN (0,1)));
CREATE TABLE ledger_entry(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tx_id INTEGER NOT NULL REFERENCES ledger_tx(id),
  account TEXT NOT NULL CHECK (length(account) > 0), asset TEXT NOT NULL,
  amount INTEGER NOT NULL CHECK (amount <> 0));
CREATE INDEX ledger_entry_tx ON ledger_entry(tx_id);

-- derived but constraint-protected: only ext:* accounts (the outside world) may be negative
CREATE TABLE balances(
  account TEXT NOT NULL, asset TEXT NOT NULL, atoms INTEGER NOT NULL,
  PRIMARY KEY (account, asset),
  CHECK (atoms >= 0 OR substr(account,1,4) = 'ext:'));

CREATE TRIGGER entry_no_insert_when_sealed BEFORE INSERT ON ledger_entry
WHEN (SELECT sealed FROM ledger_tx WHERE id = NEW.tx_id) = 1
BEGIN SELECT RAISE(ABORT, 'ledger: transaction already sealed'); END;
CREATE TRIGGER entry_no_update BEFORE UPDATE ON ledger_entry BEGIN SELECT RAISE(ABORT, 'ledger: entries are append-only'); END;
CREATE TRIGGER entry_no_delete BEFORE DELETE ON ledger_entry BEGIN SELECT RAISE(ABORT, 'ledger: entries are append-only'); END;
CREATE TRIGGER tx_no_delete BEFORE DELETE ON ledger_tx BEGIN SELECT RAISE(ABORT, 'ledger: transactions are append-only'); END;
CREATE TRIGGER tx_only_seal BEFORE UPDATE ON ledger_tx
WHEN NOT (OLD.sealed = 0 AND NEW.sealed = 1 AND NEW.id = OLD.id AND NEW.ref = OLD.ref AND NEW.ts = OLD.ts)
BEGIN SELECT RAISE(ABORT, 'ledger: a transaction can only be sealed, once'); END;
-- double entry enforced by the database: cannot seal unless every asset nets to zero
CREATE TRIGGER tx_seal_check BEFORE UPDATE OF sealed ON ledger_tx WHEN NEW.sealed = 1
BEGIN
  SELECT RAISE(ABORT, 'ledger: needs at least two entries') WHERE (SELECT COUNT(*) FROM ledger_entry WHERE tx_id = OLD.id) < 2;
  SELECT RAISE(ABORT, 'ledger: unbalanced') WHERE EXISTS
    (SELECT 1 FROM ledger_entry WHERE tx_id = OLD.id GROUP BY asset HAVING SUM(amount) <> 0);
END;
-- balances move only when a transaction is sealed; the CHECK on balances rejects an overdraft and aborts the seal
CREATE TRIGGER tx_seal_apply AFTER UPDATE OF sealed ON ledger_tx WHEN NEW.sealed = 1
BEGIN
  INSERT OR IGNORE INTO balances(account, asset, atoms)
    SELECT DISTINCT account, asset, 0 FROM ledger_entry WHERE tx_id = NEW.id;
  UPDATE balances SET atoms = atoms + (SELECT SUM(e.amount) FROM ledger_entry e
        WHERE e.tx_id = NEW.id AND e.account = balances.account AND e.asset = balances.asset)
  WHERE EXISTS (SELECT 1 FROM ledger_entry e WHERE e.tx_id = NEW.id AND e.account = balances.account AND e.asset = balances.asset);
END;
