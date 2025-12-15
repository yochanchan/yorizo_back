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
    """Reset booking-related tables for each test."""
    tables = [models.User.__table__, models.Expert.__table__, models.ConsultationBooking.__table__]
    models.Base.metadata.drop_all(bind=database.engine, tables=tables)
    models.Base.metadata.create_all(bind=database.engine, tables=tables)
    db = database.SessionLocal()
    try:
        db.query(models.ConsultationBooking).delete()
        db.query(models.Expert).delete()
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


def _seed_bookings(base_today: date):
    db = database.SessionLocal()
    try:
        user = models.User(id="demo-user", nickname=None)
        db.add(user)
        expert = models.Expert(name="テスト専門家")
        db.add(expert)
        db.commit()
        db.refresh(expert)

        past = models.ConsultationBooking(
            expert_id=expert.id,
            user_id=user.id,
            date=base_today - timedelta(days=1),
            time_slot="15:00-16:00",
            channel="online",
            name="Past Booking",
            status="pending",
        )
        first = models.ConsultationBooking(
            expert_id=expert.id,
            user_id=user.id,
            date=base_today + timedelta(days=1),
            time_slot="09:00-10:00",
            channel="online",
            name="First Future",
            status="pending",
        )
        second = models.ConsultationBooking(
            expert_id=expert.id,
            user_id=user.id,
            date=base_today + timedelta(days=1),
            time_slot="10:00-11:00",
            channel="in-person",
            name="Second Future",
            status="confirmed",
        )
        later = models.ConsultationBooking(
            expert_id=expert.id,
            user_id=user.id,
            date=base_today + timedelta(days=3),
            time_slot="12:00-13:00",
            channel="online",
            name="Later Future",
            status="pending",
        )
        db.add_all([past, first, second, later])
        db.commit()
        return {
            "first": str(first.id),
            "second": str(second.id),
            "later": str(later.id),
            "past": str(past.id),
            "expert_name": expert.name,
        }
    finally:
        db.close()


def test_list_consultations_filters_future_and_limits(monkeypatch, client_base: TestClient):
    base_today = date(2025, 12, 24)
    monkeypatch.setattr(booking_rules, "get_jst_today", lambda: base_today)
    bookings = _seed_bookings(base_today)

    resp = client_base.get("/api/consultations", params={"user_id": "demo-user", "limit": 2})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    items = data.get("bookings", [])

    assert len(items) == 2
    assert items[0]["id"] == bookings["first"]
    assert items[1]["id"] == bookings["second"]
    assert items[0]["expert_name"] == bookings["expert_name"]
    # Past booking should not appear
    returned_ids = {item["id"] for item in items}
    assert bookings["past"] not in returned_ids
    # Later booking trimmed by limit
    assert bookings["later"] not in returned_ids
