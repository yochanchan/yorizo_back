from typing import List
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]  # backend directory
PROJECT_ROOT = ROOT.parent  # repository root
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend import models  # type: ignore  # noqa: E402
from backend.database import SessionLocal, engine  # type: ignore  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_rag_table():
    """Ensure rag_documents schema matches the model and is empty before each test."""
    models.Base.metadata.drop_all(bind=engine, tables=[models.RAGDocument.__table__])
    models.Base.metadata.create_all(bind=engine, tables=[models.RAGDocument.__table__])
    db = SessionLocal()
    try:
        db.query(models.RAGDocument).delete()
        db.commit()
    finally:
        db.close()


@pytest.fixture
def client(monkeypatch) -> TestClient:
    """Test client with OpenAI calls mocked out."""
    # Align module aliases so bare imports in app code resolve to the same modules
    sys.modules["models"] = models
    from backend import database as database_module
    sys.modules["database"] = database_module

    from backend.app.core import openai_client as backend_openai_client
    sys.modules["app.core.openai_client"] = backend_openai_client

    from backend.app import rag as rag_module  # noqa: F401
    from backend.app.rag import store
    from backend.main import app

    # Avoid real OpenAI calls
    monkeypatch.setattr(backend_openai_client.settings, "openai_api_key", "test-key")

    class _DummyClient:
        def __init__(self):
            self.embeddings = self

        def create(self, model, input):
            texts = [input] if isinstance(input, str) else list(input)
            return type("Resp", (), {"data": [type("Item", (), {"embedding": [float(len(t))]})() for t in texts]})

    monkeypatch.setattr(backend_openai_client, "get_client", lambda: _DummyClient())

    async def fake_embed_texts(texts: str | List[str]):
        if isinstance(texts, str):
            texts = [texts]
        return [[float(len(t)), float(len(t) % 10), float(len(t) % 5)] for t in texts]

    async def fake_chat(messages, with_system_prompt: bool = True):
        return "mocked answer"

    monkeypatch.setattr(store, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(backend_openai_client, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(backend_openai_client, "generate_chat_reply", fake_chat)
    monkeypatch.setattr("backend.api.rag.generate_chat_reply", fake_chat, raising=False)
    monkeypatch.setattr("api.rag.generate_chat_reply", fake_chat, raising=False)

    return TestClient(app)


def test_create_and_search(client: TestClient):
    create_payload = {
        "user_id": "u1",
        "documents": [
            {"title": "テスト", "text": "これはRAGのテスト用ドキュメントです。", "metadata": {"k": "v"}}
        ],
    }
    resp = client.post("/api/rag/documents", json=create_payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["documents"][0]["title"] == "テスト"
    doc_id = data["documents"][0]["id"]

    search_payload = {"user_id": "u1", "query": "テスト", "top_k": 3}
    resp = client.post("/api/rag/search", json=search_payload)
    assert resp.status_code == 200, resp.text
    matches = resp.json()["matches"]
    assert len(matches) >= 1
    assert matches[0]["id"] == doc_id


def test_chat_returns_citations(client: TestClient):
    resp = client.post(
        "/api/rag/documents",
        json={"user_id": "chat-user", "documents": [{"title": "Doc", "text": "チャット用ドキュメント"}]},
    )
    assert resp.status_code == 200, resp.text
    doc_id = resp.json()["documents"][0]["id"]

    chat_payload = {
        "user_id": "chat-user",
        "messages": [{"role": "user", "content": "ドキュメントについて教えて"}],
        "top_k": 3,
    }
    resp = client.post("/api/rag/chat", json=chat_payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["answer"] == "mocked answer"
    assert doc_id in data["citations"]
