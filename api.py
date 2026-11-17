"""FastAPI application serving both the API and the dashboard frontend on one port."""
import asyncio
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from finpilot.config import ROOT, get_settings
from finpilot.evidence import LEDGER, to_jsonable
from frontend.dashboard import build_dashboard

app = FastAPI(title="FinPilot", version="1.1")
_lock = asyncio.Lock()
_TASKS: set = set()
JOBS: dict[str, dict] = {}
_RUNS: dict[str, dict] = {}
RUN_ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")
QUANT_ONLY = "Quant analysis"


class Ask(BaseModel):
    question: str


class Scenario(BaseModel):
    kind: str
    equity_move_pct: float = -10.0
    rate_bp: float = 0.0
    sector: Optional[str] = None
    loss_pct: float = 15.0
    spillover: bool = True


@app.get("/healthz")
def healthz():
    return {"ok": True}


# ----------------------------------------------------------------------------- dashboard + scenarios
@app.get("/api/dashboard")
def dashboard(start: Optional[str] = None, end: Optional[str] = None, tickers: Optional[str] = Query(None)):
    try:
        return build_dashboard(start, end, tickers.split(",") if tickers else None)
    except Exception as e:
        raise HTTPException(400, f"{type(e).__name__}: {e}")


@app.post("/api/scenario")
def scenario(b: Scenario):
    from finpilot import tools

    fn = tools.REGISTRY
    try:
        if b.kind == "market_decline":
            r = fn["run_market_decline"](b.equity_move_pct, b.rate_bp)
        elif b.kind == "rate_shock":
            r = fn["run_rate_shock"](b.rate_bp, b.equity_move_pct)
        elif b.kind == "sector_shock":
            r = fn["run_sector_shock"](b.sector or "", b.loss_pct, b.spillover)
        else:
            raise ValueError(f"unknown scenario kind {b.kind!r}")
    except Exception as e:
        raise HTTPException(400, f"{type(e).__name__}: {e}")
    return to_jsonable(r)


# ----------------------------------------------------------------------------- investigations

def _phase(ev: list[dict]) -> str:
    if not ev:
        return "Planning"
    return QUANT_ONLY if not any(e["kind"] == "NEWS" for e in ev) else "News, challenge and reporting"


async def _run_job(job_id: str, question: str) -> None:
    job = JOBS[job_id]
    async with _lock:
        job.update(status="running", started=time.time())
        try:
            from finpilot.runner import run_investigation

            run = await run_investigation(question)
            out = Path(run["output_dir"])
            (out / "meta.json").write_text(json.dumps({"question": question, "created": time.strftime("%Y-%m-%d %H:%M:%S")}))
            v = run["verification"]
            job["evidence"] = [{"id": e["id"], "kind": e["kind"], "tool": e["tool"], "args": e["args"]} for e in run["evidence"]]
            job["result"] = {
                "run_id": run["run_id"], "report_markdown": run["report_markdown"], "repairs_used": run["repairs_used"],
                "verification": {
                    "passed": v["passed"],
                    "issues": len(v["unsupported_claims"]) + len(v["numeric_mismatches"]) + len(v["invalid_citations"]) + len(v["structural_problems"]),
                    **v["stats"],
                },
            }
            job["status"] = "done"
        except Exception as e:
            job.update(status="error", error=f"{type(e).__name__}: {str(e)[:500]}")
        finally:
            job["finished"] = time.time()


@app.post("/api/investigate/start")
async def start(body: Ask):
    q = body.question.strip()
    if not q:
        raise HTTPException(400, "Question is empty")
    if _lock.locked() or any(j["status"] in ("queued", "running") for j in JOBS.values()):
        raise HTTPException(409, "An investigation is already running")
    job_id = uuid.uuid4().hex[:8]
    JOBS[job_id] = {"status": "queued", "question": q, "created": time.time()}
    task = asyncio.create_task(_run_job(job_id, q))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def job(job_id: str):
    j = JOBS.get(job_id)
    if not j:
        raise HTTPException(404, "unknown job")
    running = j["status"] in ("queued", "running")
    ev = LEDGER.index() if running else j.get("evidence", [])
    ev = [{"id": e["id"], "kind": e["kind"], "tool": e["tool"], "args": e.get("args")} for e in ev]
    end = j.get("finished") or time.time()
    return {
        "status": j["status"], "question": j["question"], "elapsed_s": round(end - j.get("started", j["created"]), 1),
        "phase": _phase(ev) if running else None, "evidence": ev,
        "tool_errors": len(LEDGER.errors) if running else None,
        "result": j.get("result"), "error": j.get("error"),
    }


@app.post("/investigate")
async def investigate(body: Ask):
    from finpilot.runner import run_investigation

    async with _lock:
        run = await run_investigation(body.question)
    _RUNS[run["run_id"]] = run
    return {k: run[k] for k in ("run_id", "report_markdown", "verification", "repairs_used", "output_dir")}


@app.get("/runs/{run_id}/evidence")
def evidence(run_id: str):
    if run_id not in _RUNS:
        raise HTTPException(404, "unknown run")
    return _RUNS[run_id]["evidence"]


# ----------------------------------------------------------------------------- history / evidence (from disk)
def _run_dir(run_id: str) -> Path:
    if not RUN_ID_RE.match(run_id):
        raise HTTPException(400, "bad run id")
    d = Path(get_settings().output_dir) / run_id
    if not d.is_dir():
        raise HTTPException(404, "unknown run")
    return d


def _read_json(p: Path, default):
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


@app.get("/api/history")
def history():
    root = Path(get_settings().output_dir)
    rows = []
    if root.exists():
        for d in sorted(root.iterdir(), reverse=True):
            if d.is_dir() and RUN_ID_RE.match(d.name):
                rows.append({
                    "run_id": d.name,
                    "question": _read_json(d / "meta.json", {}).get("question"),
                    "passed": _read_json(d / "verification.json", {}).get("passed"),
                })
    return rows[:50]


@app.get("/api/history/{run_id}")
def history_item(run_id: str):
    d = _run_dir(run_id)
    return {
        "run_id": run_id,
        "question": _read_json(d / "meta.json", {}).get("question"),
        "report_markdown": (d / "report.md").read_text(encoding="utf-8"),
    }


@app.get("/api/runs/{run_id}/evidence/{eid}")
def evidence_by_id(run_id: str, eid: str):
    for e in _read_json(_run_dir(run_id) / "evidence.json", []):
        if e["id"] == eid:
            return e
    raise HTTPException(404, "unknown evidence id")


# ----------------------------------------------------------------------------- static frontend (mounted last)
def _frontend_dir() -> Optional[Path]:
    for p in (os.environ.get("FINPILOT_FRONTEND"), ROOT / "frontend", Path(__file__).parent / "frontend", Path.cwd() / "frontend"):
        if p and (Path(p) / "index.html").exists():
            return Path(p)
    return None


_fd = _frontend_dir()
if _fd:
    app.mount("/", StaticFiles(directory=str(_fd), html=True), name="ui")
