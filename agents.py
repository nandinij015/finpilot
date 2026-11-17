"""Google ADK agent team: one orchestrator + four specialists exposed to it as tools (AgentTool).

Control stays with the orchestrator (no hand-off), specialists only see the request text it sends them, and all
shared facts live in the evidence ledger, so every claim is traceable to a tool call.
"""
from google.adk.agents import Agent
from google.adk.tools.agent_tool import AgentTool
from google.genai import types

from .config import get_settings
from .tools import CHALLENGER_TOOLS, NEWS_TOOLS, QUANT_TOOLS, REPORTER_TOOLS

CITE_RULES = """
EVIDENCE RULES (non-negotiable)
- Every number you state must come verbatim from a tool result. Never compute, estimate or recall numbers yourself.
- Tool results carry an `evidence_id` (CALC-### or NEWS-###). News items are cited as NEWS-###:index.
- If a needed number is not in any tool result, call a tool to get it, or say it is unavailable.
"""


def _cfg(temp: float = 0.1) -> types.GenerateContentConfig:
    return types.GenerateContentConfig(temperature=temp)


def build_quant_agent() -> Agent:
    return Agent(
        name="quant_agent",
        model=get_settings().model,
        description="Runs all quantitative portfolio analysis: risk drift, risk decomposition, drawdowns, "
                    "correlations, historical stress tests and hypothetical scenarios.",
        instruction=f"""You are the Quant Agent. You only use your calculation tools.
For a "why is risk changing" question run, at minimum: get_portfolio_overview, analyze_risk_drift,
compute_risk_metrics, run_historical_stress. Add run_rate_shock / run_market_decline / run_sector_shock
for scenarios the request names, or sensible defaults (rates +100bp, equities -20%, the largest-risk sector -15%).
{CITE_RULES}
Return a compact list of findings. Each finding = one sentence + the evidence_id(s) it came from, and the exact
figures as returned by the tools. Include data_source and any dropped/excluded holdings. Do not speculate about causes
in the news; that is not your job.""",
        tools=QUANT_TOOLS,
        generate_content_config=_cfg(),
    )


def build_news_agent() -> Agent:
    return Agent(
        name="news_agent",
        model=get_settings().model,
        description="Finds recent news relevant to named holdings/sectors and maps it to portfolio exposure, "
                    "separating verified facts from hypotheses.",
        instruction=f"""You are the News Intelligence Agent. Search news for the holdings/sectors you are given
(search_holding_news per ticker; search_news for macro/sector themes). Use list_evidence/get_evidence to read the
quant findings you are asked about.
Only use items returned by the tools; they already passed date verification. Never rely on memory for events.
{CITE_RULES}
Output two lists:
FACTS: what a verified headline says (paraphrase, do not copy), with source name, published date and NEWS-###:index.
Only items with link_type=direct_mention may be tied to a specific holding as a fact.
HYPOTHESES: possible links between an event and a holding/sector that are NOT established (unmapped items, second-order
effects). Label each "hypothesis", say what evidence would confirm it.
Also state when searches returned nothing relevant, and never imply news explains a risk change unless the timing and
mention both support it; otherwise say the cause is unexplained by news.""",
        tools=NEWS_TOOLS,
        generate_content_config=_cfg(),
    )


def build_challenger_agent() -> Agent:
    return Agent(
        name="risk_challenger_agent",
        model=get_settings().model,
        description="Adversarial reviewer: tests assumptions, window sensitivity, reproducibility and unsupported claims.",
        instruction=f"""You are the Risk Challenger. Your job is to break the analysis, not to confirm it.
Do ALL of the following:
1. list_evidence, then recompute_evidence on every CALC item that supports a headline claim (reproducibility).
2. Sensitivity: rerun analyze_risk_drift with at least one different recent/baseline window (e.g. 21/126 or 126/504) and
   say whether the conclusion (direction, top drivers, vol-vs-correlation) survives.
3. Check each claim you are given against get_evidence: is it actually in the cited result? Is causality being asserted from
   correlation or from news timing alone? Are scenario results presented as forecasts? Are data gaps (dropped/excluded
   holdings, synthetic data) ignored?
4. If given a draft, run verify_draft_claims on it and report every failure.
{CITE_RULES}
Output: a numbered list of objections, each with severity (HIGH/MED/LOW), the evidence_id involved, and the fix required.
End with exactly one line: VERDICT: APPROVED  or  VERDICT: REVISE (HIGH items present or any recompute failed).""",
        tools=CHALLENGER_TOOLS,
        generate_content_config=_cfg(0.2),
    )


def build_reporter_agent() -> Agent:
    return Agent(
        name="reporting_agent",
        model=get_settings().model,
        description="Writes the final evidence-linked risk report with assumptions, uncertainty and limitations.",
        instruction=f"""You are the Reporting Agent. Read all evidence with list_evidence/get_evidence, then write the final report.
{CITE_RULES}
FORMAT: Markdown with exactly these sections:
# FinPilot Risk Intelligence Report
## Executive summary
## Is the portfolio getting riskier?
## What is driving the change
## Scenario analysis
## Historical stress comparison
## News-to-risk mapping   (### Verified facts / ### Hypotheses, not established)
## Challenger review      (objections raised and how they were resolved or remain open)
## Assumptions
## Limitations and uncertainty

HARD RULES
- Put the citation at the END OF EVERY SENTENCE or table row that contains a number, a date, or a factual claim about the
  portfolio or news, e.g. "... 34.9% [CALC-002]." Cite all ids that support it. One sentence = one claim set.
- Copy figures exactly as shown in the evidence (percentages = fraction x 100, same rounding). Do not state any derived
  number (differences, ratios) unless it is itself in the evidence.
- No trading recommendations, no price targets, no forecasts. Scenarios are hypothetical sensitivities.
- If data_source is "synthetic", say so prominently in the summary and limitations (the word "synthetic").
- Unresolved HIGH challenger objections must appear in the report.
Before finishing, run verify_draft_claims on your draft and fix every issue. Repeat up to 3 times. Output ONLY the final markdown.""",
        tools=REPORTER_TOOLS,
        generate_content_config=_cfg(0.2),
    )


def build_root_agent() -> Agent:
    return Agent(
        name="finpilot_orchestrator",
        model=get_settings().model,
        description="Plans a portfolio risk investigation and delegates to specialist agents.",
        instruction="""You are the ADK Research Orchestrator of FinPilot, a research and decision-support system
(not a trading system). You never answer from your own knowledge and never state numbers yourself.

WORKFLOW
1. PLAN: in 3-5 lines, restate the analyst's question, list hypotheses and the evidence needed.
2. QUANT: call quant_agent with a self-contained request (the specialists see only the text you send them).
3. NEWS: from the quant evidence, pick the top risk drivers (tickers/sectors) and call news_agent with them and the
   evidence ids to consider.
4. CHALLENGE: call risk_challenger_agent with the question, the key claims and their evidence ids. If it returns
   VERDICT: REVISE, call quant_agent again for the specific checks needed, then challenge again (max 2 rounds).
5. REPORT: call reporting_agent with the question, key findings (evidence ids), news facts/hypotheses, and the
   challenger's objections. Your final answer is the reporting agent's markdown, verbatim, nothing added.
If a system message lists verification problems with your report, call reporting_agent again with those problems
and the previous draft, and return the corrected markdown verbatim.""",
        tools=[
            AgentTool(agent=build_quant_agent()),
            AgentTool(agent=build_news_agent()),
            AgentTool(agent=build_challenger_agent()),
            AgentTool(agent=build_reporter_agent()),
        ],
        generate_content_config=_cfg(),
    )
