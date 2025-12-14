from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

DEFAULT_SQLITE_URL = "sqlite:///./yorizo.db"
DRIVER_NORMALIZATION = {
    # async -> sync
    "mysql+asyncmy": "mysql+pymysql",
    "sqlite+aiosqlite": "sqlite",
    # mysql connector flavors -> pymysql (default in requirements)
    "mysql+mysqlconnector": "mysql+pymysql",
    "mysql+mysqldb": "mysql+pymysql",
    "mysql": "mysql+pymysql",
}


class Settings(BaseSettings):
    db_host: str = Field(default="localhost", validation_alias=AliasChoices("DB_HOST"))
    db_port: int = Field(default=3306, validation_alias=AliasChoices("DB_PORT"))
    # NOTE: Azure App Service cannot use an app setting named "username";
    # use DB_USERNAME instead for environment configuration.
    db_username: str | None = Field(default=None, validation_alias=AliasChoices("DB_USERNAME"))
    db_password: str | None = Field(default=None, validation_alias=AliasChoices("DB_PASSWORD"))
    # Azure production DB name defaults to "yorizo" when not provided explicitly.
    db_name: str | None = Field(default="yorizo", validation_alias=AliasChoices("DB_NAME"))
    database_url: str | None = Field(default=None, validation_alias=AliasChoices("DATABASE_URL"))
    app_env: str | None = Field(default=None, validation_alias=AliasChoices("APP_ENV"))

    openai_api_key: str | None = Field(default=None, validation_alias=AliasChoices("OPENAI_API_KEY"))
    openai_model_chat: str = Field(default="gpt-4.1-mini", validation_alias=AliasChoices("OPENAI_MODEL_CHAT"))
    openai_model_embedding: str = Field(
        default="text-embedding-3-small",
        validation_alias=AliasChoices("OPENAI_MODEL_EMBEDDING"),
    )
    openai_base_url: str | None = Field(default=None, validation_alias=AliasChoices("OPENAI_BASE_URL"))
    azure_openai_endpoint: str | None = Field(
        default=None,
        validation_alias=AliasChoices("AZURE_OPENAI_ENDPOINT"),
    )
    azure_openai_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("AZURE_OPENAI_API_KEY"),
    )
    azure_openai_chat_deployment: str | None = Field(
        default=None,
        validation_alias=AliasChoices("AZURE_OPENAI_CHAT_DEPLOYMENT", "AZURE_OPENAI_DEPLOYMENT"),
    )
    azure_openai_api_version: str = Field(
        default="2024-02-15-preview",
        validation_alias=AliasChoices("AZURE_OPENAI_API_VERSION"),
    )
    azure_speech_key: str | None = Field(default=None, validation_alias=AliasChoices("AZURE_SPEECH_KEY"))
    azure_speech_region: str | None = Field(default=None, validation_alias=AliasChoices("AZURE_SPEECH_REGION"))
    rag_persist_dir: str = Field(default="./rag_store", validation_alias=AliasChoices("RAG_PERSIST_DIR"))
    rag_enabled: bool = Field(default=True, validation_alias=AliasChoices("ENABLE_RAG"))

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


def get_db_url(settings: "Settings") -> str:
    app_env = (settings.app_env or "").strip().lower()
    if app_env in {"local", "dev", "development"}:
        # 1) ローカルは常に SQLite を使い、MySQL+SSL 強制を避ける
        return DEFAULT_SQLITE_URL

    # 2) 明示的に DATABASE_URL があればそれを優先
    if settings.database_url:
        return settings.database_url

    # 3) DB_* が揃っていれば MySQL 接続文字列を組み立てる
    if settings.db_username and settings.db_password and settings.db_name:
        return (
            f"mysql+asyncmy://{settings.db_username}:"
            f"{settings.db_password}@{settings.db_host}:{settings.db_port}/"
            f"{settings.db_name}"
        )

    # 4) 本番系の指定があるのに DB 設定が無い場合は落として気づけるようにする
    if app_env in {"production", "prod", "staging", "azure"}:
        raise ValueError("APP_ENV is set to production/staging but DB configuration is missing")

    # 5) それ以外は SQLite フォールバック
    return DEFAULT_SQLITE_URL


def normalize_db_url(url: str) -> str:
    """
    Convert async driver URLs to sync equivalents so they can be used
    with the current synchronous SQLAlchemy engine/session setup.
    """
    url_obj = make_url(url)
    driver = url_obj.drivername
    if driver in DRIVER_NORMALIZATION:
        url_obj = url_obj.set(drivername=DRIVER_NORMALIZATION[driver])
    return url_obj.render_as_string(hide_password=False)


settings = Settings()
