"""Loads portfolio + prices once and exposes them to tools."""
from __future__ import annotations

import json
from dataclasses import dataclass

import pandas as pd

from .config import get_settings
from .data.market import load_prices


@dataclass
class Context:
    portfolio_name: str
    holdings: list[dict]
    weights: pd.Series
    sectors: pd.Series
    prices: pd.DataFrame
    returns: pd.DataFrame
    data_source: str
    benchmark: str
    rate_proxy: str


_CTX: Context | None = None


def reset_context() -> None:
    global _CTX
    _CTX = None


def get_context() -> Context:
    global _CTX
    if _CTX is None:
        _CTX = _build()
    return _CTX


def _build() -> Context:
    s = get_settings()
    spec = json.loads(s.portfolio_path.read_text())
    holdings = spec["holdings"]
    tickers = [h["ticker"] for h in holdings]
    prices, source = load_prices(tickers + [s.benchmark, s.rate_proxy])
    if all("weight" in h for h in holdings):
        w = pd.Series({h["ticker"]: float(h["weight"]) for h in holdings})
    else:  # quantity x last price
        last = prices.ffill().iloc[-1]
        w = pd.Series({h["ticker"]: float(h["quantity"]) * float(last[h["ticker"]]) for h in holdings})
    w = w / w.sum()
    sectors = pd.Series({h["ticker"]: h.get("sector", "Unknown") for h in holdings})
    for h in holdings:
        h["weight"] = float(w[h["ticker"]])
    returns = prices.pct_change(fill_method=None).iloc[1:]
    return Context(spec.get("name", "portfolio"), holdings, w, sectors, prices, returns, source, s.benchmark, s.rate_proxy)
