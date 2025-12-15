import os
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("APP_ENV", "local")

import models  # noqa: E402
import database  # noqa: E402
from app.services import booking_rules  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_booking_tables():
    tables = [
        models.User.__table__,
        models.Expert.__table__,
        models.ExpertAvailability.__table__,
        models.ConsultationBooking.__table__,
    ]
    models.Base.metadata.drop_all(bind=database.engine, tables=tables)
    models.Base.metadata.create_all(bind=database.engine, tables=tables)
    yield
    db = database.SessionLocal()
    try:
        for model in [models.ConsultationBooking, models.ExpertAvailability, models.Expert, models.User]:
            db.query(model).delete()
        db.commit()
    finally:
        db.close()


@pytest.fixture
def client_base() -> TestClient:
    sys.modules["models"] = models
    sys.modules["database"] = database
    from main import app  # noqa: E402

    return TestClient(app)


def _create_expert() -> str:
    db = database.SessionLocal()
    try:
        expert = models.Expert(name="Conflict Expert")
        db.add(expert)
        db.commit()
        db.refresh(expert)
        return expert.id
    finally:
        db.close()


def _base_payload(expert_id: str, target_date: date, time_slot: str):
    return {
        "expert_id": expert_id,
        "user_id": "demo-user",
        "date": target_date.isoformat(),
        "time_slot": time_slot,
        "channel": "online",
        "name": "Tester",
    }


def _add_booking(expert_id: str, booking_date: date, time_slot: str, status: str = "pending"):
    db = database.SessionLocal()
    try:
        db.add(
            models.ConsultationBooking(
                expert_id=expert_id,
                user_id="demo-user",
                date=booking_date,
                time_slot=time_slot,
                channel="online",
                status=status,
                name="Existing",
            )
        )
        db.commit()
    finally:
        db.close()


def test_booking_same_slot_twice_returns_409(monkeypatch, client_base: TestClient):
    base_today = date(2025, 12, 24)
    monkeypatch.setattr(booking_rules, "get_jst_today", lambda: base_today)
    target_date = base_today + timedelta(days=1)

    expert_id = _create_expert()

    payload = _base_payload(expert_id, target_date, booking_rules.DEFAULT_SLOTS[0])
    first = client_base.post("/api/consultations", json=payload)
    assert first.status_code == 200, first.text

    second = client_base.post("/api/consultations", json=payload)
    assert second.status_code == 409, second.text


@pytest.mark.parametrize(
    "target_date",
    [
        date(2025, 12, 24),  # same day
        date(2025, 12, 27),  # weekend
        date(2025, 12, 29),  # additional closure
        date(2026, 1, 1),  # holiday
        date(2026, 1, 22),  # beyond 28 days
    ],
)
def test_booking_outside_rules_is_rejected(monkeypatch, client_base: TestClient, target_date: date):
    base_today = date(2025, 12, 24)
    monkeypatch.setattr(booking_rules, "get_jst_today", lambda: base_today)

    expert_id = _create_expert()

    payload = _base_payload(expert_id, target_date, booking_rules.DEFAULT_SLOTS[0])
    resp = client_base.post("/api/consultations", json=payload)
    assert resp.status_code == 400, resp.text


def test_booking_with_invalid_time_slot(monkeypatch, client_base: TestClient):
    base_today = date(2025, 12, 24)
    monkeypatch.setattr(booking_rules, "get_jst_today", lambda: base_today)
    target_date = base_today + timedelta(days=1)

    expert_id = _create_expert()

    payload = _base_payload(expert_id, target_date, "16:00-17:00")
    resp = client_base.post("/api/consultations", json=payload)
    assert resp.status_code == 400, resp.text
    assert resp.json().get("detail") == "予約可能な時間帯ではありません"


def test_cancelled_booking_allows_rebooking(monkeypatch, client_base: TestClient):
    base_today = date(2025, 12, 24)
    monkeypatch.setattr(booking_rules, "get_jst_today", lambda: base_today)
    target_date = base_today + timedelta(days=1)
    slot = booking_rules.DEFAULT_SLOTS[1]

    expert_id = _create_expert()
    _add_booking(expert_id, target_date, slot, status="cancelled")

    payload = _base_payload(expert_id, target_date, slot)
    resp = client_base.post("/api/consultations", json=payload)
    assert resp.status_code == 200, resp.text
