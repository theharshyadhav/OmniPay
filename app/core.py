"""Audit log, kill switches / feature flags, and authentication (secure sessions + step-up + lockout)."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from decimal import Decimal as D
from typing import Optional

from omnipay import PaymentError
from app.dbledger import DbLedger


class Audit:
    def __init__(self, ledger: DbLedger):
        self.ledger = ledger
        self.request_id = threading.local()

    def log(self, actor: str, action: str, target: str = "", **detail) -> None:
        self.ledger.conn().execute(
            "INSERT INTO audit_log(ts, actor, action, target, detail, request_id) VALUES(?,?,?,?,?,?)",
            (self.ledger.clock(), actor, action, target, json.dumps(detail, default=str, sort_keys=True),
             getattr(self.request_id, "v", "")))

    def recent(self, limit: int = 100, actor: str | None = None) -> list[dict]:
        q, a = "SELECT * FROM audit_log", ()
        if actor:
            q, a = q + " WHERE actor=?", (actor,)
        return [dict(r) for r in self.ledger.conn().execute(q + " ORDER BY id DESC LIMIT ?", a + (limit,))]


class Switches:
    """enabled=1 (default when no row) means the thing is ON. Admin flips scopes OFF = kill switch."""
    SCOPES = {"global", "asset", "kind", "region", "paypal_in", "paypal_out", "agents"}

    def __init__(self, ledger: DbLedger, audit: Audit):
        self.ledger, self.audit = ledger, audit

    def set(self, scope: str, key: str, enabled: bool, actor: str, reason: str = "") -> None:
        if scope not in self.SCOPES:
            raise PaymentError("BAD_SCOPE", scope)
        self.ledger.conn().execute(
            "INSERT INTO switches VALUES(?,?,?,?,?,?) ON CONFLICT(scope,key) DO UPDATE SET enabled=excluded.enabled, "
            "reason=excluded.reason, updated_by=excluded.updated_by, updated_at=excluded.updated_at",
            (scope, key, int(enabled), reason, actor, self.ledger.clock()))
        self.audit.log(actor, "switch.set", f"{scope}:{key}", enabled=enabled, reason=reason)

    def on(self, scope: str, key: str = "*") -> bool:
        r = self.ledger.conn().execute("SELECT enabled FROM switches WHERE scope=? AND key=?", (scope, key)).fetchone()
        return True if r is None else bool(r[0])

    def require(self, scope: str, key: str = "*", code: str = "KILL_SWITCH") -> None:
        if not self.on(scope, key):
            raise PaymentError(code, f"{scope}:{key} is switched off")

    def all(self) -> list[dict]:
        return [dict(r) for r in self.ledger.conn().execute("SELECT * FROM switches ORDER BY scope, key")]


class RateLimiter:
    """Sliding window, in process. Behind several instances use Redis/Postgres instead (see README)."""

    def __init__(self, clock=time.time):
        self.clock, self.hits, self.lock = clock, defaultdict(deque), threading.Lock()

    def check(self, key: str, limit: int, window: float = 60.0) -> None:
        now = self.clock()
        with self.lock:
            q = self.hits[key]
            while q and q[0] <= now - window:
                q.popleft()
            if len(q) >= limit:
                raise PaymentError("RATE_LIMITED", f"more than {limit} per {int(window)}s")
            q.append(now)


class Auth:
    MAX_FAILS, LOCK_SECONDS, SCRYPT = 5, 900, dict(n=2**14, r=8, p=1)

    def __init__(self, ledger: DbLedger, audit: Audit, session_ttl: int = 8 * 3600, stepup_window: int = 900,
                 scrypt_n: int | None = None):
        self.ledger, self.audit, self.ttl, self.stepup = ledger, audit, session_ttl, stepup_window
        self.params = dict(self.SCRYPT, **({"n": scrypt_n} if scrypt_n else {}))

    def _kdf(self, pw: str, salt: bytes) -> bytes:
        return hashlib.scrypt(pw.encode(), salt=salt, dklen=32, maxmem=64 * 1024 * 1024, **self.params)

    def set_password(self, handle: str, password: str, *, email: str = "", is_admin: bool = False) -> None:
        if len(password) < 10:
            raise PaymentError("WEAK_PASSWORD", "at least 10 characters")
        salt = os.urandom(16)
        self.ledger.conn().execute(
            "INSERT INTO credentials(handle,email,pw_salt,pw_hash,is_admin) VALUES(?,?,?,?,?) "
            "ON CONFLICT(handle) DO UPDATE SET pw_salt=excluded.pw_salt, pw_hash=excluded.pw_hash",
            (handle, email, salt, self._kdf(password, salt), int(is_admin)))
        self.audit.log(handle, "auth.set_password", handle)

    def login(self, handle: str, password: str) -> str:
        c, now = self.ledger.conn(), self.ledger.clock()
        r = c.execute("SELECT * FROM credentials WHERE handle=?", (handle,)).fetchone()
        if r is None:                                           # same cost and same error: no user enumeration
            self._kdf(password, b"0" * 16)
            raise PaymentError("BAD_LOGIN", "wrong handle or password")
        if r["locked_until"] > now:
            raise PaymentError("LOGIN_LOCKED", "too many attempts, try later")
        if not hmac.compare_digest(r["pw_hash"], self._kdf(password, r["pw_salt"])):
            with self.ledger.tx() as t:
                t.execute("UPDATE credentials SET failed=failed+1 WHERE handle=?", (handle,))
                if t.execute("SELECT failed FROM credentials WHERE handle=?", (handle,)).fetchone()[0] >= self.MAX_FAILS:
                    t.execute("UPDATE credentials SET locked_until=?, failed=0 WHERE handle=?", (now + self.LOCK_SECONDS, handle))
            self.audit.log(handle, "auth.login_failed", handle)
            raise PaymentError("BAD_LOGIN", "wrong handle or password")
        token = secrets.token_urlsafe(32)
        with self.ledger.tx() as t:
            t.execute("UPDATE credentials SET failed=0, last_login=? WHERE handle=?", (now, handle))
            t.execute("INSERT INTO sessions VALUES(?,?,?,?,?)",
                      (self._h(token), handle, now, now + self.ttl, now))
        self.audit.log(handle, "auth.login", handle)
        return token

    @staticmethod
    def _h(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()      # only a hash of the token is stored

    def session(self, token: Optional[str]) -> dict:
        if not token:
            raise PaymentError("UNAUTHENTICATED", "login required")
        r = self.ledger.conn().execute(
            "SELECT s.handle, s.authed_at, c.is_admin, c.frozen FROM sessions s JOIN credentials c ON c.handle=s.handle "
            "WHERE s.token_hash=? AND s.expires_at>?", (self._h(token), self.ledger.clock())).fetchone()
        if r is None:
            raise PaymentError("UNAUTHENTICATED", "session expired or invalid")
        return {"handle": r["handle"], "admin": bool(r["is_admin"]), "frozen": bool(r["frozen"]), "authed_at": r["authed_at"]}

    def logout(self, token: str) -> None:
        self.ledger.conn().execute("DELETE FROM sessions WHERE token_hash=?", (self._h(token),))

    def require_fresh(self, sess: dict) -> None:
        """Step-up for sensitive actions: the password must have been entered recently."""
        if self.ledger.clock() - sess["authed_at"] > self.stepup:
            raise PaymentError("STEP_UP_REQUIRED", "re-enter your password")

    def step_up(self, token: str, password: str) -> None:
        s = self.session(token)
        r = self.ledger.conn().execute("SELECT pw_salt, pw_hash FROM credentials WHERE handle=?", (s["handle"],)).fetchone()
        if not hmac.compare_digest(r["pw_hash"], self._kdf(password, r["pw_salt"])):
            raise PaymentError("BAD_LOGIN", "wrong password")
        self.ledger.conn().execute("UPDATE sessions SET authed_at=? WHERE token_hash=?", (self.ledger.clock(), self._h(token)))
        self.audit.log(s["handle"], "auth.step_up", s["handle"])
