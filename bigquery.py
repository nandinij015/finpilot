"""Optional: persist each run to BigQuery. Set FINPILOT_BQ_TABLE=project.dataset.table.

DDL:
  CREATE TABLE `project.dataset.table` (run_id STRING, created_at TIMESTAMP, question STRING,
    passed BOOL, report_markdown STRING, verification JSON, evidence JSON);
"""
import json
import os
from datetime import datetime, timezone


def persist_run(run: dict) -> None:
    table = os.environ.get("FINPILOT_BQ_TABLE")
    if not table:
        return
    from google.cloud import bigquery

    row = {
        "run_id": run["run_id"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "question": run["question"],
        "passed": run["verification"]["passed"],
        "report_markdown": run["report_markdown"],
        "verification": json.dumps(run["verification"]),
        "evidence": json.dumps(run["evidence"]),
    }
    errors = bigquery.Client().insert_rows_json(table, [row])
    if errors:
        raise RuntimeError(f"BigQuery insert failed: {errors}")
