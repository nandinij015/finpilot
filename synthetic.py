"""Deterministic synthetic market data for offline demos, tests and evals.

A regime shift is *planted* in the last RECENT_REGIME_DAYS sessions (market vol up, NVDA idiosyncratic
vol and beta up) so evals can check that the system identifies the known driver. Never use for real decisions.
"""
from __future__ import annotations

import zlib

import numpy as np
import pandas as pd

from .config import STRESS_WINDOWS

SYNTH_START = "2007-01-03"
SYNTH_END = "2026-11-13"
RECENT_REGIME_DAYS = 63
PLANTED = {"top_risk_driver": "NVDA", "regime_days": RECENT_REGIME_DAYS}

# ticker: (beta_market, beta_rate, idiosyncratic daily vol)
PARAMS = {
    "SPY": (1.0, 0.0, 0.002), "TLT": (-0.1, 1.0, 0.002),
    "AAPL": (1.1, -0.1, 0.010), "MSFT": (1.0, -0.1, 0.009), "NVDA": (1.8, -0.2, 0.016),
    "AMZN": (1.2, -0.1, 0.012), "TSLA": (1.9, -0.2, 0.022), "JPM": (1.1, 0.1, 0.009),
    "XOM": (0.8, 0.0, 0.011), "JNJ": (0.5, 0.2, 0.007), "GLD": (0.0, 0.3, 0.007),
}
# (equity factor total move, rate factor total move) injected over each stress window
STRESS_SHOCKS = {
    "gfc_2008": (-0.50, 0.10), "q4_selloff_2018": (-0.19, 0.02),
    "covid_crash_2020": (-0.30, 0.05), "rate_shock_2022": (-0.22, -0.30),
}


def synthetic_prices(tickers: list[str], seed: int = 7) -> pd.DataFrame:
    idx = pd.bdate_range(SYNTH_START, SYNTH_END)
    n = len(idx)
    rng = np.random.default_rng(seed)
    mkt = rng.normal(0.0004, 0.010, n)
    rate = rng.normal(0.0, 0.006, n)
    recent = np.arange(n) >= n - RECENT_REGIME_DAYS
    mkt = np.where(recent, mkt * 1.8, mkt)
    for name, (m_tot, r_tot) in STRESS_SHOCKS.items():
        s, e = STRESS_WINDOWS[name]
        mask = (idx >= s) & (idx <= e)
        k = int(mask.sum())
        if k:
            mkt[mask] += m_tot / k
            rate[mask] += r_tot / k
    out = {}
    for t in sorted(set(tickers)):
        bm, br, iv = PARAMS.get(t, (1.0, 0.0, 0.015))
        trng = np.random.default_rng(seed + zlib.crc32(t.encode()) % 100000)
        eps = trng.normal(0, 1, n)
        bm_v, iv_v = np.full(n, bm), np.full(n, iv)
        if t == "NVDA":
            bm_v, iv_v = np.where(recent, bm * 1.3, bm), np.where(recent, iv * 3, iv)
        if t == "TSLA":
            iv_v = np.where(recent, iv * 1.5, iv)
        r = 0.0002 + bm_v * mkt + br * rate + iv_v * eps
        out[t] = 100 * np.cumprod(1 + r)
    df = pd.DataFrame(out, index=idx)
    if "TSLA" in df:  # emulate a late listing so stress-window coverage logic is exercised
        df.loc[df.index < "2010-06-29", "TSLA"] = np.nan
    return df
