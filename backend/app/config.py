from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "RYZE AI/BI"
    jwt_secret: str = "ryze-dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 480
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_redirect_uri: str = ""
    oidc_discovery_url: str = "https://accounts.google.com/.well-known/openid-configuration"
    oidc_tenant_claim: str = ""
    frontend_url: str = "http://localhost:3000"
    cors_origins: str = (
        "http://localhost:3000,http://localhost:3002,"
        "http://127.0.0.1:3000,http://127.0.0.1:3002"
    )

    database_url: str = "sqlite:///./ryze_bi.db"
    cache_url: str = ""
    cache_ttl_seconds: int = 120
    warehouse: str = "local"
    demo_mode: bool = True
    query_row_limit: int = 200
    query_timeout_seconds: int = 8
    llm_timeout_seconds: int = 30
    dashboard_cache_seconds: int = 120
    report_cache_seconds: int = 120
    # Requests/queries slower than these are logged at WARNING so they are visible by default.
    slow_request_ms: int = 1500
    slow_query_ms: int = 1000
    log_level: str = "INFO"
    # Business users' prediction requests wait for an administrator's approval before the
    # model runs (administrators' own requests are not held).
    hitl_prediction_review: bool = True
    # Ask AI routing to metrics, knowledge and application tools before free-form SQL generation.
    ask_ai_agentic_routing: bool = True
    # Banking questions use the metric registry and forecasting workflow before generated SQL.
    banking_agent_enabled: bool = True
    # Lets a model propose a validated query plan when rules cannot read a banking question.
    banking_llm_planning: bool = True
    # Off until a model passes evals/run_sql_eval.py: wrong generated SQL can be valid, read-only and
    # on an approved table, so validation alone cannot catch it. llama3.2 scored 0/10.
    banking_generated_sql: bool = False
    # Domains answered first from their approved metric registry (services/domain_registries.py).
    metric_registry_domains: str = "finance,insurance,retail,healthcare"
    # Whether questions those registries cannot represent still go to model-written SQL.
    registry_generated_sql: bool = True
    # Plans and AI allowances. Separate from domain access: a plan never unlocks business data.
    subscriptions_enabled: bool = True
    # False records usage but limits nobody.
    subscription_enforcement: bool = True
    subscription_notice_thresholds: str = "30,50,80,90,100"
    subscription_currency: str = "INR"
    # Empty: no online payment. "mock" moves no money and works only in demo mode.
    payment_gateway: str = ""
    # "provider:model=input,output;..." per million tokens. Without a rate, cost is reported as unknown.
    llm_cost_per_million_tokens: str = ""
    # Total time budget for one Ask AI request; 0 disables it.
    ask_deadline_seconds: int = 300
    # auto: TabPFN when its weights load, otherwise the statistical baseline, labelled as such.
    # tabpfn: TabPFN only. baseline: the statistical baseline only.
    prediction_engine: str = "auto"
    # TabPFN stays off for banking until a weight license is approved and weights are on disk.
    tabpfn_enabled: bool = False
    tabpfn_model_path: str = ""
    llm_max_concurrency: int = 8
    # Server-side limit per Databricks statement; 0 leaves the warehouse default.
    databricks_statement_timeout_seconds: int = 120
    # Requests per minute; 0 disables the limit.
    rate_limit_query_per_minute: int = 30
    rate_limit_login_per_minute: int = 20
    knowledge_min_confidence: float = 0.5
    # Cosine similarity at which a document counts as relevant without shared keywords. 0 disables
    # embeddings, leaving keyword search.
    knowledge_min_similarity: float = 0.62
    knowledge_stale_after_days: int = 365
    # Used by purge_history.py; 0 keeps history indefinitely.
    history_retention_days: int = 0
    log_format: str = "text"
    # Optional bearer token for Prometheus scrapes of /metrics; administrators can always read it.
    metrics_token: str = ""
    # Writes randomly generated orders into the local database on each dashboard load.
    # Off by default: it produces fake business data.
    live_simulate: bool = False
    seed_customers_per_domain: int = 80

    llm_provider: str = "auto"
    # Used when the selected provider fails (quota, invalid key, outage); the switch is recorded
    # in each response's trace. Empty disables the fallback.
    llm_fallback_provider: str = "ollama"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    google_api_key: str = ""
    google_model: str = "gemini-2.5-flash"
    xai_api_key: str = ""
    xai_model: str = "grok-2-latest"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-haiku-4-5"
    ollama_base_url: str = "http://127.0.0.1:11434/v1"
    ollama_model: str = "qwen3:4b"
    rag_embedding_model: str = "nomic-embed-text"
    chroma_db_path: str = ".chroma"
    mcp_access_token: str = ""
    # MCP exposes approved tools only. True also exposes ask_business, which can run generated SQL.
    mcp_allow_generated_sql: bool = False

    databricks_server_hostname: str = Field(
        default="",
        validation_alias=AliasChoices("DATABRICKS_SERVER_HOSTNAME", "DATABRICKS_HOST"),
    )
    databricks_http_path: str = ""
    databricks_warehouse_id: str = Field(
        default="",
        validation_alias=AliasChoices("DATABRICKS_WAREHOUSE_ID", "WAREHOUSE_ID"),
    )
    databricks_token: str = Field(
        default="",
        validation_alias=AliasChoices("DATABRICKS_TOKEN", "DATABRICKS_PAT"),
    )
    databricks_catalog: str = "banking"
    databricks_schema: str = "silver"
    databricks_domain_catalogs: str = "banking:banking,insurance:insurance,retail:retail"
    databricks_domain_schema: str = "silver"
    databricks_healthcare_tenant_catalogs: str = ""

    snowflake_account: str = ""
    snowflake_user: str = ""
    snowflake_password: str = ""
    snowflake_role: str = ""
    snowflake_warehouse: str = ""
    snowflake_database: str = ""
    snowflake_schema: str = "PUBLIC"


settings = Settings()
