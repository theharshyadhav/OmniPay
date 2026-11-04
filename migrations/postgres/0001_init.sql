-- STATUS: WRITTEN, NOT EXECUTED (no Postgres in this sandbox). Mirrors migrations/sqlite/0001_init.sql.
-- Money is NUMERIC(38,8). Double entry is enforced by a DEFERRED constraint trigger evaluated at COMMIT.
CREATE TABLE users(handle text PRIMARY KEY CHECK (handle LIKE '%@%'), region text NOT NULL, settle_in text NOT NULL,
  accepts jsonb NOT NULL, salt bytea, pin_hash bytea, pin_fails int NOT NULL DEFAULT 0,
  pin_locked_until double precision NOT NULL DEFAULT 0, created_at double precision NOT NULL);
CREATE TABLE quotes(id text PRIMARY KEY, payer text NOT NULL REFERENCES users, payee text NOT NULL REFERENCES users,
  invoice numeric(38,8) NOT NULL, invoice_asset text NOT NULL, route text NOT NULL CHECK (route IN ('direct','convert')),
  pay_asset text NOT NULL, amount numeric(38,8) NOT NULL, receive_asset text NOT NULL, receive numeric(38,8) NOT NULL,
  usd numeric(38,8) NOT NULL, expires_at double precision NOT NULL, created_at double precision NOT NULL);
CREATE TABLE payments(id text PRIMARY KEY, quote_id text NOT NULL UNIQUE REFERENCES quotes,
  state text NOT NULL CHECK (state IN ('HELD','UNKNOWN','SETTLED','FAILED')), reason text NOT NULL DEFAULT '',
  created_at double precision NOT NULL, updated_at double precision NOT NULL);
CREATE TABLE daily_spend(payer text NOT NULL, day bigint NOT NULL, usd numeric(38,8) NOT NULL, PRIMARY KEY(payer, day));
CREATE TABLE ledger_tx(id bigserial PRIMARY KEY, ref text NOT NULL, ts double precision NOT NULL);
CREATE TABLE ledger_entry(id bigserial PRIMARY KEY, tx_id bigint NOT NULL REFERENCES ledger_tx,
  account text NOT NULL CHECK (account <> ''), asset text NOT NULL, amount numeric(38,8) NOT NULL CHECK (amount <> 0));
CREATE INDEX ON ledger_entry(tx_id);
CREATE TABLE balances(account text NOT NULL, asset text NOT NULL, amount numeric(38,8) NOT NULL,
  PRIMARY KEY(account, asset), CHECK (amount >= 0 OR account LIKE 'ext:%'));

CREATE FUNCTION entry_applied() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO balances(account, asset, amount) VALUES (NEW.account, NEW.asset, NEW.amount)
  ON CONFLICT (account, asset) DO UPDATE SET amount = balances.amount + EXCLUDED.amount;
  RETURN NEW;
END $$;
CREATE TRIGGER entry_apply AFTER INSERT ON ledger_entry FOR EACH ROW EXECUTE FUNCTION entry_applied();

CREATE FUNCTION tx_balanced() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM ledger_entry WHERE tx_id = NEW.tx_id GROUP BY asset HAVING SUM(amount) <> 0)
     OR (SELECT COUNT(*) FROM ledger_entry WHERE tx_id = NEW.tx_id) < 2 THEN
    RAISE EXCEPTION 'ledger: transaction % unbalanced', NEW.tx_id;
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER entry_balanced AFTER INSERT ON ledger_entry
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION tx_balanced();

CREATE FUNCTION append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'ledger is append-only'; END $$;
CREATE TRIGGER entry_ro BEFORE UPDATE OR DELETE ON ledger_entry FOR EACH ROW EXECUTE FUNCTION append_only();
CREATE TRIGGER tx_ro BEFORE UPDATE OR DELETE ON ledger_tx FOR EACH ROW EXECUTE FUNCTION append_only();
-- Port TODO: DbLedger uses SQLite SQL dialect (BEGIN IMMEDIATE, SAVEPOINT, INSERT OR REPLACE); a psycopg
-- adapter with SELECT ... FOR UPDATE on balances rows is still to be written and run against a real Postgres.
