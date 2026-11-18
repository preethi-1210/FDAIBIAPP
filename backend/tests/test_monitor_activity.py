"""Monitor rows: `passed` only when data tables were queried; audit references only for audited work."""

import json
import unittest
from datetime import datetime

from app.main import _monitor_activity
from app.models import QueryHistory


def _row(status, sql=None, tables=None, guardrail_ok=1, rows=0):
    return QueryHistory(
        id=7, username="bank.user", domain="banking", question="q", answer="a",
        sql=sql, tables_used=tables, row_count=rows, guardrail_ok=guardrail_ok,
        audit_id="AU-ABC123", result_json=json.dumps({"status": status}), created_at=datetime(2026, 11, 17),
    )


class MonitorActivityTest(unittest.TestCase):
    def test_answer_from_data_tables_passes_with_audit(self):
        item = _monitor_activity(_row("complete", "SELECT 1 FROM accounts", "accounts", rows=6))
        self.assertEqual((item["status"], item["audit_id"], item["reason"]), ("passed", "AU-ABC123", None))

    def test_out_of_scope_question_fails_without_audit(self):
        item = _monitor_activity(_row("out_of_scope"))
        self.assertEqual(item["status"], "failed")
        self.assertIsNone(item["audit_id"])
        self.assertEqual(item["reason"], "Outside your business data")

    def test_answer_without_tables_fails(self):
        for status in ("complete", "needs_clarification", "insufficient_data"):
            item = _monitor_activity(_row(status))
            self.assertEqual(item["status"], "failed", status)
            self.assertIsNone(item["audit_id"], status)

    def test_query_with_no_matching_rows_still_passes(self):
        item = _monitor_activity(_row("no_data", "SELECT 1 FROM accounts", "accounts"))
        self.assertEqual(item["status"], "passed")
        self.assertEqual(item["audit_id"], "AU-ABC123")

    def test_execution_error_fails_and_keeps_audit(self):
        item = _monitor_activity(_row("error", "SELECT 1 FROM accounts", "accounts"))
        self.assertEqual((item["status"], item["audit_id"]), ("failed", "AU-ABC123"))

    def test_guardrail_block_keeps_audit(self):
        item = _monitor_activity(_row("error", "DROP TABLE accounts", "accounts", guardrail_ok=0))
        self.assertEqual((item["status"], item["audit_id"]), ("blocked", "AU-ABC123"))

    def test_pending_review(self):
        item = _monitor_activity(_row("awaiting_review", "SELECT 1 FROM accounts", "accounts", rows=3))
        self.assertEqual(item["status"], "review")


if __name__ == "__main__":
    unittest.main()
