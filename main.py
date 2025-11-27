import os
from fastapi import FastAPI
from sqlalchemy import text
from fastapi.middleware.cors import CORSMiddleware

from api import (
    admin_bookings,
    case_examples,
    chat,
    company_profile,
    conversations,
    diagnosis,
    documents,
    experts,
    homework,
    memory,
    rag,
    report,
)
from database import Base, engine
import models  # noqa: F401
from seed import seed_demo_data

default_origins = ["http://localhost:3000"]
cors_origins = os.getenv("CORS_ORIGINS")
origins = [origin.strip() for origin in cors_origins.split(",")] if cors_origins else default_origins

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


@app.on_event("startup")
def on_startup() -> None:
    Base.metadata.create_all(bind=engine)
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
app.include_router(diagnosis.router, prefix="/api", tags=["diagnosis"])
app.include_router(memory.router, prefix="/api", tags=["memory"])
app.include_router(rag.router, prefix="/api", tags=["rag"])
app.include_router(documents.router, prefix="/api", tags=["documents"])
app.include_router(experts.router, prefix="/api", tags=["experts"])
app.include_router(homework.router, prefix="/api", tags=["homework"])
app.include_router(report.router, prefix="/api", tags=["report"])
app.include_router(admin_bookings.router, prefix="/api", tags=["admin"])
app.include_router(case_examples.router, prefix="/api", tags=["case-examples"])


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
