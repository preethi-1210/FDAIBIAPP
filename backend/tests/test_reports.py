from datetime import date
from unittest import TestCase
from unittest.mock import patch

from app.auth import AuthUser
from app.main import fastapi_app, get_current_user, reports
from app.schemas import ReportResponse
from app.services.reports import comparison_dates, get_report
from app.warehouse import _pyformat_parameters


def _many(execute):
    """Adapt a per-query fake to the batched execute_readonly_many interface."""
    def run(statements, domain=None, row_limit=None):
        return [execute(sql, domain, parameters, row_limit) for sql, parameters in statements]
    return run


class ReportValidationTests(TestCase):
    def test_comparison_periods_are_derived_and_bounded(self) -> None:
        self.assertEqual(
            comparison_dates(date(2026, 10, 1), date(2026, 10, 31), "previous_month"),
            (date(2026, 9, 1), date(2026, 9, 30)),
        )
        with self.assertRaisesRegex(ValueError, "may not exceed"):
            comparison_dates(date(2025, 1, 1), date(2026, 1, 2), "previous_period")

    def test_custom_comparison_requires_a_prior_valid_period(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires"):
            comparison_dates(date(2026, 10, 1), date(2026, 10, 31), "custom")
        with self.assertRaisesRegex(ValueError, "end before"):
            comparison_dates(
                date(2026, 10, 1),
                date(2026, 10, 31),
                "custom",
                date(2026, 9, 1),
                date(2026, 10, 1),
            )
        with self.assertRaisesRegex(ValueError, "only valid"):
            comparison_dates(
                date(2026, 10, 1),
                date(2026, 10, 31),
                "previous_month",
                date(2026, 9, 1),
                None,
            )

    def test_finance_unavailable_source_does_not_invent_metrics_or_accept_filters(self) -> None:
        with patch("app.services.reports.settings.warehouse", "local"):
            result = get_report(
                "finance", None, date(2026, 10, 1), date(2026, 10, 31),
                "monthly", "previous_period",
            )
            self.assertEqual(result["metrics"], [])
            self.assertEqual(result["categories"], [])
            with self.assertRaisesRegex(ValueError, "not supported"):
                get_report(
                    "finance", None, date(2026, 10, 1), date(2026, 10, 31),
                    "monthly", "previous_period", category="Checking",
                )

    def test_unverified_remote_insurance_claim_date_is_not_reported(self) -> None:
        with patch("app.services.reports.settings.warehouse", "snowflake"):
            result = get_report(
                "insurance", None, date(2026, 10, 1), date(2026, 10, 31),
                "monthly", "previous_period",
            )
        self.assertEqual(result["metrics"], [])
        self.assertEqual(result["trend"], [])

    def test_finance_daily_and_weekly_reports_use_additive_daily_account_aggregates(self) -> None:
        calls = []

        def execute(sql, domain=None, parameters=None, row_limit=None):
            calls.append(sql)
            if " AS first_date" in sql:
                return [{"first_date": date(2010, 1, 2), "last_date": date(2026, 6, 6)}]
            return [
                {"event_date": date(2026, 6, 2), "amount_value": 100.0, "count_value": 1},
                {"event_date": date(2026, 6, 3), "amount_value": 50.0, "count_value": 2},
            ]

        for interval in ("daily", "weekly", "monthly"):
            calls.clear()
            with (
                patch("app.services.reports.settings.warehouse", "databricks"),
                patch("app.services.reports.settings.databricks_domain_schema", "gold"),
                patch("app.services.reports.databricks_domain_catalog", return_value="banking"),
                patch("app.services.reports.execute_readonly_many", side_effect=_many(execute)),
            ):
                result = get_report(
                    "finance", None, date(2026, 6, 1), date(2026, 6, 7), interval, "previous_period",
                )
            self.assertTrue(all("`banking`.`gold`.`agg_accounts_daily`" in sql for sql in calls))
            # Accounts opened on different days add up; no snapshot "latest value" is used.
            self.assertEqual(result["metrics"][0]["current"], 150.0)
            self.assertEqual(result["metrics"][1]["current"], 3.0)


class ReportQueryTests(TestCase):
    def test_databricks_banking_uses_gold_transactions_and_returns_source_dates(self) -> None:
        calls = []

        def execute(sql, domain=None, parameters=None, row_limit=None):
            calls.append((sql, domain, parameters))
            if "MIN(" in sql and "MAX(" in sql:
                return [{"first_date": date(2025, 7, 2), "last_date": date(2026, 7, 1)}]
            if "AS category" in sql:
                return [{
                    "event_date": date(2026, 6, 30),
                    "category": "ATM",
                    "region": "Karnataka",
                    "amount_value": 2400.0,
                    "count_value": 2,
                }]
            return [{
                "event_date": date(2026, 6, 30),
                "amount_value": 2400.0,
                "count_value": 2,
            }]

        with (
            patch("app.services.reports.settings.warehouse", "databricks"),
            patch("app.services.reports.settings.databricks_domain_schema", "gold"),
            patch("app.services.reports.databricks_domain_catalog", return_value="banking"),
            patch("app.services.reports.execute_readonly_many", side_effect=_many(execute)),
        ):
            result = get_report(
                "banking",
                None,
                date(2026, 6, 29),
                date(2026, 7, 1),
                "daily",
                "previous_period",
            )

        self.assertEqual(len(calls), 6)
        for sql, domain, parameters in calls:
            self.assertEqual(domain, "banking")
            self.assertIn("`banking`.`gold`.`gold_transactions`", sql)
            self.assertIn("t.txn_timestamp", sql)
        self.assertTrue(any("start_date" in parameters for _, _, parameters in calls))
        self.assertEqual(result["availability"], {"start": "2025-07-02", "end": "2026-07-01"})
        self.assertEqual(result["metrics"][0]["current"], 2400.0)
        self.assertEqual(result["metrics"][1]["current"], 2.0)
        self.assertEqual(result["categories"], [{"label": "ATM", "value": 2400.0}])
        self.assertEqual(result["regions"], [{"label": "Karnataka", "value": 2400.0}])

    def test_remote_driver_bind_markers_are_deterministic(self) -> None:
        sql, values = _pyformat_parameters(
            "SELECT * FROM source WHERE event_date >= :start_date AND category = :category "
            "AND region = :region AND category <> :category",
            {
                "start_date": date(2026, 10, 1),
                "category": "Food",
                "region": "West",
            },
        )
        self.assertEqual(sql.count("%s"), 4)
        self.assertEqual(values, (date(2026, 10, 1), "Food", "West", "Food"))

    def test_date_and_filter_values_are_passed_as_parameters_and_response_is_aggregated(self) -> None:
        calls = []

        def execute(sql, domain=None, parameters=None, row_limit=None):
            calls.append((sql, domain, parameters))
            if " AS first_date" in sql:
                return [{
                    "first_date": date(2025, 7, 2),
                    "last_date": date(2026, 7, 1),
                }]
            event_date = parameters["start_date"]
            if " AS category" in sql:
                return [{
                    "event_date": event_date,
                    "category": "Food",
                    "region": "West",
                    "amount_value": 75.0,
                    "count_value": 3,
                }]
            return [{
                "event_date": event_date,
                "amount_value": 75.0,
                "count_value": 3,
            }]

        filter_value = "Food' OR 1=1 --"
        with patch("app.services.reports.settings.warehouse", "local"), patch(
            "app.services.reports.execute_readonly_many", side_effect=_many(execute)
        ):
            result = get_report(
                "retail",
                None,
                date(2026, 10, 1),
                date(2026, 10, 7),
                "weekly",
                "previous_period",
                category=filter_value,
            )

        self.assertEqual(len(calls), 6)
        self.assertTrue(all(domain == "retail" for _, domain, _ in calls))
        self.assertTrue(all(filter_value not in sql for sql, _, _ in calls))
        filtered_calls = [parameters for _, _, parameters in calls if "category" in parameters]
        self.assertEqual(len(filtered_calls), 4)
        self.assertTrue(all(parameters["category"] == filter_value for parameters in filtered_calls))
        self.assertEqual(result["metrics"][0]["current"], 75.0)
        self.assertEqual(result["metrics"][0]["comparison"], 75.0)
        self.assertIsNone(result["metrics"][0]["higher_is_better"])
        self.assertEqual(result["trend"][0]["value"], 75.0)
        self.assertEqual(result["metrics"][0]["trend"][0]["value"], 75.0)
        self.assertEqual(result["metrics"][1]["trend"][0]["value"], 3.0)
        self.assertEqual(result["rows"][0]["metric"], "revenue")
        self.assertEqual(result["filter_options"]["categories"], ["Food"])
        response = ReportResponse.model_validate(result)
        self.assertEqual(
            set(response.model_dump()),
            {
                "domain", "domain_label", "period", "availability", "metrics", "trend", "categories",
                "regions", "rows", "filter_options", "updated_at",
            },
        )

    def test_healthcare_report_applies_server_tenant_scope_and_bound_dates(self) -> None:
        for warehouse in ("local", "databricks"):
            executed = []

            def execute(sql, domain=None, parameters=None, row_limit=None):
                executed.append((sql, domain, parameters))
                return []

            with patch("app.services.reports.settings.warehouse", warehouse), patch(
                "app.services.reports.execute_readonly_many", side_effect=_many(execute)
            ):
                get_report(
                    "healthcare",
                    "tenant-a",
                    date(2026, 10, 1),
                    date(2026, 10, 7),
                    "daily",
                    "previous_period",
                )

            self.assertEqual(len(executed), 6)
            for sql, domain, parameters in executed:
                self.assertEqual(domain, "healthcare")
                self.assertIn("tenant-a", sql)
                if " AS first_date" not in sql:
                    self.assertIn(":start_date", sql)
                    self.assertIn(":end_exclusive", sql)
                self.assertNotIn("tenant-a", str(parameters))

    def test_endpoint_uses_only_the_authenticated_domain_and_tenant(self) -> None:
        authenticated = AuthUser("health.user", "healthcare", "analyst", tenant_id="tenant-a")
        with patch("app.main.get_report", return_value={"domain": "healthcare"}) as service:
            response = reports(
                start_date=date(2026, 10, 1),
                end_date=date(2026, 10, 7),
                interval="daily",
                comparison="previous_period",
                user=authenticated,
            )
        self.assertEqual(response["domain"], "healthcare")
        self.assertEqual(service.call_args.kwargs["domain"], "healthcare")
        self.assertEqual(service.call_args.kwargs["tenant_id"], "tenant-a")
        self.assertNotIn("domain", reports.__annotations__)
        route = next(
            route for route in fastapi_app.routes
            if getattr(route, "path", None) == "/reports"
        )
        self.assertTrue(
            any(dependency.call is get_current_user for dependency in route.dependant.dependencies)
        )
