-- Payment requests created by a payee. memo and display_name are UNTRUSTED text shown to payers and to agents.
CREATE TABLE invoices(
  id TEXT PRIMARY KEY, payee TEXT NOT NULL REFERENCES users(handle), amount TEXT NOT NULL, currency TEXT NOT NULL,
  memo TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','PAID','CANCELLED')), created_at REAL NOT NULL);
CREATE TABLE display_names(handle TEXT PRIMARY KEY REFERENCES users(handle), name TEXT NOT NULL);
