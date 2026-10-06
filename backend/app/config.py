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
    live_simulate: bool = True
    seed_customers_per_domain: int = 80

    llm_provider: str = "auto"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    xai_api_key: str = ""
    xai_model: str = "grok-2-latest"
    ollama_base_url: str = "http://127.0.0.1:11434/v1"
    ollama_model: str = "llama3.2"

    databricks_server_hostname: str = ""
    databricks_http_path: str = ""
    databricks_warehouse_id: str = ""
    databricks_token: str = ""
    databricks_catalog: str = "main"
    databricks_schema: str = "ryze_bi"


settings = Settings()
