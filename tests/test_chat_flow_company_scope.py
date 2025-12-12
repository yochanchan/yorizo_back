import asyncio

import os
import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

import models
import database
from app.schemas.chat import ChatTurnRequest

os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"


@pytest.fixture(autouse=True)
def _prepare_db(monkeypatch):
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionTesting = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", SessionTesting)
    from app.rag import store as rag_store
    monkeypatch.setattr(rag_store, "SessionLocal", SessionTesting)

    models.Base.metadata.drop_all(bind=engine)
    models.Base.metadata.create_all(bind=engine)


@pytest.mark.anyio
async def test_run_guided_chat_uses_company_scope(monkeypatch):
    from app.services import chat_flow
    from app.services import rag as rag_service
    from app.core import openai_client

    captured = {}

    async def fake_retrieve_context(*, db, user_id, company_id, query, top_k):
        captured["company_id"] = company_id
        captured["user_id"] = user_id
        captured["query"] = query
        captured["top_k"] = top_k
        return ["ctx"]

    async def fake_chat_json_safe(prompt_id, messages, max_tokens=None, temperature=None):
        return openai_client.LlmResult(
            ok=True,
            value={
                "reply": "ok",
                "question": "",
                "options": [],
                "allow_free_text": True,
                "done": False,
                "step": 1,
            },
        )

    monkeypatch.setattr(rag_service, "retrieve_context", fake_retrieve_context)
    monkeypatch.setattr(chat_flow, "chat_json_safe", fake_chat_json_safe)

    db = database.SessionLocal()
    try:
        payload = ChatTurnRequest(user_id="u-1", company_id="c-1", message="hello")
        result = await chat_flow.run_guided_chat(payload, db)
    finally:
        db.close()

    assert captured["company_id"] == "c-1"
    assert result.conversation_id
    assert result.reply == "ok"
