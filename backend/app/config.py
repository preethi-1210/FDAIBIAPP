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
