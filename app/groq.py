"""Groq chat-completions client (OpenAI-compatible). All AI in OmniPay goes through here; there is no other LLM provider.
Key and models come from config (GROQ_API_KEY, GROQ_MODEL, ...). 429 -> backoff honouring Retry-After; timeouts; response cache.
NOTE (per Groq docs read 2026-10-30): strict structured output and tool calling are not available in the same request,
so payee-setup uses json_schema WITHOUT tools and the agents use tools WITHOUT response_format.
NOT yet run against the live API (no key / network in the build sandbox) - see evals/run.py --live."""
from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from typing import Callable, Optional


class GroqError(Exception):
    pass


def _http(method, url, headers, body, timeout):
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:       # noqa: S310 (fixed https host from config)
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


class GroqClient:
    def __init__(self, settings, ledger=None, http: Callable = _http, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep):
        if not settings.groq_api_key:
            raise GroqError("GROQ_API_KEY not set")
        self.s, self.ledger, self.http, self.clock, self.sleep = settings, ledger, http, clock, sleep
        self.calls = self.cache_hits = self.retries = 0

    def chat(self, *, model: str, messages: list[dict], tools: Optional[list[dict]] = None, response_format: Optional[dict] = None,
             temperature: float = 0.1, max_tokens: int = 700, cache_ttl: int = 0, tries: int = 3) -> dict:
        if tools and response_format:
            raise GroqError("structured output and tools cannot be combined in one request")
        payload = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        if tools:
            payload.update(tools=tools, tool_choice="auto")
        if response_format:
            payload["response_format"] = response_format
        body = json.dumps(payload, sort_keys=True).encode()
        key = hashlib.sha256(body).hexdigest()
        if cache_ttl and self.ledger:
            r = self.ledger.conn().execute("SELECT body, created_at FROM llm_cache WHERE key=?", (key,)).fetchone()
            if r and self.clock() - r["created_at"] < cache_ttl:
                self.cache_hits += 1
                return json.loads(r["body"])
        headers = {"Authorization": f"Bearer {self.s.groq_api_key}", "Content-Type": "application/json"}
        last = "unknown"
        for attempt in range(tries):
            try:
                st, hdrs, raw = self.http("POST", self.s.groq_base_url + "/chat/completions", headers, body, self.s.groq_timeout)
            except (TimeoutError, OSError, urllib.error.URLError) as e:
                last = f"network {e!r}"
                self.retries += 1
                self.sleep(min(2 ** attempt, 6))
                continue
            self.calls += 1
            if st == 429 or st >= 500:
                last = f"http {st}"
                self.retries += 1
                self.sleep(float(hdrs.get("retry-after", hdrs.get("Retry-After", min(2 ** attempt, 6)))) if st == 429 else min(2 ** attempt, 6))
                continue
            if st >= 400:
                raise GroqError(f"http {st}: {raw[:200]!r}")
            try:
                msg = json.loads(raw)["choices"][0]["message"]
            except (KeyError, IndexError, ValueError):
                raise GroqError("malformed response")
            if cache_ttl and self.ledger and not tools:
                self.ledger.conn().execute("INSERT OR REPLACE INTO llm_cache VALUES(?,?,?)", (key, json.dumps(msg), self.clock()))
            return msg
        raise GroqError(f"unavailable after {tries} tries: {last}")
