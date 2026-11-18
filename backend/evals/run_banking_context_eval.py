"""Evaluate the banking business context on the 46 capability questions, in three separate modes.

    python -m evals.run_banking_context_eval --mode rules       # offline: context + rule planner only
    python -m evals.run_banking_context_eval --mode workflow    # live: real agent, model planning, Databricks
    python -m evals.run_banking_context_eval --mode model       # live: the model planner alone, every question
    python -m evals.run_banking_context_eval --mode all --report out.json

Rule-based and model-assisted results are reported separately. A case whose model call failed is
reported as "model unavailable" and never counted as a pass. Live modes are read-only: Databricks is
queried through the application's guarded read path, and nothing is written to PostgreSQL.
"""

import argparse
import json
import logging
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

logging.disable(logging.WARNING)

from app.auth import AuthUser  # noqa: E402
from app.config import settings  # noqa: E402
from app.llm import trace_steps  # noqa: E402
from app.sensitive_data import require_compiled_sql_safe  # noqa: E402
from app.services import banking_context  # noqa: E402
from app.services.banking_query import ParseResult, PlanError, parse_question, plan_with_llm, validate_plan  # noqa: E402
from app.services.banking_registry import APPROVED_TABLES, BANKING, use_registry  # noqa: E402
from app.services.sql_policy import enforce_select_policy  # noqa: E402

DATASET = Path(__file__).with_name("banking_context_dataset.json")
USER = AuthUser(username="eval.banking", domain="banking", role="analyst")
_TOLERANCE = 0.011
_ANSWER_STATUSES = {"complete", "no_data", "insufficient_history"}


def load() -> list[dict[str, Any]]:
    return json.loads(DATASET.read_text(encoding="utf-8"))


def expected_intent(case: dict[str, Any]) -> str:
    return "breakdown" if case.get("dimensions") else "value"


def _plan_matches(case: dict[str, Any], plan) -> dict[str, bool]:
    return {
        "metric": plan is not None and plan.metric_id == case.get("metric"),
        "filters": plan is not None and dict(plan.filters) == case.get("filters", {}),
        "dimensions": plan is not None and list(plan.dimensions) == case.get("dimensions", []),
        "intent": plan is not None and plan.intent == expected_intent(case),
    }


# ----------------------------------------------------------------------------- rules (offline)


def run_rules(cases: list[dict[str, Any]], today: date) -> list[dict[str, Any]]:
    results = []
    for case in cases:
        resolution = banking_context.resolve_terminology(case["question"])
        plan, kind, note = None, None, ""
        if resolution.refusal:
            kind, note = "refuse", "business context: not in banking.gold"
        elif resolution.clarification:
            kind, note = "clarify", "business context: no approved meaning"
        else:
            parsed = parse_question(resolution.question, None, today)
            if parsed.refusal:
                kind, note = "refuse", "registry refusal"
            elif parsed.clarification:
                kind, note = "clarify", "planner clarification"
            elif parsed.plan is not None:
                plan, kind = parsed.plan, "answer"
            elif parsed.unknown_terms:
                kind, note = "clarify", f"rules could not place {parsed.unknown_terms}; the model would be consulted"
            else:
                kind, note = "refuse", "no banking metric; general path refuses (BANKING_GENERATED_SQL=false)"
        row = {"id": case["id"], "expect": case["expect"], "got": kind, "kind_ok": kind == case["expect"], "note": note,
               "terms": list(resolution.terms)}
        if case["expect"] == "answer":
            row.update({f"{name}_ok": ok for name, ok in _plan_matches(case, plan).items()})
        if case.get("terms"):
            row["terms_ok"] = all(term in resolution.terms for term in case["terms"])
        if case.get("rule"):
            row["rule_ok"] = case["rule"] in resolution.rules or (
                plan is not None and case["rule"] in {rule.id for rule in banking_context.rules_for(plan.metric_id)}
            )
        results.append(row)
    return results


# ----------------------------------------------------------------------------- live helpers


def _close(left: Any, right: Any) -> bool:
    try:
        return abs(float(left) - float(right)) <= _TOLERANCE
    except (TypeError, ValueError):
        return left == right


def _truth_failures(case: dict[str, Any], rows: list[dict[str, Any]], plan=None) -> list[str]:
    from app.warehouse import execute_readonly

    truth = execute_readonly(case["truth_sql"], "banking", None, 500)
    key = case.get("key")
    if plan is not None and plan.top_n:
        # "Which ... the highest" is read as a ranking: compare with the same ranking of the truth.
        measure = next(column for column in truth[0] if column != key)
        truth = sorted(truth, key=lambda row: float(row[measure] or 0), reverse=plan.order != "asc")[: plan.top_n]
    if len(truth) != len(rows):
        return [f"{len(rows)} rows, truth has {len(truth)}"]
    pairs = [(row, {str(r.get(key)): r for r in rows}.get(str(row.get(key)))) for row in truth] if key else list(zip(truth, rows))
    failures = []
    for expected, actual in pairs:
        if actual is None:
            failures.append(f"missing row {expected.get(key)}")
            continue
        failures += [f"{column}: got {actual.get(column)}, truth {value}" for column, value in expected.items() if column != key and not _close(value, actual.get(column))]
    return failures[:3]


def _model_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [step for step in steps if step.get("stage") == "planner" and ":" in str(step.get("engine", ""))]


def _model_unavailable(steps: list[dict[str, Any]]) -> bool:
    calls = _model_steps(steps)
    return bool(calls) and not any(step.get("ok") for step in calls)


# ----------------------------------------------------------------------------- workflow (live)


def run_workflow(cases: list[dict[str, Any]], provider: str | None) -> list[dict[str, Any]]:
    from app.services.banking_agent import BankingAgent
    from app.warehouse import execute_readonly

    agent = BankingAgent(lambda sql, domain, parameters, limit: execute_readonly(sql, domain, parameters, limit))
    results = []
    for case in cases:
        with trace_steps() as steps:
            try:
                result = agent.run(case["question"], USER, None, provider, approved=False)
                error = None
            except Exception as exc:  # reported, never hidden
                result, error = None, f"{type(exc).__name__}: {str(exc)[:160]}"
        status = result.status if result else ("exception" if error else "general_path")
        kind = (
            "answer" if status in _ANSWER_STATUSES else "clarify" if status == "needs_clarification"
            else "refuse" if status in {"insufficient_data", "general_path"} else "error"
        )
        model_calls = _model_steps(steps)
        row = {
            "id": case["id"], "expect": case["expect"], "got": kind, "status": status, "kind_ok": kind == case["expect"],
            "model_called": bool(model_calls), "model_unavailable": _model_unavailable(steps),
            "interpreted_by": result.plan.interpreted_by if result and result.plan else None,
            "error": error or (result.error if result else None),
        }
        evidence = result.evidence if result else {}
        context = evidence.get("business_context") or {}
        row["context_ok"] = bool(context) or status == "general_path"
        if case.get("terms"):
            row["terms_ok"] = all(term in context.get("terms", []) for term in case["terms"])
        if case.get("rule"):
            row["rule_ok"] = case["rule"] in {rule["id"] for rule in context.get("rules", [])}
        if case["expect"] == "answer":
            row.update({f"{name}_ok": ok for name, ok in _plan_matches(case, result.plan if result else None).items()})
            row["status_ok"] = status == case.get("status")
            sql_ok = False
            if result and result.sql:
                try:
                    enforce_select_policy(result.sql, APPROVED_TABLES, "banking", "gold")
                    require_compiled_sql_safe(result.sql)
                    # A period outside the data is answered without running the query; its SQL still passes the policy.
                    sql_ok = status != "complete" or any(step.get("stage") == "sql_validation" and step.get("ok") for step in steps)
                except Exception:
                    sql_ok = False
            row["sql_validated_ok"] = sql_ok
            if case.get("truth_sql") and status == "complete":
                failures = _truth_failures(case, result.table, result.plan)
                row["execution_ok"], row["execution_detail"] = not failures, failures
            sources = evidence.get("sources") or []
            row["evidence_ok"] = bool(
                sources and all(str(source).startswith("banking.gold.") for source in sources)
                and evidence.get("definition") and evidence.get("registry_version")
            )
            if case.get("discloses"):
                row["disclosure_ok"] = bool(result and case["discloses"] in result.answer and "pending" in result.answer)
        else:
            # Hallucination check: nothing measured, no SQL, no figure presented as an answer.
            row["no_hallucination_ok"] = (result is None or (result.sql is None and not result.table)) and kind != "answer"
        results.append(row)
    return results


# ----------------------------------------------------------------------------- model planner alone (live)


def run_model(cases: list[dict[str, Any]], provider: str | None) -> list[dict[str, Any]]:
    results = []
    for case in cases:
        with use_registry(BANKING), trace_steps() as steps:
            try:
                plan, unsupported = plan_with_llm(case["question"], ParseResult(), None, provider)
                error = None
            except Exception as exc:
                plan, unsupported, error = None, [], f"{type(exc).__name__}: {str(exc)[:160]}"
            valid = False
            if plan is not None:
                try:
                    validate_plan(plan, USER.role)
                    valid = True
                except PlanError:
                    valid = False
        unavailable = error is not None or _model_unavailable(steps) or not _model_steps(steps)
        mapped = plan is not None and valid and not unsupported
        row = {
            "id": case["id"], "expect": case["expect"], "model_unavailable": unavailable,
            "model_metric": plan.metric_id if plan else None, "unsupported_terms": unsupported, "valid_plan": valid, "error": error,
            "model_plan": plan.to_dict() if plan else None,
        }
        if not unavailable:
            if case["expect"] == "answer":
                matches = _plan_matches(case, plan if mapped else None)
                # Metric choice is scored apart from the rest of the plan, which validation may reject.
                row["metric_selected_ok"] = plan is not None and plan.metric_id == case.get("metric")
                row["valid_plan_ok"] = valid
                row["exact_plan_ok"] = all(matches.values())
            else:
                # The model alone should decline; the business context stops these before the model in production.
                row["declined_ok"] = not mapped
        results.append(row)
    return results


# ----------------------------------------------------------------------------- summary


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [row for row in rows if not row.get("model_unavailable")]
    summary: dict[str, Any] = {"cases": len(rows), "scored": len(scored), "model_unavailable": len(rows) - len(scored)}
    keys = sorted({key for row in scored for key in row if key.endswith("_ok")})
    for key in keys:
        values = [row[key] for row in scored if key in row]
        summary[key[:-3]] = f"{sum(values)}/{len(values)}"
    if any("model_called" in row for row in rows):
        summary["model_called"] = sum(1 for row in rows if row.get("model_called"))
    summary["outcomes"] = dict(Counter(f"{row['expect']}->{row.get('got', row.get('model_metric'))}" for row in rows))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=("rules", "workflow", "model", "all"), default="rules")
    parser.add_argument("--provider", default=None, help="LLM provider; default is the configured one")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    cases = load()
    report: dict[str, Any] = {
        "dataset": DATASET.name, "context_version": banking_context.CONTEXT_VERSION, "today": date.today().isoformat(),
        "provider": args.provider or settings.llm_provider, "banking_llm_planning": settings.banking_llm_planning,
        "banking_generated_sql": settings.banking_generated_sql,
    }
    modes = ("rules", "workflow", "model") if args.mode == "all" else (args.mode,)
    for mode in modes:
        rows = run_rules(cases, date.today()) if mode == "rules" else run_workflow(cases, args.provider) if mode == "workflow" else run_model(cases, args.provider)
        report[mode] = {"summary": summarize(rows), "cases": rows}
        print(f"\n== {mode}")
        for key, value in report[mode]["summary"].items():
            print(f"  {key}: {value}")
        failed = [row for row in rows if any(row.get(key) is False for key in row if key.endswith("_ok"))]
        for row in failed:
            print(f"  FAIL {row['id']}: " + ", ".join(key[:-3] for key in row if key.endswith("_ok") and row[key] is False)
                  + (f" | got {row.get('got', row.get('model_metric'))}" if row.get("got") or row.get("model_metric") else "")
                  + (f" | {row['note']}" if row.get("note") else "") + (f" | {row['execution_detail']}" if row.get("execution_detail") else ""))
        for row in rows:
            if row.get("model_unavailable"):
                print(f"  MODEL UNAVAILABLE {row['id']}: {row.get('error') or 'no successful model call'}")
    if args.report:
        args.report.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"\nreport: {args.report}")


if __name__ == "__main__":
    main()
