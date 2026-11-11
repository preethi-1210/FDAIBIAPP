"""Score the finance, insurance, retail and healthcare registries against real Databricks data.

Each truth_sql was written by hand, independently of the compiler, and runs directly on the warehouse;
the registry's rows must match it. Healthcare runs in the tenant catalog of an assigned user.
    python -m evals.run_registry_eval [--domain finance] [--report out.json]
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

logging.disable(logging.WARNING)

from app.auth import AuthUser  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import UserDomainTenant  # noqa: E402
from app.services.banking_agent import BankingAgent  # noqa: E402
from app.services.domain_registries import REGISTRIES  # noqa: E402
from app.warehouse import execute_healthcare_tenant_readonly, execute_readonly  # noqa: E402

DATASET = Path(__file__).with_name("registry_dataset.json")
_TOLERANCE = 0.011


def _close(left: Any, right: Any) -> bool:
    try:
        return abs(float(left) - float(right)) <= _TOLERANCE
    except (TypeError, ValueError):
        return str(left) == str(right)


def _tenant() -> str | None:
    with SessionLocal() as db:
        row = db.query(UserDomainTenant).filter(UserDomainTenant.domain == "healthcare").first()
        return row.tenant_id if row else None


def _executor(domain: str, tenant: str | None):
    if domain == "healthcare":
        return lambda sql, _domain, parameters, limit: execute_healthcare_tenant_readonly(sql, tenant, parameters, limit)
    return lambda sql, _domain, parameters, limit: execute_readonly(sql, domain, parameters, limit)


def check_truth(case: dict[str, Any], rows: list[dict[str, Any]], execute) -> list[str]:
    truth = execute(case["truth_sql"], case["domain"], None, 500)
    key = case.get("key")
    if len(truth) != len(rows):
        return [f"{len(rows)} rows, truth has {len(truth)}"]
    got = {str(row.get(key)): row for row in rows} if key else {}
    failures = []
    for index, expected in enumerate(truth):
        actual = got.get(str(expected.get(key))) if key else rows[index]
        if actual is None:
            failures.append(f"missing row {expected.get(key)}")
            continue
        for column, value in expected.items():
            if column != key and not _close(value, actual.get(column)):
                failures.append(f"{column}: got {actual.get(column)}, truth {value}")
    return failures[:3]


def score(case: dict[str, Any], tenant: str | None) -> dict[str, Any]:
    domain = case["domain"]
    user = AuthUser("eval.user", domain, "analyst", tenant if domain == "healthcare" else None)
    execute = _executor(domain, tenant)
    agent = BankingAgent(execute, REGISTRIES[domain], scope=tenant or "")
    started = time.perf_counter()
    result = agent.run(case["question"], user)
    seconds = round(time.perf_counter() - started, 2)
    failures: list[str] = []
    expected = case.get("status", "complete")
    if expected == "fallthrough":
        if result is not None:
            failures.append(f"answered ({result.status}) instead of leaving it to the existing path")
        return {"id": case["id"], "failures": failures, "seconds": seconds, "status": "fallthrough" if result is None else result.status}
    if result is None:
        return {"id": case["id"], "failures": ["left to the existing path"], "seconds": seconds, "status": "fallthrough"}
    if result.status != expected:
        failures.append(f"status {result.status}, expected {expected}: {result.answer[:120]}")
    plan = result.plan
    if "metric" in case and (plan is None or plan.metric_id != case["metric"]):
        failures.append(f"metric {plan.metric_id if plan else None}, expected {case['metric']}")
    if plan is not None and "filters" in case and plan.filters != case["filters"]:
        failures.append(f"filters {plan.filters}, expected {case['filters']}")
    if case.get("truth_sql") and not failures:
        rows = [{k: v for k, v in row.items() if k != "record_count"} for row in result.table]
        failures += check_truth(case, rows, execute)
    if result.status == "complete" and not result.evidence.get("sources"):
        failures.append("answer has no source")
    return {"id": case["id"], "failures": failures, "seconds": seconds, "status": result.status}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--domain")
    parser.add_argument("--report")
    args = parser.parse_args()
    cases = [case for case in json.loads(DATASET.read_text(encoding="utf-8")) if not args.domain or case["domain"] == args.domain]
    tenant = _tenant()
    results = []
    for case in cases:
        outcome = score(case, tenant)
        results.append(outcome)
        mark = "PASS" if not outcome["failures"] else "FAIL"
        print(f"{mark} {case['id']:26} {outcome['seconds']:>6.2f}s {outcome['status']:18} {'; '.join(outcome['failures'])[:170]}", flush=True)
    passed = [item for item in results if not item["failures"]]
    summary = {"passed": f"{len(passed)}/{len(results)}", "failed": [item["id"] for item in results if item["failures"]]}
    print(json.dumps(summary, indent=2))
    if args.report:
        Path(args.report).write_text(json.dumps({"summary": summary, "results": results}, indent=2), encoding="utf-8")
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
