"""Compatibility package for the flat project layout."""

from ..market import load_prices
from ..synthetic import PLANTED, RECENT_REGIME_DAYS, STRESS_SHOCKS, SYNTH_END, SYNTH_START, synthetic_prices

__all__ = [
    "load_prices",
    "PLANTED",
    "RECENT_REGIME_DAYS",
    "STRESS_SHOCKS",
    "SYNTH_END",
    "SYNTH_START",
    "synthetic_prices",
]
