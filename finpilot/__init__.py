"""Compatibility package that exposes the repo-root modules as finpilot.*."""

from pathlib import Path

__path__ = [str(Path(__file__).resolve().parents[1])]
