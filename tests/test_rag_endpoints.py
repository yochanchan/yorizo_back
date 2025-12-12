from typing import List
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import models  # noqa: E402
import database  # noqa: E402

from app.core.openai_client import LlmError, LlmResult  # noqa: E402


@pytest.fixture
def client(monkeypatch) -> TestClient:
    """Test client with OpenAI calls mocked out."""
    sys.modules["models"] = models
    sys.modules["database"] = database

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

    from app.core import openai_client as backend_openai_client
    sys.modules["app.core.openai_client"] = backend_openai_client

    from app import rag as rag_module  # noqa: F401
    from app.rag import store
    from main import app

    async def fake_embed_texts(texts: str | List[str]):
        if isinstance(texts, str):
            texts = [texts]
        return [[float(len(t)), float(len(t) % 10), float(len(t) % 5)] for t in texts]

    async def fake_chat_text_safe(prompt_id: str, messages, temperature: float = 0.4):
        return LlmResult(ok=True, value="mocked answer")

    monkeypatch.setattr(store, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(backend_openai_client, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(backend_openai_client, "chat_text_safe", fake_chat_text_safe)
    monkeypatch.setattr("app.api.rag.chat_text_safe", fake_chat_text_safe, raising=False)

    # Ensure tables exist for the patched in-memory DB
    models.Base.metadata.create_all(bind=database.engine)

    return TestClient(app)


def test_create_and_search(client: TestClient):
    create_payload = {
        "user_id": "u1",
        "documents": [
            {"title": "test doc", "text": "This is a RAG test document.", "metadata": {"k": "v"}}
        ],
    }
    resp = client.post("/api/rag/documents", json=create_payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["documents"][0]["title"] == "test doc"
    doc_id = data["documents"][0]["id"]

    search_payload = {"user_id": "u1", "query": "test", "top_k": 3}
    resp = client.post("/api/rag/search", json=search_payload)
    assert resp.status_code == 200, resp.text
    matches = resp.json()["matches"]
    assert len(matches) >= 1
    assert matches[0]["id"] == doc_id


def test_chat_returns_citations(client: TestClient):
    resp = client.post(
        "/api/rag/documents",
        json={"user_id": "chat-user", "documents": [{"title": "Doc", "text": "Chat document"}]},
    )
    assert resp.status_code == 200, resp.text
    doc_id = resp.json()["documents"][0]["id"]

    chat_payload = {
        "user_id": "chat-user",
        "messages": [{"role": "user", "content": "Tell me about the document"}],
        "top_k": 3,
    }
    resp = client.post("/api/rag/chat", json=chat_payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["answer"] == "mocked answer"
    assert doc_id in data["citations"]


def test_rag_chat_fallback_on_llm_failure(client: TestClient, monkeypatch):
    from app.api import rag as rag_api

    resp = client.post(
        "/api/rag/documents",
        json={"user_id": "chat-user", "documents": [{"title": "Doc", "text": "Chat document"}]},
    )
    assert resp.status_code == 200, resp.text

    async def fail_chat(prompt_id, messages, temperature: float = 0.4):
        return LlmResult(ok=False, error=LlmError(code="test", message="boom"))

    monkeypatch.setattr("app.api.rag.chat_text_safe", fail_chat, raising=False)

    chat_payload = {
        "user_id": "chat-user",
        "messages": [{"role": "user", "content": "Tell me about the document"}],
        "top_k": 3,
    }
    resp = client.post("/api/rag/chat", json=chat_payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["answer"] == rag_api.FALLBACK_RAG_MESSAGE
    assert data["contexts"] == []
    assert data["citations"] == []
