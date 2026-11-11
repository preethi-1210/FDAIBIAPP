"""SQL policy at the execution boundary, with the Databricks connection replaced by a recorder.

These are unit tests: nothing here reaches a real warehouse. They show that rejected SQL is never
sent, on every route that can send SQL, for every business domain.
"""

import json
import unittest
from unittest.mock import patch

from app.config import settings
from app.domains import DOMAINS
from app.services.guardrail import GuardrailError, validate_sql
from app.services.sql_policy import enforce_select_policy
from fastapi import HTTPException
from test_ask_ai_workflow import SCHEMAS, AskAiTestCase

from app.auth import AuthUser
from app.warehouse import (
    _databricks_execute_many,
    _databricks_run,
    execute_databricks_domain_batch,
    execute_readonly,
    execute_readonly_batch,
    execute_readonly_many,
)

_CATALOGS = ",".join(f"{domain}:{domain}" for domain in DOMAINS)
_TENANTS = "tenant-a:healthcare"

ROUTES = {
    "execute_readonly": lambda sql, domain: execute_readonly(sql, domain),
    "execute_readonly_row_limit": lambda sql, domain: execute_readonly(sql, domain, None, 2000),
    "execute_readonly_many": lambda sql, domain: execute_readonly_many([(sql, {})], domain),
    "execute_readonly_batch": lambda sql, domain: execute_readonly_batch([sql], domain),
    "domain_batch": lambda sql, domain: execute_databricks_domain_batch(domain, [sql], "tenant-a" if domain == "healthcare" else None),
    "private_execute_many": lambda sql, domain: _databricks_execute_many([sql], domain),
    "private_run": lambda sql, domain: _databricks_run(domain, "gold", [sql], None, 200),
}

DANGEROUS = {
    "secret": "SELECT secret('scope', 'key') AS v FROM t LIMIT 5",
    "http_request": "SELECT http_request('GET', 'http://evil.example') AS v FROM t LIMIT 5",
    "ai_query": "SELECT ai_query('model', a) AS v FROM t LIMIT 5",
    "upper": "SELECT SECRET('scope', 'key') AS v FROM t LIMIT 5",
    "mixed_case": "SELECT Http_ReQuest('GET', 'x') AS v FROM t LIMIT 5",
    "spaces": "SELECT secret   \n\t ('scope', 'key') AS v FROM t LIMIT 5",
    "comment_before_parenthesis": "SELECT secret/* x */('scope', 'key') AS v FROM t LIMIT 5",
    "comment_after_select": "SELECT /* harmless */ ai_query('m', a) AS v FROM t -- note\nLIMIT 5",
    "backticks": "SELECT `secret`('scope', 'key') AS v FROM t LIMIT 5",
    "nested": "SELECT upper(concat('x', secret('scope', 'key'))) AS v FROM t LIMIT 5",
    "in_where": "SELECT a FROM t WHERE a = secret('scope', 'key') LIMIT 5",
    "in_case": "SELECT CASE WHEN a > 1 THEN http_request('GET', 'x') ELSE 0 END AS v FROM t LIMIT 5",
    "in_group_by": "SELECT a FROM t GROUP BY a, ai_query('m', a) LIMIT 5",
    "in_order_by": "SELECT a FROM t ORDER BY secret('s', 'k') LIMIT 5",
    "in_cte": "WITH c AS (SELECT secret('s', 'k') AS v FROM t) SELECT v FROM c LIMIT 5",
    "in_subquery": "SELECT v FROM (SELECT ai_query('m', a) AS v FROM t) x LIMIT 5",
    "in_scalar_subquery": "SELECT (SELECT http_request('GET', 'x')) AS v FROM t LIMIT 5",
    "in_union_arm": "SELECT a FROM t UNION ALL SELECT secret('s', 'k') FROM t LIMIT 5",
    "qualified": "SELECT system.ai.ai_query('m', a) AS v FROM t LIMIT 5",
    "try_secret": "SELECT try_secret('s', 'k') AS v FROM t LIMIT 5",
    "list_secrets": "SELECT * FROM list_secrets() LIMIT 5",
    "other_ai": "SELECT ai_summarize(a) AS v FROM t LIMIT 5",
    "vector_search": "SELECT * FROM vector_search(index => 'i', query => 'q') LIMIT 5",
    "reflect": "SELECT reflect('java.lang.Runtime', 'getRuntime') AS v FROM t LIMIT 5",
    "java_method": "SELECT java_method('java.lang.System', 'getenv') AS v FROM t LIMIT 5",
    "read_files": "SELECT a FROM read_files('s3://bucket/x') LIMIT 5",
    "read_table_wrapped": "SELECT a FROM TABLE(read_files('s3://bucket/x')) LIMIT 5",
    "read_kafka": "SELECT a FROM read_kafka(bootstrapServers => 'h:1') LIMIT 5",
    "file_path_table": "SELECT a FROM parquet.`s3://bucket/x` LIMIT 5",
    "dbfs_path_table": "SELECT a FROM delta.`/Volumes/c/s/v` LIMIT 5",
    "identifier_clause": "SELECT a FROM IDENTIFIER('healthcare.gold.t') LIMIT 5",
    "script_transform": "SELECT TRANSFORM(a) USING 'cat' AS (b) FROM t LIMIT 5",
    "current_user": "SELECT current_user() AS v FROM t LIMIT 5",
    "is_member": "SELECT is_account_group_member('admins') AS v FROM t LIMIT 5",
    "input_file": "SELECT input_file_name() AS v FROM t LIMIT 5",
    "catalog_udf": "SELECT banking.gold.my_function(a) AS v FROM t LIMIT 5",
}

COMMANDS = {
    "two_statements": "SELECT a FROM t LIMIT 5; DROP TABLE t",
    "two_selects": "SELECT a FROM t LIMIT 5; SELECT b FROM u LIMIT 5",
    "statement_after_comment": "SELECT a FROM t LIMIT 5 /* c */; DELETE FROM t",
    "insert": "INSERT INTO t VALUES (1)",
    "update": "UPDATE t SET a = 1",
    "delete": "DELETE FROM t",
    "merge": "MERGE INTO t USING u ON t.a = u.a WHEN MATCHED THEN DELETE",
    "drop": "DROP TABLE t",
    "create": "CREATE TABLE x AS SELECT a FROM t",
    "alter": "ALTER TABLE t ADD COLUMN b INT",
    "truncate": "TRUNCATE TABLE t",
    "grant": "GRANT SELECT ON t TO someone",
    "revoke": "REVOKE SELECT ON t FROM someone",
    "copy": "COPY INTO t FROM 's3://bucket/x'",
    "execute_immediate": "EXECUTE IMMEDIATE 'DROP TABLE t'",
    "call": "CALL do_something()",
    "set": "SET spark.sql.ansi.enabled = false",
    "use": "USE CATALOG healthcare",
    "show": "SHOW TABLES",
    "describe": "DESCRIBE t",
    "explain": "EXPLAIN SELECT a FROM t",
    "optimize": "OPTIMIZE t",
    "vacuum": "VACUUM t",
    "select_into": "SELECT a INTO b FROM t LIMIT 5",
    "empty": "   ",
    "only_comment": "-- nothing here",
    "unparseable": "SELEC a FORM t",
    "unterminated": "SELECT 'a FROM t",
}

LEGITIMATE = {
    "select": "SELECT a FROM t LIMIT 5",
    "aggregate": "SELECT COUNT(*) AS n, SUM(a) AS total, AVG(a) AS mean FROM t LIMIT 5",
    "group_order": "SELECT b, SUM(a) AS total FROM t GROUP BY b ORDER BY total DESC LIMIT 5",
    "having": "SELECT b, COUNT(*) AS n FROM t GROUP BY b HAVING COUNT(*) > 1 LIMIT 5",
    "join": "SELECT x.a, y.b FROM t x JOIN u y ON x.id = y.id LIMIT 5",
    "left_join": "SELECT x.a FROM t x LEFT JOIN u y ON x.id = y.id WHERE y.id IS NULL LIMIT 5",
    "cte": "WITH c AS (SELECT a FROM t) SELECT a FROM c LIMIT 5",
    "two_ctes": "WITH c AS (SELECT a FROM t), d AS (SELECT a FROM c) SELECT a FROM d LIMIT 5",
    "subquery": "SELECT a FROM (SELECT a FROM t) x LIMIT 5",
    "in_subquery": "SELECT a FROM t WHERE a IN (SELECT a FROM u) LIMIT 5",
    "date_filter": "SELECT a FROM t WHERE d >= DATE '2026-01-01' AND d < DATE_ADD(CURRENT_DATE(), 1) LIMIT 5",
    "date_trunc": "SELECT DATE_TRUNC('MONTH', d) AS m, SUM(a) AS s FROM t GROUP BY 1 ORDER BY 1 LIMIT 5",
    "window": "SELECT a, ROW_NUMBER() OVER (PARTITION BY b ORDER BY a DESC) AS r FROM t LIMIT 5",
    "window_sum": "SELECT a, SUM(a) OVER (ORDER BY d ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) AS s FROM t LIMIT 5",
    "union": "SELECT a FROM t UNION ALL SELECT b FROM u LIMIT 5",
    "case": "SELECT CASE WHEN a > 1 THEN 'x' ELSE 'y' END AS c FROM t LIMIT 5",
    "replace_function": "SELECT replace(a, 'x', 'y') AS r FROM t LIMIT 5",
    "word_in_string": "SELECT a FROM t WHERE status = 'update; drop' LIMIT 5",
    "secret_as_text": "SELECT a FROM t WHERE note = 'secret(1)' LIMIT 5",
    "column_named_secret": "SELECT secret FROM t LIMIT 5",
    "column_named_limit": "SELECT credit_limit FROM t LIMIT 5",
    "column_like_function": "SELECT read_count, ai_score, http_status FROM t LIMIT 5",
    "higher_order_transform": "SELECT transform(arr, x -> x + 1) AS y FROM t LIMIT 5",
    "qualified_table": "SELECT a FROM banking.gold.t LIMIT 5",
    "information_schema": "SELECT table_name FROM banking.information_schema.tables LIMIT 5",
    "current_catalog": "SELECT current_catalog() AS c, current_schema() AS s LIMIT 5",
    "parameters": "SELECT a FROM t WHERE d = :day AND b IN (:one, :two) LIMIT 5",
    "offset": "SELECT a FROM t ORDER BY a LIMIT 5 OFFSET 2",
    "trailing_semicolon": "SELECT a FROM t LIMIT 5;",
    "semicolon_then_comment": "SELECT a FROM t LIMIT 5; -- done",
    "leading_comment": "/* report */ SELECT a FROM t LIMIT 5",
    "wrapped": "(SELECT a FROM t LIMIT 5)",
}


class _Boundary(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch.multiple(
            settings,
            warehouse="databricks",
            databricks_server_hostname="host",
            databricks_http_path="/sql/path",
            databricks_token="token",
            databricks_domain_catalogs=_CATALOGS,
            databricks_domain_schema="gold",
            databricks_healthcare_tenant_catalogs=_TENANTS,
            query_row_limit=200,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        sender = patch("app.warehouse._databricks_query", side_effect=self._record)
        sender.start()
        self.addCleanup(sender.stop)
        self.sent: list[str] = []

    def _record(self, _namespace, sql, _values, _fetch_limit):
        self.sent.append(sql)
        return []


class DangerousSqlTests(_Boundary):
    def assert_refused_everywhere(self, sql: str) -> None:
        for domain in DOMAINS:
            for route_name, route in ROUTES.items():
                with self.subTest(domain=domain, route=route_name):
                    with self.assertRaises(GuardrailError):
                        route(sql, domain)
                    self.assertEqual(self.sent, [], "rejected SQL reached Databricks")

    def test_dangerous_functions_never_reach_databricks(self) -> None:
        for name, sql in DANGEROUS.items():
            with self.subTest(case=name):
                self.assert_refused_everywhere(sql)

    def test_commands_and_multiple_statements_never_reach_databricks(self) -> None:
        for name, sql in COMMANDS.items():
            with self.subTest(case=name):
                self.assert_refused_everywhere(sql)

    def test_a_rejection_does_not_repeat_the_sql(self) -> None:
        with self.assertRaises(GuardrailError) as raised:
            execute_readonly("SELECT secret('private-scope', 'private-key') AS v FROM t LIMIT 5", "retail")
        self.assertNotIn("private-key", str(raised.exception))

    def test_the_banking_policy_applies_the_same_rules(self) -> None:
        with self.assertRaises(GuardrailError):
            enforce_select_policy("SELECT secret('s', 'k') AS v FROM gold_x LIMIT 5", frozenset({"gold_x"}), "banking", "gold")


class LegitimateSqlTests(_Boundary):
    def test_analytical_queries_are_sent_unchanged_in_every_domain(self) -> None:
        for name, sql in LEGITIMATE.items():
            for domain in DOMAINS:
                with self.subTest(case=name, domain=domain):
                    self.sent.clear()
                    execute_readonly(sql, domain, {"day": "2026-01-01", "one": 1, "two": 2} if ":" in sql else None)
                    self.assertEqual(len(self.sent), 1)

    def test_every_route_sends_a_valid_query(self) -> None:
        for route_name, route in ROUTES.items():
            with self.subTest(route=route_name):
                self.sent.clear()
                route("SELECT a FROM t LIMIT 5", "retail")
                self.assertEqual(self.sent, ["SELECT a FROM t LIMIT 5"])

    def test_text_is_preserved_when_nothing_needs_changing(self) -> None:
        sql = "SELECT  a  -- keep\nFROM t WHERE b = 'x;y'\nLIMIT 5"
        self.assertEqual(validate_sql(sql), sql)


class LimitTests(_Boundary):
    def sent_for(self, sql: str, row_limit: int | None = None) -> str:
        self.sent.clear()
        execute_readonly(sql, "retail", None, row_limit)
        self.assertEqual(len(self.sent), 1)
        return self.sent[0]

    def assert_refused(self, sql: str) -> None:
        self.sent.clear()
        with self.assertRaises(GuardrailError):
            execute_readonly(sql, "retail")
        self.assertEqual(self.sent, [])

    # The reported defect: a word containing "limit" counted as a LIMIT clause.
    def test_a_column_containing_the_word_limit_still_gets_a_limit(self) -> None:
        self.assertTrue(self.sent_for("SELECT credit_limit FROM t").endswith("LIMIT 200"))

    def test_a_string_or_comment_mentioning_limit_still_gets_a_limit(self) -> None:
        self.assertTrue(self.sent_for("SELECT a FROM t WHERE b = 'limit 1'").endswith("LIMIT 200"))
        self.assertTrue(self.sent_for("SELECT a FROM t /* LIMIT 5 */").endswith("LIMIT 200"))
        self.assertTrue(self.sent_for("SELECT a FROM t -- LIMIT 5").endswith("LIMIT 200"))

    def test_a_limit_only_inside_a_subquery_or_cte_does_not_bound_the_result(self) -> None:
        self.assertTrue(self.sent_for("SELECT a FROM (SELECT a FROM t LIMIT 3) x").endswith("LIMIT 200"))
        self.assertTrue(self.sent_for("WITH c AS (SELECT a FROM t LIMIT 3) SELECT a FROM c").endswith("LIMIT 200"))
        self.assertTrue(self.sent_for("(SELECT a FROM t LIMIT 3) UNION ALL (SELECT b FROM u LIMIT 4)").endswith("LIMIT 200"))

    def test_a_missing_limit_uses_the_configured_row_limit(self) -> None:
        with patch.object(settings, "query_row_limit", 17):
            self.assertTrue(self.sent_for("SELECT a FROM t").endswith("LIMIT 17"))

    def test_valid_limits_are_kept(self) -> None:
        for value in (1, 10, 100, 200):
            with self.subTest(value=value):
                self.assertEqual(self.sent_for(f"SELECT a FROM t LIMIT {value}"), f"SELECT a FROM t LIMIT {value}")

    def test_a_limit_above_the_effective_maximum_is_reduced(self) -> None:
        self.assertEqual(self.sent_for("SELECT a FROM t LIMIT 99999999"), "SELECT a FROM t LIMIT 200")
        self.assertEqual(self.sent_for("SELECT a FROM t LIMIT 201"), "SELECT a FROM t LIMIT 200")
        self.assertEqual(self.sent_for("SELECT a FROM t LIMIT 99999999999999999999999"), "SELECT a FROM t LIMIT 200")

    def test_a_caller_row_limit_sets_the_maximum_up_to_the_ceiling(self) -> None:
        self.assertEqual(self.sent_for("SELECT a FROM t LIMIT 1500", 2000), "SELECT a FROM t LIMIT 1500")
        self.assertEqual(self.sent_for("SELECT a FROM t LIMIT 5000", 2000), "SELECT a FROM t LIMIT 2000")
        self.assertEqual(self.sent_for("SELECT a FROM t LIMIT 5000", 999999), "SELECT a FROM t LIMIT 2000")
        self.assertEqual(self.sent_for("SELECT a FROM t LIMIT 50", 10), "SELECT a FROM t LIMIT 10")

    def test_a_misconfigured_default_cannot_exceed_the_ceiling(self) -> None:
        with patch.object(settings, "query_row_limit", 10**9):
            self.assertEqual(self.sent_for("SELECT a FROM t LIMIT 5000"), "SELECT a FROM t LIMIT 2000")

    def test_only_the_outer_limit_value_is_changed(self) -> None:
        self.assertEqual(
            self.sent_for("SELECT a FROM (SELECT a FROM t LIMIT 9) x LIMIT 900"),
            "SELECT a FROM (SELECT a FROM t LIMIT 9) x LIMIT 200",
        )

    def test_limit_with_offset_keeps_the_offset(self) -> None:
        self.assertEqual(self.sent_for("SELECT a FROM t ORDER BY a LIMIT 900 OFFSET 7"), "SELECT a FROM t ORDER BY a LIMIT 200 OFFSET 7")

    def test_limit_with_union_applies_to_the_whole_result(self) -> None:
        self.assertEqual(self.sent_for("SELECT a FROM t UNION ALL SELECT b FROM u LIMIT 900"), "SELECT a FROM t UNION ALL SELECT b FROM u LIMIT 200")
        self.assertTrue(self.sent_for("SELECT a FROM t UNION ALL SELECT b FROM u").endswith("LIMIT 200"))

    def test_limit_with_comments_and_whitespace(self) -> None:
        self.assertEqual(self.sent_for("SELECT a FROM t limit\n\t 900"), "SELECT a FROM t limit\n\t 200")
        self.assertEqual(self.sent_for("SELECT a FROM t LIMIT /* c */ 900"), "SELECT a FROM t LIMIT /* c */ 200")
        self.assertEqual(self.sent_for("SELECT a FROM t LIMIT 5 -- trailing"), "SELECT a FROM t LIMIT 5")

    def test_a_trailing_comment_cannot_swallow_the_added_limit(self) -> None:
        self.assertEqual(self.sent_for("SELECT a FROM t -- note"), "SELECT a FROM t\nLIMIT 200")
        self.assertEqual(self.sent_for("SELECT a FROM t; -- note"), "SELECT a FROM t\nLIMIT 200")

    def test_invalid_limits_are_refused(self) -> None:
        for sql in (
            "SELECT a FROM t LIMIT 0",
            "SELECT a FROM t LIMIT -1",
            "SELECT a FROM t LIMIT 1.5",
            "SELECT a FROM t LIMIT abc",
            "SELECT a FROM t LIMIT 'ten'",
            "SELECT a FROM t LIMIT 1+1",
            "SELECT a FROM t LIMIT (SELECT 1)",
            "SELECT a FROM t LIMIT ALL",
            "SELECT a FROM t LIMIT NULL",
            "SELECT a FROM t LIMIT 5, 10",
            "SELECT a FROM t LIMIT 5 LIMIT 6",
            "SELECT a FROM t LIMIT",
            "SELECT a FROM t LIMIT :n",
            "SELECT a FROM t FETCH FIRST 5 ROWS ONLY",
        ):
            with self.subTest(sql=sql):
                self.assert_refused(sql)

    def test_a_column_called_limit_is_not_mistaken_for_the_clause(self) -> None:
        self.assertEqual(self.sent_for("SELECT `limit` FROM t"), "SELECT `limit` FROM t\nLIMIT 200")
        self.assertEqual(self.sent_for("SELECT `limit` FROM t LIMIT 900"), "SELECT `limit` FROM t LIMIT 200")

    def test_invalid_nested_limits_are_refused(self) -> None:
        for sql in (
            "SELECT a FROM (SELECT a FROM t LIMIT -1) x LIMIT 5",
            "WITH c AS (SELECT a FROM t LIMIT abc) SELECT a FROM c LIMIT 5",
            "SELECT a FROM t UNION ALL (SELECT b FROM u LIMIT 0) LIMIT 5",
        ):
            with self.subTest(sql=sql):
                self.assert_refused(sql)

    def test_injected_limit_expressions_are_refused(self) -> None:
        for sql in (
            "SELECT a FROM t LIMIT 5; DROP TABLE t",
            "SELECT a FROM t LIMIT 5; SELECT secret('s', 'k')",
            "SELECT a FROM t LIMIT (SELECT secret('s', 'k'))",
            "SELECT a FROM t LIMIT 5 -- ; DELETE FROM t",
        ):
            with self.subTest(sql=sql):
                self.assert_refused(sql)

    def test_the_limit_is_enforced_at_the_last_step_for_every_route(self) -> None:
        for route_name, route in ROUTES.items():
            with self.subTest(route=route_name):
                self.sent.clear()
                route("SELECT credit_limit FROM t LIMIT 99999", "retail")
                self.assertEqual(len(self.sent), 1)
                self.assertTrue(self.sent[0].endswith(("LIMIT 200", "LIMIT 2000")), self.sent[0])

    def test_the_private_boundary_applies_its_own_fetch_limit(self) -> None:
        _databricks_run("retail", "gold", ["SELECT a FROM t LIMIT 900"], None, 25)
        self.assertEqual(self.sent, ["SELECT a FROM t LIMIT 25"])


class GeneratedSqlPathTests(AskAiTestCase):
    """Model-written SQL through the real orchestrator; only the model and connection are replaced."""

    def setUp(self) -> None:
        super().setUp()
        sender = patch("app.warehouse._databricks_query", side_effect=self._record)
        sender.start()
        self.addCleanup(sender.stop)
        self.sent: list[str] = []

    def _record(self, _namespace, sql, _values, _fetch_limit):
        self.sent.append(sql)
        return [{"total": 5}]

    def ask_with_model_sql(self, sql: str, domain: str):
        self.llm_sql.append(json.dumps({"sql": sql}))
        self.llm_text.append("The total is 5.")
        user = AuthUser(username=f"{domain}.user", domain=domain, role="analyst", tenant_id="tenant-a" if domain == "healthcare" else None)
        return self.orchestrator.run(self.db, "What is the total?", user)

    def test_a_dangerous_function_from_the_model_is_not_run_in_any_domain(self) -> None:
        # The fixture has no healthcare schema; healthcare is covered at the boundary above.
        for domain in SCHEMAS:
            table = next(iter(SCHEMAS[domain]))
            for function in ("secret('s', 'k')", "http_request('GET', 'x')", "ai_query('m', 'p')"):
                with self.subTest(domain=domain, function=function):
                    self.sent.clear()
                    try:
                        response = self.ask_with_model_sql(f"SELECT {function} AS total FROM {table} LIMIT 5", domain)
                    except HTTPException as exc:
                        self.assertIn(exc.status_code, {400, 403, 422})
                    else:
                        self.assertNotIn(response.status, {"complete"})
                    self.assertEqual(self.sent, [])

    def test_the_same_harness_does_send_a_safe_query(self) -> None:
        # Without this, "nothing was sent" above could simply mean the recorder was never reached.
        table = next(iter(SCHEMAS["retail"]))
        response = self.ask_with_model_sql(f"SELECT COUNT(*) AS total FROM {table} LIMIT 5", "retail")
        self.assertEqual(response.status, "complete", response.answer)
        self.assertEqual(len(self.sent), 1)

    def test_a_model_query_without_a_limit_is_bounded_when_sent(self) -> None:
        table = next(iter(SCHEMAS["retail"]))
        self.ask_with_model_sql(f"SELECT COUNT(*) AS total FROM {table}", "retail")
        self.assertEqual(len(self.sent), 1)
        self.assertRegex(self.sent[0], r"LIMIT \d+$")


if __name__ == "__main__":
    unittest.main()
