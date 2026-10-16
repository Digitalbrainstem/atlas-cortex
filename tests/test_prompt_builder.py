"""Tests for the pre-cached domain prompt builder."""

from __future__ import annotations

import os
import textwrap
from pathlib import Path
from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock

import pytest

from cortex.pipeline.prompt_builder import (
    PromptBuilder,
    _BASE_PROMPT,
    get_prompt_builder,
    reset_prompt_builder,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Ensure every test gets a fresh singleton."""
    reset_prompt_builder()
    yield
    reset_prompt_builder()


@pytest.fixture()
def builder() -> PromptBuilder:
    return PromptBuilder()


@pytest.fixture()
def builder_no_files() -> PromptBuilder:
    """Builder pointing at a non-existent directory (no file loading)."""
    return PromptBuilder(prompt_dir="/nonexistent_dir_for_test")


# ---------------------------------------------------------------------------
# Shared prefix tests
# ---------------------------------------------------------------------------


class TestSharedPrefix:
    """The shared prefix must be identical across all domains."""

    def test_base_prompt_present_in_all_domains(self, builder: PromptBuilder):
        for domain in builder.list_domains():
            prompt = builder.build(domain)
            assert prompt.startswith(_BASE_PROMPT), (
                f"domain {domain!r} does not start with the shared prefix"
            )

    def test_prefix_identical_across_domains(self, builder: PromptBuilder):
        prompts = {d: builder.build(d) for d in builder.list_domains()}
        prefix_len = len(_BASE_PROMPT)
        prefixes = {d: p[:prefix_len] for d, p in prompts.items()}
        unique = set(prefixes.values())
        assert len(unique) == 1, "prefix differs across domains"

    def test_get_base_prompt(self, builder: PromptBuilder):
        assert builder.get_base_prompt() == _BASE_PROMPT

    def test_custom_base_prompt(self):
        custom = "You are a custom bot."
        b = PromptBuilder(base_prompt=custom)
        assert b.get_base_prompt() == custom
        assert b.build("general") == custom


# ---------------------------------------------------------------------------
# Domain suffix tests
# ---------------------------------------------------------------------------


class TestDomainSuffix:
    """Domain suffixes are correctly attached after the prefix."""

    def test_general_has_no_suffix(self, builder_no_files: PromptBuilder):
        prompt = builder_no_files.build("general")
        assert prompt == _BASE_PROMPT

    def test_coding_suffix_present(self, builder_no_files: PromptBuilder):
        prompt = builder_no_files.build("coding")
        assert "expert programmer" in prompt
        assert prompt.startswith(_BASE_PROMPT)

    def test_medical_suffix_present(self, builder_no_files: PromptBuilder):
        prompt = builder_no_files.build("medical")
        assert "healthcare professional" in prompt

    def test_home_suffix_present(self, builder_no_files: PromptBuilder):
        prompt = builder_no_files.build("home")
        assert "Home Assistant" in prompt

    def test_education_suffix_present(self, builder_no_files: PromptBuilder):
        prompt = builder_no_files.build("education")
        assert "young children" in prompt

    def test_sight_suffix_present(self, builder_no_files: PromptBuilder):
        prompt = builder_no_files.build("sight")
        assert "visually impaired" in prompt

    def test_suffix_separated_by_blank_line(self, builder_no_files: PromptBuilder):
        prompt = builder_no_files.build("coding")
        assert f"{_BASE_PROMPT}\n\n" in prompt

    def test_unknown_domain_falls_back_to_general(self, builder_no_files: PromptBuilder):
        prompt = builder_no_files.build("nonexistent_domain_xyz")
        assert prompt == _BASE_PROMPT


# ---------------------------------------------------------------------------
# Domain registration and listing
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_register_new_domain(self, builder_no_files: PromptBuilder):
        builder_no_files.register_domain("robotics", "You build robots.")
        assert "robotics" in builder_no_files.list_domains()
        prompt = builder_no_files.build("robotics")
        assert "You build robots." in prompt
        assert prompt.startswith(_BASE_PROMPT)

    def test_overwrite_existing_domain(self, builder_no_files: PromptBuilder):
        builder_no_files.register_domain("coding", "New coding rules.")
        assert builder_no_files.get_domain_suffix("coding") == "New coding rules."

    def test_list_domains_sorted(self, builder_no_files: PromptBuilder):
        domains = builder_no_files.list_domains()
        assert domains == sorted(domains)

    def test_list_includes_builtins(self, builder_no_files: PromptBuilder):
        domains = builder_no_files.list_domains()
        for expected in ("coding", "medical", "home", "education", "sight", "general"):
            assert expected in domains


# ---------------------------------------------------------------------------
# Domain detection
# ---------------------------------------------------------------------------


class TestDomainDetection:
    @pytest.mark.parametrize(
        "message, expected",
        [
            ("Write a Python function to sort a list", "coding"),
            ("Can you refactor this class for me?", "coding"),
            ("debug this stack trace please", "coding"),
            ("What are the side effects of ibuprofen?", "medical"),
            ("My blood pressure is 140/90", "medical"),
            ("Turn on the living room lights", "home"),
            ("Set thermostat to 72 degrees", "home"),
            ("What's the temperature in the bedroom?", "home"),
            ("Teach me the alphabet", "education"),
            ("Let's practice counting numbers", "education"),
            ("Describe what's ahead of me", "sight"),
            ("What obstacles are around me?", "sight"),
            ("What's the weather like?", "general"),
            ("Tell me a joke", "general"),
            ("Hello there!", "general"),
        ],
    )
    def test_detect_domain(self, builder: PromptBuilder, message: str, expected: str):
        assert builder.detect_domain(message) == expected

    def test_ambiguous_defaults_to_general(self, builder: PromptBuilder):
        # Single weak keyword — should not trigger a domain
        result = builder.detect_domain("I see")
        assert result == "general"

    def test_case_insensitive(self, builder: PromptBuilder):
        assert builder.detect_domain("WRITE A PYTHON FUNCTION") == "coding"

    def test_empty_message(self, builder: PromptBuilder):
        assert builder.detect_domain("") == "general"

    def test_context_does_not_crash(self, builder: PromptBuilder):
        result = builder.detect_domain("hello", context={"room": "kitchen"})
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Context injection
# ---------------------------------------------------------------------------


class TestContextInjection:
    def test_user_name_injected(self, builder: PromptBuilder):
        prompt = builder.build("general", context={"user_name": "Alice"})
        assert "You are speaking with Alice." in prompt

    def test_room_injected(self, builder: PromptBuilder):
        prompt = builder.build("general", context={"room": "kitchen"})
        assert "The user is in the kitchen." in prompt

    def test_time_of_day_injected(self, builder: PromptBuilder):
        prompt = builder.build("general", context={"time_of_day": "evening"})
        assert "Current time of day: evening." in prompt

    def test_multiple_context_fields(self, builder: PromptBuilder):
        ctx = {"user_name": "Bob", "room": "office", "time_of_day": "morning"}
        prompt = builder.build("coding", ctx)
        assert "Bob" in prompt
        assert "office" in prompt
        assert "morning" in prompt
        # Suffix still present
        assert "expert programmer" in prompt

    def test_none_context_fields_skipped(self, builder: PromptBuilder):
        prompt = builder.build("general", context={"user_name": None, "room": "den"})
        assert "den" in prompt
        assert "speaking with" not in prompt

    def test_empty_context(self, builder: PromptBuilder):
        prompt = builder.build("general", context={})
        assert prompt == _BASE_PROMPT

    def test_no_context(self, builder: PromptBuilder):
        prompt_a = builder.build("general", context=None)
        prompt_b = builder.build("general")
        assert prompt_a == prompt_b == _BASE_PROMPT


# ---------------------------------------------------------------------------
# File-based domain loading
# ---------------------------------------------------------------------------


class TestFileLoading:
    def test_loads_from_config_prompts(self, builder: PromptBuilder):
        # The default prompt_dir points to config/prompts/ which has our files
        domains = builder.list_domains()
        for name in ("coding", "medical", "home", "education", "sight"):
            assert name in domains

    def test_file_domain_content(self, builder: PromptBuilder):
        suffix = builder.get_domain_suffix("coding")
        assert "expert programmer" in suffix

    def test_custom_prompt_dir(self, tmp_path: Path):
        custom = tmp_path / "my_prompts"
        custom.mkdir()
        (custom / "gardening.txt").write_text("You are a gardening expert.\nGrow things.")
        b = PromptBuilder(prompt_dir=custom)
        assert "gardening" in b.list_domains()
        assert "gardening expert" in b.get_domain_suffix("gardening")

    def test_empty_file_skipped(self, tmp_path: Path):
        d = tmp_path / "prompts"
        d.mkdir()
        (d / "empty.txt").write_text("   \n  ")
        b = PromptBuilder(prompt_dir=d)
        # Empty file should not override builtins or add a blank domain
        assert b.get_domain_suffix("empty") == ""

    def test_env_var_prompt_dir(self, tmp_path: Path, monkeypatch):
        d = tmp_path / "env_prompts"
        d.mkdir()
        (d / "astro.txt").write_text("You know astronomy.")
        monkeypatch.setenv("PROMPT_DIR", str(d))
        b = PromptBuilder(prompt_dir=None)
        assert "astro" in b.list_domains()
        assert "astronomy" in b.get_domain_suffix("astro")

    def test_reload_picks_up_new_files(self, tmp_path: Path):
        d = tmp_path / "prompts"
        d.mkdir()
        b = PromptBuilder(prompt_dir=d)
        assert "cooking" not in b.list_domains()

        (d / "cooking.txt").write_text("You are a chef.")
        count = b.reload_file_domains()
        assert count == 1
        assert "cooking" in b.list_domains()


# ---------------------------------------------------------------------------
# Pre-warming
# ---------------------------------------------------------------------------


class TestPrewarm:
    async def test_prewarm_all_success(self, builder_no_files: PromptBuilder):
        provider = AsyncMock()
        provider.chat.return_value = {"choices": [{"message": {"content": ""}}]}

        results = await builder_no_files.prewarm_all(provider)

        assert all(results.values()), f"some domains failed: {results}"
        # One call per domain
        assert provider.chat.call_count == len(builder_no_files.list_domains())

    async def test_prewarm_sends_correct_prompts(self, builder_no_files: PromptBuilder):
        provider = AsyncMock()
        provider.chat.return_value = {}

        await builder_no_files.prewarm_all(provider)

        for call_args in provider.chat.call_args_list:
            messages = call_args[0][0]
            assert messages[0]["role"] == "system"
            assert messages[0]["content"].startswith(_BASE_PROMPT)
            assert messages[1]["role"] == "user"
            assert messages[1]["content"] == "warmup"

    async def test_prewarm_uses_minimal_tokens(self, builder_no_files: PromptBuilder):
        provider = AsyncMock()
        provider.chat.return_value = {}

        await builder_no_files.prewarm_all(provider)

        for call_args in provider.chat.call_args_list:
            kwargs = call_args[1] if len(call_args) > 1 else call_args.kwargs
            assert kwargs.get("max_tokens") == 1
            assert kwargs.get("stream") is False

    async def test_prewarm_handles_failure(self, builder_no_files: PromptBuilder):
        provider = AsyncMock()
        provider.chat.side_effect = ConnectionError("no server")

        results = await builder_no_files.prewarm_all(provider)

        assert all(v is False for v in results.values())

    async def test_prewarm_partial_failure(self, builder_no_files: PromptBuilder):
        call_count = 0

        async def flaky_chat(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("flaky")
            return {}

        provider = AsyncMock()
        provider.chat = flaky_chat

        results = await builder_no_files.prewarm_all(provider)

        assert any(v is True for v in results.values())
        assert any(v is False for v in results.values())


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_get_returns_same_instance(self):
        a = get_prompt_builder()
        b = get_prompt_builder()
        assert a is b

    def test_reset_clears(self):
        a = get_prompt_builder()
        reset_prompt_builder()
        b = get_prompt_builder()
        assert a is not b


# ---------------------------------------------------------------------------
# Prompt structure integrity
# ---------------------------------------------------------------------------


class TestPromptStructure:
    """Verify the prompt layout is cache-friendly."""

    def test_all_domains_share_exact_prefix_bytes(self, builder: PromptBuilder):
        """The byte-level prefix must be identical so the KV cache matches."""
        prefix = builder.get_base_prompt()
        for domain in builder.list_domains():
            full = builder.build(domain)
            assert full[: len(prefix)] == prefix

    def test_suffix_only_appended_after_prefix(self, builder_no_files: PromptBuilder):
        for domain in builder_no_files.list_domains():
            full = builder_no_files.build(domain)
            suffix = builder_no_files.get_domain_suffix(domain)
            if suffix:
                idx = full.index(suffix)
                assert idx > len(_BASE_PROMPT)

    def test_context_appended_last(self, builder: PromptBuilder):
        ctx = {"user_name": "Zara", "room": "garage"}
        prompt = builder.build("coding", ctx)
        suffix_end = prompt.index("Zara")
        base_end = len(_BASE_PROMPT)
        assert suffix_end > base_end
