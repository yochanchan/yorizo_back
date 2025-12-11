from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Generic, List, Optional, Sequence, Tuple, TypeVar, Union, cast

from dotenv import load_dotenv
from fastapi import HTTPException
from openai import AzureOpenAI, OpenAIError
from openai.types.chat import ChatCompletionMessageParam

load_dotenv()

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Restrict messages to the OpenAI chat message type for stronger type safety.
ChatMessage = ChatCompletionMessageParam


def _as_message_list(messages: Sequence[ChatMessage]) -> List[ChatMessage]:
    """Force messages into a list with the correct ChatMessage type."""
    return [cast(ChatMessage, m) for m in messages]


class AzureNotConfiguredError(RuntimeError):
    """Raised when Azure OpenAI settings are missing."""


@dataclass
class LlmError:
    code: str
    message: str
    retryable: bool = False
    raw: Any | None = None


@dataclass
class LlmResult(Generic[T]):
    ok: bool
    value: Optional[T] = None
    error: Optional[LlmError] = None


@dataclass
class AzureOpenAIConfig:
    endpoint: str | None
    api_key: str | None
    api_version: str | None
    chat_deployment: str | None
    embedding_deployment: str | None

    @classmethod
    def from_env(cls) -> "AzureOpenAIConfig":
        return cls(
            endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview"),
            chat_deployment=os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT"),
            embedding_deployment=os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT"),
        )


_azure_client: AzureOpenAI | None = None
_azure_config: AzureOpenAIConfig | None = None


def _get_config(*, require_embedding: bool = False) -> AzureOpenAIConfig:
    """
    Load Azure OpenAI config from the environment.
    Embedding deployment is only enforced when explicitly requested.
    """
    global _azure_config
    if _azure_config is None:
        _azure_config = AzureOpenAIConfig.from_env()

    cfg = cast(AzureOpenAIConfig, _azure_config)
    required = [
        ("AZURE_OPENAI_ENDPOINT", cfg.endpoint),
        ("AZURE_OPENAI_API_KEY", cfg.api_key),
        ("AZURE_OPENAI_API_VERSION", cfg.api_version),
        ("AZURE_OPENAI_CHAT_DEPLOYMENT", cfg.chat_deployment),
    ]
    if require_embedding:
        required.append(("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", cfg.embedding_deployment))

    missing = [name for name, value in required if not value]
    logger.debug(
        "Azure OpenAI config present flags: %s",
        {
            "endpoint": bool(cfg.endpoint),
            "api_key": bool(cfg.api_key),
            "api_version": bool(cfg.api_version),
            "chat_deployment": bool(cfg.chat_deployment),
            "embedding_deployment": bool(cfg.embedding_deployment),
        },
    )
    if missing:
        logger.warning("Azure OpenAI not configured; missing keys: %s", ", ".join(missing))
        raise AzureNotConfiguredError("Azure OpenAI is not configured")
    return cfg


def _get_client() -> AzureOpenAI:
    global _azure_client
    cfg = _get_config()
    if _azure_client is None:
        _azure_client = AzureOpenAI(
            api_key=cfg.api_key,
            api_version=cfg.api_version,
            azure_endpoint=cfg.endpoint,
        )
    return cast(AzureOpenAI, _azure_client)


def _resolve_response(resp: Any):
    return resp


def chat_completion_json(
    messages: Sequence[ChatMessage],
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> str:
    """
    Call Azure OpenAI (chat completions) in JSON mode and return the raw content string.
    temperature is accepted for compatibility but ignored (some deployments only allow the default).
    """
    try:
        client = _get_client()
        cfg = _get_config()
        params: Dict[str, Any] = {
            "model": cfg.chat_deployment,
            "messages": _as_message_list(messages),
            "response_format": {"type": "json_object"},
        }
        if max_tokens is not None:
            params["max_tokens"] = max_tokens
        resp = _resolve_response(client.chat.completions.create(**params))
        return resp.choices[0].message.content or "{}"
    except AzureNotConfiguredError:
        logger.warning("Azure OpenAI is not configured; cannot complete chat (json).")
        raise
    except OpenAIError as exc:  # pragma: no cover - upstream error handling
        logger.exception("Azure OpenAI error during chat completion")
        raise HTTPException(status_code=502, detail="upstream AI error") from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error during chat completion")
        raise HTTPException(status_code=500, detail="chat generation failed") from exc


def chat_completion_text(
    messages: Sequence[ChatMessage],
    temperature: float = 0.4,
) -> str:
    """
    Call Azure OpenAI (chat completions) for plain text responses.
    """
    try:
        client = _get_client()
        cfg = _get_config()
        resp = _resolve_response(
            client.chat.completions.create(
                model=cfg.chat_deployment,
                messages=_as_message_list(messages),
                temperature=temperature,
            )
        )
        return resp.choices[0].message.content or ""
    except AzureNotConfiguredError:
        logger.warning("Azure OpenAI is not configured; cannot complete chat (text).")
        raise
    except OpenAIError as exc:  # pragma: no cover
        logger.exception("Azure OpenAI error during text completion")
        raise HTTPException(status_code=502, detail="upstream AI error") from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error during text completion")
        raise HTTPException(status_code=500, detail="chat generation failed") from exc


async def generate_chat_reply(
    messages: Sequence[ChatMessage],
    with_system_prompt: bool = True,
    system_prompt: str = "",
) -> str:
    """
    Helper used by RAG chat endpoints (non-guided). Uses Azure chat completions in text mode.
    """
    prompt_messages: List[ChatMessage] = _as_message_list(messages)
    if with_system_prompt and system_prompt:
        prompt_messages = [cast(ChatMessage, {"role": "system", "content": system_prompt})] + prompt_messages
    return chat_completion_text(prompt_messages, temperature=0.4).strip()


async def embed_texts(texts: Union[str, List[str]]) -> List[List[float]]:
    """
    Create vector embeddings for a single text or a list of texts using Azure OpenAI embeddings.
    """
    if isinstance(texts, str):
        input_texts = [texts]
    else:
        input_texts = list(texts)

    if not input_texts:
        return []

    try:
        cfg = _get_config(require_embedding=True)
        client = _get_client()
        resp = await asyncio.to_thread(
            client.embeddings.create,
            model=cfg.embedding_deployment,
            input=input_texts,
        )
        return [item.embedding for item in resp.data]
    except AzureNotConfiguredError:
        logger.warning("Azure OpenAI embedding error: Azure OpenAI is not configured")
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Azure OpenAI embedding error")
        raise


async def generate_consultation_memo(
    messages: Sequence[ChatMessage],
    company_profile: Optional[Dict[str, Any]] = None,
) -> Tuple[List[str], List[str]]:
    """
    Summarize a conversation into consultation memo bullets (used elsewhere in the app).
    """
    history_messages = _as_message_list(messages)

    def _fallback_from_history() -> Tuple[List[str], List[str]]:
        """Fallback memo when Azure OpenAI is not available."""
        user_lines = [
            m.get("content") for m in history_messages if (m.get("role") == "user" and m.get("content"))
        ]
        summary = [str(txt) for txt in user_lines[-3:] if txt]
        return summary, []

    profile_lines = []
    if company_profile:
        profile_lines = [f"{k}: {v}" for k, v in company_profile.items() if v]

    system_prompt = (
        "You are a Japanese SME consultant. Summarize the past conversation in Japanese.\n"
        "1) current_concerns: 1-3 short bullets of what the user worries about now\n"
        "2) important_points_for_expert: 1-3 bullets the expert should know\n"
        "3) homework: 1-3 small homework items to prepare\n"
        "4) next_consultation_theme: 1-2 themes for the next session\n"
        "Return JSON only."
    )

    prompt_messages: List[ChatMessage] = [cast(ChatMessage, {"role": "system", "content": system_prompt})]
    if profile_lines:
        prompt_messages.append(
            cast(ChatMessage, {"role": "system", "content": "Company profile:\n" + "\n".join(profile_lines)})
        )
    prompt_messages.extend(history_messages[-30:])

    try:
        raw = chat_completion_json(prompt_messages)
    except AzureNotConfiguredError:
        logger.warning("Azure OpenAI is not configured; returning fallback memo.")
        return _fallback_from_history()
    except Exception:
        logger.exception("Consultation memo generation failed; returning fallback memo.")
        return _fallback_from_history()

    data = json.loads(raw or "{}")
    current = data.get("current_concerns") or []
    important = data.get("important_points_for_expert") or []
    if not isinstance(current, list):
        current = [str(current)]
    if not isinstance(important, list):
        important = [str(important)]
    return [str(x) for x in current][:5], [str(x) for x in important][:5]


# --- Non-raising LLM wrappers (vNext) ---


def _error_from_exception(code: str, exc: Exception, *, retryable: bool = False) -> LlmError:
    message = str(getattr(exc, "detail", None) or exc)
    return LlmError(code=code, message=message, retryable=retryable, raw=exc)


async def chat_json_safe(
    prompt_id: str,
    messages: Sequence[ChatMessage],
    max_tokens: int | None = None,
) -> LlmResult[dict]:
    try:
        raw_json = chat_completion_json(messages, max_tokens=max_tokens)
        data = json.loads(raw_json or "{}")
        if not isinstance(data, dict):
            raise ValueError("LLM JSON response was not a dict")
        return LlmResult(ok=True, value=data)
    except AzureNotConfiguredError as exc:
        return LlmResult(ok=False, error=_error_from_exception("not_configured", exc, retryable=False))
    except HTTPException as exc:
        retryable = exc.status_code >= 500
        return LlmResult(ok=False, error=_error_from_exception("upstream_http_error", exc, retryable=retryable))
    except Exception as exc:  # noqa: BLE001
        logger.warning("chat_json_safe failed for %s: %s", prompt_id, exc)
        return LlmResult(ok=False, error=_error_from_exception("bad_json", exc, retryable=False))


async def chat_text_safe(
    prompt_id: str,
    messages: Sequence[ChatMessage],
    temperature: float = 0.4,
) -> LlmResult[str]:
    try:
        text = chat_completion_text(messages, temperature=temperature)
        if text is None or text == "":
            raise ValueError("Empty text response")
        return LlmResult(ok=True, value=text)
    except AzureNotConfiguredError as exc:
        return LlmResult(ok=False, error=_error_from_exception("not_configured", exc, retryable=False))
    except HTTPException as exc:
        retryable = exc.status_code >= 500
        return LlmResult(ok=False, error=_error_from_exception("upstream_http_error", exc, retryable=retryable))
    except Exception as exc:  # noqa: BLE001
        logger.warning("chat_text_safe failed for %s: %s", prompt_id, exc)
        return LlmResult(ok=False, error=_error_from_exception("unexpected_error", exc, retryable=False))


async def embed_safe(texts: Union[str, List[str]]) -> LlmResult[List[List[float]]]:
    try:
        vectors = await embed_texts(texts)
        return LlmResult(ok=True, value=vectors)
    except Exception as exc:  # noqa: BLE001
        logger.warning("embed_safe failed: %s", exc)
        return LlmResult(ok=False, error=_error_from_exception("embedding_error", exc, retryable=True))
