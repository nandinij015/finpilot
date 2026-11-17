"""Compatibility wrapper so README command works: python -m evals.run_evals"""

from __future__ import annotations

import runpy
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

if __name__ == "__main__":
    runpy.run_path(str(_ROOT / "run_evals.py"), run_name="__main__")
