"""Deterministic scenario engine: historical replay + linear factor / sector shocks.

Assumptions are explicit and returned with every result. Factor betas come from OLS on trailing daily
returns; large shocks are therefore a *linear approximation* (no convexity, no regime change).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def estimate_betas(returns: pd.DataFrame, factors: list[str], window: int = 504) -> pd.DataFrame:
    R = returns.tail(window)
    X = R[factors].dropna()
    Xm = np.column_stack([np.ones(len(X)), X.values])
    rows = {}
    for col in R.columns:
        y = R[col].reindex(X.index)
        ok = y.notna().values
        if ok.sum() < 60:
            continue
        coef, *_ = np.linalg.lstsq(Xm[ok], y.values[ok], rcond=None)
        resid = y.values[ok] - Xm[ok] @ coef
        row = {f"beta_{f}": float(coef[i + 1]) for i, f in enumerate(factors)}
        row["r2"] = float(1 - resid.var() / y.values[ok].var())
        rows[col] = row
    return pd.DataFrame(rows).T


def _result(name: str, shocks: dict, impacts: pd.Series, w: pd.Series, assumptions: list[str]) -> dict:
    contrib = (w * impacts).sort_values()
    tbl = pd.DataFrame({"weight": w, "asset_impact": impacts, "portfolio_contribution": w * impacts}).loc[contrib.index]
    return {
        "scenario": name,
        "inputs": shocks,
        "portfolio_impact": float(contrib.sum()),
        "worst_contributors": tbl.head(3).reset_index(names="ticker").to_dict("records"),
        "holdings": tbl.reset_index(names="ticker").to_dict("records"),
        "assumptions": assumptions,
    }


def factor_scenario(returns, weights, benchmark, rate_proxy, duration, equity_pct, rate_bp, window=504) -> dict:
    """Shock the equity factor (benchmark return) and/or the rate factor (rate-proxy return = -duration * dy)."""
    rate_ret = -duration * rate_bp / 10_000.0
    shocks = {benchmark: equity_pct / 100.0, rate_proxy: rate_ret}
    betas = estimate_betas(returns, [benchmark, rate_proxy], window)
    w = weights[weights.index.isin(betas.index)]
    w = w / w.sum()
    b = betas.loc[w.index]
    impacts = b[f"beta_{benchmark}"] * shocks[benchmark] + b[f"beta_{rate_proxy}"] * shocks[rate_proxy]
    out = _result(
        "factor_shock",
        {"equity_move_pct": equity_pct, "rate_move_bp": rate_bp, "implied_rate_proxy_return": rate_ret},
        impacts, w,
        [
            f"Two-factor OLS betas on {benchmark} and {rate_proxy} daily returns over the trailing {window} sessions.",
            f"{rate_proxy} return = -duration({duration}) x yield change; no convexity.",
            "Linear approximation: large moves, gaps, liquidity and correlation breakdowns are not modelled.",
            "Betas are partial: the non-shocked factor is held flat.",
        ],
    )
    out["mean_r2"] = float(b["r2"].mean())
    return out


def sector_shock(returns, weights, sectors, sector, loss_pct, spillover=True, window=504) -> dict:
    members = [t for t in weights.index if str(sectors.get(t, "")).lower() == sector.lower()]
    if not members:
        raise ValueError(f"Unknown sector {sector!r}. Available: {sorted(set(sectors))}")
    shock = -abs(loss_pct) / 100.0
    wm = weights[members] / weights[members].sum()
    R = returns.tail(window)
    basket = R[members].mul(wm, axis=1).sum(axis=1)
    impacts = pd.Series(0.0, index=weights.index)
    impacts[members] = shock
    betas = {}
    if spillover:
        for t in weights.index.difference(members):
            pair = pd.concat([R[t], basket], axis=1).dropna()
            if len(pair) >= 60:
                betas[t] = float(pair.cov().iloc[0, 1] / pair.iloc[:, 1].var())
                impacts[t] = betas[t] * shock
    out = _result(
        "sector_shock", {"sector": sector, "loss_pct": abs(loss_pct), "spillover": spillover}, impacts, weights,
        [
            "Directly hit holdings lose the full shock.",
            "Spillover = OLS beta of each other holding to the sector basket x shock (negative betas gain)." if spillover
            else "No spillover to other sectors.",
            "Linear approximation; no second-round or liquidity effects.",
        ],
    )
    out["directly_exposed_weight"] = float(weights[members].sum())
    out["spillover_betas"] = betas
    return out


def historical_stress(prices, weights, windows: dict, benchmark: str) -> list[dict]:
    """Buy-and-hold replay of today's weights through past stress windows. Holdings without data are excluded
    and the remainder re-normalised; coverage is reported."""
    out = []
    for name, (s, e) in windows.items():
        seg = prices.loc[s:e]
        if len(seg) < 10 or (seg.index[0] - pd.Timestamp(s)).days > 7:
            out.append({"window": name, "start": s, "end": e, "status": "no_data"})
            continue
        cols = [c for c in weights.index if c in seg and pd.notna(seg[c].iloc[0]) and pd.notna(seg[c].iloc[-1])]
        covered = float(weights[cols].sum() / weights.sum()) if cols else 0.0
        if covered < 0.5:
            out.append({"window": name, "start": s, "end": e, "status": "insufficient_coverage", "weight_covered": covered})
            continue
        w = weights[cols] / weights[cols].sum()
        asset_ret = seg[cols].iloc[-1] / seg[cols].iloc[0] - 1
        contrib = (w * asset_ret).sort_values()
        path = (seg[cols] / seg[cols].iloc[0]).mul(w).sum(axis=1)
        bench = float(seg[benchmark].iloc[-1] / seg[benchmark].iloc[0] - 1) if benchmark in seg else None
        out.append({
            "window": name, "start": s, "end": e, "status": "ok", "trading_days": len(seg),
            "portfolio_return": float(contrib.sum()),
            "portfolio_max_drawdown_in_window": float((path / path.cummax() - 1).min()),
            "benchmark_return": bench,
            "weight_covered": covered,
            "excluded_holdings": [c for c in weights.index if c not in cols],
            "worst_contributors": [{"ticker": t, "contribution": float(v), "asset_return": float(asset_ret[t])} for t, v in contrib.head(3).items()],
        })
    return out
