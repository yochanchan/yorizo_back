from __future__ import annotations

import inspect
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Generic, List, Optional, Sequence, Tuple, TypeVar, Union, cast

from fastapi import HTTPException
from openai import AsyncOpenAI, AzureOpenAI, OpenAIError
from openai.types.chat import ChatCompletionMessageParam

from app.core.config import settings

logger = logging.getLogger(__name__)

_client: Optional[AsyncOpenAI] = None
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


# Azure chat client (required for chat completions)
azure_client: AzureOpenAI | None = None
if (
    settings.azure_openai_endpoint
    and settings.azure_openai_api_key
    and settings.azure_openai_chat_deployment
):
    azure_client = AzureOpenAI(
        api_key=settings.azure_openai_api_key,
        api_version=settings.azure_openai_api_version,
        azure_endpoint=settings.azure_openai_endpoint,
    )


def _get_azure_client() -> AzureOpenAI:
    if azure_client is None:
        raise AzureNotConfiguredError("Azure OpenAI is not configured")
    return azure_client


def _get_azure_model() -> str:
    model = settings.azure_openai_chat_deployment
    if not model:
        raise AzureNotConfiguredError("Azure OpenAI is not configured")
    return cast(str, model)


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
        client = _get_azure_client()
        params: Dict[str, Any] = {
            "model": _get_azure_model(),
            "messages": _as_message_list(messages),
            "response_format": {"type": "json_object"},
        }
        if max_tokens is not None:
            params["max_completion_tokens"] = max_tokens
        resp = client.chat.completions.create(**params)
        return resp.choices[0].message.content or "{}"
    except AzureNotConfiguredError:
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
        client = _get_azure_client()
        resp = client.chat.completions.create(
            model=_get_azure_model(),
            messages=_as_message_list(messages),
            temperature=temperature,
        )
        return resp.choices[0].message.content or ""
    except AzureNotConfiguredError:
        raise
    except OpenAIError as exc:  # pragma: no cover
        logger.exception("Azure OpenAI error during text completion")
        raise HTTPException(status_code=502, detail="upstream AI error") from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error during text completion")
        raise HTTPException(status_code=500, detail="chat generation failed") from exc


# --- Existing utilities (embeddings & summaries) keep OpenAI embeddings for now ---
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"


def get_client() -> AsyncOpenAI:
    """
    Return a singleton AsyncOpenAI client using settings (not raw os.getenv).

    - Prefer azure_openai_api_key; fallback to openai_api_key if provided.
    - Optionally use openai_base_url when set (for Azure-compatible endpoints or proxies).
    """
    global _client
    if _client is not None:
        return _client

    api_key = settings.azure_openai_api_key or settings.openai_api_key
    if not api_key:
        raise RuntimeError("AZURE_OPENAI_API_KEY is not set.")

    base_url = settings.openai_base_url

    if base_url:
        _client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
        )
    else:
        _client = AsyncOpenAI(api_key=api_key)

    return _client


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
    Create vector embeddings for a single text or a list of texts using the OpenAI embeddings API.
    """
    if isinstance(texts, str):
        input_texts = [texts]
    else:
        input_texts = list(texts)

    if not input_texts:
        return []

    client = get_client()
    model_name = getattr(settings, "openai_model_embedding", DEFAULT_EMBEDDING_MODEL) or DEFAULT_EMBEDDING_MODEL

    resp = client.embeddings.create(
        model=model_name,
        input=input_texts,
    )
    if inspect.isawaitable(resp):
        resp = await resp

    return [item.embedding for item in resp.data]


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
