# FinPilot — Autonomous Financial Research & Risk Intelligence

Research / decision-support only. Not an autonomous trading system; no return guarantees.

```
Question -> ADK Orchestrator -> quant_agent | news_agent | risk_challenger_agent | reporting_agent
                                      |            |              |                    |
                                      +------ evidence ledger (CALC-### / NEWS-###:n) -+
                                                         |
                          deterministic verifier (outside the LLMs) -> repair loop -> audited report
```

**Why agents can't invent explanations**
1. Numbers only exist as tool outputs; each gets an evidence ID with its arguments and timestamp.
2. `verify.py` checks the final report: IDs exist, numeric claims are cited, cited numbers appear in the cited evidence,
   news items have verified dates, news-style claims cite NEWS, limitations present, synthetic data disclosed.
3. Failures go back to the orchestrator for up to 2 repairs; if still failing the report is stamped with a warning.
4. The Challenger re-runs calculations (`recompute_evidence`) and tests window sensitivity.

## Quickstart
```bash
pip install -r requirements.txt
cp .env.example .env            # add GOOGLE_API_KEY (or Vertex vars)
python -m pytest -q tests       # offline unit tests
python -m evals.run_evals       # offline scorecard (planted-driver synthetic data)

python -m finpilot --data-source synthetic            # offline demo (clearly labelled synthetic)
python -m finpilot "Why is risk rising and what could make losses worse?"   # real data via yfinance
adk web                                               # ADK dev UI (agent: adk_app)
python -m evals.run_evals --llm                       # end-to-end scoring incl. unsupported-claim rate
```
Each run writes `outputs/<run_id>/{report.md,evidence.json,verification.json,trace.json,tool_errors.json}`.

## Your own portfolio
Edit `data/sample_portfolio.json` (`weight` or `quantity` per holding, plus `sector`, `name`) or set `FINPILOT_PORTFOLIO`.
Offline prices: `FINPILOT_DATA_SOURCE=csv FINPILOT_PRICES_CSV=...` (Date index, one column per ticker, must include SPY and TLT).

## Deploy (Cloud Run + optional BigQuery)
```bash
gcloud run deploy finpilot --source . --concurrency 1 --memory 1Gi --set-env-vars FINPILOT_MODEL=gemini-2.5-flash,GOOGLE_GENAI_USE_VERTEXAI=TRUE
# optional run archive: set FINPILOT_BQ_TABLE (DDL in finpilot/sinks/bigquery.py)
curl -X POST $URL/investigate -H 'content-type: application/json' -d '{"question":"..."}'
```
`--concurrency 1` because the evidence ledger is process-wide.

## Layout
| Path | Role |
|---|---|
| `finpilot/quant/risk.py` | vol, VaR/ES, drawdown, Euler risk contributions, exact drift decomposition |
| `finpilot/quant/scenarios.py` | historical replay, rate/market factor shocks, sector shock + spillover |
| `finpilot/news/rss.py` | news retrieval + date verification + holding matching |
| `finpilot/evidence.py`, `verify.py` | ledger and deterministic claim verifier |
| `finpilot/tools.py`, `agents.py`, `runner.py` | ADK tools, agent team, run + repair loop |
| `tests/`, `evals/run_evals.py` | unit tests and scorecard |

## Known limitations (also surfaced to the agents)
- Scenarios are linear factor approximations (OLS betas, rate shock via TLT duration); no convexity or liquidity effects.
- "Verified date" = parseable, in-window publication timestamp from the feed, not an article-body cross-check.
- The verifier checks that numbers match cited evidence and that claims are cited; it cannot prove a qualitative causal story is right.
- Absolute-value matching ignores sign errors; the Challenger covers those.
- Historical stress replays current weights; holdings without history are excluded and coverage is reported.
