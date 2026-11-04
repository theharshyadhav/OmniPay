"""Thin PayPal REST client (Orders v2, Webhooks verify, Payouts, Transaction Search). stdlib only.
Endpoints/fields follow developer.paypal.com / docs.paypal.ai as read on 2026-10-30; sandbox first, `live` is gated
in config. `http` is injectable so everything is testable without the network. NOT yet run against the real Sandbox."""
from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal as D
from typing import Callable, Optional

ZERO_DECIMAL = {"JPY", "HUF", "TWD"}


def fmt_amount(currency: str, amount: D) -> str:
    dp = 0 if currency in ZERO_DECIMAL else 2
    q = D(1).scaleb(-dp)
    if amount != amount.quantize(q):
        raise ValueError(f"{amount} {currency} is finer than PayPal's minor unit")
    return f"{amount.quantize(q):.{dp}f}"


class PayPalError(Exception):
    def __init__(self, status: int, body: dict | str, *, retryable: bool = False):
        super().__init__(f"PayPal HTTP {status}: {str(body)[:300]}")
        self.status, self.body, self.retryable = status, body, retryable

    @property
    def definitive(self) -> bool:
        """A 4xx (not 429/401) means PayPal looked at the request and refused it: nothing was done."""
        return 400 <= self.status < 500 and self.status not in (401, 408, 429)


def _urllib_http(method, url, headers, body, timeout):
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:       # noqa: S310 (fixed https PayPal hosts)
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


class PayPalClient:
    def __init__(self, settings, http: Callable = _urllib_http, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep, timeout: float = 20.0):
        if not (settings.paypal_client_id and settings.paypal_secret):
            raise ValueError("PAYPAL_CLIENT_ID / PAYPAL_SECRET not set")
        self.s, self.http, self.clock, self.sleep, self.timeout = settings, http, clock, sleep, timeout
        self.base = settings.paypal_base
        self._tok, self._tok_exp = "", 0.0
        self.simulated = False

    # ---- transport ----------------------------------------------------------
    def _token(self, force=False) -> str:
        if self._tok and not force and self.clock() < self._tok_exp - 60:
            return self._tok
        basic = base64.b64encode(f"{self.s.paypal_client_id}:{self.s.paypal_secret}".encode()).decode()
        st, _, body = self.http("POST", f"{self.base}/v1/oauth2/token",
                                {"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
                                b"grant_type=client_credentials", self.timeout)
        if st != 200:
            raise PayPalError(st, "oauth failed", retryable=st >= 500)
        d = json.loads(body)
        self._tok, self._tok_exp = d["access_token"], self.clock() + int(d.get("expires_in", 300))
        return self._tok

    def request(self, method: str, path: str, payload: Optional[dict] = None, *, request_id: str = "",
                query: Optional[dict] = None, tries: int = 3) -> dict:
        url = self.base + path + (("?" + urllib.parse.urlencode(query)) if query else "")
        body = json.dumps(payload).encode() if payload is not None else None
        last: Exception | None = None
        for attempt in range(tries):
            headers = {"Authorization": f"Bearer {self._token()}", "Content-Type": "application/json", "Accept": "application/json"}
            if request_id:
                headers["PayPal-Request-Id"] = request_id                    # idempotency across retries
            try:
                st, hdrs, raw = self.http(method, url, headers, body, self.timeout)
            except (TimeoutError, OSError, urllib.error.URLError) as e:       # outcome unknown
                last = PayPalError(0, f"network: {e!r}", retryable=True)
                self.sleep(min(2 ** attempt, 8))
                continue
            if st == 401 and attempt == 0:
                self._token(force=True)
                continue
            if st == 429 or st >= 500:
                last = PayPalError(st, raw.decode(errors="replace"), retryable=True)
                self.sleep(float(hdrs.get("Retry-After", min(2 ** attempt, 8))) if st == 429 else min(2 ** attempt, 8))
                continue
            data = json.loads(raw) if raw else {}
            if st >= 400:
                raise PayPalError(st, data)
            return data
        raise last or PayPalError(0, "unknown", retryable=True)

    # ---- Orders v2 (money in) ----------------------------------------------
    def create_order(self, order_id: str, currency: str, amount: D, description: str = "OmniPay wallet top-up") -> str:
        d = self.request("POST", "/v2/checkout/orders", request_id=f"{order_id}-create", payload={
            "intent": "CAPTURE",
            "purchase_units": [{"reference_id": order_id, "custom_id": order_id, "description": description,
                                "amount": {"currency_code": currency, "value": fmt_amount(currency, amount)}}]})
        return d["id"]

    def capture_order(self, paypal_order_id: str, order_id: str) -> dict:
        return self.request("POST", f"/v2/checkout/orders/{paypal_order_id}/capture", request_id=f"{order_id}-capture", payload={})

    def get_capture(self, capture_id: str) -> dict:
        return self.request("GET", f"/v2/payments/captures/{capture_id}")

    # ---- Webhooks -----------------------------------------------------------
    def verify_webhook(self, headers: dict, event: dict) -> bool:
        h = {k.lower(): v for k, v in headers.items()}
        try:
            d = self.request("POST", "/v1/notifications/verify-webhook-signature", payload={
                "auth_algo": h.get("paypal-auth-algo", ""), "cert_url": h.get("paypal-cert-url", ""),
                "transmission_id": h.get("paypal-transmission-id", ""), "transmission_sig": h.get("paypal-transmission-sig", ""),
                "transmission_time": h.get("paypal-transmission-time", ""), "webhook_id": self.s.paypal_webhook_id,
                "webhook_event": event})
        except PayPalError:
            return False
        return d.get("verification_status") == "SUCCESS"

    # ---- Payouts (money out) -----------------------------------------------
    def create_payout(self, payout_id: str, email: str, currency: str, amount: D, note: str = "OmniPay payout") -> dict:
        return self.request("POST", "/v1/payments/payouts", request_id=payout_id, payload={
            "sender_batch_header": {"sender_batch_id": payout_id, "email_subject": "You have a payout from OmniPay"},
            "items": [{"recipient_type": "EMAIL", "receiver": email, "sender_item_id": payout_id, "note": note,
                       "amount": {"currency": currency, "value": fmt_amount(currency, amount)}}]})

    def get_payout(self, batch_id: str) -> dict:
        return self.request("GET", f"/v1/payments/payouts/{batch_id}")

    # ---- Transaction Search (reconciliation) -------------------------------
    def search_transactions(self, start_iso: str, end_iso: str) -> list[dict]:
        out, page = [], 1
        while True:
            d = self.request("GET", "/v1/reporting/transactions", query={
                "start_date": start_iso, "end_date": end_iso, "fields": "transaction_info", "page_size": 100, "page": page})
            out += d.get("transaction_details", [])
            if page >= int(d.get("total_pages", 1)):
                return out
            page += 1
