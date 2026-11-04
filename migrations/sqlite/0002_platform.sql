-- M2-M6: PayPal edges, persisted promo market, auth, kill switches, audit, agents.

-- ---------- auth ----------
CREATE TABLE credentials(
  handle TEXT PRIMARY KEY REFERENCES users(handle), email TEXT,
  pw_salt BLOB NOT NULL, pw_hash BLOB NOT NULL,
  failed INTEGER NOT NULL DEFAULT 0, locked_until REAL NOT NULL DEFAULT 0,
  is_admin INTEGER NOT NULL DEFAULT 0, frozen INTEGER NOT NULL DEFAULT 0, last_login REAL NOT NULL DEFAULT 0,
  payout_email TEXT);
CREATE TABLE sessions(
  token_hash TEXT PRIMARY KEY, handle TEXT NOT NULL REFERENCES users(handle),
  created_at REAL NOT NULL, expires_at REAL NOT NULL, authed_at REAL NOT NULL);

-- ---------- audit (append-only) ----------
CREATE TABLE audit_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
  target TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '{}', request_id TEXT NOT NULL DEFAULT '');
CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit_log BEGIN SELECT RAISE(ABORT,'audit log is append-only'); END;
CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_log BEGIN SELECT RAISE(ABORT,'audit log is append-only'); END;

-- ---------- kill switches & feature flags ----------
-- scope: global | asset | kind | region | paypal_in | paypal_out | agents ; enabled=0 means OFF/killed
CREATE TABLE switches(
  scope TEXT NOT NULL, key TEXT NOT NULL, enabled INTEGER NOT NULL CHECK (enabled IN (0,1)),
  reason TEXT NOT NULL DEFAULT '', updated_by TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL,
  PRIMARY KEY (scope, key));

-- ---------- PayPal edge ----------
CREATE TABLE paypal_orders(
  id TEXT PRIMARY KEY, paypal_order_id TEXT UNIQUE, handle TEXT NOT NULL REFERENCES users(handle),
  purpose TEXT NOT NULL CHECK (purpose IN ('fund','promoter_topup')),
  currency TEXT NOT NULL, amount TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('NEW','CREATED','CAPTURED','FAILED')),
  created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE paypal_captures(
  capture_id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES paypal_orders(id),
  gross TEXT NOT NULL, fee TEXT NOT NULL, net TEXT NOT NULL, currency TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('CREDITED','REVERSED')), created_at REAL NOT NULL);
CREATE TABLE webhook_events(
  event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL, received_at REAL NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('PROCESSED','IGNORED','FAILED')), detail TEXT NOT NULL DEFAULT '');
CREATE TABLE payouts(
  id TEXT PRIMARY KEY, handle TEXT NOT NULL REFERENCES users(handle), currency TEXT NOT NULL, amount TEXT NOT NULL,
  receiver_email TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('HELD','SUBMITTED','PENDING','SUCCESS','FAILED','RETURNED','UNKNOWN')),
  paypal_batch_id TEXT, paypal_item_id TEXT, fee TEXT NOT NULL DEFAULT '0', reason TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE recon_breaks(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, kind TEXT NOT NULL, ref TEXT NOT NULL,
  detail TEXT NOT NULL, resolved INTEGER NOT NULL DEFAULT 0, UNIQUE(kind, ref));
CREATE TABLE chargebacks(
  capture_id TEXT PRIMARY KEY, handle TEXT NOT NULL, currency TEXT NOT NULL, owed TEXT NOT NULL, recovered TEXT NOT NULL,
  shortfall TEXT NOT NULL, created_at REAL NOT NULL);

-- ---------- promo market (persisted) ----------
CREATE TABLE bids(
  promoter TEXT NOT NULL REFERENCES users(handle), asset TEXT NOT NULL, bps TEXT NOT NULL, fund_asset TEXT NOT NULL,
  regions TEXT NOT NULL, placed_at REAL NOT NULL, PRIMARY KEY (promoter, asset));
CREATE TABLE listings(id TEXT PRIMARY KEY, body TEXT NOT NULL, expires_at REAL NOT NULL);
-- one row per tapped sponsored row; `reserved` is the promoter's maximum charge, held out of escrow at tap time
CREATE TABLE attributions(
  quote_id TEXT PRIMARY KEY REFERENCES quotes(id), promoter TEXT NOT NULL, asset TEXT NOT NULL,
  fund_asset TEXT NOT NULL, price_bps TEXT NOT NULL, reserved TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('RESERVED','BILLED','RELEASED','UNBILLED')),
  billed TEXT NOT NULL DEFAULT '0', payer TEXT NOT NULL, payee TEXT NOT NULL, day INTEGER NOT NULL,
  reason TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE INDEX attributions_state ON attributions(state);
CREATE TABLE autobids(
  promoter TEXT NOT NULL, asset TEXT NOT NULL, max_bps TEXT NOT NULL, daily_cap_usd TEXT NOT NULL, total_cap_usd TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL, PRIMARY KEY (promoter, asset));

-- ---------- agents ----------
CREATE TABLE mandates(
  handle TEXT PRIMARY KEY REFERENCES users(handle), enabled INTEGER NOT NULL DEFAULT 0,
  per_payment_usd TEXT NOT NULL, daily_usd TEXT NOT NULL, allowed_assets TEXT NOT NULL,
  payee_allowlist TEXT NOT NULL, confirm_above_usd TEXT NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE agent_spend(handle TEXT NOT NULL, day INTEGER NOT NULL, usd TEXT NOT NULL, PRIMARY KEY (handle, day));
CREATE TABLE pending_confirmations(
  id TEXT PRIMARY KEY, handle TEXT NOT NULL, quote_id TEXT NOT NULL, usd TEXT NOT NULL, summary TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('PENDING','CONFIRMED','REJECTED','EXPIRED')), expires_at REAL NOT NULL, created_at REAL NOT NULL);
CREATE TABLE agent_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, handle TEXT NOT NULL, session TEXT NOT NULL, agent TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('user','assistant','tool_call','tool_result','blocked','fallback','error')),
  name TEXT NOT NULL DEFAULT '', body TEXT NOT NULL);
CREATE TABLE pending_profiles(
  id TEXT PRIMARY KEY, handle TEXT NOT NULL, profile TEXT NOT NULL, state TEXT NOT NULL CHECK (state IN ('PENDING','SAVED','CANCELLED')),
  created_at REAL NOT NULL);
CREATE TABLE llm_cache(key TEXT PRIMARY KEY, body TEXT NOT NULL, created_at REAL NOT NULL);
