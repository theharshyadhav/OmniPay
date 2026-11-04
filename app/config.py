"""All configuration comes from environment variables. Secrets are never defaulted or logged."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _csv(v: str) -> frozenset:
    return frozenset(x.strip() for x in v.split(",") if x.strip())


@dataclass(frozen=True)
class Settings:
    db_path: str = "omnipay.db"
    paypal_env: str = "sandbox"                       # sandbox | live  (live stays disabled until LAUNCH_READINESS is done)
    paypal_client_id: str = ""
    paypal_secret: str = ""
    paypal_webhook_id: str = ""
    paypal_currencies: frozenset = frozenset({"USD", "EUR", "GBP"})
    allow_live: bool = False                          # second lock: PAYPAL_ENV=live is refused unless OMNIPAY_ALLOW_LIVE=1
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"       # concierge (tool calling)
    groq_advisor_model: str = "openai/gpt-oss-120b"   # bid advisor (stronger)
    groq_struct_model: str = "openai/gpt-oss-20b"     # strict structured output (payee setup)
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_timeout: float = 20.0
    demo: bool = False
    session_ttl: int = 8 * 3600
    stepup_window: int = 15 * 60
    payout_stepup_usd: str = "200"
    rate_per_min_ip: int = 120
    rate_per_min_user: int = 60
    admin_token: str = ""
    simulated_kinds: frozenset = frozenset({"crypto", "equity"})   # no real provider yet: UI shows "Simulated"

    @property
    def paypal_base(self) -> str:
        return "https://api-m.paypal.com" if self.paypal_env == "live" else "https://api-m.sandbox.paypal.com"

    @staticmethod
    def from_env(env=os.environ) -> "Settings":
        s = Settings(
            db_path=env.get("OMNIPAY_DB", "omnipay.db"),
            paypal_env=env.get("PAYPAL_ENV", "sandbox"),
            paypal_client_id=env.get("PAYPAL_CLIENT_ID", ""), paypal_secret=env.get("PAYPAL_SECRET", ""),
            paypal_webhook_id=env.get("PAYPAL_WEBHOOK_ID", ""),
            paypal_currencies=_csv(env.get("PAYPAL_CURRENCIES", "USD,EUR,GBP")),
            allow_live=env.get("OMNIPAY_ALLOW_LIVE") == "1",
            groq_api_key=env.get("GROQ_API_KEY", ""),
            groq_model=env.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
            groq_advisor_model=env.get("GROQ_ADVISOR_MODEL", "openai/gpt-oss-120b"),
            groq_struct_model=env.get("GROQ_STRUCT_MODEL", "openai/gpt-oss-20b"),
            groq_base_url=env.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
            demo=env.get("OMNIPAY_DEMO") == "1",
            admin_token=env.get("ADMIN_TOKEN", ""),
        )
        if s.paypal_env not in ("sandbox", "live"):
            raise ValueError("PAYPAL_ENV must be sandbox or live")
        if s.paypal_env == "live" and not s.allow_live:
            raise ValueError("PAYPAL_ENV=live refused: complete LAUNCH_READINESS.md, then set OMNIPAY_ALLOW_LIVE=1")
        return s
