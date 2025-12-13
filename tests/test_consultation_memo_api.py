import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("APP_ENV", "local")

import models  # noqa: E402
import database  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_memo_tables():
    """Ensure memo-related tables are recreated for each test."""
    tables = [
        models.User.__table__,
        models.Conversation.__table__,
        models.Message.__table__,
        models.ConsultationMemo.__table__,
    ]
    models.Base.metadata.drop_all(bind=database.engine, tables=tables)
    models.Base.metadata.create_all(bind=database.engine, tables=tables)
    db = database.SessionLocal()
    try:
        db.query(models.Message).delete()
        db.query(models.ConsultationMemo).delete()
        db.query(models.Conversation).delete()
        db.query(models.User).delete()
        db.commit()
    finally:
        db.close()


@pytest.fixture
def client_base() -> TestClient:
    """Base TestClient wired to the local app instance."""
    sys.modules["models"] = models
    sys.modules["database"] = database
    from main import app  # noqa: E402

    return TestClient(app)


def _create_conversation_with_message():
    db = database.SessionLocal()
    try:
        conv = models.Conversation(title="memo-test")
        db.add(conv)
        db.commit()
        db.refresh(conv)
        db.add(models.Message(conversation_id=conv.id, role="user", content="first message"))
        db.commit()
        return conv.id
    finally:
        db.close()


def test_get_memo_generates_and_reuses_existing_record(client_base: TestClient, monkeypatch):
    from app.api import conversations as conversations_api

    calls = {"count": 0}

    async def fake_generate(payload):
        calls["count"] += 1
        return ["current-point"], ["important-point"]

    monkeypatch.setattr(conversations_api, "generate_consultation_memo", fake_generate)

    conversation_id = _create_conversation_with_message()

    resp1 = client_base.get(f"/api/conversations/{conversation_id}/memo")
    assert resp1.status_code == 200, resp1.text
    data1 = resp1.json()
    assert data1["current_points"] == ["current-point"]
    assert data1["important_points"] == ["important-point"]
    assert data1["updated_at"]

    resp2 = client_base.get(f"/api/conversations/{conversation_id}/memo")
    assert resp2.status_code == 200, resp2.text
    data2 = resp2.json()

    assert data2["current_points"] == data1["current_points"]
    assert data2["important_points"] == data1["important_points"]
    assert data2["updated_at"]
    assert calls["count"] == 1
