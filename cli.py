import argparse
import asyncio
import os


def main() -> None:
    p = argparse.ArgumentParser(prog="finpilot", description="Autonomous financial research & risk intelligence")
    p.add_argument("question", nargs="?", default="Why is this portfolio becoming riskier, which holdings are driving the "
                   "change, and what scenarios could make the losses worse?")
    p.add_argument("--data-source", choices=["yfinance", "csv", "synthetic"])
    p.add_argument("--portfolio", help="Path to portfolio JSON")
    a = p.parse_args()
    if a.data_source:
        os.environ["FINPILOT_DATA_SOURCE"] = a.data_source
    if a.portfolio:
        os.environ["FINPILOT_PORTFOLIO"] = a.portfolio
    from .runner import run_investigation

    run = asyncio.run(run_investigation(a.question))
    print(run["report_markdown"])
    print(f"\n[artifacts saved in {run['output_dir']}]  verification passed: {run['verification']['passed']}")


if __name__ == "__main__":
    main()
