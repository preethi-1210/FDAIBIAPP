"""Evaluate Ask AI against evals/dataset.json.

Offline (no services, no LLM): checks routing and input refusal for the cases that do not need data.
    python -m evals.run_eval --offline

Live: sends every case to a running API as real local users and scores the responses. Expected
figures in the dataset were read from the warehouse by hand; refresh them when the data changes.
    python -m evals.run_eval --base-url http://localhost:8099 [--report results.json]

Live mode signs session tokens with this environment's JWT secret, so it is for development and
staging only. Answers are never written to the report, only pass/fail reasons.
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

DATASET = Path(__file__).with_name("dataset.json")
DEMO_USERS = {
    "banking": "bank.user",
    "finance": "fin.user",
    "healthcare": "health.user",
    "insurance": "ins.user",
    "retail": "ret.user",
}


def load_cases() -> list[dict[str, Any]]:
    return json.loads(DATASET.read_text(encoding="utf-8"))


def score(case: dict[str, Any], http_status: int, body: dict[str, Any]) -> list[str]:
    """Every way this response fails the case; an empty list is a pass."""
    expected_http = case.get("http", 200)
    allowed = expected_http if isinstance(expected_http, list) else [expected_http]
    if http_status not in allowed:
        return [f"http {http_status}, expected {allowed}"]
    if http_status != 200:
        detail = str(body.get("detail") or "")
        failures = [f"refusal contains {text!r}" for text in case.get("absent", []) if text in detail]
        if case.get("contains_detail", "") not in detail:
            failures.append(f"refusal lacks {case['contains_detail']!r}")
        return failures
    failures = []
    answer = body.get("answer") or ""
    if "status" in case and body.get("status") not in case["status"]:
        failures.append(f"status {body.get('status')}, expected {case['status']}")
    route = (body.get("plan") or {}).get("route")
    if "route" in case and route != case["route"] and not (case["route"] == "databricks" and route is None):
        failures.append(f"route {route}, expected {case['route']}")
    failures += [f"answer lacks {text!r}" for text in case.get("contains", []) if text not in answer]
    failures += [f"answer contains {text!r}" for text in case.get("absent", []) if text in answer]
    sql = (body.get("sql") or "").lower()
    if case.get("no_sql") and sql:
        failures.append("ran SQL")
    failures += [f"SQL references {text!r}" for text in case.get("absent_in_sql", []) if text in sql]
    columns = {column.lower() for row in body.get("table") or [] for column in row}
    failures += [f"result returned column {text!r}" for text in case.get("absent_in_table", []) if text in columns]
    if case.get("no_llm") and body.get("llm_provider"):
        failures.append(f"used LLM {body['llm_provider']}")
    return failures


def run_offline(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from app.services.metrics import match_metric
    from app.services.planner import route_question
    from app.services.policy import detect_prompt_injection, normalize_input, other_domain_named

    results = []
    for case in cases:
        if case.get("live_only"):
            continue
        question = normalize_input(case["question"])
        if case.get("http") == 400:
            failures = [] if detect_prompt_injection(question) else ["injection not detected"]
        elif case.get("http") == 422:
            failures = [] if other_domain_named(question, case["user"]) else ["other domain not detected"]
        else:
            route = route_question(question, {"intent": "retrieval"})
            if route == "metric" and match_metric(question, case["user"]) is None:
                route = "databricks"
            failures = [] if route == case["route"] else [f"route {route}, expected {case['route']}"]
        results.append({"id": case["id"], "failures": failures})
    return results


def _tokens() -> dict[str, str]:
    """Only the seeded demo accounts: each case adds conversations and history to its user."""
    from app.auth import create_access_token
    from app.db import SessionLocal
    from app.models import AppUser

    tokens = {}
    with SessionLocal() as db:
        for domain, username in DEMO_USERS.items():
            user = db.query(AppUser).filter_by(username=username).one_or_none()
            if user is not None and user.domain == domain:
                tokens[domain] = create_access_token(username, domain, "analyst")
    return tokens


def run_live(cases: list[dict[str, Any]], base_url: str, provider: str | None, timeout: float) -> list[dict[str, Any]]:
    import httpx

    tokens = _tokens()
    results = []
    for case in cases:
        if case["user"] not in tokens:
            results.append({"id": case["id"], "failures": [f"no local {case['user']} user"], "seconds": 0})
            continue
        payload = {"question": case["question"], **({"llm_provider": provider} if provider else {})}
        started = time.monotonic()
        try:
            response = httpx.post(
                f"{base_url}/v1/query",
                json=payload,
                headers={"Authorization": f"Bearer {tokens[case['user']]}"},
                timeout=timeout,
            )
            body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
            failures = score(case, response.status_code, body)
            detail = {
                "http": response.status_code,
                "status": body.get("status"),
                "route": (body.get("plan") or {}).get("route"),
                "provider": body.get("llm_provider"),
            }
        except httpx.HTTPError as exc:
            failures, detail = [f"request failed: {type(exc).__name__}"], {}
        results.append({"id": case["id"], "failures": failures, "seconds": round(time.monotonic() - started, 1), **detail})
        print(f"{'PASS' if not failures else 'FAIL'} {case['id']:24} {results[-1]['seconds']:>6}s {'; '.join(failures)}", flush=True)
    return results


def summarize(results: list[dict[str, Any]], cases: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {case["id"]: case for case in cases}
    def rate(ids: list[str]) -> str:
        return f"{sum(not r['failures'] for r in results if r['id'] in ids)}/{len(ids)}"

    ran = [r["id"] for r in results]
    return {
        "passed": rate(ran),
        "deterministic": rate([i for i in ran if not by_id[i].get("uses_llm")]),
        "llm_dependent": rate([i for i in ran if by_id[i].get("uses_llm")]),
        "security": rate([i for i in ran if i.startswith("sec-")]),
        "failed": [r["id"] for r in results if r["failures"]],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--base-url", default="http://localhost:8013")
    parser.add_argument("--provider", help="LLM provider for the cases that need one, e.g. ollama")
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--report", help="Write per-case results to this JSON file")
    args = parser.parse_args()
    cases = load_cases()
    results = run_offline(cases) if args.offline else run_live(cases, args.base_url.rstrip("/"), args.provider, args.timeout)
    summary = summarize(results, cases)
    print(json.dumps(summary, indent=2))
    if args.report:
        Path(args.report).write_text(json.dumps({"summary": summary, "results": results}, indent=2), encoding="utf-8")
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
