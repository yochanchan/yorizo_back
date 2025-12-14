import json
import os
import sys
from datetime import datetime, timedelta
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
        models.ConsultationMemo.__table__,
    ]
    models.Base.metadata.drop_all(bind=database.engine, tables=tables)
    models.Base.metadata.create_all(bind=database.engine, tables=tables)
    db = database.SessionLocal()
    try:
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


def _create_memos():
    db = database.SessionLocal()
    try:
        user = models.User(id="demo-user", nickname=None)
        db.add(user)
        db.commit()

        older_conv = models.Conversation(user_id=user.id, title="older")
        newer_conv = models.Conversation(user_id=user.id, title="newer")
        db.add_all([older_conv, newer_conv])
        db.commit()
        db.refresh(older_conv)
        db.refresh(newer_conv)

        older_memo = models.ConsultationMemo(
            conversation_id=older_conv.id,
            current_points=json.dumps(["古い current", "other"]),
            important_points=json.dumps(["古い important"]),
            created_at=datetime.utcnow() - timedelta(days=5),
        )
        newer_memo = models.ConsultationMemo(
            conversation_id=newer_conv.id,
            current_points=json.dumps(["新しい current"]),
            important_points=json.dumps(["新しい important", "extra"]),
            created_at=datetime.utcnow() - timedelta(days=1),
        )
        db.add_all([older_memo, newer_memo])
        db.commit()
        return {
            "older_conv_id": str(older_conv.id),
            "newer_conv_id": str(newer_conv.id),
            "older_current": "古い current",
            "older_important": "古い important",
            "newer_current": "新しい current",
            "newer_important": "新しい important",
        }
    finally:
        db.close()


def test_list_consultation_memos_returns_existing_previews(client_base: TestClient):
    memos = _create_memos()

    resp = client_base.get("/api/consultation-memos", params={"user_id": "demo-user", "limit": 5})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    items = data.get("memos", [])

    assert len(items) == 2
    # Ordered by created_at desc
    assert items[0]["conversation_id"] == memos["newer_conv_id"]
    assert items[0]["current_point_preview"] == memos["newer_current"]
    assert items[0]["important_point_preview"] == memos["newer_important"]

    assert items[1]["conversation_id"] == memos["older_conv_id"]
    assert items[1]["current_point_preview"] == memos["older_current"]
    assert items[1]["important_point_preview"] == memos["older_important"]


def test_list_consultation_memos_does_not_generate_missing(client_base: TestClient):
    db = database.SessionLocal()
    try:
        user = models.User(id="demo-user", nickname=None)
        db.add(user)
        conv = models.Conversation(user_id=user.id, title="no memo yet")
        db.add(conv)
        db.commit()
        memo_count_before = db.query(models.ConsultationMemo).count()
    finally:
        db.close()

    resp = client_base.get("/api/consultation-memos", params={"user_id": "demo-user"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data.get("memos") == []

    db = database.SessionLocal()
    try:
        memo_count_after = db.query(models.ConsultationMemo).count()
    finally:
        db.close()
    assert memo_count_after == memo_count_before
