from __future__ import annotations

from typing import Any, Dict, List


def estimate_tokens(text: str) -> int:
    """
    Rough + safe token estimator.
    We bias to over-estimation to avoid context_length_exceeded.
    """
    if not text:
        return 0

    n = len(text)
    # Non-ascii ratio (JP, etc.)
    non_ascii = sum(1 for c in text if ord(c) > 127)
    ratio = non_ascii / max(1, n)

    # Conservative estimation:
    # - mostly ASCII: ~4 chars/token
    # - mostly non-ASCII: ~2 chars/token (safer for Japanese)
    if ratio < 0.2:
        est = n / 4
    elif ratio < 0.6:
        est = n / 3
    else:
        est = n / 2

    # add buffer
    return int(est * 1.2) + 32


def truncate_text(text: str, max_chars: int) -> str:
    if text is None:
        return ""
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)] + "…"


def _pick_text_field(hit: Dict[str, Any]) -> str:
    for k in ("content", "text", "chunk", "body", "snippet", "page_content"):
        v = hit.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def _set_text_field(hit: Dict[str, Any], new_text: str) -> None:
    for k in ("content", "text", "chunk", "body", "snippet", "page_content"):
        if k in hit:
            hit[k] = new_text
            return
    # fallback
    hit["content"] = new_text


def compact_hits(
    hits: List[Dict[str, Any]],
    *,
    max_hits: int = 8,
    max_chars_per_hit: int = 1500,
    max_total_chars: int = 16000,
) -> List[Dict[str, Any]]:
    if not hits:
        return []

    trimmed = []
    total = 0

    for h in hits[:max_hits]:
        if not isinstance(h, dict):
            continue

        # copy & drop huge keys if present
        x = dict(h)
        for drop_key in ("raw", "raw_text", "embedding", "vector", "tokens", "metadata_raw"):
            if drop_key in x:
                x.pop(drop_key, None)

        text = _pick_text_field(x)
        text = truncate_text(text, max_chars_per_hit)

        # enforce total budget
        remain = max_total_chars - total
        if remain <= 0:
            break
        if len(text) > remain:
            text = truncate_text(text, remain)

        _set_text_field(x, text)
        trimmed.append(x)
        total += len(text)

    return trimmed


def messages_estimate_tokens(messages: List[Dict[str, Any]]) -> int:
    total = 0
    for m in messages:
        if not isinstance(m, dict):
            continue
        total += estimate_tokens(str(m.get("role", "")))
        c = m.get("content", "")
        if isinstance(c, str):
            total += estimate_tokens(c)
        else:
            # If content is structured (e.g. list), stringify safely
            total += estimate_tokens(str(c))
    return total


def shrink_messages(messages: List[Dict[str, Any]], *, token_budget: int = 110000) -> List[Dict[str, Any]]:
    """
    Shrink messages to fit within token_budget (estimated).
    Strategy:
      1) Keep the first system message.
      2) Keep the latest conversation turns; drop older ones.
      3) Truncate overly long contents.
    """
    if not messages:
        return []

    # Keep system (first found)
    system_msgs = [m for m in messages if isinstance(m, dict) and m.get("role") == "system"]
    non_system = [m for m in messages if isinstance(m, dict) and m.get("role") != "system"]

    system = system_msgs[:1]
    rest = non_system

    # First, cap each message size to avoid extremes (safety)
    capped = []
    for m in rest:
        mm = dict(m)
        if isinstance(mm.get("content"), str):
            mm["content"] = truncate_text(mm["content"], 20000)
        else:
            mm["content"] = truncate_text(str(mm.get("content", "")), 20000)
        capped.append(mm)

    result = system + capped
    if messages_estimate_tokens(result) <= token_budget:
        return result

    # Drop older messages, keep recent N
    for keep_n in (20, 12, 8, 6, 4):
        result = system + capped[-keep_n:]
        if messages_estimate_tokens(result) <= token_budget:
            return result

    # If still too big, aggressively truncate remaining contents
    aggressive = []
    for m in capped[-4:]:
        mm = dict(m)
        if isinstance(mm.get("content"), str):
            mm["content"] = truncate_text(mm["content"], 6000)
        else:
            mm["content"] = truncate_text(str(mm.get("content", "")), 6000)
        aggressive.append(mm)

    result = system + aggressive
    if messages_estimate_tokens(result) <= token_budget:
        return result

    # Last resort: keep system + last user message truncated
    last_user = None
    for m in reversed(capped):
        if m.get("role") == "user":
            last_user = dict(m)
            break
    if last_user:
        last_user["content"] = truncate_text(str(last_user.get("content", "")), 4000)
        return system + [last_user]

    return system
