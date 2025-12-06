import logging
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    admin_bookings,
    case_examples,
    chat,
    company_profile,
    company_reports,
    conversations,
    diagnosis,
    documents,
    experts,
    homework,
    memory,
    rag,
    report,
    reports,
)
from database import engine
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


@app.on_event("startup")
def on_startup() -> None:
    try:
        with engine.connect() as connection:
            connection.execute("SELECT 1")
    except Exception as exc:  # noqa: BLE001
        logger.error("Database connectivity check failed during startup: %s", exc)
        # Avoid crashing the process to keep health endpoint reachable,
        # but clearly surface the issue in logs.
    else:
        logger.info("Database connectivity check succeeded on %s", engine.url.get_backend_name())

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


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
