from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
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
    db_host: str = Field(default="localhost", env="DB_HOST")
    db_port: int = Field(default=3306, env="DB_PORT")
    # NOTE: Azure App Service cannot use an app setting named "username";
    # use DB_USERNAME instead for environment configuration.
    db_username: str | None = Field(default=None, env="DB_USERNAME")
    db_password: str | None = Field(default=None, env="DB_PASSWORD")
    # Azure 本番の DB 名は "yorizo" 想定。未指定ならこの名前を使う。
    db_name: str | None = Field(default="yorizo", env="DB_NAME")
    database_url: str | None = Field(default=None, env="DATABASE_URL")

    openai_api_key: str | None = Field(default=None, env="OPENAI_API_KEY")
    openai_model_chat: str = Field(default="gpt-4.1-mini", env="OPENAI_MODEL_CHAT")
    openai_model_embedding: str = Field(default="text-embedding-3-small", env="OPENAI_MODEL_EMBEDDING")
    rag_persist_dir: str = Field(default="./rag_store", env="RAG_PERSIST_DIR")
    rag_enabled: bool = Field(default=True, env="ENABLE_RAG")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


def get_db_url(settings: "Settings") -> str:
    if settings.database_url:
        return settings.database_url

    if settings.db_username and settings.db_password and settings.db_name:
        return (
            f"mysql+asyncmy://{settings.db_username}:"
            f"{settings.db_password}@{settings.db_host}:{settings.db_port}/"
            f"{settings.db_name}"
        )

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
