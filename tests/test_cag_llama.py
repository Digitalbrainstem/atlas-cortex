"""Tests for the llama-server CAG integration layer."""

from __future__ import annotations

import json
from dataclasses import asdict
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from cortex.memory.cag_llama import (
    CacheStats,
    LlamaCagEngine,
    _extract_timing,
    get_llama_cag_engine,
    set_llama_cag_engine,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _ok_completion(content: str = "Hello!", timings: dict | None = None) -> dict:
    """Fabricate a minimal /v1/chat/completions non-streaming response."""
    body: dict = {
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "model": "test-model",
    }
    if timings:
        body["timings"] = timings
    return body


def _sse_events(content: str = "Hello world") -> str:
    """Build a multi-event SSE payload split by word."""
    lines: list[str] = []
    for i, word in enumerate(content.split()):
        prefix = "" if i == 0 else " "
        chunk = {
            "choices": [{"delta": {"content": prefix + word}}],
        }
        lines.append(f"data: {json.dumps(chunk)}")
    lines.append("data: [DONE]")
    return "\n".join(lines)


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def engine():
    """Fresh LlamaCagEngine pointing at a fake URL."""
    return LlamaCagEngine(llm_url="http://fake:8080", model="test-model")


# ── CacheStats dataclass ────────────────────────────────────────────


def test_cache_stats_defaults():
    cs = CacheStats()
    assert cs.knowledge_tokens == 0
    assert cs.hit_rate == 0.0


def test_cache_stats_hit_rate():
    cs = CacheStats(total_queries=10, cache_hits=7, cache_misses=3)
    assert cs.hit_rate == pytest.approx(0.7)


def test_cache_stats_serializable():
    cs = CacheStats(knowledge_tokens=500, total_queries=1, cache_hits=1)
    d = asdict(cs)
    assert d["knowledge_tokens"] == 500


# ── load_knowledge ───────────────────────────────────────────────────


async def test_load_knowledge_sets_prefix(engine):
    await engine.load_knowledge("Medical reference: NeoCardiol is used for heart failure.")
    assert "NeoCardiol" in engine._knowledge_prefix
    assert engine._prefix_token_count > 0
    assert engine._knowledge_hash != ""


async def test_load_knowledge_empty(engine):
    await engine.load_knowledge("")
    assert engine._knowledge_prefix == ""
    assert engine._prefix_token_count >= 1  # max(1, ...)


async def test_load_knowledge_updates_hash(engine):
    await engine.load_knowledge("Version 1")
    h1 = engine._knowledge_hash
    await engine.load_knowledge("Version 2")
    h2 = engine._knowledge_hash
    assert h1 != h2


# ── load_knowledge_from_tree ─────────────────────────────────────────


async def test_load_knowledge_from_tree(engine):
    """Verify integration with KnowledgeTree-like objects."""
    root1 = MagicMock(id="r1")
    root2 = MagicMock(id="r2")
    tree = MagicMock()
    tree.get_roots.return_value = [root1, root2]
    tree.get_branch_content.side_effect = lambda nid, max_depth: {
        "r1": "## Heart\nCardiology info",
        "r2": "## Brain\nNeurology info",
    }[nid]

    await engine.load_knowledge_from_tree(tree)
    assert "Cardiology info" in engine._knowledge_prefix
    assert "Neurology info" in engine._knowledge_prefix
    assert engine._prefix_token_count > 0


async def test_load_knowledge_from_tree_empty(engine):
    tree = MagicMock()
    tree.get_roots.return_value = []
    await engine.load_knowledge_from_tree(tree)
    assert engine._knowledge_prefix == ""


# ── warm_cache ───────────────────────────────────────────────────────


async def test_warm_cache_sends_request(engine):
    await engine.load_knowledge("Some knowledge")

    mock_resp = httpx.Response(
        200,
        json=_ok_completion("ok", timings={"prompt_ms": 42.0}),
        request=httpx.Request("POST", "http://fake:8080/v1/chat/completions"),
    )
    engine._client = AsyncMock()
    engine._client.post = AsyncMock(return_value=mock_resp)

    ms = await engine.warm_cache()
    assert ms == pytest.approx(42.0)
    engine._client.post.assert_called_once()

    # Check the payload includes cache_prompt and system message
    call_kwargs = engine._client.post.call_args
    payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
    assert payload["cache_prompt"] is True
    assert payload["messages"][0]["role"] == "system"
    assert payload["max_tokens"] == 1


async def test_warm_cache_no_knowledge(engine):
    ms = await engine.warm_cache()
    assert ms == 0.0


# ── query (non-streaming) ───────────────────────────────────────────


async def test_query_non_stream(engine):
    await engine.load_knowledge("Reference material")

    mock_resp = httpx.Response(
        200,
        json=_ok_completion("NeoCardiol dosing is 50mg", timings={"prompt_ms": 5.0, "predicted_ms": 30.0}),
        request=httpx.Request("POST", "http://fake:8080/v1/chat/completions"),
    )
    engine._client = AsyncMock()
    engine._client.post = AsyncMock(return_value=mock_resp)

    result = await engine.query("What is the dosing?", stream=False)
    assert result == "NeoCardiol dosing is 50mg"
    assert engine._total_queries == 1


async def test_query_non_stream_with_conversation(engine):
    await engine.load_knowledge("Knowledge base")

    mock_resp = httpx.Response(
        200,
        json=_ok_completion("The answer is 42"),
        request=httpx.Request("POST", "http://fake:8080/v1/chat/completions"),
    )
    engine._client = AsyncMock()
    engine._client.post = AsyncMock(return_value=mock_resp)

    conversation = [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello!"},
    ]
    result = await engine.query("What is 6*7?", conversation=conversation, stream=False)
    assert result == "The answer is 42"

    # Verify conversation was included in the messages
    call_kwargs = engine._client.post.call_args
    payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
    messages = payload["messages"]
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == "Hi"
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"] == "What is 6*7?"


async def test_query_no_knowledge_prefix(engine):
    """Queries work without knowledge loaded (no system prompt)."""
    mock_resp = httpx.Response(
        200,
        json=_ok_completion("Just a chat"),
        request=httpx.Request("POST", "http://fake:8080/v1/chat/completions"),
    )
    engine._client = AsyncMock()
    engine._client.post = AsyncMock(return_value=mock_resp)

    result = await engine.query("hello", stream=False)
    assert result == "Just a chat"

    call_kwargs = engine._client.post.call_args
    payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
    # No system message when knowledge is empty
    assert payload["messages"][0]["role"] == "user"


# ── query (streaming) ───────────────────────────────────────────────


async def test_query_stream(engine):
    await engine.load_knowledge("Knowledge base")

    sse_content = _sse_events("Hello streaming world")
    mock_response = httpx.Response(
        200,
        content=sse_content.encode(),
        headers={"content-type": "text/event-stream"},
        request=httpx.Request("POST", "http://fake:8080/v1/chat/completions"),
    )

    def mock_stream(*args, **kwargs):
        return _AsyncContextManager(mock_response)

    engine._client = AsyncMock()
    engine._client.stream = mock_stream

    gen = await engine.query("Tell me", stream=True)
    chunks = [tok async for tok in gen]
    text = "".join(chunks)
    assert "Hello" in text
    assert "streaming" in text
    assert "world" in text


# ── update_knowledge ─────────────────────────────────────────────────


async def test_update_knowledge(engine):
    await engine.load_knowledge("Version A")
    hash_a = engine._knowledge_hash

    await engine.update_knowledge("Version B")
    assert engine._knowledge_hash != hash_a
    assert engine._knowledge_prefix == "Version B"


async def test_update_knowledge_same_content(engine):
    await engine.load_knowledge("Same content")
    hash1 = engine._knowledge_hash

    await engine.update_knowledge("Same content")
    assert engine._knowledge_hash == hash1


# ── get_cache_stats ──────────────────────────────────────────────────


async def test_cache_stats_after_queries(engine):
    await engine.load_knowledge("Knowledge")

    # Simulate some stats
    engine._total_queries = 10
    engine._cache_hits = 7
    engine._cache_misses = 3
    engine._prompt_ms_accum = 100.0
    engine._generation_ms_accum = 500.0

    stats = engine.get_cache_stats()
    assert stats.total_queries == 10
    assert stats.hit_rate == pytest.approx(0.7)
    assert stats.avg_prompt_ms == pytest.approx(10.0)
    assert stats.avg_generation_ms == pytest.approx(50.0)
    assert stats.knowledge_hash == engine._knowledge_hash


def test_cache_stats_empty(engine):
    stats = engine.get_cache_stats()
    assert stats.total_queries == 0
    assert stats.hit_rate == 0.0
    assert stats.knowledge_tokens == 0


# ── Compatibility shims (CAGEngine interface) ────────────────────────


def test_mode_property(engine):
    assert engine.mode == "llama_server"


async def test_init_noop(engine):
    await engine.init()  # should not raise


async def test_format_context_returns_prefix(engine):
    await engine.load_knowledge("The full knowledge base text")
    ctx = await engine.format_context("any query")
    assert ctx == "The full knowledge base text"


async def test_format_context_empty(engine):
    ctx = await engine.format_context("query")
    assert ctx == ""


async def test_get_stats_dict_shape(engine):
    await engine.load_knowledge("Some text")
    stats = await engine.get_stats()
    assert stats["mode"] == "llama_server"
    assert stats["banks"] == 1
    assert stats["total_tokens"] > 0
    assert "hit_rate" in stats
    assert "total_queries" in stats


async def test_get_stats_empty(engine):
    stats = await engine.get_stats()
    assert stats["banks"] == 0
    assert stats["total_tokens"] == 0


# ── health ───────────────────────────────────────────────────────────


async def test_health_ok(engine):
    mock_resp = httpx.Response(
        200,
        json={"data": []},
        request=httpx.Request("GET", "http://fake:8080/v1/models"),
    )
    engine._client = AsyncMock()
    engine._client.get = AsyncMock(return_value=mock_resp)
    assert await engine.health() is True


async def test_health_down(engine):
    engine._client = AsyncMock()
    engine._client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
    assert await engine.health() is False


# ── aclose ───────────────────────────────────────────────────────────


async def test_aclose(engine):
    engine._client = AsyncMock()
    await engine.aclose()
    engine._client.aclose.assert_called_once()


# ── _extract_timing helper ───────────────────────────────────────────


def test_extract_timing_prompt_ms():
    data = {"timings": {"prompt_ms": 42.5}}
    assert _extract_timing(data, "prompt") == pytest.approx(42.5)


def test_extract_timing_predicted_ms():
    data = {"timings": {"predicted_ms": 100.0}}
    assert _extract_timing(data, "predicted") == pytest.approx(100.0)


def test_extract_timing_missing():
    assert _extract_timing({}, "prompt") is None
    assert _extract_timing({"timings": {}}, "prompt") is None


def test_extract_timing_from_usage():
    data = {"usage": {"prompt_ms": 10.0}}
    assert _extract_timing(data, "prompt") == pytest.approx(10.0)


# ── Module singleton ─────────────────────────────────────────────────


def test_singleton():
    assert get_llama_cag_engine() is None
    eng = LlamaCagEngine()
    set_llama_cag_engine(eng)
    assert get_llama_cag_engine() is eng
    set_llama_cag_engine(None)
    assert get_llama_cag_engine() is None


# ── _build_messages ──────────────────────────────────────────────────


async def test_build_messages_with_knowledge(engine):
    await engine.load_knowledge("System knowledge")
    msgs = engine._build_messages("Hello", conversation=None)
    assert msgs[0] == {"role": "system", "content": "System knowledge"}
    assert msgs[1] == {"role": "user", "content": "Hello"}


async def test_build_messages_without_knowledge(engine):
    msgs = engine._build_messages("Hello", conversation=None)
    assert len(msgs) == 1
    assert msgs[0] == {"role": "user", "content": "Hello"}


async def test_build_messages_with_conversation(engine):
    await engine.load_knowledge("KB")
    conv = [{"role": "user", "content": "A"}, {"role": "assistant", "content": "B"}]
    msgs = engine._build_messages("C", conversation=conv)
    assert len(msgs) == 4  # system + 2 conv + user
    assert msgs[0]["role"] == "system"
    assert msgs[-1]["content"] == "C"


# ── _build_payload ───────────────────────────────────────────────────


def test_build_payload_defaults(engine):
    msgs = [{"role": "user", "content": "test"}]
    payload = engine._build_payload(msgs, stream=False)
    assert payload["cache_prompt"] is True
    assert payload["stream"] is False
    assert payload["temperature"] == 0.7
    assert payload["model"] == "test-model"
    assert "max_tokens" not in payload


def test_build_payload_with_max_tokens(engine):
    msgs = [{"role": "user", "content": "test"}]
    payload = engine._build_payload(msgs, stream=True, max_tokens=512)
    assert payload["max_tokens"] == 512
    assert payload["stream"] is True


def test_build_payload_no_model():
    eng = LlamaCagEngine(llm_url="http://x:8080", model=None)
    msgs = [{"role": "user", "content": "test"}]
    payload = eng._build_payload(msgs, stream=False)
    assert "model" not in payload


def test_build_payload_default_max_tokens():
    eng = LlamaCagEngine(default_max_tokens=1024)
    msgs = [{"role": "user", "content": "test"}]
    payload = eng._build_payload(msgs, stream=False)
    assert payload["max_tokens"] == 1024


# ── Cache hit/miss detection ─────────────────────────────────────────


async def test_cache_hit_detection(engine):
    """Prompt time < 30% of elapsed → cache hit."""
    await engine.load_knowledge("KB")

    mock_resp = httpx.Response(
        200,
        json=_ok_completion("ok", timings={"prompt_ms": 2.0, "predicted_ms": 100.0}),
        request=httpx.Request("POST", "http://fake:8080/v1/chat/completions"),
    )
    engine._client = AsyncMock()
    engine._client.post = AsyncMock(return_value=mock_resp)

    # Simulate 1 second of real elapsed time so prompt_ms (2) < elapsed (1000)*0.3
    call_count = 0
    def fake_monotonic():
        nonlocal call_count
        call_count += 1
        return call_count  # each call returns +1 second

    with patch("cortex.memory.cag_llama.time.monotonic", side_effect=fake_monotonic):
        await engine.query("q", stream=False)
    stats = engine.get_cache_stats()
    assert stats.cache_hits == 1
    assert stats.cache_misses == 0


async def test_cache_miss_detection(engine):
    """Prompt time > 30% of elapsed → cache miss."""
    await engine.load_knowledge("KB")

    mock_resp = httpx.Response(
        200,
        json=_ok_completion("ok", timings={"prompt_ms": 500.0, "predicted_ms": 50.0}),
        request=httpx.Request("POST", "http://fake:8080/v1/chat/completions"),
    )
    engine._client = AsyncMock()
    engine._client.post = AsyncMock(return_value=mock_resp)

    # Simulate 1 second elapsed → prompt_ms (500) > elapsed (1000)*0.3 → miss
    call_count = 0
    def fake_monotonic():
        nonlocal call_count
        call_count += 1
        return call_count

    with patch("cortex.memory.cag_llama.time.monotonic", side_effect=fake_monotonic):
        await engine.query("q", stream=False)
    stats = engine.get_cache_stats()
    assert stats.cache_hits == 0
    assert stats.cache_misses == 1


# ── Streaming SSE helper ────────────────────────────────────────────


class _AsyncContextManager:
    """Wrap an httpx.Response to behave like an async context manager
    returned by client.stream()."""

    def __init__(self, response: httpx.Response) -> None:
        self._response = response
        self._lines = response.content.decode().splitlines()

    async def __aenter__(self) -> _FakeStreamResponse:
        return _FakeStreamResponse(self._response, self._lines)

    async def __aexit__(self, *args: object) -> None:
        pass


class _FakeStreamResponse:
    """Minimal mock for an httpx streaming response with aiter_lines."""

    def __init__(self, response: httpx.Response, lines: list[str]) -> None:
        self.status_code = response.status_code
        self._lines = lines

    def raise_for_status(self) -> None:
        pass

    async def aiter_lines(self):
        for line in self._lines:
            yield line
