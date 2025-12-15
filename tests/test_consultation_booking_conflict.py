import json
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


def _create_expert_with_availability(dates_and_slots: list[tuple[date, list[str]]]) -> str:
    db = database.SessionLocal()
    try:
        expert = models.Expert(name="Conflict Expert")
        db.add(expert)
        db.commit()
        db.refresh(expert)
        for dt, slots in dates_and_slots:
            db.add(models.ExpertAvailability(expert_id=expert.id, date=dt, slots_json=json.dumps(slots)))
        db.commit()
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


def test_booking_same_slot_twice_returns_409(monkeypatch, client_base: TestClient):
    base_today = date(2025, 12, 24)
    monkeypatch.setattr(booking_rules, "get_jst_today", lambda: base_today)
    target_date = base_today + timedelta(days=1)

    expert_id = _create_expert_with_availability([(target_date, ["10:00-11:00"])])

    payload = _base_payload(expert_id, target_date, "10:00-11:00")
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

    expert_id = _create_expert_with_availability([(target_date, ["09:00-10:00"])])

    payload = _base_payload(expert_id, target_date, "09:00-10:00")
    resp = client_base.post("/api/consultations", json=payload)
    assert resp.status_code == 400, resp.text


def test_booking_with_invalid_time_slot(monkeypatch, client_base: TestClient):
    base_today = date(2025, 12, 24)
    monkeypatch.setattr(booking_rules, "get_jst_today", lambda: base_today)
    target_date = base_today + timedelta(days=1)

    expert_id = _create_expert_with_availability([(target_date, ["09:00-10:00"])])

    payload = _base_payload(expert_id, target_date, "10:00-11:00")
    resp = client_base.post("/api/consultations", json=payload)
    assert resp.status_code == 400, resp.text

    payload_missing_avail = _base_payload(expert_id, target_date + timedelta(days=1), "09:00-10:00")
    resp_missing = client_base.post("/api/consultations", json=payload_missing_avail)
    assert resp_missing.status_code == 400, resp_missing.text
