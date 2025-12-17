from app.core.prompt_budget import compact_hits, messages_estimate_tokens, shrink_messages


def test_shrink_messages_respects_budget():
    system = {"role": "system", "content": "system prompt"}
    oversized = [{"role": "user", "content": "テスト" * 5000} for _ in range(3)]
    messages = [system] + oversized

    budget = 5000
    shrunk = shrink_messages(messages, token_budget=budget)

    assert shrunk[0]["role"] == "system"
    assert messages_estimate_tokens(shrunk) <= budget


def test_compact_hits_limits_total_chars():
    hits = [{"content": "a" * 5000}, {"text": "b" * 5000}]

    trimmed = compact_hits(hits, max_hits=2, max_chars_per_hit=3000, max_total_chars=4000)
    total_chars = sum(len(item.get("content", "") or item.get("text", "")) for item in trimmed)

    assert len(trimmed) <= 2
    assert total_chars <= 4000
