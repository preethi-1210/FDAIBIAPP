from datetime import date, datetime
from random import Random
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..auth import AuthUser
from ..config import settings
from ..domains import DOMAIN_LABELS
from ..llm import resolve_provider
from ..governance import build_lineage, chart_spec, extract_tables, guardrail_report, model_gov
from ..models import Claim, Customer, HitlReview, LiveEvent, Order, OrderItem, Product, QueryHistory
from ..schemas import QueryResponse
from ..sensitive_data import (
    require_sensitive_request_access,
    require_sql_sensitive_access,
    requested_sensitive_type,
    sanitize_sensitive_rows,
)
from ..warehouse import execute_readonly, execute_readonly_batch
from .context_graph import ContextGraphService
from .feature_builder import build_features
from .guardrail import GuardrailError
from .planner import plan_query
from .sql_generator import generate_sql
from .synthesis import synthesize
from .tabpfn_service import TabPFNService

RISK_RATIO_DEFINITIONS = {
    "banking": {
        "label": "Delinquency ratio",
        "description": "Delinquent banking exposure divided by non-cancelled banking volume.",
        "numerator_label": "Delinquent exposure",
        "denominator_label": "Active banking volume",
    },
    "finance": {
        "label": "Cancelled trade ratio (demo proxy)",
        "description": "Cancelled trade notional divided by gross trade notional; this demo proxy is not an investment-loss measure.",
        "numerator_label": "Cancelled trade notional",
        "denominator_label": "Gross trade notional",
    },
    "insurance": {
        "label": "Insurance loss ratio",
        "description": "Insurance claims divided by earned premium.",
        "numerator_label": "Claims",
        "denominator_label": "Premium",
    },
    "retail": {
        "label": "Cancelled sales ratio (demo proxy)",
        "description": "Cancelled order value divided by gross order value; this demo proxy is not an accounting loss measure.",
        "numerator_label": "Cancelled sales",
        "denominator_label": "Gross sales",
    },
}


def _risk_ratio_query(domain: str, domain_sql: str, parameterized: bool = False) -> str:
    predicate = "=:d" if parameterized else f"= {domain_sql}"
    if domain == "insurance":
        return f"""
            SELECT regions.region AS region,
                   COALESCE(claims_by_region.numerator, 0) AS numerator,
                   COALESCE(premium_by_region.denominator, 0) AS denominator
            FROM (
                SELECT region FROM customers WHERE domain {predicate} GROUP BY region
            ) regions
            LEFT JOIN (
                SELECT cu.region AS region, SUM(o.total_amount) AS denominator
                FROM orders o
                JOIN customers cu ON cu.id = o.customer_id
                WHERE o.domain {predicate} AND cu.domain {predicate} AND o.status != 'cancelled'
                GROUP BY cu.region
            ) premium_by_region ON premium_by_region.region = regions.region
            LEFT JOIN (
                SELECT cu.region AS region, SUM(cl.amount) AS numerator
                FROM claims cl
                JOIN customers cu ON cu.id = cl.customer_id
                WHERE cl.domain {predicate} AND cu.domain {predicate}
                GROUP BY cu.region
            ) claims_by_region ON claims_by_region.region = regions.region
            ORDER BY region
        """

    exposure_filter = "o.status = 'delinquent'" if domain == "banking" else "o.status = 'cancelled'"
    denominator_filter = "o.status != 'cancelled'" if domain == "banking" else "1 = 1"
    return f"""
        SELECT cu.region AS region,
               COALESCE(SUM(CASE WHEN {exposure_filter} THEN o.total_amount ELSE 0 END), 0) AS numerator,
               COALESCE(SUM(CASE WHEN {denominator_filter} THEN o.total_amount ELSE 0 END), 0) AS denominator
        FROM orders o
        JOIN customers cu ON cu.id = o.customer_id
        WHERE o.domain {predicate} AND cu.domain {predicate}
        GROUP BY cu.region
        ORDER BY cu.region
    """


def _build_risk_ratio_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    risk_rows = []
    for row in rows:
        numerator = float(row.get("numerator") or 0)
        denominator = float(row.get("denominator") or 0)
        risk_rows.append(
            {
                "region": str(row["region"]),
                "numerator": numerator,
                "denominator": denominator,
                "loss_ratio": numerator / denominator if denominator else 0.0,
            }
        )
    return risk_rows


class Orchestrator:
    def __init__(self) -> None:
        self.graph = ContextGraphService()
        self.tabpfn = TabPFNService()
        self._ready = False

    def warmup(self, db: Session) -> None:
        self.graph.rebuild(db)
        self._ready = True

    def run(
        self,
        db: Session,
        question: str,
        user: AuthUser,
        llm_override: str | None = None,
        persist_history: bool = True,
    ) -> QueryResponse:
        if not self._ready:
            self.warmup(db)

        require_sensitive_request_access(question, user.role)
        admin_sensitive_request = (
            user.role == "admin" and requested_sensitive_type(question) == "admin_only"
        )
        if admin_sensitive_request:
            try:
                self.graph.refresh_warehouse_schema()
            except GuardrailError as exc:
                raise HTTPException(
                    status_code=503,
                    detail=f"Could not inspect the connected schema for approved sensitive fields: {exc}",
                ) from exc
        plan = plan_query(question, llm_override)
        schema_ctx = self.graph.resolve(
            question,
            user.domain,
            include_all_tables=admin_sensitive_request,
        )
        try:
            sql = generate_sql(
                question,
                schema_ctx,
                bool(plan.get("is_prediction")),
                user.domain,
                llm_override,
                role=user.role,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        require_sql_sensitive_access(sql, user.role)
        audit_id = f"AU-{int(datetime.utcnow().timestamp()) % 100000:04d}"
        schema_used = list(schema_ctx.get("tables", {}).keys())

        try:
            table = sanitize_sensitive_rows(execute_readonly(sql), user.role)
            guardrail_ok = True
            error = None
        except GuardrailError as exc:
            table = []
            guardrail_ok = False
            error = str(exc)

        prediction = None
        uncertainty = None
        if guardrail_ok and plan.get("is_prediction"):
            try:
                prepared = build_features(question, table)
                prediction, uncertainty = self.tabpfn.predict(prepared)
            except Exception as exc:
                error = f"Retrieved data, but prediction could not run: {exc}"

        answer = (
            error
            if not guardrail_ok
            else synthesize(question, table, sql, prediction, uncertainty, user.domain, llm_override)
        )
        if error and guardrail_ok and prediction is None and plan.get("is_prediction"):
            answer = error

        if settings.warehouse == "databricks":
            domain_sql = "'" + user.domain + "'"
            remote_counts = execute_readonly_batch(
                [
                    f"""
                        SELECT
                            (SELECT COUNT(*) FROM orders WHERE domain = {domain_sql}) AS orders,
                            (SELECT COUNT(*) FROM claims WHERE domain = {domain_sql}) AS claims,
                            (SELECT COUNT(*) FROM customers WHERE domain = {domain_sql}) AS customers
                    """
                ]
            )[0]
            count_row = remote_counts[0] if remote_counts else {}
            orders_count = int(count_row.get("orders") or 0)
            counts = {
                "bronze": orders_count + int(count_row.get("claims") or 0),
                "silver": int(count_row.get("customers") or 0),
                "gold": orders_count,
            }
        else:
            orders_count = db.query(Order).filter(Order.domain == user.domain).count()
            counts = {
                "bronze": orders_count + db.query(Claim).filter(Claim.domain == user.domain).count(),
                "silver": db.query(Customer).filter(Customer.domain == user.domain).count(),
                "gold": orders_count,
            }
        lineage = build_lineage(
            user.domain,
            question,
            sql,
            table,
            user.username,
            audit_id,
            schema_used,
            guardrail_ok,
            counts,
        )
        hitl_needed = bool(plan.get("is_prediction")) or (not guardrail_ok)
        hitl = {"status": "pending" if hitl_needed else "not_required", "audit_id": audit_id}
        if hitl_needed and persist_history:
            db.add(
                HitlReview(
                    audit_id=audit_id,
                    username=user.username,
                    domain=user.domain,
                    question=question,
                    status="pending",
                    reason="prediction" if plan.get("is_prediction") else "guardrail",
                )
            )

        response = QueryResponse(
            answer=answer,
            sql=sql,
            table=table,
            plan=plan,
            schema_used=schema_used,
            prediction=prediction,
            uncertainty=uncertainty,
            error=error,
            domain=user.domain,
            llm_provider=resolve_provider(llm_override),
            audit_id=audit_id,
            chart=chart_spec(table),
            lineage=lineage,
            guardrails=guardrail_report(sql, guardrail_ok, error),
            hitl=hitl,
            model_gov=model_gov(prediction, resolve_provider(llm_override)),
        )
        if persist_history:
            history = QueryHistory(
                username=user.username,
                domain=user.domain,
                question=question,
                sql=sql,
                answer=answer,
                result_json=response.model_dump_json(),
                audit_id=audit_id,
                tables_used=",".join(extract_tables(sql)),
                row_count=len(table),
                guardrail_ok=1 if guardrail_ok else 0,
            )
            db.add(history)
            db.commit()
        return response

    def dashboard_metrics(self, db: Session, domain: str, simulate: bool = False) -> dict[str, Any]:
        if settings.warehouse == "databricks":
            return self._dashboard_metrics_databricks(db, domain)
        if simulate and settings.live_simulate:
            _simulate_tick(db, domain)

        metric_name = {
            "banking": "Deposits / flow",
            "finance": "AUM / notional",
            "insurance": "Gross written premium",
            "retail": "Revenue",
        }.get(domain, "Revenue")

        volume = db.execute(
            text(
                "SELECT COALESCE(SUM(total_amount),0) FROM orders WHERE domain=:d AND status!='cancelled'"
            ),
            {"d": domain},
        ).scalar()
        events = db.execute(
            text("SELECT COUNT(*) FROM orders WHERE domain=:d AND status!='cancelled'"),
            {"d": domain},
        ).scalar()
        customers = db.execute(text("SELECT COUNT(*) FROM customers WHERE domain=:d"), {"d": domain}).scalar()
        churn = db.execute(text("SELECT AVG(churned) FROM customers WHERE domain=:d"), {"d": domain}).scalar()
        by_region = [
            dict(r)
            for r in db.execute(
                text(
                    """
                    SELECT c.region AS label, COALESCE(SUM(o.total_amount),0) AS value
                    FROM customers c
                    LEFT JOIN orders o ON o.customer_id=c.id AND o.domain=:d AND o.status!='cancelled'
                    WHERE c.domain=:d
                    GROUP BY c.region ORDER BY value DESC
                    """
                ),
                {"d": domain},
            ).mappings()
        ]
        by_category = [
            dict(r)
            for r in db.execute(
                text(
                    """
                    SELECT p.category AS label, COALESCE(SUM(oi.amount),0) AS value
                    FROM products p
                    LEFT JOIN order_items oi ON oi.product_id=p.id AND oi.domain=:d
                    WHERE p.domain=:d
                    GROUP BY p.category ORDER BY value DESC
                    """
                ),
                {"d": domain},
            ).mappings()
        ]
        if settings.database_url.startswith("postgresql") or settings.warehouse == "databricks":
            month_sql = "to_char(order_date,'YYYY-MM')"
        else:
            month_sql = "strftime('%Y-%m', order_date)"
        by_month = [
            dict(r)
            for r in db.execute(
                text(
                    f"""
                    SELECT {month_sql} AS label, COALESCE(SUM(total_amount),0) AS value, COUNT(*) AS events
                    FROM orders WHERE domain=:d AND status!='cancelled'
                    GROUP BY {month_sql} ORDER BY label
                    """
                ),
                {"d": domain},
            ).mappings()
        ]
        live = [
            dict(r)
            for r in db.execute(
                text(
                    """
                    SELECT event_type AS label, amount AS value, created_at
                    FROM live_events WHERE domain=:d
                    ORDER BY id DESC LIMIT 12
                    """
                ),
                {"d": domain},
            ).mappings()
        ]
        heatmap = [
            {"region": r["region"], "category": r["category"], "value": float(r["value"] or 0)}
            for r in db.execute(
                text(
                    """
                    SELECT c.region AS region, p.category AS category, COALESCE(SUM(oi.amount),0) AS value
                    FROM customers c
                    JOIN orders o ON o.customer_id = c.id AND o.domain=:d AND o.status!='cancelled'
                    JOIN order_items oi ON oi.order_id = o.id AND oi.domain=:d
                    JOIN products p ON p.id = oi.product_id
                    WHERE c.domain=:d
                    GROUP BY c.region, p.category
                    """
                ),
                {"d": domain},
            ).mappings()
        ]
        risk_definition = RISK_RATIO_DEFINITIONS[domain]
        risk_source_rows = [
            dict(row)
            for row in db.execute(
                text(_risk_ratio_query(domain, "", parameterized=True)),
                {"d": domain},
            ).mappings()
        ]
        loss_by_region = _build_risk_ratio_rows(risk_source_rows)
        risk_numerator = sum(row["numerator"] for row in loss_by_region)
        risk_denominator = sum(row["denominator"] for row in loss_by_region)
        audits = db.query(QueryHistory).filter(QueryHistory.domain == domain).order_by(QueryHistory.id.desc()).limit(12).all()
        audit_rows = [
            {
                "audit_id": a.audit_id or f"AU-{a.id}",
                "question": a.question,
                "sql": a.sql,
                "rows": a.row_count,
                "guardrail_ok": bool(a.guardrail_ok),
                "user": a.username,
                "created_at": str(a.created_at),
            }
            for a in audits
        ]
        passed = db.query(QueryHistory).filter(QueryHistory.domain == domain, QueryHistory.guardrail_ok == 1).count()
        total_q = db.query(QueryHistory).filter(QueryHistory.domain == domain).count()
        claims_total = 0.0
        if domain == "insurance":
            claims_total = float(
                db.execute(text("SELECT COALESCE(SUM(amount),0) FROM claims WHERE domain=:d"), {"d": domain}).scalar()
                or 0
            )
        return {
            "domain": domain,
            "domain_label": DOMAIN_LABELS.get(domain, domain),
            "metric_name": metric_name,
            "volume": float(volume or 0),
            "events": int(events or 0),
            "customers": int(customers or 0),
            "churn_rate": float(churn or 0),
            "claims": claims_total,
            "loss_ratio": risk_numerator / risk_denominator if risk_denominator else 0.0,
            "loss_ratio_label": risk_definition["label"],
            "loss_ratio_description": risk_definition["description"],
            "loss_numerator_label": risk_definition["numerator_label"],
            "loss_denominator_label": risk_definition["denominator_label"],
            "guardrail_pass_rate": (passed / total_q) if total_q else 1,
            "queries": total_q,
            "heatmap": heatmap,
            "loss_by_region": loss_by_region,
            "audits": audit_rows,
            "by_region": by_region,
            "by_category": by_category,
            "by_month": [{**r, "value": float(r["value"]), "events": int(r["events"])} for r in by_month],
            "live": [
                {**r, "value": float(r["value"]), "created_at": str(r["created_at"])} for r in live
            ],
            "updated_at": datetime.utcnow().isoformat() + "Z",
        }

    def _dashboard_metrics_databricks(self, db: Session, domain: str) -> dict[str, Any]:
        if domain not in DOMAIN_LABELS:
            raise ValueError(f"Unsupported business domain '{domain}'")
        d = "'" + domain + "'"
        queries = [
            f"""
                SELECT COALESCE(SUM(total_amount), 0) AS volume, COUNT(*) AS events
                FROM orders
                WHERE domain = {d} AND status != 'cancelled'
            """,
            f"""
                SELECT COUNT(*) AS customers, COALESCE(AVG(churned), 0) AS churn_rate
                FROM customers WHERE domain = {d}
            """,
            f"""
                SELECT c.region AS label, COUNT(o.id) AS events,
                       COALESCE(SUM(o.total_amount), 0) AS value
                FROM customers c
                LEFT JOIN orders o ON o.customer_id = c.id AND o.domain = {d}
                    AND o.status != 'cancelled'
                WHERE c.domain = {d}
                GROUP BY c.region ORDER BY value DESC
            """,
            f"""
                SELECT p.category AS label, COALESCE(SUM(oi.amount), 0) AS value
                FROM products p
                LEFT JOIN order_items oi ON oi.product_id = p.id AND oi.domain = {d}
                WHERE p.domain = {d}
                GROUP BY p.category ORDER BY value DESC
            """,
            f"""
                SELECT date_format(order_date, 'yyyy-MM') AS label,
                       COALESCE(SUM(total_amount), 0) AS value, COUNT(*) AS events
                FROM orders
                WHERE domain = {d} AND status != 'cancelled'
                GROUP BY date_format(order_date, 'yyyy-MM') ORDER BY label
            """,
            f"""
                SELECT event_type AS label, amount AS value, created_at
                FROM live_events WHERE domain = {d} ORDER BY id DESC LIMIT 12
            """,
            f"""
                SELECT c.region AS region, p.category AS category,
                       COALESCE(SUM(oi.amount), 0) AS value
                FROM customers c
                JOIN orders o ON o.customer_id = c.id AND o.domain = {d}
                    AND o.status != 'cancelled'
                JOIN order_items oi ON oi.order_id = o.id AND oi.domain = {d}
                JOIN products p ON p.id = oi.product_id
                WHERE c.domain = {d}
                GROUP BY c.region, p.category
            """,
            f"SELECT COALESCE(SUM(amount), 0) AS claims FROM claims WHERE domain = {d}",
            _risk_ratio_query(domain, d),
        ]
        (
            overview,
            customer_stats,
            by_region,
            by_category,
            by_month,
            live,
            heatmap,
            claims,
            loss_by_region_rows,
        ) = execute_readonly_batch(queries)
        audits = (
            db.query(QueryHistory)
            .filter(QueryHistory.domain == domain)
            .order_by(QueryHistory.id.desc())
            .limit(12)
            .all()
        )
        audit_rows = [
            {
                "audit_id": row.audit_id or f"AU-{row.id}",
                "question": row.question,
                "sql": row.sql,
                "rows": row.row_count,
                "guardrail_ok": bool(row.guardrail_ok),
                "user": row.username,
                "created_at": str(row.created_at),
            }
            for row in audits
        ]
        passed = db.query(QueryHistory).filter(
            QueryHistory.domain == domain, QueryHistory.guardrail_ok == 1
        ).count()
        total_queries = db.query(QueryHistory).filter(QueryHistory.domain == domain).count()
        volume = float(overview[0]["volume"] or 0) if overview else 0.0
        claims_total = float(claims[0]["claims"] or 0) if claims else 0.0
        risk_definition = RISK_RATIO_DEFINITIONS[domain]
        loss_by_region = _build_risk_ratio_rows(loss_by_region_rows)
        risk_numerator = sum(row["numerator"] for row in loss_by_region)
        risk_denominator = sum(row["denominator"] for row in loss_by_region)
        customers_row = customer_stats[0] if customer_stats else {}
        return {
            "domain": domain,
            "domain_label": DOMAIN_LABELS[domain],
            "metric_name": {
                "banking": "Deposits / flow",
                "finance": "AUM / notional",
                "insurance": "Gross written premium",
                "retail": "Revenue",
            }.get(domain, "Revenue"),
            "volume": volume,
            "events": int(overview[0]["events"] or 0) if overview else 0,
            "customers": int(customers_row.get("customers") or 0),
            "churn_rate": float(customers_row.get("churn_rate") or 0),
            "claims": claims_total if domain == "insurance" else 0.0,
            "loss_ratio": risk_numerator / risk_denominator if risk_denominator else 0.0,
            "loss_ratio_label": risk_definition["label"],
            "loss_ratio_description": risk_definition["description"],
            "loss_numerator_label": risk_definition["numerator_label"],
            "loss_denominator_label": risk_definition["denominator_label"],
            "guardrail_pass_rate": (passed / total_queries) if total_queries else 1,
            "queries": total_queries,
            "heatmap": heatmap,
            "loss_by_region": loss_by_region,
            "audits": audit_rows,
            "by_region": by_region,
            "by_category": by_category,
            "by_month": [
                {**row, "value": float(row["value"] or 0), "events": int(row["events"] or 0)}
                for row in by_month
            ],
            "live": [
                {**row, "value": float(row["value"] or 0), "created_at": str(row["created_at"])}
                for row in live
            ],
            "updated_at": datetime.utcnow().isoformat() + "Z",
        }


def _simulate_tick(db: Session, domain: str) -> None:
    rng = Random()
    customer = db.query(Customer).filter(Customer.domain == domain).first()
    product = db.query(Product).filter(Product.domain == domain).first()
    if not customer or not product:
        return
    amount = round(rng.uniform(40, 900), 2)
    order = Order(
        domain=domain,
        customer_id=customer.id,
        order_date=date.today(),
        status="completed",
        total_amount=amount,
    )
    db.add(order)
    db.flush()
    db.add(
        OrderItem(
            domain=domain,
            order_id=order.id,
            product_id=product.id,
            quantity=1,
            amount=amount,
        )
    )
    db.add(LiveEvent(domain=domain, event_type="live_tick", amount=amount, created_at=datetime.utcnow()))
    if domain == "insurance" and rng.random() < 0.2:
        db.add(
            Claim(
                domain=domain,
                customer_id=customer.id,
                claim_date=date.today(),
                amount=round(amount * 0.6, 2),
                status="open",
            )
        )
    db.commit()
