import numpy as np
import pandas as pd
import pytest

from finpilot import tools
from finpilot.context import get_context, reset_context
from finpilot.data.synthetic import PLANTED
from finpilot.evidence import LEDGER
from finpilot.quant import risk, scenarios
from finpilot.verify import verify_report


@pytest.fixture(autouse=True)
def fresh():
    LEDGER.reset()
    reset_context()


def test_risk_contributions_match_finite_difference():
    c = get_context()
    R, w, *_ = risk.prep_window(c.returns, c.weights, 252)
    cov = R.cov().values * 252
    f = lambda x: np.sqrt(x @ cov @ x)
    wv, eps = w.values, 1e-6
    grad = np.array([(f(wv + eps * e) - f(wv - eps * e)) / (2 * eps) for e in np.eye(len(wv))])
    rc = risk.risk_contributions(R, w)
    assert np.allclose(rc.contribution_vol.values, wv * grad, atol=1e-6)
    assert rc.contribution_vol.sum() == pytest.approx(f(wv))


def test_drift_decompositions_are_exact_and_find_planted_driver():
    c = get_context()
    d = risk.drift_analysis(c.returns, c.weights)
    assert sum(h["delta_contribution"] for h in d["holdings"]) == pytest.approx(d["vol_change"], abs=1e-9)
    assert d["vol_effect"] + d["correlation_effect"] == pytest.approx(d["vol_change"], abs=1e-9)
    assert PLANTED["top_risk_driver"] in d["top_drivers_of_increase"][:2]


def test_drawdown_known_series():
    r = pd.Series([0.1, -0.2, 0.05], index=pd.bdate_range("2024-01-01", periods=3))
    dd = risk.drawdown_stats(r)
    assert dd["max_drawdown"] == pytest.approx(-0.2)
    assert dd["peak_date"] == "2024-01-01" and dd["trough_date"] == "2024-01-02"


def test_scenarios_are_reproducible_and_sane():
    a = tools.run_market_decline(-20)
    b = tools.run_market_decline(-20)
    assert a["portfolio_impact"] == b["portfolio_impact"] < 0
    assert tools.recompute_evidence(a["evidence_id"])["reproduced"] is True
    assert tools.run_rate_shock(100)["status"] == "ok"
    with pytest.raises(Exception):
        scenarios.sector_shock(get_context().returns, get_context().weights, get_context().sectors, "Nope", 10)


def test_stress_excludes_missing_history_and_reports_coverage():
    gfc = next(w for w in tools.run_historical_stress(["gfc_2008"])["windows"])
    assert gfc["excluded_holdings"] == ["TSLA"] and gfc["weight_covered"] < 1


def test_verifier_flags_fabrications():
    d = tools.compute_risk_metrics()
    good = f"Annualised volatility was {d['annualised_vol']*100:.1f}% [{d['evidence_id']}]. This uses synthetic data.\n## Limitations\nNone."
    assert verify_report(good, LEDGER)["passed"]
    bad = "Volatility was 99.9% [CALC-001]. Volatility was 12.3%. See [CALC-777]. This uses synthetic data.\n## Limitations"
    v = verify_report(bad, LEDGER)
    assert v["numeric_mismatches"] and v["unsupported_claims"] and v["invalid_citations"] and not v["passed"]
    assert "synthetic_data_not_disclosed" in verify_report("Vol 1.0% [CALC-001]. Limitations", LEDGER)["structural_problems"]
