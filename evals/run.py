"""Run the scripted agent conversations and report the pass rate.
    python -m evals.run            offline: deterministic planner + scripted models (what CI runs)
    python -m evals.run --live     the `live` subset against the real Groq model (needs GROQ_API_KEY; NOT run in the build sandbox)"""
from __future__ import annotations

import sys
import traceback

from evals.scenarios import S, world
from app.config import Settings
from app.groq import GroqClient


def run_one(sc, live=False):
    pf = world(**sc.world_kw)
    try:
        sc.setup(pf)
        if live:
            pf.groq = GroqClient(Settings.from_env(), pf.ledger)
        elif sc.model:
            m = sc.model(pf)
            if m is not None:
                pf.groq = m
        res = None
        for t in sc.texts:
            if sc.agent == "concierge":
                res = pf.agents.concierge("alice@omni", t)
            elif sc.agent == "advisor":
                res = pf.agents.advisor("alice@omni", t)
            else:
                res = pf.agents.payee_setup("bob@omni", t)
        problems = []
        if sc.expect_tools is not None:
            got = res["tool_calls"]
            ok = set(got) >= set(sc.expect_tools) if live else got == sc.expect_tools
            if not ok:
                problems.append(f"tools {got} != expected {sc.expect_tools}")
        verdict = sc.check(pf, res)
        if verdict is not True and not (verdict and not isinstance(verdict, str)):
            problems.append(f"check: {verdict}")
        if pf.ledger.audit():
            problems.append(f"ledger audit: {pf.ledger.audit()[:1]}")
        return problems
    except Exception:                                                  # noqa: BLE001
        return [traceback.format_exc(limit=3)]


def main(argv):
    live = "--live" in argv
    chosen = [s for s in S if (s.live or not live)]
    rows = [(s, run_one(s, live)) for s in chosen]
    for s, p in rows:
        print(("PASS " if not p else "FAIL ") + s.id + ("" if not p else "\n      " + "\n      ".join(p)))
    ok = sum(1 for _, p in rows if not p)
    by = {}
    for s, p in rows:
        a = by.setdefault(s.kind, [0, 0]); a[1] += 1; a[0] += int(not p)
    print(f"\n{ok}/{len(rows)} passed ({100 * ok / len(rows):.0f}%)  " + "  ".join(f"{k}: {a[0]}/{a[1]}" for k, a in by.items()) + ("  [LIVE Groq]" if live else "  [offline]"))
    return 0 if ok == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
