from typing import List

import pytest
from fastapi.testclient import TestClient

import models
from database import SessionLocal, engine


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
    from app import rag as rag_module
    from app.rag import store
    from app.core import openai_client
    import main

    async def fake_embed_texts(texts: str | List[str]):
        if isinstance(texts, str):
            texts = [texts]
        # simple deterministic embedding based on text length
        return [[float(len(t)), float(len(t) % 10), float(len(t) % 5)] for t in texts]

    async def fake_chat(messages, with_system_prompt: bool = True):
        return "mocked answer"

    monkeypatch.setattr(store, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(openai_client, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(openai_client, "generate_chat_reply", fake_chat)
    monkeypatch.setattr("api.rag.generate_chat_reply", fake_chat, raising=False)

    return TestClient(main.app)


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
    # prepare one document
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
