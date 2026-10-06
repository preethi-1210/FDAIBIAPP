import logging

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from .auth import AuthUser, authenticate, get_current_user
from .config import settings
from .db import SessionLocal, get_db
from .domains import DEMO_USERS, DOMAINS, DOMAIN_LABELS, PROMPTS
from .llm import provider_status
from .models import HitlReview, QueryHistory
from .schemas import LoginRequest, QueryRequest, QueryResponse, TokenResponse
from .sensitive_data import (
    has_cvv_columns,
    has_restricted_columns,
    require_sensitive_request_access,
    requested_sensitive_type,
    sanitize_sensitive_rows,
)
from .seed import apply_postgres_routines, ensure_schema, seed_if_empty
from .services.orchestrator import Orchestrator
from .warehouse import test_warehouse_connection, warehouse_status

app = FastAPI(title="RYZE AI/BI Platform", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        "http://localhost:3002",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3001",
        "http://127.0.0.1:3002",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

orchestrator = Orchestrator()
logger = logging.getLogger(__name__)


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
        orchestrator.warmup(db)
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


@app.post("/query", response_model=QueryResponse)
def query(
    body: QueryRequest,
    db: Session = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
) -> QueryResponse:
    return orchestrator.run(db, body.question, user, body.llm_provider)


@app.get("/dashboard")
def dashboard(db: Session = Depends(get_db), user: AuthUser = Depends(get_current_user)) -> dict:
    metrics = orchestrator.dashboard_metrics(db, user.domain, simulate=True)
    metrics["user"] = user.username
    metrics["role"] = user.role
    return metrics


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
    warehouse = test_warehouse_connection()
    return {
        "username": user.username,
        "domain": user.domain,
        "role": user.role,
        "warehouse": warehouse,
        "data_mode": "live" if warehouse["mode"] == "databricks" else "sample",
        "demo_mode": settings.demo_mode,
        "llm": provider_status(),
        "tabpfn": orchestrator.tabpfn.package_status(),
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
