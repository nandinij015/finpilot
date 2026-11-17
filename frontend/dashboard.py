"""Dashboard data: deterministic numbers only (no LLM). Everything shown on the Overview comes from here."""
import math
from datetime import datetime

import pandas as pd

from finpilot.context import get_context
from finpilot.evidence import to_jsonable
from finpilot.quant import risk


def _market_status() -> dict:
    try:
        from zoneinfo import ZoneInfo

        n = datetime.now(ZoneInfo("America/New_York"))
    except Exception:  # Windows without tzdata
        return {"status": "UNKNOWN", "note": "time-zone database unavailable (pip install tzdata)"}
    is_open = n.weekday() < 5 and (9, 30) <= (n.hour, n.minute) < (16, 0)
    return {
        "status": "OPEN" if is_open else "CLOSED",
        "as_of_et": n.strftime("%Y-%m-%d %H:%M ET"),
        "note": "Estimated from US cash hours; holidays not modelled. Prices are end-of-day.",
    }


def _strip(c) -> list[dict]:
    names = {h["ticker"]: h["name"] for h in c.holdings}
    order: list[str] = []
    for t in [c.benchmark, c.rate_proxy, *c.weights.sort_values(ascending=False).index[:4]]:
        if t not in order and t in c.prices.columns:
            order.append(t)
    out = []
    for t in order:
        s = c.prices[t].dropna()
        if len(s) >= 2:
            out.append({
                "ticker": t,
                "name": names.get(t, "Benchmark" if t == c.benchmark else "Rate proxy"),
                "price": float(s.iloc[-1]),
                "change_pct": float(s.iloc[-1] / s.iloc[-2] - 1),
                "as_of": s.index[-1],
            })
    return out


def _thin(n: int, max_points: int) -> list[int]:
    step = max(1, math.ceil(n / max_points))
    idx = list(range(0, n, step))
    if idx[-1] != n - 1:
        idx.append(n - 1)
    return idx


def build_dashboard(start=None, end=None, tickers=None, max_points: int = 400) -> dict:
    c = get_context()
    universe = [{"ticker": h["ticker"], "name": h["name"], "sector": h["sector"], "weight": h["weight"]} for h in c.holdings]
    sel = [t for t in (tickers or list(c.weights.index)) if t in c.weights.index]
    if len(sel) < 2:
        raise ValueError("Select at least two assets.")
    end_ts = pd.Timestamp(end) if end else c.returns.index[-1]
    start_ts = pd.Timestamp(start) if start else end_ts - pd.DateOffset(years=1)
    if start_ts >= end_ts:
        raise ValueError("Start date must be before end date.")
    window = c.returns.loc[start_ts:end_ts]
    if len(window) < 30:
        raise ValueError(f"Only {len(window)} trading days in range; need at least 30.")
    R, w, covered, dropped = risk.prep_window(window, c.weights[sel], None)
    if R.shape[1] < 2:
        raise ValueError("Fewer than two selected assets have data for the full range; shorten the range.")
    pr = risk.portfolio_returns(R, w)
    rc = risk.risk_contributions(R, w).sort_values("pct_contribution", ascending=False)
    dd = risk.drawdown_stats(pr)
    level = 100 * (1 + pr).cumprod()
    bench = 100 * (1 + c.returns[c.benchmark].reindex(R.index).fillna(0)).cumprod()
    i = _thin(len(R), max_points)
    held = [h for h in c.holdings if h["ticker"] in R.columns]
    amount = None
    if all("quantity" in h for h in held):
        last = c.prices.ffill().iloc[-1]
        amount = float(sum(h["quantity"] * last[h["ticker"]] for h in held))
    sectors = w.groupby(c.sectors.reindex(w.index)).sum().sort_values(ascending=False)
    corr = R.corr()
    names = {u["ticker"]: u for u in universe}
    as_of = c.prices.index[-1]
    out = {
        "data_source": c.data_source,
        "is_sample": c.data_source == "synthetic",
        "portfolio_name": c.portfolio_name,
        "as_of": as_of,
        "generated_utc": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "bounds": {"min": c.returns.index[0], "max": c.returns.index[-1]},
        "period": {"start": R.index[0], "end": R.index[-1], "days": len(R)},
        "universe": universe,
        "selected": list(R.columns),
        "dropped_holdings": dropped,
        "weight_covered": covered,
        "market": _market_status(),
        "market_strip": _strip(c),
        "cards": {
            "portfolio_value": {"amount": amount, "index_level": float(level.iloc[-1]), "index_base": 100,
                                "basis": "Constant-weight index rebased to 100 at range start" if amount is None
                                else "Quantity x last close"},
            "volatility": {"value": risk.annualised_vol(pr), "basis": "Std. dev. of daily returns x sqrt(252)"},
            "max_drawdown": {"value": dd["max_drawdown"], "peak_date": dd["peak_date"], "trough_date": dd["trough_date"],
                             "current": dd["current_drawdown"]},
            "top_contributor": {"ticker": rc.index[0], "name": names[rc.index[0]]["name"],
                                "pct_of_risk": float(rc.pct_contribution.iloc[0]),
                                "weight": float(rc.weight.iloc[0])},
        },
        "performance": {
            "dates": [R.index[k] for k in i],
            "portfolio": [float(level.iloc[k]) for k in i],
            "benchmark": [float(bench.iloc[k]) for k in i],
            "benchmark_name": c.benchmark,
        },
        "allocation": {
            "holdings": [{"ticker": t, "sector": names[t]["sector"], "weight": float(w[t])} for t in w.sort_values(ascending=False).index],
            "sectors": [{"sector": s, "weight": float(v)} for s, v in sectors.items()],
        },
        "correlation": {"tickers": list(corr.columns), "matrix": corr.round(2).values.tolist()},
        "risk_table": [
            {"ticker": t, "name": names[t]["name"], "sector": names[t]["sector"], "weight": float(r.weight),
             "standalone_vol": float(r.standalone_vol), "contribution_vol": float(r.contribution_vol),
             "pct_contribution": float(r.pct_contribution)}
            for t, r in rc.iterrows()
        ],
    }
    return to_jsonable(out)
