"""ADK tool functions. Plain typed Python functions: ADK turns signature + docstring into the tool schema.

(No `from __future__ import annotations` here on purpose: ADK introspects real type objects.)

Every @evidence_tool result is stored in the ledger and returned with an `evidence_id` the agents must cite.
"""
import functools
import inspect
from typing import Callable, Dict, List, Optional

import pandas as pd

from .config import STRESS_WINDOWS, get_settings
from .context import get_context
from .evidence import LEDGER, to_jsonable
from .news.rss import fetch_news
from .quant import risk, scenarios
from .verify import verify_report

REGISTRY: Dict[str, Callable] = {}


def evidence_tool(kind: str):
    def deco(fn):
        REGISTRY[fn.__name__] = fn

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            bound = inspect.signature(fn).bind(*args, **kwargs)
            bound.apply_defaults()
            try:
                result = to_jsonable(fn(*args, **kwargs))
            except Exception as e:  # surfaced to the agent, never silently swallowed
                LEDGER.log_error(fn.__name__, dict(bound.arguments), f"{type(e).__name__}: {e}")
                return {"status": "error", "error": f"{type(e).__name__}: {e}"}
            ev = LEDGER.add(kind, fn.__name__, dict(bound.arguments), result)
            return {"status": "ok", "evidence_id": ev.id, **result}

        return wrapper

    return deco


def _ctx_meta(ctx) -> dict:
    return {"data_source": ctx.data_source}


# --------------------------------------------------------------------------- quant tools
@evidence_tool("CALC")
def get_portfolio_overview() -> dict:
    """Returns the holdings, weights, sector weights and concentration of the portfolio under analysis,
    plus the price-history date range and the data source."""
    c = get_context()
    sec = c.weights.groupby(c.sectors).sum().sort_values(ascending=False)
    return {
        "portfolio": c.portfolio_name,
        "holdings": [{k: h[k] for k in ("ticker", "name", "sector", "asset_class", "weight")} for h in c.holdings],
        "sector_weights": sec.to_dict(),
        "concentration_hhi": risk.hhi(c.weights),
        "effective_number_of_holdings": 1 / risk.hhi(c.weights),
        "price_history_start": c.prices.index[0], "price_history_end": c.prices.index[-1],
        **_ctx_meta(c),
    }


@evidence_tool("CALC")
def analyze_risk_drift(recent_days: int = 63, baseline_days: int = 252) -> dict:
    """Answers "is the portfolio getting riskier and why?". Compares annualised volatility, VaR/ES and average
    correlation in the most recent window with the baseline window right before it, and decomposes the change
    exactly into (a) per-holding risk-contribution changes, (b) a volatility effect vs a correlation effect.

    Args:
        recent_days: Trading days in the recent window (default 63, about 3 months).
        baseline_days: Trading days in the baseline window (default 252, about 1 year).
    """
    c = get_context()
    out = risk.drift_analysis(c.returns, c.weights, recent_days, baseline_days)
    sec = pd.DataFrame(out["holdings"]).assign(sector=lambda d: d.ticker.map(c.sectors))
    out["sector_delta_contribution"] = sec.groupby("sector").delta_contribution.sum().sort_values(ascending=False).to_dict()
    return {**out, **_ctx_meta(c)}


@evidence_tool("CALC")
def compute_risk_metrics(window_days: int = 252) -> dict:
    """Computes volatility, 1-day 95% historical VaR and expected shortfall, max/current drawdown, average
    pairwise correlation, and each holding's percentage contribution to portfolio risk over the last N days.

    Args:
        window_days: Trading days to look back (default 252).
    """
    c = get_context()
    R, w, covered, dropped = risk.prep_window(c.returns, c.weights, window_days)
    pr = risk.portfolio_returns(R, w)
    rc = risk.risk_contributions(R, w).sort_values("pct_contribution", ascending=False)
    var, es = risk.hist_var_es(pr)
    sect = rc.pct_contribution.groupby(c.sectors.reindex(rc.index)).sum().sort_values(ascending=False)
    return {
        "window": {"start": R.index[0], "end": R.index[-1], "days": len(R)},
        "annualised_vol": risk.annualised_vol(pr),
        "var95_1d": var, "es95_1d": es,
        "avg_pairwise_corr": risk.avg_pairwise_corr(R),
        **risk.drawdown_stats(pr),
        "risk_contributions": rc.reset_index(names="ticker").to_dict("records"),
        "sector_pct_contribution": sect.to_dict(),
        "weight_covered": covered, "dropped_holdings": dropped,
        **_ctx_meta(c),
    }


@evidence_tool("CALC")
def run_historical_stress(windows: Optional[List[str]] = None) -> dict:
    """Replays today's portfolio weights (buy-and-hold) through historical stress windows and compares with the
    benchmark. Holdings that did not exist then are excluded and coverage is reported.

    Args:
        windows: Subset of gfc_2008, q4_selloff_2018, covid_crash_2020, rate_shock_2022. Default: all.
    """
    c = get_context()
    chosen = {k: v for k, v in STRESS_WINDOWS.items() if not windows or k in windows}
    if not chosen:
        raise ValueError(f"Unknown windows. Available: {list(STRESS_WINDOWS)}")
    return {"windows": scenarios.historical_stress(c.prices, c.weights, chosen, c.benchmark), **_ctx_meta(c)}


@evidence_tool("CALC")
def run_rate_shock(rate_bp: float, equity_move_pct: float = 0.0) -> dict:
    """Hypothetical interest-rate shock via two-factor betas (equity benchmark + long-duration rate proxy).

    Args:
        rate_bp: Parallel yield change in basis points (e.g. 100 = yields up 1pp, which hurts long bonds).
        equity_move_pct: Optional simultaneous equity-market move in percent (e.g. -5). Default 0 = held flat.
    """
    c, s = get_context(), get_settings()
    return {**scenarios.factor_scenario(c.returns, c.weights, c.benchmark, c.rate_proxy, s.rate_proxy_duration, equity_move_pct, rate_bp), **_ctx_meta(c)}


@evidence_tool("CALC")
def run_market_decline(equity_move_pct: float, rate_move_bp: float = 0.0) -> dict:
    """Hypothetical equity-market decline via two-factor betas.

    Args:
        equity_move_pct: Market move in percent, e.g. -20 for a 20% decline.
        rate_move_bp: Optional simultaneous yield change in bp. Default 0.
    """
    c, s = get_context(), get_settings()
    return {**scenarios.factor_scenario(c.returns, c.weights, c.benchmark, c.rate_proxy, s.rate_proxy_duration, equity_move_pct, rate_move_bp), **_ctx_meta(c)}


@evidence_tool("CALC")
def run_sector_shock(sector: str, loss_pct: float, spillover: bool = True) -> dict:
    """Hypothetical sector-specific loss, with optional beta-based spillover to the other holdings.

    Args:
        sector: Sector name exactly as in the portfolio overview (e.g. "Technology").
        loss_pct: Loss in percent applied to holdings in that sector (e.g. 15).
        spillover: Whether other holdings are hit via their beta to the sector basket.
    """
    c = get_context()
    return {**scenarios.sector_shock(c.returns, c.weights, c.sectors, sector, loss_pct, spillover), **_ctx_meta(c)}


# --------------------------------------------------------------------------- news tools
@evidence_tool("NEWS")
def search_news(query: str, days: int = 14, max_items: int = 8) -> dict:
    """Searches recent news. Only items with a verified publication date inside the window are returned.
    Each item has an `index`; cite items as [NEWS-id:index]. `link_type` is `direct_mention` when the headline
    names a holding (a fact about the headline) and `unmapped_requires_hypothesis` otherwise.

    Args:
        query: Search terms, e.g. "NVIDIA export restrictions".
        days: Look-back window in days (default 14).
        max_items: Maximum items to return (default 8).
    """
    c = get_context()
    return fetch_news(query, days, max_items, c.holdings)


@evidence_tool("NEWS")
def search_holding_news(ticker: str, days: int = 14, max_items: int = 6) -> dict:
    """Searches recent news for one portfolio holding by ticker and company name.

    Args:
        ticker: A ticker from the portfolio, e.g. "NVDA".
        days: Look-back window in days (default 14).
        max_items: Maximum items to return (default 6).
    """
    c = get_context()
    h = next((h for h in c.holdings if h["ticker"].upper() == ticker.upper()), None)
    if h is None:
        raise ValueError(f"{ticker} is not in the portfolio")
    return fetch_news(f'"{h["name"]}" OR {h["ticker"]} stock', days, max_items, c.holdings)


# --------------------------------------------------------------------------- audit tools (not recorded)
def list_evidence() -> dict:
    """Lists every evidence item recorded so far in this investigation (id, kind, tool, arguments)."""
    return {"status": "ok", "evidence": LEDGER.index()}


def get_evidence(evidence_id: str) -> dict:
    """Returns the full stored result of one evidence item, e.g. "CALC-002".

    Args:
        evidence_id: The evidence id.
    """
    ev = LEDGER.get(evidence_id)
    if ev is None:
        return {"status": "error", "error": f"unknown evidence id {evidence_id}"}
    return {"status": "ok", "id": ev.id, "kind": ev.kind, "tool": ev.tool, "args": ev.args, "result": ev.result}


def _diff(a, b, path="", out=None, tol=1e-9):
    out = [] if out is None else out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in set(a) | set(b):
            _diff(a.get(k), b.get(k), f"{path}.{k}", out, tol)
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            _diff(x, y, f"{path}[{i}]", out, tol)
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        if abs(a - b) > tol:
            out.append({"path": path, "original": a, "recomputed": b})
    elif a != b:
        out.append({"path": path, "original": a, "recomputed": b})
    return out


def recompute_evidence(evidence_id: str) -> dict:
    """Re-runs the calculation behind a CALC evidence item with its original arguments and checks that the
    result is identical (reproducibility check). NEWS evidence is a live snapshot and cannot be recomputed.

    Args:
        evidence_id: A CALC evidence id, e.g. "CALC-002".
    """
    ev = LEDGER.get(evidence_id)
    if ev is None:
        return {"status": "error", "error": f"unknown evidence id {evidence_id}"}
    if ev.kind != "CALC":
        return {"status": "skipped", "reason": "NEWS evidence is a live snapshot and not reproducible"}
    fresh = to_jsonable(REGISTRY[ev.tool](**ev.args))
    diffs = _diff(ev.result, fresh)
    return {"status": "ok", "evidence_id": evidence_id, "reproduced": not diffs, "differences": diffs[:10]}


def verify_draft_claims(draft_markdown: str) -> dict:
    """Runs the deterministic claim verifier on a draft report: invalid citations, uncited numbers, numbers not
    found in the cited evidence, news claims without NEWS citations, missing limitations section.

    Args:
        draft_markdown: The full draft report text.
    """
    return {"status": "ok", **verify_report(draft_markdown, LEDGER)}


QUANT_TOOLS = [get_portfolio_overview, analyze_risk_drift, compute_risk_metrics, run_historical_stress,
               run_rate_shock, run_market_decline, run_sector_shock]
NEWS_TOOLS = [search_news, search_holding_news, get_portfolio_overview, list_evidence, get_evidence]
CHALLENGER_TOOLS = [list_evidence, get_evidence, recompute_evidence, verify_draft_claims, analyze_risk_drift, compute_risk_metrics]
REPORTER_TOOLS = [list_evidence, get_evidence, verify_draft_claims]
