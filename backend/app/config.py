from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "RYZE AI/BI"
    jwt_secret: str = "ryze-dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 480

    database_url: str = "sqlite:///./ryze_bi.db"
    warehouse: str = "local"
    demo_mode: bool = True
    query_row_limit: int = 200
    query_timeout_seconds: int = 8
    llm_timeout_seconds: int = 30
    dashboard_cache_seconds: int = 30
    live_simulate: bool = True
    seed_customers_per_domain: int = 80

    llm_provider: str = "auto"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
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

    snowflake_account: str = ""
    snowflake_user: str = ""
    snowflake_password: str = ""
    snowflake_role: str = ""
    snowflake_warehouse: str = ""
    snowflake_database: str = ""
    snowflake_schema: str = "PUBLIC"


settings = Settings()
