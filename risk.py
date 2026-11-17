"""Pure, deterministic risk math. No I/O, no LLM. Everything here is unit-tested and eval'd."""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def prep_window(returns: pd.DataFrame, weights: pd.Series, n: int | None = None):
    """Slice the last n rows, drop holdings with gaps in the window, renormalise weights.

    Returns (R, w, weight_covered, dropped_tickers)."""
    R = returns.reindex(columns=weights.index)
    R = R.tail(n) if n else R
    ok = R.columns[R.notna().all()]
    dropped = [c for c in R.columns if c not in ok]
    w = weights[ok]
    covered = float(w.sum() / weights.sum())
    return R[ok], w / w.sum(), covered, dropped


def portfolio_returns(R: pd.DataFrame, w: pd.Series) -> pd.Series:
    """Daily-rebalanced (constant-weight) portfolio return series."""
    return R.mul(w.reindex(R.columns), axis=1).sum(axis=1)


def annualised_vol(r: pd.Series) -> float:
    return float(r.std() * np.sqrt(TRADING_DAYS))


def risk_contributions(R: pd.DataFrame, w: pd.Series) -> pd.DataFrame:
    """Euler decomposition: contribution_i = w_i * d(sigma)/d(w_i); sums exactly to portfolio vol."""
    cov = R.cov().values * TRADING_DAYS
    wv = w.reindex(R.columns).values
    sigma = float(np.sqrt(wv @ cov @ wv))
    rc = wv * (cov @ wv) / sigma
    return pd.DataFrame(
        {
            "weight": wv,
            "standalone_vol": np.sqrt(np.diag(cov)),
            "contribution_vol": rc,
            "pct_contribution": rc / sigma,
        },
        index=R.columns,
    )


def avg_pairwise_corr(R: pd.DataFrame) -> float:
    c = R.corr().values
    iu = np.triu_indices_from(c, k=1)
    return float(np.nanmean(c[iu]))


def hhi(w: pd.Series) -> float:
    return float((w ** 2).sum())


def hist_var_es(r: pd.Series, level: float = 0.95) -> tuple[float, float]:
    """1-day historical VaR / expected shortfall, as positive loss fractions."""
    q = np.percentile(r, (1 - level) * 100)
    return float(-q), float(-r[r <= q].mean())


def drawdown_stats(r: pd.Series) -> dict:
    cum = (1 + r).cumprod()
    dd = cum / cum.cummax() - 1
    trough = dd.idxmin()
    peak = cum.loc[:trough].idxmax()
    return {
        "max_drawdown": float(dd.min()),
        "peak_date": peak.strftime("%Y-%m-%d"),
        "trough_date": trough.strftime("%Y-%m-%d"),
        "current_drawdown": float(dd.iloc[-1]),
    }


def _vol_with(sd: np.ndarray, corr: np.ndarray, wv: np.ndarray) -> float:
    cov = np.outer(sd, sd) * corr
    return float(np.sqrt(wv @ cov @ wv))


def drift_analysis(returns: pd.DataFrame, weights: pd.Series, recent_days: int = 63, baseline_days: int = 252) -> dict:
    """Compare a recent window with the baseline window immediately before it.

    Exact decompositions:
      * vol_change = sum_i delta_contribution_i
      * vol_change = vol_effect + correlation_effect  (counterfactual: recent vols with baseline correlations)
    """
    need = recent_days + baseline_days
    if len(returns) < need:
        raise ValueError(f"Need {need} observations, have {len(returns)}")
    R, w, covered, dropped = prep_window(returns, weights, need)
    rec, base = R.iloc[-recent_days:], R.iloc[:-recent_days]
    rc_r, rc_b = risk_contributions(rec, w), risk_contributions(base, w)
    vol_r, vol_b = float(rc_r.contribution_vol.sum()), float(rc_b.contribution_vol.sum())
    wv = w.values
    vol_a = _vol_with(rec.std().values * np.sqrt(TRADING_DAYS), base.corr().values, wv)
    vol_effect, corr_effect = vol_a - vol_b, vol_r - vol_a
    tbl = pd.DataFrame(
        {
            "weight": w,
            "vol_recent": rc_r.standalone_vol,
            "vol_baseline": rc_b.standalone_vol,
            "contribution_recent": rc_r.contribution_vol,
            "contribution_baseline": rc_b.contribution_vol,
            "delta_contribution": rc_r.contribution_vol - rc_b.contribution_vol,
            "pct_contribution_recent": rc_r.pct_contribution,
            "pct_contribution_baseline": rc_b.pct_contribution,
        }
    ).sort_values("delta_contribution", ascending=False)
    pr = portfolio_returns(R, w)
    roll = pr.rolling(21).std().dropna() * np.sqrt(TRADING_DAYS)
    monthly = roll.iloc[::-21][:6][::-1]
    var_r, es_r = hist_var_es(portfolio_returns(rec, w))
    var_b, es_b = hist_var_es(portfolio_returns(base, w))
    c_r, c_b = avg_pairwise_corr(rec), avg_pairwise_corr(base)
    win = lambda d: {"start": d.index[0].strftime("%Y-%m-%d"), "end": d.index[-1].strftime("%Y-%m-%d"), "days": len(d)}
    return {
        "recent_window": win(rec),
        "baseline_window": win(base),
        "portfolio_vol_recent": vol_r,
        "portfolio_vol_baseline": vol_b,
        "vol_change": vol_r - vol_b,
        "vol_change_pct": (vol_r / vol_b - 1),
        "var95_1d_recent": var_r, "var95_1d_baseline": var_b,
        "es95_1d_recent": es_r, "es95_1d_baseline": es_b,
        "avg_pairwise_corr_recent": c_r,
        "avg_pairwise_corr_baseline": c_b,
        "corr_change": c_r - c_b,
        "vol_effect": vol_effect,
        "correlation_effect": corr_effect,
        "primary_driver_of_vol_change": "volatility" if abs(vol_effect) >= abs(corr_effect) else "correlation",
        "holdings": tbl.reset_index(names="ticker").to_dict("records"),
        "top_drivers_of_increase": [t for t in tbl.index[:3] if tbl.loc[t, "delta_contribution"] > 0],
        "rolling_vol_21d_monthly": [{"date": d.strftime("%Y-%m-%d"), "vol": float(v)} for d, v in monthly.items()],
        "concentration_hhi": hhi(w),
        "weight_covered": covered,
        "dropped_holdings": dropped,
    }
