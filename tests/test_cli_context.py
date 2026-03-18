"""Tests for CLI context manager, session persistence, and LoRA router."""

from __future__ import annotations

import json
import time

import pytest

from cortex.cli.context import ContextItem, ContextManager
from cortex.cli.lora_router import LoRARouter
from cortex.cli.session import SessionManager, SessionMessage


# ── ContextManager ──────────────────────────────────────────────────


class TestContextManagerBasics:
    """Core add / remove / token accounting."""

    def test_add_file_and_token_estimate(self):
        cm = ContextManager(max_tokens=4096)
        content = "x" * 400  # 400 chars → 100 tokens
        cm.add_file("foo.py", content)
        assert cm.used_tokens == 100
        assert cm.available_tokens == 4096 - 100

    def test_add_replaces_existing_source(self):
        cm = ContextManager()
        cm.add_file("a.py", "hello")
        cm.add_file("a.py", "world")
        assert len(cm.list_items()) == 1
        assert "world" in cm.get_context_string()

    def test_add_memory(self):
        cm = ContextManager()
        cm.add_memory("cats", "result text")
        items = cm.list_items()
        assert len(items) == 1
        assert items[0]["source"] == "memory:cats"

    def test_remove_existing(self):
        cm = ContextManager()
        cm.add_file("a.py", "data")
        assert cm.remove("a.py") is True
        assert cm.list_items() == []

    def test_remove_missing(self):
        cm = ContextManager()
        assert cm.remove("nope") is False

    def test_used_tokens_empty(self):
        cm = ContextManager()
        assert cm.used_tokens == 0
        assert cm.available_tokens == 8192


class TestContextManagerPinning:
    """Pin / unpin behaviour."""

    def test_pin_and_unpin(self):
        cm = ContextManager()
        cm.add_file("a.py", "x" * 40)
        assert cm.pin("a.py") is True
        assert cm.list_items()[0]["pinned"] is True
        assert cm.unpin("a.py") is True
        assert cm.list_items()[0]["pinned"] is False

    def test_pin_missing_returns_false(self):
        cm = ContextManager()
        assert cm.pin("ghost") is False

    def test_unpin_missing_returns_false(self):
        cm = ContextManager()
        assert cm.unpin("ghost") is False

    def test_clear_preserves_pinned(self):
        cm = ContextManager()
        cm.add_file("keep.py", "important", pinned=True)
        cm.add_file("drop.py", "ephemeral")
        cm.clear()
        sources = [it["source"] for it in cm.list_items()]
        assert "keep.py" in sources
        assert "drop.py" not in sources


class TestContextManagerCompact:
    """Compaction evicts oldest unpinned items to 75 % budget."""

    def test_compact_removes_oldest_first(self):
        cm = ContextManager(max_tokens=100)
        # Each item is 25 tokens → 4 items = 100 tokens (at capacity)
        for i in range(4):
            cm.add_file(f"f{i}.py", "x" * 100)
            time.sleep(0.01)  # ensure distinct timestamps

        removed = cm.compact()
        assert removed >= 1
        assert cm.used_tokens <= 75  # 75 % of 100

    def test_compact_preserves_pinned(self):
        cm = ContextManager(max_tokens=100)
        cm.add_file("pinned.py", "x" * 200, pinned=True)  # 50 tok, pinned
        cm.add_file("old.py", "x" * 200)                   # 50 tok
        cm.add_file("new.py", "x" * 200)                   # 50 tok
        cm.compact()
        sources = {it["source"] for it in cm.list_items()}
        assert "pinned.py" in sources

    def test_compact_noop_when_under_budget(self):
        cm = ContextManager(max_tokens=10000)
        cm.add_file("tiny.py", "hi")
        assert cm.compact() == 0


class TestContextManagerOutput:
    """get_context_string formatting."""

    def test_empty_context_string(self):
        cm = ContextManager()
        assert cm.get_context_string() == ""

    def test_context_string_format(self):
        cm = ContextManager()
        cm.add_file("a.py", "alpha")
        cm.add_memory("q", "beta")
        ctx = cm.get_context_string()
        assert "[a.py]" in ctx
        assert "alpha" in ctx
        assert "[memory:q]" in ctx
        assert "beta" in ctx


# ── SessionManager ──────────────────────────────────────────────────


class TestSessionManagerLifecycle:
    """New session, add messages, save, load."""

    def test_new_session_id_format(self, tmp_path):
        sm = SessionManager(session_dir=str(tmp_path))
        sid = sm.new_session("chat")
        assert sid.endswith("-chat")
        assert "-chat" in sid
        assert sm.current_session_id == sid

    def test_add_and_get_history(self, tmp_path):
        sm = SessionManager(session_dir=str(tmp_path))
        sm.new_session()
        sm.add_message("user", "hello")
        sm.add_message("assistant", "hi")
        hist = sm.get_history()
        assert len(hist) == 2
        assert hist[0] == {"role": "user", "content": "hello"}

    def test_save_and_load_roundtrip(self, tmp_path):
        sm = SessionManager(session_dir=str(tmp_path))
        sid = sm.new_session("agent")
        sm.add_message("user", "do stuff")
        sm.add_message("assistant", "done", tool_call={"name": "ls"})
        sm.save()

        msgs = sm.load(sid)
        assert len(msgs) == 2
        assert msgs[0].role == "user"
        assert msgs[0].content == "do stuff"
        assert msgs[1].tool_call == {"name": "ls"}

    def test_save_writes_valid_json(self, tmp_path):
        sm = SessionManager(session_dir=str(tmp_path))
        sid = sm.new_session()
        sm.add_message("user", "hi")
        sm.save()

        path = tmp_path / f"{sid}.json"
        data = json.loads(path.read_text())
        assert data["id"] == sid
        assert data["mode"] == "chat"
        assert len(data["messages"]) == 1

    def test_save_no_session_is_noop(self, tmp_path):
        sm = SessionManager(session_dir=str(tmp_path))
        sm.save()  # should not raise
        assert list(tmp_path.glob("*.json")) == []


class TestSessionManagerListAndResume:
    """list_sessions and resume_session."""

    def test_list_sessions(self, tmp_path):
        sm = SessionManager(session_dir=str(tmp_path))
        sm.new_session("chat")
        sm.add_message("user", "a")
        sm.save()
        sm.new_session("agent")
        sm.add_message("user", "b")
        sm.add_message("assistant", "c")
        sm.save()

        listing = sm.list_sessions()
        assert len(listing) == 2
        assert all("id" in s and "mode" in s for s in listing)

    def test_list_sessions_limit(self, tmp_path):
        sm = SessionManager(session_dir=str(tmp_path))
        for i in range(5):
            sm.new_session("chat")
            sm.add_message("user", f"msg {i}")
            sm.save()

        listing = sm.list_sessions(limit=3)
        assert len(listing) == 3

    def test_resume_session(self, tmp_path):
        sm = SessionManager(session_dir=str(tmp_path))
        sid = sm.new_session("agent")
        sm.add_message("user", "first")
        sm.save()

        sm2 = SessionManager(session_dir=str(tmp_path))
        sm2.resume_session(sid)
        assert sm2.current_session_id == sid
        hist = sm2.get_history()
        assert len(hist) == 1
        assert hist[0]["content"] == "first"


class TestSessionManagerErrorHandling:
    """Graceful handling of missing / corrupt files."""

    def test_load_missing_returns_empty(self, tmp_path):
        sm = SessionManager(session_dir=str(tmp_path))
        assert sm.load("nonexistent-session") == []

    def test_load_corrupt_json_returns_empty(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{{{not json!!!", encoding="utf-8")
        sm = SessionManager(session_dir=str(tmp_path))
        assert sm.load("bad") == []

    def test_load_missing_keys_returns_empty(self, tmp_path):
        bad = tmp_path / "missing-keys.json"
        bad.write_text(json.dumps({"messages": [{"oops": True}]}), encoding="utf-8")
        sm = SessionManager(session_dir=str(tmp_path))
        assert sm.load("missing-keys") == []

    def test_list_sessions_skips_corrupt(self, tmp_path):
        # One good, one bad
        sm = SessionManager(session_dir=str(tmp_path))
        sm.new_session("chat")
        sm.add_message("user", "ok")
        sm.save()

        bad = tmp_path / "corrupt.json"
        bad.write_text("NOT JSON", encoding="utf-8")

        listing = sm.list_sessions()
        assert len(listing) == 1


# ── LoRARouter ──────────────────────────────────────────────────────


class TestLoRARouter:
    """Domain classification."""

    @pytest.mark.parametrize(
        "task,expected",
        [
            ("Write a Python function to sort a list", "coding"),
            ("debug this TypeError in my code", "coding"),
            ("fix the git merge conflict", "coding"),
            ("Calculate the integral of x^2", "math"),
            ("What is the average of 10 and 20?", "math"),
            ("Deploy the docker container to production", "sysadmin"),
            ("Set up an nginx reverse proxy", "sysadmin"),
            ("Configure the firewall rules for SSH", "sysadmin"),
            ("Explain the pros and cons of microservices", "reasoning"),
            ("Analyze this architecture decision", "reasoning"),
            ("What's for dinner tonight?", "general"),
            ("Tell me a joke", "general"),
        ],
    )
    def test_classify(self, task: str, expected: str):
        router = LoRARouter()
        assert router.classify(task) == expected

    @pytest.mark.asyncio
    async def test_route_returns_domain(self):
        router = LoRARouter()
        domain = await router.route("write a Python class", None)
        assert domain == "coding"

    def test_available_loras_empty_stub(self):
        router = LoRARouter()
        assert router.available_loras == []
