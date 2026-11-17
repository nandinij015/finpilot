"""Deterministic claim verifier. Runs OUTSIDE the LLMs, so agents cannot talk their way past it.

Checks: (1) every cited ID exists, (2) numeric claims are cited, (3) cited numbers actually appear in the cited
evidence (args or results), (4) cited news items have verified dates, (5) news-style assertions carry a NEWS
citation, (6) a limitations section exists, (7) synthetic data is disclosed.
"""
from __future__ import annotations

import re
from typing import Any

CITE_RE = re.compile(r"\[((?:CALC|NEWS)-\d{3})(?::(\d+))?\]")
DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
NUM_RE = re.compile(r"(?<![\w.])([-+\u2212]?)(\d[\d,]*(?:\.\d+)?)\s?(%|bps|bp)?")
SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\[\*])")
NEWSY_RE = re.compile(r"\b(announced|reported|according to|said|filed|reuters|bloomberg|cnbc|wsj)\b", re.I)


def _flatten(obj: Any, out: list[float]) -> None:
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        out.append(float(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            _flatten(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _flatten(v, out)


def _units(text: str):
    for raw in text.splitlines():
        line = raw.strip().replace("**", "").replace("`", "")
        if not line or line.startswith("#") or set(line) <= set("-|: "):
            continue
        line = re.sub(r"^([-*+]|\d+\.)\s+", "", line)
        if line.startswith("|"):
            yield line
            continue
        for s in SPLIT_RE.split(line):
            if s.strip():
                yield s.strip()


def _tokens(body: str):
    for m in NUM_RE.finditer(body):
        sign, num, unit = m.groups()
        dec = len(num.split(".")[1]) if "." in num else 0
        v = float(num.replace(",", ""))
        trivial = unit is None and dec == 0 and (v <= 12 or 1900 <= v <= 2100)
        yield {"text": m.group(0).strip(), "value": v, "decimals": dec, "unit": unit, "trivial": trivial}


def _supported(tok: dict, pool: list[float]) -> bool:
    tol = 0.5 * 10 ** (-tok["decimals"]) + 1e-9
    v = tok["value"]
    if tok["unit"] == "%":
        cands = [abs(x) * 100 for x in pool] + [abs(x) for x in pool]
    elif tok["unit"] in ("bp", "bps"):
        cands = [abs(x) * 10_000 for x in pool] + [abs(x) for x in pool]
    else:
        cands = [abs(x) for x in pool] + [abs(x) * 100 for x in pool]
    return any(abs(v - c) <= tol for c in cands)


def verify_report(text: str, ledger) -> dict:
    invalid, unsupported, mismatches = [], [], []
    n_units = n_cited = 0
    for unit in _units(text):
        n_units += 1
        cites = CITE_RE.findall(unit)
        valid_ids = []
        for eid, idx in cites:
            ev = ledger.get(eid)
            if ev is None:
                invalid.append({"citation": eid, "reason": "evidence id does not exist", "sentence": unit[:160]})
                continue
            if idx:
                items = ev.result.get("items") if ev.kind == "NEWS" else None
                if not items or not (1 <= int(idx) <= len(items)):
                    invalid.append({"citation": f"{eid}:{idx}", "reason": "item index does not exist", "sentence": unit[:160]})
                    continue
                if not items[int(idx) - 1].get("date_verified"):
                    invalid.append({"citation": f"{eid}:{idx}", "reason": "cited news item has no verified date", "sentence": unit[:160]})
                    continue
            valid_ids.append(eid)
        if cites:
            n_cited += 1
        body = DATE_RE.sub(" ", CITE_RE.sub(" ", unit))
        claims = [t for t in _tokens(body) if not t["trivial"]]
        if claims and not cites:
            unsupported.append({"sentence": unit[:200], "reason": "numeric claim without citation"})
        elif NEWSY_RE.search(body) and not any(c[0].startswith("NEWS") for c in cites):
            unsupported.append({"sentence": unit[:200], "reason": "news-style assertion without NEWS citation"})
        if claims and valid_ids:
            pool: list[float] = []
            for eid in valid_ids:
                ev = ledger.get(eid)
                _flatten(ev.result, pool)
                _flatten(ev.args, pool)
            for t in claims:
                if not _supported(t, pool):
                    mismatches.append({"number": t["text"], "sentence": unit[:200], "reason": "number not found in cited evidence"})
    lower = text.lower()
    problems = []
    if "limitation" not in lower:
        problems.append("missing_limitations_section")
    if any(e.data_source == "synthetic" for e in ledger.all()) and "synthetic" not in lower:
        problems.append("synthetic_data_not_disclosed")
    passed = not (invalid or unsupported or mismatches or problems)
    return {
        "passed": passed,
        "invalid_citations": invalid,
        "unsupported_claims": unsupported,
        "numeric_mismatches": mismatches,
        "structural_problems": problems,
        "stats": {"sentences_checked": n_units, "sentences_with_citations": n_cited,
                  "evidence_items_available": len(ledger.all())},
    }
