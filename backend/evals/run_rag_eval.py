"""Compare keyword and hybrid retrieval on the banking documents, in a temporary SQLite database.

Scored on whether the right source is retrieved first, whether unanswerable questions retrieve
nothing, and whether another domain's documents are ever returned.
    python -m evals.run_rag_eval [--report out.json]
"""

import argparse
import json
import logging
import time
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

logging.disable(logging.WARNING)

from app.config import settings  # noqa: E402
from app.db import Base  # noqa: E402
from app.services.knowledge import ingest_document, search_knowledge  # noqa: E402

CORPUS = Path(__file__).resolve().parents[1] / "knowledge" / "banking"
CASES = [
    ("Why is there only one day of transactions?", "banking/reporting_periods"),
    ("What period do account openings cover?", "banking/reporting_periods"),
    ("When was the last pipeline load for balances?", "banking/reporting_periods"),
    ("Why can't I see a transaction trend?", "banking/reporting_periods"),
    ("Are failed transactions included in totals?", "banking/business_rules"),
    ("Why do some breakdowns show unmatched rows?", "banking/business_rules"),
    ("Can non-performing loans be derived?", "banking/business_rules"),
    ("Do negative amounts count?", "banking/business_rules"),
    ("What does the region of a transaction mean?", "banking/data_dictionary"),
    ("Which channels exist for payments?", "banking/data_dictionary"),
    ("What is a missed instalment?", "banking/data_dictionary"),
    ("Which tables are approved for analytics?", "banking/data_dictionary"),
    ("Can anyone retrieve a CVV?", "banking/access_policy"),
    ("Can an approval override a privacy rule?", "banking/access_policy"),
    ("Who approves forecasts?", "banking/access_policy"),
    # Paraphrases with few shared keywords.
    ("How come the monthly payment chart is empty for spending?", "banking/reporting_periods"),
    ("Is money from unsuccessful payments counted?", "banking/business_rules"),
    ("May staff look up someone's security code?", "banking/access_policy"),
]
UNANSWERABLE = [
    "What is the capital of France?",
    "What is the weather in Mumbai tomorrow?",
    "How do I bake sourdough bread?",
    "Who won the cricket match yesterday?",
    "What is the RBI repo rate today?",
]


def run(database, mode: str) -> dict:
    similarity = settings.knowledge_min_similarity if mode == "hybrid" else 0
    results = []
    with patch.object(settings, "knowledge_min_similarity", similarity):
        for question, expected in CASES:
            started = time.perf_counter()
            hits = search_knowledge(database, question, "banking")
            sources = [hit["source"] for hit in hits]
            results.append({"question": question, "first": sources[0] if sources else None, "top1": bool(sources) and sources[0] == expected, "top3": expected in sources, "seconds": round(time.perf_counter() - started, 3)})
        false_hits = [question for question in UNANSWERABLE if search_knowledge(database, question, "banking")]
        cross = search_knowledge(database, "Why is there only one day of transactions?", "retail")
    documents = [hit for hit in cross if hit["source"].startswith("banking/")]
    return {
        "mode": mode,
        "top1": f"{sum(item['top1'] for item in results)}/{len(results)}",
        "top3": f"{sum(item['top3'] for item in results)}/{len(results)}",
        "unanswerable_with_hits": f"{len(false_hits)}/{len(UNANSWERABLE)}",
        "false_hit_questions": false_hits,
        "documents_from_other_domains": len(documents),
        "median_seconds": sorted(item["seconds"] for item in results)[len(results) // 2],
        "missed": [item["question"] for item in results if not item["top3"]],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report")
    args = parser.parse_args()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as database:
        for path in sorted(CORPUS.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            ingest_document(database, f"banking/{path.stem}", text.splitlines()[0].lstrip("# "), text, "banking", "evaluation")
        report = [run(database, "keyword"), run(database, "hybrid")]
    print(json.dumps(report, indent=2))
    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
