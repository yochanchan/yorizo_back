import asyncio

from app.core import openai_client as oc


def test_chat_json_safe_handles_bad_json(monkeypatch):
    monkeypatch.setattr(oc, "chat_completion_json", lambda messages, max_tokens=None: "not-json")

    result = asyncio.run(oc.chat_json_safe("test-prompt", []))

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "bad_json"


def test_embed_safe_handles_exception(monkeypatch):
    async def _raise(_texts):
        raise RuntimeError("boom")

    monkeypatch.setattr(oc, "embed_texts", _raise)

    result = asyncio.run(oc.embed_safe(["a"]))

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "embedding_error"
