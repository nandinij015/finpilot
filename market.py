"""Price loading: yfinance (cached daily), CSV, or synthetic. Always returns (prices, data_source)."""
from __future__ import annotations

import hashlib
from datetime import date

import pandas as pd

from .config import get_settings
from .synthetic import synthetic_prices


def load_prices(tickers: list[str]) -> tuple[pd.DataFrame, str]:
    s = get_settings()
    tickers = sorted(set(tickers))
    if s.data_source == "synthetic":
        return synthetic_prices(tickers), "synthetic"
    if s.data_source == "csv":
        if not s.prices_csv:
            raise ValueError("FINPILOT_DATA_SOURCE=csv requires FINPILOT_PRICES_CSV")
        df = pd.read_csv(s.prices_csv, index_col=0, parse_dates=True).sort_index()
        missing = [t for t in tickers if t not in df.columns]
        if missing:
            raise ValueError(f"CSV is missing tickers: {missing}")
        return df[tickers], "csv"
    if s.data_source == "yfinance":
        return _yfinance(tickers, s.start_date, s.cache_dir), "yfinance"
    raise ValueError(f"Unknown FINPILOT_DATA_SOURCE={s.data_source!r}")


def _yfinance(tickers: list[str], start: str, cache_dir) -> pd.DataFrame:
    import yfinance as yf

    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.md5(f"{','.join(tickers)}|{start}|{date.today()}".encode()).hexdigest()[:12]
    cache = cache_dir / f"prices_{key}.csv"
    if cache.exists():
        return pd.read_csv(cache, index_col=0, parse_dates=True)
    raw = yf.download(tickers, start=start, auto_adjust=True, progress=False, threads=True)
    close = raw["Close"]
    if isinstance(close, pd.Series):
        close = close.to_frame(tickers[0])
    close = close.dropna(axis=1, how="all").sort_index()
    missing = [t for t in tickers if t not in close.columns]
    if missing:
        raise ValueError(f"No price data returned for: {missing}")
    close.to_csv(cache)
    return close
