from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

DRIVER_NORMALIZATION = {
    # async -> sync
    "mysql+asyncmy": "mysql+pymysql",
    # mysql connector flavors -> pymysql (default in requirements)
    "mysql+mysqlconnector": "mysql+pymysql",
    "mysql+mysqldb": "mysql+pymysql",
    "mysql": "mysql+pymysql",
}


class Settings(BaseSettings):
    db_host: str = Field(default="localhost", env="DB_HOST")
    db_port: int = Field(default=3306, env="DB_PORT")
    # NOTE: Azure App Service cannot use an app setting named "username";
    # use DB_USERNAME instead for environment configuration.
    db_username: str | None = Field(default=None, env="DB_USERNAME")
    db_password: str | None = Field(default=None, env="DB_PASSWORD")
    # Azure production DB name defaults to "yorizo" when not provided explicitly.
    db_name: str | None = Field(default="yorizo", env="DB_NAME")
    database_url: str | None = Field(default=None, env="DATABASE_URL")
    app_env: str | None = Field(default=None, env="APP_ENV")

    openai_api_key: str | None = Field(default=None, env="OPENAI_API_KEY")
    openai_model_chat: str = Field(default="gpt-4.1-mini", env="OPENAI_MODEL_CHAT")
    openai_model_embedding: str = Field(default="text-embedding-3-small", env="OPENAI_MODEL_EMBEDDING")
    openai_base_url: str | None = Field(default=None, env="OPENAI_BASE_URL")
    azure_openai_endpoint: str | None = Field(default=None, env="AZURE_OPENAI_ENDPOINT")
    azure_openai_api_key: str | None = Field(default=None, env="AZURE_OPENAI_API_KEY")
    azure_openai_chat_deployment: str | None = Field(
        default=None,
        validation_alias=AliasChoices("AZURE_OPENAI_CHAT_DEPLOYMENT", "AZURE_OPENAI_DEPLOYMENT"),
    )
    azure_openai_api_version: str = Field(default="2024-02-15-preview", env="AZURE_OPENAI_API_VERSION")
    rag_persist_dir: str = Field(default="./rag_store", env="RAG_PERSIST_DIR")
    rag_enabled: bool = Field(default=True, env="ENABLE_RAG")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


def get_db_url(settings: "Settings") -> str:
    """
    Resolve the database URL.

    The application always expects a MySQL URL.
    SQLite へのフォールバックや自動的なローカル SQLite 利用は行わず、
    設定不備は例外として検出します。
    """
    app_env = (settings.app_env or "").strip().lower()

    if settings.database_url:
        url_obj = make_url(settings.database_url)
        if not url_obj.drivername.startswith("mysql"):
            raise ValueError(f"DATABASE_URL must be a MySQL URL (got {url_obj.drivername})")
        return settings.database_url

    if settings.db_username and settings.db_password and settings.db_name:
        return (
            f"mysql+asyncmy://{settings.db_username}:"
            f"{settings.db_password}@{settings.db_host}:{settings.db_port}/"
            f"{settings.db_name}"
        )

    raise ValueError(
        "Database configuration is missing. "
        "Set a MySQL DATABASE_URL or DB_USERNAME/DB_PASSWORD/DB_NAME (APP_ENV is "
        f"'{app_env or ''}')."
    )


def normalize_db_url(url: str) -> str:
    """
    Convert async or variant MySQL driver URLs to sync equivalents so they can be used
    with the current synchronous SQLAlchemy engine/session setup.
    """
    url_obj = make_url(url)
    driver = url_obj.drivername
    if driver in DRIVER_NORMALIZATION:
        url_obj = url_obj.set(drivername=DRIVER_NORMALIZATION[driver])
    return url_obj.render_as_string(hide_password=False)


settings = Settings()

