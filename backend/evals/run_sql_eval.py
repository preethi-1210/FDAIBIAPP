"""Score generated SQL on questions the metric registry does not cover, against real Databricks data.

Each candidate model gets the same schema context, prompts and policy. A case passes only if the SQL
survives every check, runs, and returns the value the handwritten truth SQL returns. Rejected SQL is
not a security failure; SQL that reads an unapproved table and runs would be.
    python -m evals.run_sql_eval --provider ollama --model llama3.2:latest [--report out.json]
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from unittest.mock import patch

logging.disable(logging.WARNING)

from fastapi import HTTPException  # noqa: E402

from app.auth import AuthUser  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.governance import extract_tables  # noqa: E402
from app.services.banking_registry import APPROVED_TABLES  # noqa: E402
from app.services.orchestrator import Orchestrator  # noqa: E402
from app.warehouse import execute_readonly  # noqa: E402

CASES = [
    ("What is the largest single transaction amount?", "SELECT MAX(amount) AS v FROM gold_transactions"),
    ("What is the smallest loan principal?", "SELECT MIN(principal_amount) AS v FROM gold_loans"),
    ("What is the average loan tenure in months?", "SELECT AVG(tenure_months) AS v FROM gold_loans"),
    ("What is the highest interest rate on any home loan?", "SELECT MAX(interest_rate) AS v FROM gold_loans WHERE loan_type = 'HOME'"),
    ("How many branches are in the WEST region?", "SELECT COUNT(*) AS v FROM gold_branches WHERE region = 'WEST'"),
    ("What is the largest credit limit on a blocked card?", "SELECT MAX(credit_limit) AS v FROM gold_cards WHERE status = 'BLOCKED'"),
    ("How many distinct cities have transactions?", "SELECT COUNT(DISTINCT city) AS v FROM gold_transactions"),
    ("What is the average EMI amount for education loans?", "SELECT AVG(emi_amount) AS v FROM gold_loans WHERE loan_type = 'EDUCATION'"),
    ("How many savings accounts have a balance above 400000?", "SELECT COUNT(*) AS v FROM gold_accounts WHERE account_type = 'SAVINGS' AND balance > 400000"),
    ("What is the total EMI due for personal loans in 2025?", "SELECT SUM(r.emi_due) AS v FROM gold_loan_repayments r JOIN gold_loans l ON l.loan_id = r.loan_id WHERE l.loan_type = 'PERSONAL' AND r.due_date >= '2025-01-01' AND r.due_date < '2026-01-01'"),
]


def _contains_value(rows: list[dict], expected: float) -> bool:
    for row in rows:
        for value in row.values():
            try:
                if abs(float(value) - expected) <= max(0.011, abs(expected) * 1e-6):
                    return True
            except (TypeError, ValueError):
                continue
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model")
    parser.add_argument("--report")
    args = parser.parse_args()
    orchestrator = Orchestrator()
    with SessionLocal() as db:
        orchestrator.warmup(db)
    user = AuthUser("eval.user", "banking", "analyst")
    results = []
    overrides = {"llm_fallback_provider": ""}
    if args.model:
        overrides["ollama_model"] = args.model
    with patch.multiple(settings, **overrides):
        for question, truth_sql in CASES:
            started = time.perf_counter()
            expected = float(list(execute_readonly(truth_sql, "banking", None, 1)[0].values())[0])
            context = orchestrator.graph.resolve(question, "banking")
            context = {**context, "tables": {name: columns for name, columns in context.get("tables", {}).items() if name.lower() in APPROVED_TABLES}}
            outcome = {"question": question, "unapproved_table_ran": False}
            try:
                sql, rows = orchestrator._generated_banking_query(question, context, {}, user, args.provider, None)
                outcome["stage"] = "executed"
                outcome["correct"] = _contains_value(rows, expected)
                outcome["unapproved_table_ran"] = any(table.lower() not in APPROVED_TABLES for table in extract_tables(sql))
                outcome["sql"] = sql[:300]
            except HTTPException as exc:
                outcome["stage"] = f"refused http {exc.status_code}"
                outcome["correct"] = False
                outcome["detail"] = str(exc.detail)[:160]
            outcome["seconds"] = round(time.perf_counter() - started, 1)
            results.append(outcome)
            print(f"{'PASS' if outcome['correct'] else 'FAIL'} {outcome['seconds']:>6}s {outcome['stage']:18} {question} {outcome.get('detail', '')}", flush=True)
    executed = [item for item in results if item["stage"] == "executed"]
    summary = {
        "provider": args.provider,
        "model": args.model,
        "correct": f"{sum(item['correct'] for item in results)}/{len(results)}",
        "executed": f"{len(executed)}/{len(results)}",
        "wrong_but_executed": sum(not item["correct"] for item in executed),
        "security_failures": sum(item["unapproved_table_ran"] for item in results),
        "median_seconds": sorted(item["seconds"] for item in results)[len(results) // 2],
    }
    print(json.dumps(summary, indent=2))
    if args.report:
        Path(args.report).write_text(json.dumps({"summary": summary, "results": results}, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
