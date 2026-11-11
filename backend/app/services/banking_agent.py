"""LangGraph workflow for banking questions: understand, then measure or forecast, verify, explain.

Workflow code decides each step. A language model can only propose a query plan, which passes the same
validation as a rule-based one, and it never writes the answer: every figure in an answer was computed
from rows this workflow fetched and verified.
"""

import logging
import re
import statistics
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, TypedDict

from fastapi import HTTPException
from langgraph.graph import END, START, StateGraph

from ..auth import AuthUser
from ..cache import LocalTTLCache
from ..config import settings
from ..llm import record_step
from ..schemas import PredictionPayload, UncertaintyPayload
from ..sensitive_data import require_compiled_sql_safe, sanitize_sensitive_rows
from .banking_query import (
    PROMPT_VERSION,
    CompiledQuery,
    ParseResult,
    Period,
    PlanError,
    QueryPlan,
    clarification_for_terms,
    compile_plan,
    parse_question,
    plan_with_llm,
    validate_plan,
)
from .banking_registry import BANKING, DomainRegistry, Measure, Metric, Source, active_registry, metric_by_id, use_registry
from .forecasting import InsufficientHistoryError, assess_history, forecast_series
from .guardrail import GuardrailError
from .sql_policy import USER_MESSAGES, classify_sql_error, enforce_select_policy

logger = logging.getLogger(__name__)
_coverage_cache = LocalTTLCache(max_entries=32)
_COVERAGE_SECONDS = 300
# Words that make a question worth a model's time when rules found no metric.
_DATA_WORDS = re.compile(
    r"\b(?:money|amount|spent|spend|spending|paid|moved|transactions?|txns?|accounts?|loans?|cards?|customers?|balances?|emis?|"
    r"repayments?|instal?lments?|merchants?|channels?|upi|atm|neft|imps|pos|debits?|credits?|deposits?|withdrawals?|branch(?:es)?|disburs\w+)\b",
    re.IGNORECASE,
)
ANOMALY_Z = 3.5
ANOMALY_MIN_PERIODS = 12
HIGH_VALUE_AMOUNT = 100000


class WarehouseFailure(Exception):
    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(detail)
        self.kind = kind


@dataclass
class AgentResult:
    answer: str
    status: str
    plan: QueryPlan | None = None
    sql: str | None = None
    table: list[dict[str, Any]] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    guardrail_ok: bool = True
    prediction: PredictionPayload | None = None
    uncertainty: UncertaintyPayload | None = None
    # A forecast is ready but its release requires an administrator.
    needs_review: bool = False
    route: str = "metric"


class AgentState(TypedDict, total=False):
    question: str
    user: AuthUser
    previous: QueryPlan | None
    provider: str | None
    approved: bool
    parsed: ParseResult
    plan: QueryPlan
    result: AgentResult | None


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _format(value: float | None, unit: str) -> str:
    if value is None:
        return "not available"
    if unit == "inr":
        return f"INR {value:,.2f}"
    if unit == "amount":  # A money figure whose currency the source does not record.
        return f"{value:,.2f}"
    if unit == "percent":
        return f"{value:,.2f}%"
    return f"{value:,.0f}"


def _describe_measures(metric: Metric, row: dict[str, Any]) -> str:
    return ", ".join(f"{_format(_number(row.get(m.alias)), m.unit)} {m.label}" if m.unit == "count" else f"{m.label} {_format(_number(row.get(m.alias)), m.unit)}" for m in metric.measures)


def _filter_text(plan: QueryPlan) -> str:
    if not plan.filters:
        return ""
    parts = [f"{plan.metric.source.dimension(key).label} {' or '.join(values)}" for key, values in sorted(plan.filters.items())]
    return f" ({'; '.join(parts)})"


class BankingAgent:
    def __init__(self, execute: Callable[..., list[dict[str, Any]]], registry: DomainRegistry | None = None, scope: str = "") -> None:
        self._execute = execute
        self._registry = registry or BANKING
        # Separates cached date coverage by tenant when one domain spans several catalogs.
        self._scope = scope
        workflow = StateGraph(AgentState)
        workflow.add_node("understand", self._understand)
        workflow.add_node("measure", self._measure)
        workflow.add_node("forecast", self._forecast)
        workflow.add_node("anomaly", self._anomaly)
        workflow.add_edge(START, "understand")
        workflow.add_conditional_edges(
            "understand",
            self._next_step,
            {"measure": "measure", "forecast": "forecast", "anomaly": "anomaly", "done": END},
        )
        for node in ("measure", "forecast", "anomaly"):
            workflow.add_edge(node, END)
        self._workflow = workflow.compile()

    def run(
        self,
        question: str,
        user: AuthUser,
        previous: QueryPlan | None = None,
        provider: str | None = None,
        approved: bool = False,
    ) -> AgentResult | None:
        """The answer, or None when this is not a question the banking workflow handles."""
        with use_registry(self._registry):
            state = self._workflow.invoke({"question": question, "user": user, "previous": previous, "provider": provider, "approved": approved})
        return state.get("result")

    @staticmethod
    def _next_step(state: AgentState) -> str:
        if state.get("result") is not None or state.get("plan") is None:
            return "done"
        intent = state["plan"].intent
        return intent if intent in {"forecast", "anomaly"} else "measure"

    def _understand(self, state: AgentState) -> AgentState:
        question, previous = state["question"], state.get("previous")
        registry = active_registry()
        parsed = parse_question(question, previous)
        named_topic = any(pattern.search(question) for pattern, _reason in registry.unsupported_topics)
        if registry.fallthrough and not parsed.plan and not named_topic:
            # Not represented cleanly: the existing path handles it exactly as before.
            return {}
        if parsed.refusal:
            record_step("router", f"{registry.label} metric registry", detail="Not supported by approved data")
            return {"result": AgentResult(parsed.refusal, "insufficient_data", evidence=_registry_evidence())}
        if parsed.clarification:
            return {"result": AgentResult(parsed.clarification, "needs_clarification", evidence=_registry_evidence())}
        plan = parsed.plan
        if plan is None:
            if not parsed.unknown_terms and not _DATA_WORDS.search(question):
                return {}
            plan, refusal = self._plan_with_model(question, parsed, previous, state.get("provider"), state["user"].role)
            if refusal:
                return {"result": AgentResult(refusal, "needs_clarification", evidence=_registry_evidence())}
            if plan is None:
                return {}
        try:
            validate_plan(plan, state["user"].role)
        except PlanError as exc:
            if registry.fallthrough:
                return {}
            record_step("router", f"{registry.label} metric registry", ok=False, detail=str(exc)[:200])
            return {"result": AgentResult(str(exc), "insufficient_data", plan=plan, evidence=_registry_evidence())}
        record_step("router", f"{registry.domain} {plan.intent} ({plan.interpreted_by})", detail=plan.describe()[:200])
        return {"plan": plan}

    @staticmethod
    def _plan_with_model(question: str, parsed: ParseResult, previous: QueryPlan | None, provider: str | None, role: str) -> tuple[QueryPlan | None, str | None]:
        """(plan, None), (None, clarification), or (None, None) to leave the question to SQL generation."""
        terms = parsed.unknown_terms
        hint = previous.metric if previous else None
        if not settings.banking_llm_planning:
            return None, clarification_for_terms(terms, hint) if terms else None
        try:
            plan, unsupported = plan_with_llm(question, parsed, previous, provider)
        except Exception as exc:  # Every provider failed; never guess.
            logger.warning("Banking plan generation failed: %s", type(exc).__name__)
            return None, clarification_for_terms(terms, hint) if terms else None
        if unsupported:
            return None, clarification_for_terms(unsupported, plan.metric if plan else hint)
        if plan is None:
            return None, clarification_for_terms(terms, hint) if terms else None
        try:
            validate_plan(plan, role)
        except PlanError as exc:
            # The model's mistake, not the user's: its wording must not reach them as an explanation.
            record_step("planner", "plan validation", ok=False, detail=str(exc)[:200])
            if terms:
                return None, clarification_for_terms(terms, plan.metric if metric_by_id(plan.metric_id) else hint)
            registry = active_registry()
            return None, f"I could not match this question to approved {registry.label} data. Approved {registry.label} measures are: {registry.supported_summary()}. Which one do you mean?"
        if terms:
            # Rules found words they could not place. Only a filter value the model added can account
            # for them; a breakdown or a grain answers a different question and drops the restriction.
            detected = parsed.detected.get("filters") or {}
            if not any(values != detected.get(key) for key, values in plan.filters.items()):
                return None, clarification_for_terms(terms, plan.metric)
        return plan, None

    def _run_sql(self, compiled: CompiledQuery, role: str) -> list[dict[str, Any]]:
        registry = active_registry()
        catalog, schema = registry.catalog_schema.split(".")
        try:
            enforce_select_policy(compiled.sql, registry.approved_tables, catalog, schema)
        except GuardrailError as exc:
            record_step("sql_validation", "SQL policy", ok=False, detail=str(exc)[:200])
            raise WarehouseFailure("policy", str(exc)) from exc
        require_compiled_sql_safe(compiled.sql)
        record_step("sql_validation", "parsed SQL policy: read-only, approved tables, bounded, no identifiers returned")
        started = time.perf_counter()
        try:
            rows = self._execute(compiled.sql, registry.domain, compiled.parameters or None, compiled.limit)
        except GuardrailError as exc:
            kind = classify_sql_error(str(exc))
            record_step("sql_execution", registry.catalog_schema, ok=False, detail=kind)
            raise WarehouseFailure(kind, str(exc)) from exc
        record_step("sql_execution", registry.catalog_schema, detail=f"{len(rows)} row(s)", latency_ms=(time.perf_counter() - started) * 1000)
        return sanitize_sensitive_rows(rows, role)

    def _coverage(self, source: Source) -> dict[str, Any]:
        """Date range and distinct dates actually present, so a period can be judged before querying."""
        key = f"{active_registry().domain}:{self._scope}:{source.id}"
        cached = _coverage_cache.get(key)
        if cached is not None:
            return cached
        sql = (
            f"SELECT MIN({source.date_expression}) AS first_date, MAX({source.date_expression}) AS last_date, "
            f"COUNT(DISTINCT {source.date_expression}) AS distinct_dates FROM {source.from_clause} LIMIT 1"
        )
        rows = self._run_sql(CompiledQuery(sql, {}, (), (), 1), "admin")
        row = rows[0] if rows else {}
        coverage = {
            "first_date": str(row.get("first_date"))[:10] if row.get("first_date") else None,
            "last_date": str(row.get("last_date"))[:10] if row.get("last_date") else None,
            "distinct_dates": int(_number(row.get("distinct_dates")) or 0),
        }
        _coverage_cache.set(key, coverage, _COVERAGE_SECONDS)
        return coverage

    def _measure(self, state: AgentState) -> AgentState:
        plan, role = state["plan"], state["user"].role
        try:
            coverage = self._coverage(plan.metric.source)
            if plan.intent == "compare":
                return {"result": self._compare(plan, role, coverage)}
            outside = _outside_coverage(plan.period, coverage)
            if outside:
                return {"result": _no_data(plan, coverage, outside, compile_plan(plan).sql)}
            return {"result": self._measure_rows(plan, role, coverage)}
        except WarehouseFailure as exc:
            return {"result": _failure(plan, exc)}

    def _measure_rows(self, plan: QueryPlan, role: str, coverage: dict[str, Any]) -> AgentResult:
        metric = plan.metric
        compiled = compile_plan(plan)
        rows = self._run_sql(compiled, role)
        total_records = _record_total(rows)
        if not rows or total_records == 0:
            return _no_data(plan, coverage, f"No records match {plan.period.label if plan.period else 'this request'}.", compiled.sql)
        notes = _verify_shape(plan, compiled, rows)
        if notes:
            record_step("result_validation", "shape and range checks", ok=False, detail=notes[0][:200])
            return AgentResult("The query result failed validation, so no figure is reported.", "error", plan=plan, sql=compiled.sql, error=notes[0], evidence=_evidence(plan, coverage, rows))
        reconciliation = None
        if compiled.dimension_columns and not plan.top_n:
            reconciliation = self._reconcile(plan, role, rows)
            if reconciliation and not reconciliation["matches"]:
                record_step("result_validation", "breakdown reconciled to total", ok=False, detail=str(reconciliation))
                return AgentResult("The breakdown does not add up to the total, so no figure is reported.", "error", plan=plan, sql=compiled.sql, error="reconciliation failed", evidence=_evidence(plan, coverage, rows))
        record_step("result_validation", "shape, range and reconciliation checks", detail=f"{len(rows)} row(s) verified")
        evidence = _evidence(plan, coverage, rows, reconciliation)
        if plan.intent == "trend":
            return _trend_answer(plan, rows, coverage, compiled.sql, evidence)
        if plan.dimensions:
            return _breakdown_answer(plan, rows, coverage, compiled.sql, evidence)
        answer = f"{metric.label}{_filter_text(plan)} for {_period_label(plan.period)}: {_describe_measures(metric, rows[0])}."
        return AgentResult(_with_basis(answer, plan, coverage), "complete", plan=plan, sql=compiled.sql, table=rows, evidence=evidence)

    def _reconcile(self, plan: QueryPlan, role: str, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Rows in a breakdown must add up to the same question asked without it."""
        if len(rows) >= compile_plan(plan).limit:
            return None
        total_rows = self._run_sql(compile_plan(plan, totals_only=True), role)
        expected = int(_number(total_rows[0].get("record_count")) or 0) if total_rows else 0
        actual = _record_total(rows)
        return {"breakdown_records": actual, "total_records": expected, "matches": actual == expected}

    def _compare(self, plan: QueryPlan, role: str, coverage: dict[str, Any]) -> AgentResult:
        metric = plan.metric
        current, baseline = plan.period, plan.compare_to
        if plan.relative_compare:
            current, baseline, reason = _relative_periods(plan.relative_compare, plan.period, coverage)
            if reason:
                return AgentResult(_with_basis(reason, plan, coverage), "insufficient_history", plan=plan, evidence=_evidence(plan, coverage, []), route="metric")
        queries = [compile_plan(plan, current), compile_plan(plan, baseline)]
        measured = [self._run_sql(query, role) for query in queries]
        sql = "\n\n".join(query.sql for query in queries)
        rows = []
        for period, result in zip((current, baseline), measured):
            row = result[0] if result else {}
            rows.append({"period": period.label, **{m.alias: row.get(m.alias) for m in metric.measures}, "record_count": row.get("record_count")})
        missing = [row["period"] for row in rows if not _number(row["record_count"])]
        if missing:
            answer = f"{metric.label} cannot be compared: there are no records for {' or '.join(missing)}."
            return AgentResult(_with_basis(answer, plan, coverage), "no_data", plan=plan, sql=sql, table=rows, evidence=_evidence(plan, coverage, rows))
        record_step("result_validation", "comparison checks", detail="both periods have records")
        primary: Measure = metric.primary
        now, then = _number(rows[0][primary.alias]), _number(rows[1][primary.alias])
        change = None if now is None or then is None else now - then
        if change is None:
            change_text = "The change cannot be calculated."
        elif primary.unit == "percent":
            change_text = f"The change is {change:+,.2f} percentage points."
        else:
            percent = f" ({change / then * 100:+,.2f}%)" if then else ""
            change_text = f"The change is {_signed(change, primary.unit)}{percent}."
        answer = (
            f"{metric.label}{_filter_text(plan)}: {_describe_measures(metric, rows[0])} in {rows[0]['period']}, "
            f"against {_describe_measures(metric, rows[1])} in {rows[1]['period']}. {change_text}"
        )
        evidence = _evidence(plan, coverage, rows)
        evidence["comparison"] = {"current": current.to_dict(), "baseline": baseline.to_dict(), "change": change}
        return AgentResult(_with_basis(answer, plan, coverage), "complete", plan=plan, sql=sql, table=rows, evidence=evidence)

    def _forecast(self, state: AgentState) -> AgentState:
        plan, user = state["plan"], state["user"]
        metric = plan.metric
        history_plan = QueryPlan(metric_id=plan.metric_id, intent="trend", filters=plan.filters, grain="month")
        compiled = compile_plan(history_plan)
        try:
            coverage = self._coverage(metric.source)
            rows = self._run_sql(compiled, user.role)
        except WarehouseFailure as exc:
            return {"result": _failure(plan, exc, route="prediction")}
        evidence = _evidence(plan, coverage, rows)
        evidence["kind"] = "forecast"
        evidence["observed_rows_are_actual_data"] = True
        try:
            history = assess_history(rows, metric.primary.alias, coverage.get("last_date"), _today())
        except InsufficientHistoryError as exc:
            record_step("prediction", "history assessment", ok=False, detail=str(exc)[:200])
            evidence["history_assessment"] = exc.assessment
            answer = f"No forecast for {metric.label.lower()}{_filter_text(plan)}: {exc} Data available: {_coverage_text(coverage)}."
            return {"result": AgentResult(answer, "insufficient_history", plan=plan, sql=compiled.sql, table=rows, evidence=evidence, route="prediction")}
        record_step("data_availability", "history assessment", detail=f"{len(history.labels)} complete months, {history.labels[0]} to {history.labels[-1]}")
        if not state.get("approved") and user.role != "admin" and settings.hitl_prediction_review:
            record_step("hitl", "administrator approval required before the forecast is released")
            evidence["history"] = {"first": history.labels[0], "last": history.labels[-1], "periods": len(history.labels)}
            return {"result": AgentResult("", "awaiting_review", plan=plan, sql=compiled.sql, table=rows, evidence=evidence, needs_review=True, route="prediction")}
        started = time.perf_counter()
        try:
            outcome = forecast_series(history, plan.horizon)
        except InsufficientHistoryError as exc:
            record_step("prediction", "forecast validation", ok=False, detail=str(exc)[:200])
            return {"result": AgentResult(f"No forecast for {metric.label.lower()}: {exc}", "insufficient_history", plan=plan, sql=compiled.sql, table=rows, evidence=evidence, route="prediction")}
        record_step(
            "prediction",
            outcome["model"],
            detail=f"Best of {len(outcome['backtest'])} methods by rolling-origin backtest",
            latency_ms=(time.perf_counter() - started) * 1000,
        )
        evidence["forecast"] = outcome
        prediction = PredictionPayload(type="forecast", labels=outcome["labels"], values=outcome["values"])
        uncertainty = UncertaintyPayload(confidence_range=outcome["intervals"][0])
        answer = _forecast_answer(plan, outcome, coverage)
        return {"result": AgentResult(answer, "complete", plan=plan, sql=compiled.sql, table=rows, evidence=evidence, prediction=prediction, uncertainty=uncertainty, route="prediction")}

    def _anomaly(self, state: AgentState) -> AgentState:
        plan, role = state["plan"], state["user"].role
        metric = plan.metric
        try:
            coverage = self._coverage(metric.source)
            if active_registry() is not BANKING and not metric.supports_time:
                answer = f"{metric.label} is a current figure with no dated history, so there is no series to test for unusual values."
                return {"result": AgentResult(answer, "insufficient_data", plan=plan, evidence=_evidence(plan, coverage, []))}
            if active_registry() is BANKING and (not metric.supports_time or metric.source.id == "transactions"):
                return {"result": self._transaction_checks(plan, role, coverage)}
            history_plan = QueryPlan(metric_id=plan.metric_id, intent="trend", filters=plan.filters, grain="month")
            compiled = compile_plan(history_plan)
            rows = self._run_sql(compiled, role)
        except WarehouseFailure as exc:
            return {"result": _failure(plan, exc)}
        evidence = _evidence(plan, coverage, rows)
        evidence["method"] = f"Robust z-score (median and MAD) on monthly values, flagged above {ANOMALY_Z}."
        values = [_number(row.get(metric.primary.alias)) for row in rows]
        if len(rows) < ANOMALY_MIN_PERIODS or any(value is None for value in values):
            answer = f"Only {len(rows)} month(s) of {metric.label.lower()} exist; at least {ANOMALY_MIN_PERIODS} are needed to judge what is unusual."
            return {"result": AgentResult(answer, "insufficient_history", plan=plan, sql=compiled.sql, table=rows, evidence=evidence)}
        median = statistics.median(values)
        deviations = [abs(value - median) for value in values]
        scale = statistics.median(deviations) * 1.4826
        flagged = [
            {"period": row["period"], "value": value, "robust_z": round((value - median) / scale, 2)}
            for row, value in zip(rows, values)
            if scale and abs(value - median) / scale > ANOMALY_Z
        ]
        evidence["flagged"] = flagged
        if flagged:
            listed = "; ".join(f"{item['period']} ({_format(item['value'], metric.primary.unit)})" for item in flagged[:12])
            answer = f"{len(flagged)} unusual month(s) in {metric.label.lower()}{_filter_text(plan)}: {listed}."
        else:
            answer = f"No month of {metric.label.lower()}{_filter_text(plan)} is unusual by this test."
        answer += " This flags statistical outliers for review; it does not establish a cause."
        return {"result": AgentResult(_with_basis(answer, plan, coverage), "complete", plan=plan, sql=compiled.sql, table=flagged or rows[-12:], evidence=evidence)}

    def _transaction_checks(self, plan: QueryPlan, role: str, coverage: dict[str, Any]) -> AgentResult:
        """A single day has no history to compare with, so check against fixed rules instead."""
        sql = (
            "SELECT SUM(CASE WHEN t.amount < 0 THEN 1 ELSE 0 END) AS negative_amount_transactions, "
            "ROUND(SUM(CASE WHEN t.amount < 0 THEN t.amount ELSE 0 END), 2) AS negative_amount_total, "
            f"SUM(CASE WHEN t.amount >= {HIGH_VALUE_AMOUNT} THEN 1 ELSE 0 END) AS high_value_transactions, "
            "SUM(CASE WHEN a.status IS NULL THEN 1 ELSE 0 END) AS transactions_without_matching_account, "
            "COUNT(*) AS record_count "
            "FROM gold_transactions t LEFT JOIN gold_accounts a ON a.account_id = t.account_id LIMIT 1"
        )
        rows = self._run_sql(CompiledQuery(sql, {}, (), (), 1), role)
        row = rows[0] if rows else {}
        record_step("result_validation", "approved transaction checks", detail="aggregates only")
        answer = (
            f"Approved checks across {_format(_number(row.get('record_count')), 'count')} transactions: "
            f"{_format(_number(row.get('negative_amount_transactions')), 'count')} have negative amounts "
            f"(totalling {_format(_number(row.get('negative_amount_total')), 'inr')}), "
            f"{_format(_number(row.get('high_value_transactions')), 'count')} are at or above INR {HIGH_VALUE_AMOUNT:,}, and "
            f"{_format(_number(row.get('transactions_without_matching_account')), 'count')} have no matching account. "
            "A trend-based test is not possible because the data covers one date. These are data-quality flags, not findings of fraud."
        )
        evidence = _evidence(plan, coverage, rows)
        evidence["method"] = "Fixed-rule aggregate checks on gold_transactions."
        return AgentResult(_with_basis(answer, plan, coverage), "complete", plan=plan, sql=sql, table=rows, evidence=evidence)


def _today() -> date:
    return date.today()


def _record_total(rows: list[dict[str, Any]]) -> int:
    return int(sum(_number(row.get("record_count")) or 0 for row in rows))


def _signed(value: float, unit: str) -> str:
    return f"{'+' if value >= 0 else '-'}{_format(abs(value), unit)}"


def _period_label(period: Period | None) -> str:
    return period.label if period else "all available data"


def _coverage_text(coverage: dict[str, Any]) -> str:
    return f"{coverage.get('first_date')} to {coverage.get('last_date')}"


def _registry_evidence() -> dict[str, Any]:
    registry = active_registry()
    return {"kind": "registry", "sources": [f"{registry.label} metric registry"], "registry_version": registry.version, "row_count": 0}


def _evidence(plan: QueryPlan, coverage: dict[str, Any], rows: list[dict[str, Any]], reconciliation: dict[str, Any] | None = None) -> dict[str, Any]:
    metric = plan.metric
    evidence = {
        "kind": "metric",
        "metric": metric.id,
        "metric_version": metric.version,
        "registry_version": active_registry().version,
        "definition": metric.definition,
        "aggregation": metric.aggregation,
        "time_semantics": metric.time_semantics,
        "sources": [f"{active_registry().catalog_schema}.{table}" for table in metric.source.tables if table.split("_", 1)[-1] in metric.source.from_clause or table in metric.source.from_clause],
        "period": _period_label(plan.period),
        "coverage": coverage,
        "filters": plan.filters,
        "dimensions": plan.dimensions,
        "interpreted_as": plan.describe(),
        "interpreted_by": plan.interpreted_by,
        "data_quality": list(metric.data_quality),
        "row_count": len(rows),
        "observed_rows_are_actual_data": True,
    }
    if plan.interpreted_by == "llm":
        evidence["prompt_version"] = PROMPT_VERSION
    if reconciliation:
        evidence["reconciliation"] = reconciliation
    return evidence


def _with_basis(answer: str, plan: QueryPlan, coverage: dict[str, Any]) -> str:
    metric = plan.metric
    basis = f"Source: {active_registry().catalog_schema}.{metric.source.from_clause.split()[0]}. Reporting period: {_period_label(plan.period)}; data available {_coverage_text(coverage)}. Definition: {metric.definition}"
    if plan.interpreted_by == "llm":
        basis = f"Read your question as: {plan.describe()}. {basis}"
    return f"{answer}\n\n{basis}"


def _outside_coverage(period: Period | None, coverage: dict[str, Any]) -> str | None:
    if period is None or period.start is None or not coverage.get("first_date"):
        return None
    first, last = date.fromisoformat(coverage["first_date"]), date.fromisoformat(coverage["last_date"])
    if period.start > last or period.end_exclusive <= first:
        return f"{period.label} is outside the available data."
    return None


def _no_data(plan: QueryPlan, coverage: dict[str, Any], reason: str, sql: str | None = None) -> AgentResult:
    answer = f"No value for {plan.metric.label.lower()}{_filter_text(plan)}. {reason} Data available: {_coverage_text(coverage)}."
    return AgentResult(answer, "no_data", plan=plan, sql=sql, evidence=_evidence(plan, coverage, []))


def _failure(plan: QueryPlan, failure: WarehouseFailure, route: str = "metric") -> AgentResult:
    message = USER_MESSAGES.get(failure.kind, USER_MESSAGES["infrastructure"])
    logger.warning("Banking warehouse failure: %s", failure.kind)
    evidence = {**_registry_evidence(), "error_kind": failure.kind}
    return AgentResult(message, "error", plan=plan, error=failure.kind, guardrail_ok=failure.kind != "policy", evidence=evidence, route=route)


def _verify_shape(plan: QueryPlan, compiled: CompiledQuery, rows: list[dict[str, Any]]) -> list[str]:
    """Problems with returned rows: wrong columns, non-numeric measures, impossible values."""
    problems: list[str] = []
    expected = set(compiled.dimension_columns) | set(compiled.measures) | {"record_count"}
    for row in rows:
        if set(row) != expected:
            return [f"Unexpected columns: {sorted(set(row) ^ expected)}"]
        for measure in plan.metric.measures:
            value = row.get(measure.alias)
            number = _number(value)
            if value is not None and number is None:
                problems.append(f"{measure.alias} is not numeric")
            if number is not None and measure.unit == "count" and number < 0:
                problems.append(f"{measure.alias} is negative")
            if number is not None and measure.unit == "percent" and not -0.01 <= number <= 100.01:
                problems.append(f"{measure.alias} is outside 0 to 100")
    if plan.grain:
        periods = [str(row["period"]) for row in rows if row.get("period") is not None]
        if not plan.dimensions and (periods != sorted(periods) or len(periods) != len(set(periods))):
            problems.append("Periods are out of order or repeated")
    return problems


def _trend_answer(plan: QueryPlan, rows: list[dict[str, Any]], coverage: dict[str, Any], sql: str, evidence: dict[str, Any]) -> AgentResult:
    metric, primary = plan.metric, plan.metric.primary
    if len(rows) < 2:
        answer = (
            f"A {plan.grain}ly trend of {metric.label.lower()}{_filter_text(plan)} is not possible: "
            f"the data has only one {plan.grain} ({rows[0].get('period')}). "
            f"For that {plan.grain}: {_describe_measures(metric, rows[0])}."
        )
        return AgentResult(_with_basis(answer, plan, coverage), "insufficient_history", plan=plan, sql=sql, table=rows, evidence=evidence)
    values = [_number(row.get(primary.alias)) for row in rows]
    usable = [(row["period"], value) for row, value in zip(rows, values) if value is not None]
    highest = max(usable, key=lambda item: item[1])
    lowest = min(usable, key=lambda item: item[1])
    partial = None
    last_date = coverage.get("last_date")
    if plan.grain == "month" and last_date and str(rows[-1]["period"]) == last_date[:7]:
        final = date.fromisoformat(last_date)
        following = date(final.year + (final.month == 12), final.month % 12 + 1, 1)
        if following - final > timedelta(days=1):
            partial = str(rows[-1]["period"])
    answer = (
        f"{metric.label}{_filter_text(plan)} by {plan.grain}, {rows[0]['period']} to {rows[-1]['period']} ({len(rows)} periods): "
        f"from {_format(values[0], primary.unit)} to {_format(values[-1], primary.unit)}; "
        f"highest {_format(highest[1], primary.unit)} in {highest[0]}, lowest {_format(lowest[1], primary.unit)} in {lowest[0]}."
    )
    if partial:
        answer += f" {partial} is incomplete: data ends on {last_date}."
        evidence["partial_period"] = partial
    return AgentResult(_with_basis(answer, plan, coverage), "complete", plan=plan, sql=sql, table=rows, evidence=evidence)


def _breakdown_answer(plan: QueryPlan, rows: list[dict[str, Any]], coverage: dict[str, Any], sql: str, evidence: dict[str, Any]) -> AgentResult:
    metric, primary = plan.metric, plan.metric.primary
    labels = plan.dimensions
    def name(row: dict[str, Any]) -> str:
        return " / ".join(str(row.get(item) if row.get(item) is not None else "unmatched") for item in labels)
    dimension_label = " and ".join(metric.source.dimension(item).label for item in labels)
    lead = "; ".join(f"{name(row)}: {_format(_number(row.get(primary.alias)), primary.unit)}" for row in rows[:5])
    ranking = f"Top {plan.top_n}" if plan.top_n and plan.order == "desc" else (f"Lowest {plan.top_n}" if plan.top_n else "Breakdown")
    answer = f"{ranking} of {metric.label.lower()}{_filter_text(plan)} by {dimension_label} for {_period_label(plan.period)}: {lead}"
    answer += f"; and {len(rows) - 5} more in the table." if len(rows) > 5 else "."
    unmatched = [row for row in rows if any(row.get(item) is None for item in labels)]
    if unmatched:
        answer += f" {_format(float(_record_total(unmatched)), 'count')} records have no matching {dimension_label} and appear as unmatched."
    return AgentResult(_with_basis(answer, plan, coverage), "complete", plan=plan, sql=sql, table=rows, evidence=evidence)


def _forecast_answer(plan: QueryPlan, outcome: dict[str, Any], coverage: dict[str, Any]) -> str:
    metric, primary = plan.metric, plan.metric.primary
    points = "; ".join(
        f"{label}: {_format(value, primary.unit)} (range {_format(low, primary.unit)} to {_format(high, primary.unit)})"
        for label, value, (low, high) in zip(outcome["labels"], outcome["values"], outcome["intervals"])
    )
    history = outcome["history"]
    chosen, baseline = outcome["backtest"][0], next(item for item in outcome["backtest"] if item["method"] == outcome["baseline"])
    if outcome["model"] == outcome["baseline"]:
        comparison = f"No tested model beat this simple baseline, so it is used as is (backtest MAE {_format(baseline['mae'], primary.unit)})."
    else:
        comparison = f"Backtest MAE {_format(chosen['mae'], primary.unit)} against {_format(baseline['mae'], primary.unit)} for the best simple baseline, {outcome['baseline']}: {outcome['skill_vs_baseline_pct']:.1f}% lower."
    wape = f" WAPE {chosen['wape_pct']:.1f}%." if chosen["wape_pct"] is not None else ""
    excluded = f" {history['excluded_partial_period']} was left out because its data is incomplete." if history["excluded_partial_period"] else ""
    notes = "".join(f" Data note: {note}" for note in metric.data_quality[1:2])
    return (
        f"Forecast of {metric.label.lower()}{_filter_text(plan)}, not actual data. {points}.\n\n"
        f"Model: {outcome['model']}. {comparison}{wape} History: {history['periods']} complete months, {history['first']} to {history['last']}.{excluded} "
        f"The forecast continues from the end of the data ({coverage.get('last_date')}), not from today.\n\n"
        f"Source: {active_registry().catalog_schema}.{metric.source.from_clause.split()[0]}. Definition: {metric.definition} "
        f"{outcome['limitations'][0]}{notes}"
    )


def _relative_periods(kind: str, anchor: Period | None, coverage: dict[str, Any]) -> tuple[Period | None, Period | None, str | None]:
    """The latest complete month and the month to compare it with, taken from the data itself."""
    if not coverage.get("last_date"):
        return None, None, "No dated records exist to compare."
    last = date.fromisoformat(coverage["last_date"])
    following = date(last.year + (last.month == 12), last.month % 12 + 1, 1)
    latest = anchor.start.replace(day=1) if anchor and anchor.start else (last.replace(day=1) if following - last <= timedelta(days=1) else (last.replace(day=1) - timedelta(days=1)).replace(day=1))
    earlier = (latest - timedelta(days=1)).replace(day=1) if kind == "mom" else latest.replace(year=latest.year - 1)

    def month(start: date) -> Period:
        end = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
        return Period(start, end, f"{start:%B %Y}")

    first = date.fromisoformat(coverage["first_date"])
    if earlier < first.replace(day=1) or latest < first.replace(day=1) or coverage.get("distinct_dates", 0) < 2:
        return None, None, f"A {'month-over-month' if kind == 'mom' else 'year-over-year'} comparison is not possible: the data does not reach back that far."
    return month(latest), month(earlier), None
