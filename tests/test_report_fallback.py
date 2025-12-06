from types import SimpleNamespace
import os

os.environ.setdefault("APP_ENV", "local")

from app.services import reports as report_service


def test_generate_concerns_fallback(monkeypatch):
    # Force LLM failure to trigger fallback path
    monkeypatch.setattr(report_service, "chat_completion_json", lambda messages, max_tokens=None: "not-json")

    history = [SimpleNamespace(role="user", content="売上が不安です")]
    concerns = report_service.generate_concerns(
        conversation_text="",
        main_concern=None,
        documents_summary=[],
        history_messages=history,
    )

    assert concerns == ["売上が不安です"]
