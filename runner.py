"""Runs an investigation end to end: ADK agents -> deterministic verification -> repair loop -> audit artifacts."""
import json
import time
import uuid

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from .agents import build_root_agent
from .config import get_settings
from .context import reset_context
from .evidence import LEDGER
from .sinks.bigquery import persist_run
from .verify import verify_report

APP_NAME = "finpilot"


async def _send(runner: Runner, session_id: str, text: str, trace: list) -> str:
    final = ""
    msg = types.Content(role="user", parts=[types.Part(text=text)])
    async for ev in runner.run_async(user_id="analyst", session_id=session_id, new_message=msg):
        for fc in ev.get_function_calls() or []:
            trace.append({"author": ev.author, "type": "call", "name": fc.name, "args": dict(fc.args or {})})
        for fr in ev.get_function_responses() or []:
            trace.append({"author": ev.author, "type": "response", "name": fr.name})
        if ev.is_final_response() and ev.content and ev.content.parts:
            final = "".join(p.text or "" for p in ev.content.parts if getattr(p, "text", None)) or final
    return final


def _repair_prompt(v: dict) -> str:
    lines = ["SYSTEM VERIFICATION FAILED. Fix every item below, then return the corrected full report."]
    for k in ("invalid_citations", "unsupported_claims", "numeric_mismatches"):
        for item in v[k]:
            lines.append(f"- [{k}] {item.get('reason')}: {item.get('sentence') or item.get('number')}")
    lines += [f"- [structure] {p}" for p in v["structural_problems"]]
    return "\n".join(lines)


def _appendix(v: dict, evidence: list) -> str:
    status = "PASSED" if v["passed"] else "FAILED - treat unflagged-claim list below as unresolved"
    out = ["\n\n---\n## Verification appendix", f"Automated claim verification: **{status}**.",
           f"Sentences checked: {v['stats']['sentences_checked']}; with citations: {v['stats']['sentences_with_citations']}.\n",
           "| Evidence | Kind | Tool | Arguments |", "|---|---|---|---|"]
    out += [f"| {e['id']} | {e['kind']} | {e['tool']} | `{json.dumps(e['args'])}` |" for e in evidence]
    if not v["passed"]:
        out.append("\n**Unresolved verification problems:**")
        for k in ("invalid_citations", "unsupported_claims", "numeric_mismatches"):
            out += [f"- {k}: {i.get('reason')} - {(i.get('sentence') or i.get('number'))[:120]}" for i in v[k]]
        out += [f"- {p}" for p in v["structural_problems"]]
    return "\n".join(out)


async def run_investigation(question: str, max_repairs: int = 2) -> dict:
    LEDGER.reset()
    reset_context()
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    svc = InMemorySessionService()
    runner = Runner(agent=build_root_agent(), app_name=APP_NAME, session_service=svc)
    session = await svc.create_session(app_name=APP_NAME, user_id="analyst")
    trace: list = []
    report = await _send(runner, session.id, question, trace)
    v = verify_report(report, LEDGER)
    repairs = 0
    while not v["passed"] and repairs < max_repairs:
        repairs += 1
        fixed = await _send(runner, session.id, _repair_prompt(v), trace)
        if fixed.strip():
            report = fixed
        v = verify_report(report, LEDGER)
    evidence = LEDGER.dump()
    banner = "" if v["passed"] else "> **WARNING: this report did not pass automated verification. See appendix.**\n\n"
    run = {
        "run_id": run_id, "question": question, "repairs_used": repairs,
        "report_markdown": banner + report + _appendix(v, evidence),
        "verification": v, "evidence": evidence, "tool_errors": LEDGER.errors, "trace": trace,
    }
    out = get_settings().output_dir / run_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(run["report_markdown"])
    for name in ("verification", "evidence", "tool_errors", "trace"):
        (out / f"{name}.json").write_text(json.dumps(run[name], indent=2, default=str))
    run["output_dir"] = str(out)
    persist_run(run)
    return run
