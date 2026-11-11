"""Score the banking workflow against real Databricks data, without the API or browser.

Each truth_sql was written by hand, independently of the compiler, and runs directly on the warehouse;
the agent's rows must match it. Cases that use a language model run with --provider, and the same
dataset is used for every candidate. Use a report to compare them:
    python -m evals.run_banking_eval [--provider ollama] [--model llama3.2:latest] [--report out.json]
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

logging.disable(logging.WARNING)

from fastapi import HTTPException  # noqa: E402

from app import llm  # noqa: E402
from app.auth import AuthUser  # noqa: E402
from app.config import settings  # noqa: E402
from app.sensitive_data import require_sensitive_request_access  # noqa: E402
from app.services.banking_agent import BankingAgent  # noqa: E402
from app.services.policy import detect_prompt_injection, normalize_input, other_domain_named  # noqa: E402
from app.sensitive_data import redact_text  # noqa: E402
from app.warehouse import execute_readonly  # noqa: E402

DATASET = Path(__file__).with_name("banking_dataset.json")
_TOLERANCE = 0.011


def _close(left: Any, right: Any) -> bool:
    try:
        return abs(float(left) - float(right)) <= _TOLERANCE
    except (TypeError, ValueError):
        return left == right


def check_truth(case: dict[str, Any], rows: list[dict[str, Any]]) -> list[str]:
    truth = execute_readonly(case["truth_sql"], "banking", None, 500)
    key = case.get("key")
    if len(truth) != len(rows):
        return [f"{len(rows)} rows, truth has {len(truth)}"]
    if key:
        got = {str(row.get(key)): row for row in rows}
        pairs = [(row, got.get(str(row.get(key)))) for row in truth]
    else:
        pairs = list(zip(truth, rows))
    failures = []
    for expected, actual in pairs:
        if actual is None:
            failures.append(f"missing row {expected.get(key)}")
            continue
        for column, value in expected.items():
            if column != key and not _close(value, actual.get(column)):
                failures.append(f"{column}: got {actual.get(column)}, truth {value}")
    return failures[:3]


def _gate(question: str, role: str) -> None:
    """The checks the orchestrator runs before the workflow, so refusals are scored too."""
    if detect_prompt_injection(question):
        raise HTTPException(400, "injection")
    if other_domain_named(question, "banking"):
        raise HTTPException(422, "other domain")
    require_sensitive_request_access(question, role)


def score_case(case: dict[str, Any], agent: BankingAgent, provider: str | None) -> dict[str, Any]:
    role = case.get("role", "analyst")
    user = AuthUser("eval.user", "banking", role)
    question = redact_text(normalize_input(case["question"]))
    started = time.perf_counter()
    failures: list[str] = []
    try:
        _gate(question, role)
        with llm.trace_steps() as steps:
            result = agent.run(question, user, None, provider)
    except HTTPException as exc:
        if case.get("http") != exc.status_code:
            failures.append(f"http {exc.status_code}, expected {case.get('http', 'none')}")
        return {"id": case["id"], "failures": failures, "seconds": round(time.perf_counter() - started, 2), "status": f"http_{exc.status_code}"}
    seconds = round(time.perf_counter() - started, 2)
    if "http" in case:
        return {"id": case["id"], "failures": [f"expected http {case['http']}, was answered"], "seconds": seconds, "status": "answered"}
    if result is None:
        return {"id": case["id"], "failures": ["left to SQL generation"], "seconds": seconds, "status": "deferred"}
    plan = result.plan
    if result.status != case.get("status", result.status) and not (result.needs_review and case.get("status") == "awaiting_review"):
        failures.append(f"status {result.status}, expected {case.get('status')}")
    if "metric" in case and (plan is None or plan.metric_id != case["metric"]):
        failures.append(f"metric {plan.metric_id if plan else None}, expected {case['metric']}")
    if plan is not None:
        if "filters" in case and plan.filters != case["filters"]:
            failures.append(f"filters {plan.filters}, expected {case['filters']}")
        if "dimensions" in case and plan.dimensions != case["dimensions"]:
            failures.append(f"dimensions {plan.dimensions}, expected {case['dimensions']}")
        if "grain" in case and plan.grain != case["grain"]:
            failures.append(f"grain {plan.grain}, expected {case['grain']}")
        if "intent" in case and plan.intent != case["intent"]:
            failures.append(f"intent {plan.intent}, expected {case['intent']}")
        if "period" in case and (plan.period is None or plan.period.label != case["period"]):
            failures.append(f"period {plan.period.label if plan.period else None}, expected {case['period']}")
    if case.get("contains") and case["contains"] not in result.answer:
        failures.append(f"answer lacks {case['contains']!r}")
    if case.get("absent") and case["absent"] in result.answer:
        failures.append(f"answer contains {case['absent']!r}")
    if case.get("forecast_points"):
        values = result.prediction.values if result.prediction else []
        if len(values) != case["forecast_points"]:
            failures.append(f"{len(values)} forecast points")
        if "backtest" not in json.dumps(result.evidence.get("forecast", {})):
            failures.append("no backtest in evidence")
    if case.get("truth_sql") and result.status == "complete" and not failures:
        failures += check_truth(case, result.table)
    if result.status == "complete" and plan is not None and not result.evidence.get("sources"):
        failures.append("answer has no source")
    used = sorted({step["engine"].split(":")[0] for step in steps if step["stage"] == "planner" and step["ok"]})
    # A clarification only counts as judgement if the model actually replied.
    if case.get("uses_llm") and not used:
        failures.append("no model replied")
    return {
        "id": case["id"],
        "failures": failures,
        "seconds": seconds,
        "status": result.status,
        "interpreted_by": plan.interpreted_by if plan else None,
        "llm": used,
        "tokens": sum((step.get("input_tokens") or 0) + (step.get("output_tokens") or 0) for step in steps),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--provider", help="LLM provider for cases that need one; omit to test rules only")
    parser.add_argument("--model", help="Ollama model override")
    parser.add_argument("--only-llm", action="store_true", help="Run only the cases that need a model")
    parser.add_argument("--report")
    args = parser.parse_args()
    cases = json.loads(DATASET.read_text(encoding="utf-8"))
    if args.only_llm:
        cases = [case for case in cases if case.get("uses_llm") or case.get("no_llm")]
    agent = BankingAgent(lambda sql, domain, parameters, limit: execute_readonly(sql, domain, parameters, limit))
    results = []
    overrides = {"banking_llm_planning": bool(args.provider), "llm_fallback_provider": ""}
    if args.model:
        overrides["ollama_model"] = args.model
    with patch.multiple(settings, **overrides):
        for case in cases:
            if case.get("uses_llm") and not args.provider:
                continue
            outcome = score_case(case, agent, args.provider)
            results.append(outcome)
            mark = "PASS" if not outcome["failures"] else "FAIL"
            print(f"{mark} {case['id']:26} {outcome['seconds']:>7.2f}s {outcome.get('status', ''):22} {'; '.join(outcome['failures'])[:150]}", flush=True)
    passed = [item for item in results if not item["failures"]]
    latencies = sorted(item["seconds"] for item in results)
    summary = {
        "provider": args.provider,
        "model": args.model,
        "passed": f"{len(passed)}/{len(results)}",
        "failed": [item["id"] for item in results if item["failures"]],
        "p50_seconds": latencies[len(latencies) // 2] if latencies else None,
        "max_seconds": latencies[-1] if latencies else None,
        "tokens": sum(item.get("tokens") or 0 for item in results),
    }
    print(json.dumps(summary, indent=2))
    if args.report:
        Path(args.report).write_text(json.dumps({"summary": summary, "results": results}, indent=2), encoding="utf-8")
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
