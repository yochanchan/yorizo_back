import json
import os
import sys
from datetime import date
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


def _setup_expert_with_availability():
    db = database.SessionLocal()
    try:
        expert = models.Expert(name="Test Expert")
        db.add(expert)
        db.commit()
        db.refresh(expert)
        return expert.id
    finally:
        db.close()


def _add_availability(expert_id: str, availability: list[tuple[date, list[str]]]):
    db = database.SessionLocal()
    try:
        for dt, slots in availability:
            db.add(models.ExpertAvailability(expert_id=expert_id, date=dt, slots_json=json.dumps(slots)))
        db.commit()
    finally:
        db.close()


def _add_booking(expert_id: str, booking_date: date, time_slot: str, status: str = "pending"):
    db = database.SessionLocal()
    try:
        db.add(
            models.ConsultationBooking(
                expert_id=expert_id,
                user_id=None,
                date=booking_date,
                time_slot=time_slot,
                channel="online",
                status=status,
                name="Tester",
            )
        )
        db.commit()
    finally:
        db.close()


def test_availability_filters_closed_days_and_counts(monkeypatch, client_base: TestClient):
    base_today = date(2025, 12, 24)
    monkeypatch.setattr(booking_rules, "get_jst_today", lambda: base_today)

    expert_id = _setup_expert_with_availability()
    _add_availability(
        expert_id,
        [
            (date(2025, 12, 25), ["10:00", "13:00"]),
            (date(2025, 12, 26), ["09:00"]),
            (date(2025, 12, 27), ["10:00"]),  # weekend
            (date(2025, 12, 29), ["15:00"]),  # additional closure
            (date(2026, 1, 1), ["09:00"]),  # holiday
            (date(2026, 1, 5), ["10:00", "11:00"]),
            (date(2026, 1, 22), ["10:00"]),  # outside window
        ],
    )
    _add_booking(expert_id, date(2025, 12, 25), "13:00", status="confirmed")
    _add_booking(expert_id, date(2025, 12, 26), "09:00")
    _add_booking(expert_id, date(2026, 1, 5), "10:00")

    resp = client_base.get(f"/api/experts/{expert_id}/availability")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    availability = data.get("availability", [])
    dates = [item["date"] for item in availability]
    assert dates == ["2025-12-25", "2025-12-26", "2026-01-05"]

    day_25 = next(item for item in availability if item["date"] == "2025-12-25")
    assert day_25["booked_slots"] == ["13:00"]
    assert day_25["available_count"] == 1

    day_26 = next(item for item in availability if item["date"] == "2025-12-26")
    assert day_26["booked_slots"] == ["09:00"]
    assert day_26["available_count"] == 0

    day_105 = next(item for item in availability if item["date"] == "2026-01-05")
    assert day_105["booked_slots"] == ["10:00"]
    assert day_105["available_count"] == 1
