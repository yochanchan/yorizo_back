import logging
import os
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    admin_bookings,
    case_examples,
    chat,
    company_profile,
    company_reports,
    consultations,
    conversations,
    diagnosis,
    documents,
    experts,
    homework,
    memory,
    rag,
    report,
    reports,
    speech,
)
from database import Base, engine
import models  # noqa: F401
from seed import seed_demo_data

logger = logging.getLogger(__name__)

default_origins = [
    "http://localhost:3000",
    "https://arimakinen-or-die-app-frontend-encsefebejdxdqav.canadacentral-01.azurewebsites.net",
]
cors_origins = os.getenv("CORS_ORIGINS")
env_origins = [origin.strip() for origin in cors_origins.split(",") if origin.strip()] if cors_origins else []
origins = list({*default_origins, *env_origins})

app = FastAPI(title="Yorizo API", version="0.1.0")


def _ensure_sqlite_columns() -> None:
    if engine.dialect.name != "sqlite":
        return

    def add_column(table: str, column: str, definition: str) -> None:
        if not column or not definition:
            return
        with engine.begin() as conn:
            cols = [row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()]
            # Some older local DBs may have a stray column named "TEXT" from past migrations.
            # Ignore it and only add the new column when truly missing.
            if column in cols:
                return
            try:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            except Exception:
                # If the column already exists or cannot be altered, continue without failing startup.
                pass

    add_column("conversations", "category", "TEXT")
    add_column("conversations", "status", "TEXT DEFAULT 'in_progress'")
    add_column("conversations", "step", "INTEGER")

    add_column("documents", "company_id", "TEXT")
    add_column("documents", "conversation_id", "TEXT")
    add_column("documents", "doc_type", "TEXT")
    add_column("documents", "period_label", "TEXT")
    add_column("documents", "storage_path", "TEXT DEFAULT ''")
    add_column("documents", "ingested", "INTEGER DEFAULT 0")

    add_column("homework_tasks", "timeframe", "TEXT")
    add_column("homework_tasks", "status", "TEXT DEFAULT 'pending'")
    add_column("consultation_bookings", "conversation_id", "TEXT")
    add_column("consultation_bookings", "meeting_url", "TEXT")
    add_column("consultation_bookings", "line_contact", "TEXT")

    add_column("company_profiles", "name", "TEXT")
    add_column("company_profiles", "employees", "INTEGER")
    add_column("company_profiles", "annual_revenue_range", "TEXT")

    add_column("financial_statements", "cash_and_deposits", "NUMERIC")
    add_column("financial_statements", "receivables", "NUMERIC")
    add_column("financial_statements", "inventory", "NUMERIC")
    add_column("financial_statements", "payables", "NUMERIC")
    add_column("financial_statements", "borrowings", "NUMERIC")
    add_column("financial_statements", "previous_sales", "NUMERIC")

    add_column("companies", "name", "TEXT")
    add_column("companies", "employees", "INTEGER")
    add_column("companies", "annual_revenue_range", "TEXT")


def _should_create_all() -> bool:
    env = (os.getenv("APP_ENV") or "").lower()
    enable_flag = os.getenv("ENABLE_CREATE_ALL", "").lower() in {"1", "true", "yes"}
    if engine.url.get_backend_name() == "sqlite":
        return True
    if env in {"local", "dev", "development"} or enable_flag:
        return True
    return False


@app.on_event("startup")
def on_startup() -> None:
    if _should_create_all():
        try:
            Base.metadata.create_all(bind=engine)
        except SQLAlchemyError as exc:
            logger.warning("Base.metadata.create_all failed; continuing without fatal error: %s", exc)
    else:
        logger.info(
            "Skipping Base.metadata.create_all on %s (APP_ENV=%s); run migrations or create tables separately.",
            engine.url.get_backend_name(),
            os.getenv("APP_ENV"),
        )
    _ensure_sqlite_columns()
    seed_demo_data()


app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router)
app.include_router(conversations.router, prefix="/api", tags=["conversations"])
app.include_router(company_profile.router, prefix="/api", tags=["company-profile"])
app.include_router(company_reports.router, prefix="/api", tags=["companies"])
app.include_router(consultations.router, prefix="/api", tags=["consultations"])
app.include_router(diagnosis.router, prefix="/api", tags=["diagnosis"])
app.include_router(memory.router, prefix="/api", tags=["memory"])
app.include_router(rag.router, prefix="/api", tags=["rag"])
app.include_router(documents.router, prefix="/api", tags=["documents"])
app.include_router(experts.router, prefix="/api", tags=["experts"])
app.include_router(homework.router, prefix="/api", tags=["homework"])
app.include_router(report.router, prefix="/api", tags=["report"])
app.include_router(reports.router, prefix="/api", tags=["reports"])
app.include_router(admin_bookings.router, prefix="/api", tags=["admin"])
app.include_router(case_examples.router, prefix="/api", tags=["case-examples"])
app.include_router(speech.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
