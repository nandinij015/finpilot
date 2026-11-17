"""FinPilot scorecard.

Deterministic section (no LLM, offline, synthetic data with a planted driver):
  calculation accuracy, risk-driver identification, scenario reproducibility, verifier catches fabrications.
LLM section (--llm; needs Gemini credentials): end-to-end runs, scoring unsupported claims, citation validity,
planted-driver recall, and repair-loop usage.

    python -m evals.run_evals            # deterministic
    python -m evals.run_evals --llm      # + end-to-end agent runs
"""
import argparse
import asyncio
import os
import sys

os.environ.setdefault("FINPILOT_DATA_SOURCE", "synthetic")

import numpy as np

from finpilot import tools
from finpilot.context import get_context, reset_context
from finpilot.data.synthetic import PLANTED
from finpilot.evidence import LEDGER
from finpilot.quant import risk
from finpilot.verify import verify_report

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


def deterministic() -> None:
    LEDGER.reset(); reset_context()
    c = get_context()
    # 1. calculation accuracy vs an independent finite-difference reference
    R, w, *_ = risk.prep_window(c.returns, c.weights, 252)
    cov = R.cov().values * 252
    f = lambda x: np.sqrt(x @ cov @ x)
    grad = np.array([(f(w.values + 1e-6 * e) - f(w.values - 1e-6 * e)) / 2e-6 for e in np.eye(len(w))])
    err = np.abs(risk.risk_contributions(R, w).contribution_vol.values - w.values * grad).max()
    check("calc accuracy: risk contributions vs finite difference", err < 1e-6, f"max abs err {err:.2e}")
    # 2. largest-risk-contributor identification
    d = tools.analyze_risk_drift()
    top = d["top_drivers_of_increase"]
    check("driver identification: planted driver in top-2 of vol increase", PLANTED["top_risk_driver"] in top[:2], f"top={top}")
    m = tools.compute_risk_metrics(63)
    top_rc = max(m["risk_contributions"], key=lambda r: r["pct_contribution"])["ticker"]
    check("driver identification: planted driver is #1 risk contributor in recent window", top_rc == PLANTED["top_risk_driver"], f"top={top_rc}")
    # 3. exactness of decompositions
    check("decomposition: holdings sum to vol change", abs(sum(h["delta_contribution"] for h in d["holdings"]) - d["vol_change"]) < 1e-3)
    check("decomposition: vol effect + corr effect = vol change", abs(d["vol_effect"] + d["correlation_effect"] - d["vol_change"]) < 1e-3)
    # 4. scenario reproducibility
    tools.run_market_decline(-20); tools.run_rate_shock(100); tools.run_sector_shock("Technology", 15); tools.run_historical_stress()
    bad = [e.id for e in LEDGER.all() if e.kind == "CALC" and not tools.recompute_evidence(e.id).get("reproduced")]
    check("reproducibility: every CALC evidence recomputes identically", not bad, f"failures={bad}")
    # 5. the verifier rejects a fabricated report
    fake = "Portfolio volatility is 77.7% [CALC-001]. Tesla announced a recall. See [CALC-999]. synthetic.\n## Limitations\nx"
    v = verify_report(fake, LEDGER)
    check("unsupported claims: verifier flags fabricated number, invented id, uncited news", bool(v["numeric_mismatches"] and v["invalid_citations"] and v["unsupported_claims"]))


QUESTIONS = [
    "Why is this portfolio becoming riskier, which holdings are driving the change, and what scenarios could make the losses worse?",
    "Is correlation or volatility responsible for the recent rise in portfolio risk? Stress test a 150bp rate rise.",
]


def llm() -> None:
    from finpilot.runner import run_investigation

    for q in QUESTIONS:
        run = asyncio.run(run_investigation(q))
        v = run["verification"]
        txt = run["report_markdown"]
        n_bad = len(v["unsupported_claims"]) + len(v["numeric_mismatches"]) + len(v["invalid_citations"])
        check(f"[LLM] verification passed: {q[:45]}...", v["passed"], f"issues={n_bad}, repairs={run['repairs_used']}")
        check("[LLM] planted driver named in report", PLANTED["top_risk_driver"] in txt)
        news = [e for e in run["evidence"] if e["kind"] == "NEWS"]
        unverified = sum(1 for e in news for i in e["result"].get("items", []) if not i.get("date_verified"))
        check("[LLM] source attribution: no unverified-date news cited", unverified == 0, f"news calls={len(news)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true")
    deterministic()
    if ap.parse_args().llm:
        llm()
    width = max(len(n) for n, *_ in RESULTS)
    for n, ok, det in RESULTS:
        print(f"{'PASS' if ok else 'FAIL'}  {n.ljust(width)}  {det}")
    sys.exit(0 if all(ok for _, ok, _ in RESULTS) else 1)
