import logging
import os

from sqlalchemy import create_engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import get_db_url, normalize_db_url, settings

logger = logging.getLogger(__name__)

# Normalize DB URL aggressively to ensure we always use pymysql for MySQL.
raw_url = get_db_url(settings)
normalized_url = normalize_db_url(raw_url)
url_obj = make_url(normalized_url)
if url_obj.drivername.startswith("mysql") and url_obj.drivername != "mysql+pymysql":
    url_obj = url_obj.set(drivername="mysql+pymysql")

# Ensure utf8mb4 for MySQL connections
if url_obj.drivername.startswith("mysql"):
    query = dict(url_obj.query) if url_obj.query else {}
    query.setdefault("charset", "utf8mb4")
    url_obj = url_obj.set(query=query)

DATABASE_URL = url_obj.render_as_string(hide_password=False)

connect_args: dict = {}
if url_obj.drivername.startswith("mysql"):
    # Allow overriding CA path for MySQL SSL; default keeps existing behavior
    ca_path = os.getenv("DB_SSL_CA") or "/etc/ssl/certs/ca-certificates.crt"
    connect_args["ssl"] = {"ca": ca_path}
    connect_args.setdefault("charset", "utf8mb4")

# Log DSN without password for Azure diagnostics
safe_url = url_obj.set(password="***").render_as_string(hide_password=False)
logger.info("Connecting DB with URL: %s", safe_url)

# ASSUMPTION: Using sync engine for now; can be swapped to async engine when persistence is added.
engine = create_engine(DATABASE_URL, echo=False, future=True, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
