"""Central configuration. Settings are read from the environment on every call (test-friendly)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv is optional
    pass

ROOT = Path(__file__).resolve().parent

# Named historical stress windows (inclusive, market peak -> trough style).
STRESS_WINDOWS = {
    "gfc_2008": ("2007-10-09", "2009-03-09"),
    "q4_selloff_2018": ("2018-09-20", "2018-12-24"),
    "covid_crash_2020": ("2020-02-19", "2020-03-23"),
    "rate_shock_2022": ("2022-01-03", "2022-10-12"),
}


@dataclass(frozen=True)
class Settings:
    model: str
    data_source: str  # yfinance | csv | synthetic
    portfolio_path: Path
    prices_csv: Optional[Path]
    start_date: str
    cache_dir: Path
    output_dir: Path
    benchmark: str
    rate_proxy: str
    rate_proxy_duration: float


def get_settings() -> Settings:
    e = os.environ.get
    csv = e("FINPILOT_PRICES_CSV")
    default_portfolio = ROOT / "data" / "sample_portfolio.json"
    if not default_portfolio.exists():
        default_portfolio = ROOT / "sample_portfolio.json"
    return Settings(
        model=e("FINPILOT_MODEL", "gemini-2.5-flash"),
        data_source=e("FINPILOT_DATA_SOURCE", "yfinance").lower(),
        portfolio_path=Path(e("FINPILOT_PORTFOLIO", str(default_portfolio))),
        prices_csv=Path(csv) if csv else None,
        start_date=e("FINPILOT_START", "2007-01-01"),
        cache_dir=Path(e("FINPILOT_CACHE", str(ROOT / ".cache"))),
        output_dir=Path(e("FINPILOT_OUTPUT", str(ROOT / "outputs"))),
        benchmark=e("FINPILOT_BENCHMARK", "SPY"),
        rate_proxy=e("FINPILOT_RATE_PROXY", "TLT"),
        # Approximate effective duration of the rate proxy (TLT ~ 17y). Used for bp -> return mapping.
        rate_proxy_duration=float(e("FINPILOT_RATE_DURATION", "17.0")),
    )
