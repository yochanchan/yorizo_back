from pathlib import Path
import os
import sys
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

from app.schemas.chat import ChatTurnResponse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("APP_ENV", "local")

import models  # noqa: E402
import database  # noqa: E402


FALLBACK_SNIPPET = "Yorizo が考えるのに失敗しました"


@pytest.fixture(autouse=True)
def _reset_chat_tables():
    """Ensure chat-related tables exist and are empty for isolation."""
    tables = [
        models.User.__table__,
        models.CompanyProfile.__table__,
        models.Memory.__table__,
        models.Document.__table__,
        models.Conversation.__table__,
        models.Message.__table__,
    ]
    models.Base.metadata.drop_all(bind=database.engine, tables=tables)
    models.Base.metadata.create_all(bind=database.engine, tables=tables)
    db = database.SessionLocal()
    try:
        db.query(models.Message).delete()
        db.query(models.Conversation).delete()
        db.query(models.Document).delete()
        db.query(models.Memory).delete()
        db.query(models.CompanyProfile).delete()
        db.query(models.User).delete()
        db.commit()
    finally:
        db.close()


@pytest.fixture
def client_base() -> TestClient:
    """Base TestClient with shared module wiring."""
    sys.modules["models"] = models
    sys.modules["database"] = database
    from main import app  # noqa: E402

    return TestClient(app)


def _post_chat(client: TestClient, payload: Dict[str, Any]):
    return client.post("/api/chat", json=payload)


def _mock_chat_json(monkeypatch, *, fail_at: Optional[List[int]] = None):
    """Patch chat_json_safe to return deterministic results, with optional failures."""
    from app.core import openai_client as oc
    from app.services import chat_flow as chat_flow_service

    counter = {"n": 0}

    async def _stub(prompt_id, messages, max_tokens=None):
        counter["n"] += 1
        if fail_at and counter["n"] in fail_at:
            return oc.LlmResult(ok=False, error=oc.LlmError(code="bad_json", message="broken"))
        return oc.LlmResult(
            ok=True,
            value={
                "reply": "stub reply",
                "question": "次は？",
                "options": [],
                "allow_free_text": True,
                "done": False,
            },
        )

    monkeypatch.setattr(chat_flow_service, "chat_json_safe", _stub)
    return counter


def test_chat_fallback_keeps_response_shape(client_base: TestClient, monkeypatch):
    """LLM failure is folded into fallback response without breaking shape."""
    _mock_chat_json(monkeypatch, fail_at=[1])

    payload = {
        "user_id": "u-chat-fallback",
        "message": "フォールバックテスト",
    }
    resp = _post_chat(client_base, payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert set(data.keys()) == {
        "conversation_id",
        "reply",
        "question",
        "options",
        "allow_free_text",
        "step",
        "done",
    }

    assert FALLBACK_SNIPPET in data["reply"]
    assert data["done"] is False
    assert isinstance(data["options"], list)
    assert isinstance(data["allow_free_text"], bool)


def test_fallback_does_not_persist_assistant_on_custom_reply(client_base: TestClient, monkeypatch):
    """Fallback is detected via flags, not fallback message text."""
    _mock_chat_json(monkeypatch, fail_at=[1])
    from app.services import chat_flow as chat_flow_service

    def _custom_fallback(conv):
        return ChatTurnResponse(
            conversation_id=conv.id,
            reply="custom fallback",
            question="",
            options=[],
            allow_free_text=True,
            step=conv.step or 0,
            done=False,
        )

    monkeypatch.setattr(chat_flow_service, "_build_fallback_response", _custom_fallback)

    payload = {
        "user_id": "u-chat-fallback-custom",
        "message": "fallback",
    }
    resp = _post_chat(client_base, payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    conv_id = data["conversation_id"]

    db = database.SessionLocal()
    try:
        conversation = db.query(models.Conversation).filter(models.Conversation.id == conv_id).first()
        messages = (
            db.query(models.Message)
            .filter(models.Message.conversation_id == conv_id)
            .order_by(models.Message.created_at.asc())
            .all()
        )
    finally:
        db.close()

    assert data["reply"] == "custom fallback"
    assert [msg.role for msg in messages] == ["user"]
    assert conversation.step == 0


def test_guided_chat_step_and_done_are_server_managed(client_base: TestClient, monkeypatch):
    """Server increments step and forces done at 5 turns."""
    _mock_chat_json(monkeypatch)

    conversation_id = None
    steps: List[int] = []
    dones: List[bool] = []
    for i in range(5):
        payload = {"user_id": "u-chat-steps", "message": f"msg-{i}"}
        if conversation_id:
            payload["conversation_id"] = conversation_id
        resp = _post_chat(client_base, payload)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        conversation_id = conversation_id or data["conversation_id"]
        steps.append(data["step"])
        dones.append(data["done"])

    assert steps == [1, 2, 3, 4, 5]
    assert dones == [False, False, False, False, True]

    db = database.SessionLocal()
    try:
        conversation = db.query(models.Conversation).filter(models.Conversation.id == conversation_id).first()
    finally:
        db.close()

    assert conversation.step == 5
    assert conversation.status == "completed"


def test_fallback_does_not_advance_step(client_base: TestClient, monkeypatch):
    """When a fallback happens mid-conversation, the step counter stays put."""
    _mock_chat_json(monkeypatch, fail_at=[3])

    conversation_id = None
    steps: List[int] = []
    dones: List[bool] = []
    for i in range(6):
        payload = {"user_id": "u-chat-fallback-step", "message": f"msg-{i}"}
        if conversation_id:
            payload["conversation_id"] = conversation_id
        resp = _post_chat(client_base, payload)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        conversation_id = conversation_id or data["conversation_id"]
        steps.append(data["step"])
        dones.append(data["done"])

    assert steps == [1, 2, 2, 3, 4, 5]
    assert dones[-1] is True

    db = database.SessionLocal()
    try:
        conversation = db.query(models.Conversation).filter(models.Conversation.id == conversation_id).first()
    finally:
        db.close()

    assert conversation.step == 5
    assert conversation.status == "completed"
