import logging
import time
from datetime import datetime, timedelta, timezone
from threading import Lock
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from .auth import AuthUser, authenticate, get_current_user, register_user
from .config import settings
from .db import SessionLocal, get_db
from .domains import DEMO_USERS, DOMAINS, DOMAIN_LABELS, PROMPTS
from .llm import provider_status
from .models import Conversation, ConversationMessage, Glossary, HitlReview, QueryHistory
from .schemas import LoginRequest, QueryRequest, QueryResponse, SignupRequest, TokenResponse
from .sensitive_data import (
    has_cvv_columns,
    has_restricted_columns,
    is_sensitive_column,
    require_sensitive_request_access,
    requested_sensitive_type,
    sanitize_sensitive_rows,
)
from .seed import apply_postgres_routines, ensure_schema, seed_if_empty
from .services.guardrail import GuardrailError
from .services.orchestrator import Orchestrator
from .warehouse import (
    databricks_domain_catalog,
    test_warehouse_connection,
    verify_warehouse_domain,
    warehouse_status,
)

app = FastAPI(title="RYZE AI/BI Platform", version="0.2.0")
allowed_origins = [
    "http://localhost:3000",
    "http://localhost:3001",
    "http://localhost:3002",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:3001",
    "http://127.0.0.1:3002",
]

orchestrator = Orchestrator()
logger = logging.getLogger(__name__)
_dashboard_cache: dict[tuple[str, str], tuple[float, dict]] = {}
_dashboard_cache_lock = Lock()


def _get_conversation_context(
    db: Session,
    user: AuthUser,
    conversation_id: UUID,
    title: str,
) -> tuple[Conversation, list[dict[str, str]]]:
    conversation = db.get(Conversation, str(conversation_id))
    if conversation is not None and (
        conversation.username != user.username or conversation.domain != user.domain
    ):
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conversation is None:
        conversation = Conversation(
            id=str(conversation_id),
            username=user.username,
            domain=user.domain,
            title=title[:160],
        )
        db.add(conversation)

    prior_messages = (
        db.query(ConversationMessage)
        .filter(ConversationMessage.conversation_id == str(conversation_id))
        .order_by(ConversationMessage.id.desc())
        .limit(12)
        .all()
    )
    context = [
        {"role": message.role, "content": message.content}
        for message in reversed(prior_messages)
    ]
    return conversation, context


@app.on_event("startup")
def startup() -> None:
    if not settings.demo_mode and (
        settings.jwt_secret == "ryze-dev-secret-change-me" or len(settings.jwt_secret) < 32
    ):
        raise RuntimeError("Set a unique JWT_SECRET with at least 32 characters before disabling DEMO_MODE.")
    ensure_schema()
    db = SessionLocal()
    try:
        if settings.demo_mode:
            seed_if_empty(db)
        apply_postgres_routines(db)
        try:
            orchestrator.warmup(db)
        except GuardrailError as exc:
            if settings.warehouse not in {"databricks", "snowflake"}:
                raise
            logger.warning(
                "API startup continuing without %s schema metadata; authentication remains available, "
                "but analytics requires a working warehouse connection: %s",
                settings.warehouse,
                exc,
            )
    finally:
        db.close()


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "platform": settings.app_name,
        "genie": False,
        "warehouse": warehouse_status(),
        "llm": provider_status(),
        "tabpfn": orchestrator.tabpfn.package_status(),
        "rag": orchestrator.graph.rag.status(),
        "demo_mode": settings.demo_mode,
        "domains": list(DOMAINS),
    }


@app.get("/auth/domains")
def list_domains() -> dict:
    return {
        "domains": [{"id": d, "label": DOMAIN_LABELS[d]} for d in DOMAINS],
        "users": (
            [
                {"username": u["username"], "domain": u["domain"], "password_hint": u["password"]}
                for u in DEMO_USERS
            ]
            if settings.demo_mode
            else []
        ),
    }


@app.post("/auth/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    token, user = authenticate(db, body.username, body.password, body.domain)
    domains = list(DOMAINS) if user.role == "admin" else [user.domain]
    return TokenResponse(
        access_token=token,
        username=user.username,
        domain=user.domain,
        role=user.role,
        domains=domains,
    )


@app.post("/auth/signup", response_model=TokenResponse, status_code=201)
def signup(body: SignupRequest, db: Session = Depends(get_db)) -> TokenResponse:
    token, user = register_user(
        db,
        body.username,
        body.password,
        body.domain,
        body.display_name,
    )
    return TokenResponse(
        access_token=token,
        username=user.username,
        domain=user.domain,
        role=user.role,
        domains=[user.domain],
    )


@app.post("/query", response_model=QueryResponse)
def query(
    body: QueryRequest,
    db: Session = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
) -> QueryResponse:
    conversation_id = body.conversation_id or uuid4()
    conversation, conversation_context = _get_conversation_context(
        db,
        user,
        conversation_id,
        body.question,
    )
    response = orchestrator.run(
        db,
        body.question,
        user,
        body.llm_provider,
        conversation_context=conversation_context,
        conversation_id=conversation_id,
    )
    conversation.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add_all(
        [
            ConversationMessage(
                conversation_id=str(conversation_id),
                role="user",
                content=body.question,
            ),
            ConversationMessage(
                conversation_id=str(conversation_id),
                role="assistant",
                content=response.answer,
            ),
        ]
    )
    db.commit()
    return response


@app.get("/dashboard")
def dashboard(db: Session = Depends(get_db), user: AuthUser = Depends(get_current_user)) -> dict:
    try:
        cache_key = (user.username, user.domain)
        now = time.monotonic()
        cache_ttl = max(0, settings.dashboard_cache_seconds)
        with _dashboard_cache_lock:
            cached = _dashboard_cache.get(cache_key)
        if (
            settings.warehouse in {"databricks", "snowflake"}
            and cache_ttl > 0
            and cached
            and now - cached[0] < cache_ttl
        ):
            metrics = dict(cached[1])
        else:
            metrics = orchestrator.dashboard_metrics(db, user.domain, simulate=True)
            if settings.warehouse in {"databricks", "snowflake"} and cache_ttl > 0:
                with _dashboard_cache_lock:
                    expired = [
                        key
                        for key, (created_at, _value) in _dashboard_cache.items()
                        if now - created_at >= cache_ttl
                    ]
                    for key in expired:
                        _dashboard_cache.pop(key, None)
                    _dashboard_cache[cache_key] = (time.monotonic(), dict(metrics))
    except GuardrailError as exc:
        logger.warning("Dashboard query failed for %s warehouse: %s", settings.warehouse, exc)
        status = warehouse_status()
        if not status["ready"]:
            detail = (
                f"The configured {settings.warehouse} warehouse is not ready. "
                f"{status['detail']}. Set these values in backend/.env and restart the API. "
                "The app will not substitute sample data for the configured live source. "
                "Use WAREHOUSE=local only if you intentionally want demo data."
            )
        else:
            detail = (
                f"The configured {settings.warehouse} warehouse query failed. "
                "Check warehouse availability, SELECT permissions, and backend logs. "
                "Set WAREHOUSE=local only if you intentionally want sample data."
            )
        raise HTTPException(
            status_code=503,
            detail=detail,
        ) from exc
    metrics["user"] = user.username
    metrics["role"] = user.role
    return metrics


@app.get("/monitor")
def monitor(
    days: int = 30,
    db: Session = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
) -> dict:
    if days < 1 or days > 365:
        raise HTTPException(status_code=422, detail="Days must be between 1 and 365.")
    cutoff = datetime.utcnow() - timedelta(days=days)
    scoped_history = db.query(QueryHistory).filter(
        QueryHistory.username == user.username,
        QueryHistory.domain == user.domain,
        QueryHistory.created_at >= cutoff,
    )
    rows = scoped_history.order_by(QueryHistory.created_at.desc()).limit(100).all()
    total = scoped_history.count()
    pending_reviews = db.query(HitlReview).filter(
        HitlReview.username == user.username,
        HitlReview.domain == user.domain,
        HitlReview.status == "pending",
    ).count()
    return {
        "domain": user.domain,
        "days": days,
        "total_questions": total,
        "unique_users": 1 if total else 0,
        "guardrail_blocks": scoped_history.filter(QueryHistory.guardrail_ok == 0).count(),
        "pending_reviews": pending_reviews,
        "ratings": None,
        "activities": [
            {
                "audit_id": row.audit_id or f"AU-{row.id}",
                "question": row.question,
                "status": "passed" if row.guardrail_ok else "blocked",
                "rows": row.row_count,
                "created_at": str(row.created_at),
            }
            for row in rows
        ],
    }


@app.get("/prompts")
def prompts(user: AuthUser = Depends(get_current_user)) -> dict:
    return {"domain": user.domain, "prompts": PROMPTS.get(user.domain, [])}


@app.get("/history")
def history(db: Session = Depends(get_db), user: AuthUser = Depends(get_current_user)) -> list[dict]:
    rows = (
        db.query(QueryHistory)
        .filter(QueryHistory.username == user.username, QueryHistory.domain == user.domain)
        .order_by(QueryHistory.id.desc())
        .limit(20)
        .all()
    )
    items = []
    for row in rows:
        requested_type = requested_sensitive_type(row.question)
        if requested_type == "never_disclose" or (requested_type == "admin_only" and user.role != "admin"):
            continue
        result = QueryResponse.model_validate_json(row.result_json) if row.result_json else None
        if result:
            if has_cvv_columns(result.table):
                continue
            restricted_output = has_restricted_columns(result.table)
            result.table = sanitize_sensitive_rows(result.table, user.role)
        else:
            restricted_output = False
        items.append({
            "id": row.id,
            "question": row.question,
            "answer": "Sensitive fields are restricted for your account." if restricted_output and user.role != "admin" else row.answer,
            "sql": row.sql,
            "result": result,
            "conversation_id": str(result.conversation_id) if result and result.conversation_id else None,
            "created_at": str(row.created_at),
        })
    return items


@app.post("/history/{history_id}/replay", response_model=QueryResponse)
def replay_history(
    history_id: int,
    db: Session = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
) -> QueryResponse:
    row = (
        db.query(QueryHistory)
        .filter(
            QueryHistory.id == history_id,
            QueryHistory.username == user.username,
            QueryHistory.domain == user.domain,
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="History item not found")
    require_sensitive_request_access(row.question, user.role)
    if row.result_json:
        response = QueryResponse.model_validate_json(row.result_json)
        if has_cvv_columns(response.table):
            raise HTTPException(status_code=403, detail="This saved result contains prohibited payment data and cannot be replayed.")
        restricted_output = has_restricted_columns(response.table)
        response.table = sanitize_sensitive_rows(response.table, user.role)
        if restricted_output and user.role != "admin":
            response.answer = "Sensitive fields are restricted for your account."
        return response
    response = orchestrator.run(db, row.question, user, persist_history=False)
    row.answer = response.answer
    row.sql = response.sql
    row.result_json = response.model_dump_json()
    db.commit()
    return response


@app.get("/hitl")
def hitl_queue(db: Session = Depends(get_db), user: AuthUser = Depends(get_current_user)) -> list[dict]:
    rows = (
        db.query(HitlReview)
        .filter(HitlReview.domain == user.domain)
        .order_by(HitlReview.id.desc())
        .limit(30)
        .all()
    )
    return [
        {
            "id": r.id,
            "audit_id": r.audit_id,
            "question": r.question,
            "status": r.status,
            "reason": r.reason,
            "user": r.username,
            "created_at": str(r.created_at),
        }
        for r in rows
    ]


@app.post("/hitl/{item_id}/{action}")
def hitl_action(
    item_id: int,
    action: str,
    db: Session = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
) -> dict:
    if action not in {"approve", "reject"}:
        raise HTTPException(status_code=422, detail="Action must be 'approve' or 'reject'")
    row = db.query(HitlReview).filter(HitlReview.id == item_id, HitlReview.domain == user.domain).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Review request not found")
    row.status = "approved" if action == "approve" else "rejected"
    db.commit()
    return {"ok": True, "status": row.status}


@app.get("/me")
def me(user: AuthUser = Depends(get_current_user)) -> dict:
    warehouse = warehouse_status()
    return {
        "username": user.username,
        "domain": user.domain,
        "role": user.role,
        "warehouse": warehouse,
        "data_mode": "live" if warehouse["mode"] in {"databricks", "snowflake"} else "sample",
        "demo_mode": settings.demo_mode,
        "llm": provider_status(),
        "tabpfn": orchestrator.tabpfn.package_status(),
        "rag": orchestrator.graph.rag.status(),
    }


@app.get("/warehouse/check")
def warehouse_check(user: AuthUser = Depends(get_current_user)) -> dict:
    return test_warehouse_connection(user.domain)


@app.post("/warehouse/verify")
def warehouse_verify(user: AuthUser = Depends(get_current_user)) -> dict:
    try:
        return verify_warehouse_domain(user.domain)
    except GuardrailError as exc:
        logger.warning("Databricks verification failed for %s: %s", user.domain, exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/context")
def user_context(
    domain: str | None = None,
    db: Session = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
) -> dict:
    permitted_domains = list(DOMAINS) if user.role == "admin" else [user.domain]
    selected_domain = domain or user.domain
    if selected_domain not in permitted_domains:
        raise HTTPException(status_code=403, detail="This user cannot view context for that business domain.")

    graph = orchestrator.graph
    domain_glossary = db.query(Glossary).filter(Glossary.domain == selected_domain).all()
    if settings.warehouse in {"databricks", "snowflake"}:
        tables = graph.domain_tables.get(selected_domain, {})
    else:
        mapped_tables = {term.table_name for term in domain_glossary}
        tables = {table: columns for table, columns in graph.tables.items() if table in mapped_tables}
    source_tables = {
        table: [
            column
            for column in columns
            if not is_sensitive_column(column["name"])
        ]
        for table, columns in tables.items()
        if table not in {"glossary", "query_history", "live_events", "hitl_reviews"}
    }
    visible_relationships = []
    for source, target, metadata in graph.graph.edges(data=True):
        if source in source_tables and target in source_tables:
            visible_relationships.append(
                {
                    "source": source,
                    "target": target,
                    "keys": metadata.get("columns") or [],
                    "evidence": "database foreign-key metadata",
                }
            )

    glossary = []
    for term in domain_glossary:
        if is_sensitive_column(term.column_name):
            continue
        available_columns = source_tables.get(term.table_name, [])
        mapped = any(column["name"] == term.column_name for column in available_columns)
        glossary.append(
            {
                "term": term.business_term,
                "definition": term.description,
                "table": term.table_name,
                "column": term.column_name if mapped else None,
                "mapping_status": "mapped" if mapped else "source mapping unavailable",
            }
        )

    source = {
        "warehouse": settings.warehouse,
        "configured": warehouse_status()["ready"],
        "schema": None,
        "catalog": None,
        "metadata_available": bool(source_tables),
    }
    if settings.warehouse == "databricks":
        source["schema"] = settings.databricks_domain_schema
        try:
            source["catalog"] = databricks_domain_catalog(selected_domain)
        except GuardrailError:
            source["configured"] = False
    elif settings.warehouse == "snowflake":
        source["catalog"] = settings.snowflake_database or None
        source["schema"] = settings.snowflake_schema

    return {
        "user": {
            "username": user.username,
            "role": user.role,
            "domain": selected_domain,
            "domain_label": DOMAIN_LABELS[selected_domain],
            "permitted_domains": permitted_domains,
            "organization": None,
        },
        "source": source,
        "tables": [
            {
                "name": table,
                "columns": [column["name"] for column in columns],
            }
            for table, columns in sorted(source_tables.items())
        ],
        "relationships": visible_relationships,
        "glossary": glossary,
        "metrics": [],
        "rules": [],
        "synchronized_at": None,
    }


@app.post("/tabpfn/diagnostics")
def tabpfn_diagnostics(user: AuthUser = Depends(get_current_user)) -> dict:
    if not orchestrator.tabpfn.package_status()["installed"]:
        raise HTTPException(
            status_code=503,
            detail="TabPFN is not installed in the backend environment. Install backend/requirements.txt and restart the API.",
        )
    try:
        return orchestrator.tabpfn.diagnostic_test()
    except Exception as exc:
        logger.exception("TabPFN diagnostic prediction failed")
        raise HTTPException(
            status_code=503,
            detail=f"TabPFN is installed, but the diagnostic prediction failed: {exc}",
        ) from exc


# Keep CORS outside FastAPI's error middleware so even unexpected 500 responses
# carry the headers needed by the browser to show the actual API error.
fastapi_app = app
app = CORSMiddleware(
    app=fastapi_app,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
