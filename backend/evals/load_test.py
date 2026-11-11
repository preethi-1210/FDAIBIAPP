"""Small load test of the registry path against a running API, using real Databricks.

For a staging or verification instance with RATE_LIMIT_QUERY_PER_MINUTE=0. Each request runs real
warehouse queries, so keep the counts modest. Writes history to the bank.user demo account.
    python -m evals.load_test --base-url http://localhost:8099 --levels 1,5,10 --requests 30
"""

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from app.auth import create_access_token

QUESTIONS = (
    "Show the total transaction volume",
    "Show total transaction amount by merchant category",
    "What is the failure rate by channel?",
    "How many dormant accounts are there?",
    "Show the monthly trend of loan repayments",
)


def level(base_url: str, token: str, workers: int, total: int) -> dict:
    def one(index: int) -> tuple[int, float]:
        started = time.perf_counter()
        try:
            response = httpx.post(
                f"{base_url}/v1/query",
                json={"question": QUESTIONS[index % len(QUESTIONS)]},
                headers={"Authorization": f"Bearer {token}"},
                timeout=180,
            )
            status = response.status_code if response.status_code != 200 or response.json().get("status") == "complete" else 299
        except httpx.HTTPError:
            status = 0
        return status, time.perf_counter() - started

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = list(pool.map(one, range(total)))
    elapsed = time.perf_counter() - started
    latencies = sorted(seconds for _status, seconds in outcomes)
    errors = [status for status, _seconds in outcomes if status != 200]
    return {
        "concurrency": workers,
        "requests": total,
        "errors": len(errors),
        "error_codes": sorted(set(errors)),
        "throughput_per_second": round(total / elapsed, 2),
        "p50_seconds": round(latencies[len(latencies) // 2], 2),
        "p95_seconds": round(latencies[int(len(latencies) * 0.95) - 1], 2),
        "max_seconds": round(latencies[-1], 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://localhost:8099")
    parser.add_argument("--levels", default="1,5,10")
    parser.add_argument("--requests", type=int, default=30)
    parser.add_argument("--report")
    args = parser.parse_args()
    token = create_access_token("bank.user", "banking", "analyst")
    report = [level(args.base_url.rstrip("/"), token, int(item), args.requests) for item in args.levels.split(",")]
    print(json.dumps(report, indent=2))
    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
