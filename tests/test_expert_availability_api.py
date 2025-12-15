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


def _setup_expert():
    db = database.SessionLocal()
    try:
        expert = models.Expert(name="Test Expert")
        db.add(expert)
        db.commit()
        db.refresh(expert)
        return expert.id
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


def test_availability_uses_default_slots_and_ignores_old_slots(monkeypatch, client_base: TestClient):
    base_today = date(2025, 12, 24)
    monkeypatch.setattr(booking_rules, "get_jst_today", lambda: base_today)

    expert_id = _setup_expert()

    first_open = base_today + timedelta(days=1)  # 2025-12-25
    second_open = base_today + timedelta(days=2)  # 2025-12-26
    weekend = base_today + timedelta(days=3)  # 2025-12-27 (excluded)
    extra_closed = date(2025, 12, 29)
    new_year = date(2026, 1, 1)

    _add_booking(expert_id, first_open, "10:00-11:00", status="confirmed")
    _add_booking(expert_id, first_open, "16:00-17:00", status="pending")  # should be ignored
    _add_booking(expert_id, second_open, "11:00-12:00", status="cancelled")  # cancelled should free the slot
    _add_booking(expert_id, weekend, "14:00-15:00", status="pending")  # excluded by closed day rule

    resp = client_base.get(f"/api/experts/{expert_id}/availability")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    availability = data.get("availability", [])
    assert availability, "availability should not be empty within the window"

    # All days share the same default slots and expose booked_slots/available_count
    assert all(item["slots"] == booking_rules.DEFAULT_SLOTS for item in availability)
    assert all("booked_slots" in item and "available_count" in item for item in availability)

    dates = {item["date"] for item in availability}
    assert first_open.isoformat() in dates
    assert second_open.isoformat() in dates
    assert weekend.isoformat() not in dates
    assert extra_closed.isoformat() not in dates
    assert new_year.isoformat() not in dates

    day1 = next(item for item in availability if item["date"] == first_open.isoformat())
    assert day1["booked_slots"] == ["10:00-11:00"]
    assert day1["available_count"] == len(booking_rules.DEFAULT_SLOTS) - 1

    day2 = next(item for item in availability if item["date"] == second_open.isoformat())
    assert day2["booked_slots"] == []
    assert day2["available_count"] == len(booking_rules.DEFAULT_SLOTS)
