from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Sequence, Tuple

from openai import APIStatusError, OpenAIError, RateLimitError

from app.core.config import settings
from app.core.openai_client import AzureNotConfiguredError, ChatMessage, azure_client

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """
あなたは中小企業向けの経営支援アシスタント。
ユーザーが「事例」「成功例」「参考例」を求めたら、必ず“事例”として整形して答える。

制約：
- 必ず「事例」を3つ（事例①〜③）
- 各事例は「状況→打ち手→手順→注意点」の順で具体的に
- 根拠は【参照】の抜粋のみ。書いてないことは断定しない
- 各事例の末尾に必ず出典を付ける：「出典：<pdf名> p.<page>」
- 最後に「参照した出典一覧」を箇条書き
- 文章は短め・実務向け（1事例 200〜350字）
""".strip()

MAX_REFERENCES = 8
FALLBACK_BUSY = "現在混雑しています。もう一度お試しください。"
NO_REFERENCE_MESSAGE = "関連する出典が見つかりませんでした。検索条件を変えてお試しください。"


def _resolve_client() -> Tuple[Any, str]:
    if azure_client is None:
        raise AzureNotConfiguredError("Azure OpenAI is not configured")
    model = settings.azure_openai_chat_deployment
    if not model:
        raise AzureNotConfiguredError("AZURE_OPENAI_CHAT_DEPLOYMENT is not set")
    return azure_client, model


def _format_references(hits: Sequence[Dict[str, Any]]) -> str:
    blocks: List[str] = []
    for idx, hit in enumerate(hits[:MAX_REFERENCES], 1):
        title = (
            hit.get("title")
            or hit.get("source_title")
            or hit.get("path")
            or hit.get("source_path")
            or "出典不明"
        )
        page = hit.get("page")
        page_label = f"p.{page}" if page is not None else "p.?"
        snippet = (hit.get("snippet") or hit.get("text") or "").strip()
        blocks.append(f"[{idx}] {title} {page_label}\n{snippet}")
    return "\n\n".join(blocks)


def _is_rate_limit(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if status == 429:
        return True
    if isinstance(exc, RateLimitError):
        return True
    message = str(exc).lower()
    return "rate limit" in message or "429" in message


def build_examples_answer(user_query: str, hits: List[Dict[str, Any]]) -> str:
    """
    Build a case-style answer (事例①〜③) based on Cosmos search hits.
    """
    if not hits:
        return NO_REFERENCE_MESSAGE

    reference_block = _format_references(hits)
    if not reference_block:
        return NO_REFERENCE_MESSAGE

    user_content = (
        f"ユーザーからの質問:\n{user_query.strip() or '（質問文なし）'}\n\n"
        f"【参照】\n{reference_block}\n\n"
        "参照を根拠に「事例①〜③」を生成してください。"
    )

    messages: List[ChatMessage] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    client, model = _resolve_client()
    max_retries = 3

    for attempt in range(max_retries):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.35,
                max_tokens=900,
            )
            content = resp.choices[0].message.content or ""
            return content.strip() or NO_REFERENCE_MESSAGE
        except AzureNotConfiguredError:
            logger.warning("Azure OpenAI is not configured; skipping example answer generation")
            return FALLBACK_BUSY
        except (APIStatusError, RateLimitError, OpenAIError) as exc:
            if _is_rate_limit(exc) and attempt < max_retries - 1:
                delay = 2 ** attempt
                logger.warning("example answer rate limited (attempt %s): retrying in %ss", attempt + 1, delay)
                time.sleep(delay)
                continue
            if _is_rate_limit(exc):
                logger.warning("example answer exhausted retries after rate limit: %s", exc)
                return FALLBACK_BUSY
            logger.exception("example answer generation failed")
            return FALLBACK_BUSY
        except Exception:  # noqa: BLE001
            logger.exception("unexpected error during example answer generation")
            return FALLBACK_BUSY

    return FALLBACK_BUSY
