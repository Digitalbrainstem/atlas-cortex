"""Tests for cortex.orchestrator.agent_orchestrator — agent selection & config."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from cortex.orchestrator.agent_orchestrator import (
    CODING,
    CONVERSATION,
    EDUCATION,
    HOME,
    RESEARCH,
    SIGHT,
    AgentConfig,
    AgentOrchestrator,
    AgentTemplate,
    RequestContext,
)
from cortex.pipeline.model_router import ModelChoice, ModelRouter
from cortex.pipeline.thinking_router import ThinkingDecision, ThinkingRouter


# ── helpers ──────────────────────────────────────────────────────


def _router(**kw) -> ModelRouter:
    defaults = dict(
        model_fast="fast-4b",
        model_expert="expert-9b",
        model_reasoning="reasoning-27b",
        url_fast="http://fast:11434",
        url_expert="http://expert:11434",
        url_reasoning="http://reasoning:11434",
    )
    defaults.update(kw)
    return ModelRouter(**defaults)


def _orchestrator(**kw) -> AgentOrchestrator:
    return AgentOrchestrator(
        model_router=kw.pop("model_router", _router()),
        thinking_router=kw.pop("thinking_router", ThinkingRouter()),
        **kw,
    )


def _ctx(**kw) -> RequestContext:
    defaults: dict = {"user_id": "user-1"}
    defaults.update(kw)
    return RequestContext(**defaults)


# ── registration & listing ───────────────────────────────────────


class TestRegistration:
    def test_builtin_agents_registered(self) -> None:
        orch = _orchestrator()
        names = {a.name for a in orch.list_agents()}
        assert names == {"conversation", "coding", "research", "home", "education", "sight"}

    def test_register_custom_agent(self) -> None:
        orch = _orchestrator()
        custom = AgentTemplate(
            name="custom",
            description="A custom agent",
            model_tier="fast",
            prompt_domain="general",
            thinking_mode="auto",
        )
        orch.register_agent(custom)
        names = {a.name for a in orch.list_agents()}
        assert "custom" in names

    def test_overwrite_builtin(self) -> None:
        orch = _orchestrator()
        replacement = AgentTemplate(
            name="conversation",
            description="Replaced",
            model_tier="expert",
            prompt_domain="general",
            thinking_mode="on",
        )
        orch.register_agent(replacement)
        agents = {a.name: a for a in orch.list_agents()}
        assert agents["conversation"].description == "Replaced"
        assert agents["conversation"].model_tier == "expert"

    def test_list_agents_sorted(self) -> None:
        orch = _orchestrator()
        names = [a.name for a in orch.list_agents()]
        assert names == sorted(names)


# ── routing: input type shortcuts ────────────────────────────────


class TestInputTypeRouting:
    async def test_voice_defaults_to_conversation(self) -> None:
        orch = _orchestrator()
        cfg = await orch.route("tell me a joke", _ctx(input_type="voice"))
        assert cfg.template.name == "conversation"

    async def test_cli_defaults_to_coding(self) -> None:
        orch = _orchestrator()
        cfg = await orch.route("hello", _ctx(input_type="cli"))
        assert cfg.template.name == "coding"

    async def test_image_goes_to_sight(self) -> None:
        orch = _orchestrator()
        cfg = await orch.route("what is this?", _ctx(has_image=True))
        assert cfg.template.name == "sight"

    async def test_image_overrides_cli(self) -> None:
        orch = _orchestrator()
        cfg = await orch.route("describe this", _ctx(input_type="cli", has_image=True))
        assert cfg.template.name == "sight"

    async def test_voice_home_command(self) -> None:
        orch = _orchestrator()
        cfg = await orch.route("turn off the lights", _ctx(input_type="voice"))
        assert cfg.template.name == "home"


# ── routing: explicit requests ───────────────────────────────────


class TestExplicitRouting:
    async def test_explicit_code_request(self) -> None:
        orch = _orchestrator()
        cfg = await orch.route(
            "write me a function to sort a list",
            _ctx(),
        )
        assert cfg.template.name == "coding"

    async def test_explicit_research_request(self) -> None:
        orch = _orchestrator()
        cfg = await orch.route(
            "research the effects of caffeine on sleep",
            _ctx(),
        )
        assert cfg.template.name == "research"

    async def test_atlas_prefix_coding(self) -> None:
        orch = _orchestrator()
        cfg = await orch.route(
            "Atlas, write me a script to parse CSV files",
            _ctx(),
        )
        assert cfg.template.name == "coding"

    async def test_atlas_prefix_research(self) -> None:
        orch = _orchestrator()
        cfg = await orch.route(
            "Atlas, research quantum computing advances",
            _ctx(),
        )
        assert cfg.template.name == "research"


# ── routing: pattern matching ────────────────────────────────────


class TestPatternRouting:
    async def test_coding_keywords(self) -> None:
        orch = _orchestrator()
        cfg = await orch.route("debug this Python function", _ctx())
        assert cfg.template.name == "coding"

    async def test_research_keywords(self) -> None:
        orch = _orchestrator()
        cfg = await orch.route(
            "summarize this scientific paper on methodology",
            _ctx(),
        )
        assert cfg.template.name == "research"

    async def test_home_keywords(self) -> None:
        orch = _orchestrator()
        cfg = await orch.route("set the thermostat to 72", _ctx())
        assert cfg.template.name == "home"

    async def test_education_keywords(self) -> None:
        orch = _orchestrator()
        cfg = await orch.route("start number quest for the kids", _ctx())
        assert cfg.template.name == "education"

    async def test_sight_keywords(self) -> None:
        orch = _orchestrator()
        cfg = await orch.route("what do you see around me?", _ctx())
        assert cfg.template.name == "sight"

    async def test_fallback_to_conversation(self) -> None:
        orch = _orchestrator()
        cfg = await orch.route("tell me a joke", _ctx())
        assert cfg.template.name == "conversation"


# ── model tier & thinking ────────────────────────────────────────


class TestModelAndThinking:
    async def test_conversation_uses_fast(self) -> None:
        orch = _orchestrator()
        cfg = await orch.route("hi there", _ctx())
        assert cfg.template.model_tier == "fast"

    async def test_coding_uses_expert(self) -> None:
        orch = _orchestrator()
        cfg = await orch.route("Atlas, write me a Python function", _ctx())
        assert cfg.template.model_tier == "expert"

    async def test_research_uses_expert(self) -> None:
        orch = _orchestrator()
        cfg = await orch.route("research climate change impacts", _ctx())
        assert cfg.template.model_tier == "expert"

    async def test_complex_query_gets_higher_tier(self) -> None:
        """ModelRouter complexity may upgrade the effective tier."""
        orch = _orchestrator()
        complex_msg = (
            "Analyze the algorithmic complexity of this recursive function, "
            "explain step-by-step how to optimize it, compare the trade-offs "
            "of dynamic programming vs memoization, and prove the correctness "
            "of the solution. Derive the time complexity theorem."
        )
        cfg = await orch.route(complex_msg, _ctx())
        # The model_choice should reflect high complexity
        assert cfg.model_choice is not None
        assert cfg.model_choice.complexity_score > 0

    async def test_simple_query_stays_fast(self) -> None:
        orch = _orchestrator()
        cfg = await orch.route("hello", _ctx())
        assert cfg.model_choice is not None
        assert cfg.model_choice.tier == "fast"

    async def test_coding_thinking_off(self) -> None:
        orch = _orchestrator()
        cfg = await orch.route("Atlas, write me some code", _ctx())
        assert cfg.thinking_decision is not None
        assert cfg.thinking_decision.use_thinking is False

    async def test_research_thinking_on(self) -> None:
        orch = _orchestrator()
        cfg = await orch.route("research quantum entanglement", _ctx())
        assert cfg.thinking_decision is not None
        assert cfg.thinking_decision.use_thinking is True

    async def test_conversation_thinking_auto(self) -> None:
        orch = _orchestrator()
        cfg = await orch.route("hey there!", _ctx())
        assert cfg.thinking_decision is not None
        # "auto" delegates to ThinkingRouter — greetings should be no-think
        assert cfg.thinking_decision.use_thinking is False


# ── codex flag ───────────────────────────────────────────────────


class TestCodexFlag:
    async def test_coding_has_codex(self) -> None:
        orch = _orchestrator()
        cfg = await orch.route("Atlas, write me a Python class", _ctx())
        assert cfg.template.codex_enabled is True

    async def test_conversation_no_codex(self) -> None:
        orch = _orchestrator()
        cfg = await orch.route("how are you?", _ctx())
        assert cfg.template.codex_enabled is False

    async def test_research_no_codex(self) -> None:
        orch = _orchestrator()
        cfg = await orch.route("research machine learning trends", _ctx())
        assert cfg.template.codex_enabled is False

    async def test_home_no_codex(self) -> None:
        orch = _orchestrator()
        cfg = await orch.route("turn on the lights", _ctx())
        assert cfg.template.codex_enabled is False


# ── context propagation ─────────────────────────────────────────


class TestContextPropagation:
    async def test_context_in_config(self) -> None:
        ctx = _ctx(session_id="sess-42", room="kitchen")
        orch = _orchestrator()
        cfg = await orch.route("hello", ctx)
        assert cfg.context is ctx
        assert cfg.context.session_id == "sess-42"
        assert cfg.context.room == "kitchen"

    async def test_reason_populated(self) -> None:
        orch = _orchestrator()
        cfg = await orch.route("hello", _ctx())
        assert cfg.reason
        assert isinstance(cfg.reason, str)

    async def test_image_reason(self) -> None:
        orch = _orchestrator()
        cfg = await orch.route("what is this?", _ctx(has_image=True))
        assert "image" in cfg.reason.lower() or "sight" in cfg.reason.lower()


# ── execute streaming ────────────────────────────────────────────


class TestExecute:
    async def test_execute_streams_tokens(self) -> None:
        orch = _orchestrator()

        async def fake_chat(messages, model=None, stream=True, **kw):
            for tok in ["Hello", " ", "world"]:
                yield tok

        provider = AsyncMock()
        provider.chat = fake_chat

        tokens: list[str] = []
        async for tok in orch.execute("hi", _ctx(), provider):
            tokens.append(tok)

        assert tokens == ["Hello", " ", "world"]

    async def test_execute_passes_model(self) -> None:
        orch = _orchestrator()
        received: dict = {}

        async def fake_chat(messages, model=None, stream=True, **kw):
            received["model"] = model
            received["messages"] = messages
            yield "ok"

        provider = AsyncMock()
        provider.chat = fake_chat

        tokens = [t async for t in orch.execute("hi", _ctx(), provider)]
        assert tokens == ["ok"]
        assert received["model"] is not None

    async def test_execute_includes_domain_system_prompt(self) -> None:
        orch = _orchestrator()
        received: dict = {}

        async def fake_chat(messages, model=None, stream=True, **kw):
            received["messages"] = messages
            yield "ok"

        provider = AsyncMock()
        provider.chat = fake_chat

        _ = [t async for t in orch.execute("turn on the lights", _ctx(), provider)]
        sys_msg = received["messages"][0]
        assert sys_msg["role"] == "system"
        assert "home" in sys_msg["content"]


# ── agent template dataclass ─────────────────────────────────────


class TestAgentTemplate:
    def test_default_fields(self) -> None:
        tpl = AgentTemplate(
            name="test",
            description="Test agent",
            model_tier="fast",
            prompt_domain="general",
            thinking_mode="auto",
        )
        assert tpl.lora_domain is None
        assert tpl.tools is None
        assert tpl.priority == 1
        assert tpl.codex_enabled is False

    def test_coding_template_values(self) -> None:
        assert CODING.model_tier == "expert"
        assert CODING.prompt_domain == "coding"
        assert CODING.thinking_mode == "off"
        assert CODING.lora_domain == "coding"
        assert CODING.codex_enabled is True

    def test_research_template_values(self) -> None:
        assert RESEARCH.model_tier == "expert"
        assert RESEARCH.thinking_mode == "on"
        assert RESEARCH.codex_enabled is False

    def test_home_template_values(self) -> None:
        assert HOME.model_tier == "fast"
        assert HOME.thinking_mode == "off"
        assert HOME.tools == ["home_assistant"]

    def test_sight_template_values(self) -> None:
        assert SIGHT.model_tier == "fast"
        assert SIGHT.prompt_domain == "sight"
        assert SIGHT.tools == ["vision", "navigation"]


# ── request context dataclass ────────────────────────────────────


class TestRequestContext:
    def test_defaults(self) -> None:
        ctx = RequestContext(user_id="u1")
        assert ctx.input_type == "text"
        assert ctx.has_image is False
        assert ctx.room is None
        assert ctx.previous_agent is None
        assert ctx.conversation_summary is None

    def test_full_context(self) -> None:
        ctx = RequestContext(
            user_id="u2",
            room="bedroom",
            session_id="s1",
            input_type="voice",
            has_image=True,
            previous_agent="coding",
            conversation_summary="talked about python",
        )
        assert ctx.user_id == "u2"
        assert ctx.room == "bedroom"
        assert ctx.input_type == "voice"
        assert ctx.has_image is True
        assert ctx.previous_agent == "coding"
