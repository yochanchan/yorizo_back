import os

from fastapi.testclient import TestClient

os.environ.setdefault("APP_ENV", "local")

import models  # noqa: E402,F401
import database  # noqa: E402,F401
from app.api import speech  # noqa: E402
from app.core import config  # noqa: E402
from main import app  # noqa: E402


def test_speech_token_missing_config(monkeypatch):
    speech._token_cache.clear()
    monkeypatch.setattr(config.settings, "azure_speech_key", None, raising=False)
    monkeypatch.setattr(config.settings, "azure_speech_region", None, raising=False)

    client = TestClient(app)
    resp = client.post("/api/speech/token")
    assert resp.status_code in (500, 503)
    data = resp.json()
    assert "detail" in data


def test_speech_token_success(monkeypatch):
    speech._token_cache.clear()
    monkeypatch.setattr(config.settings, "azure_speech_key", "test-key", raising=False)
    monkeypatch.setattr(config.settings, "azure_speech_region", "japaneast", raising=False)

    def _fake_fetch(region: str, key: str) -> str:
        assert region == "japaneast"
        assert key == "test-key"
        return "token-123"

    monkeypatch.setattr(speech, "_fetch_token_from_azure", _fake_fetch)

    client = TestClient(app)
    resp = client.post("/api/speech/token")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"token": "token-123", "region": "japaneast", "expires_in": 600}
