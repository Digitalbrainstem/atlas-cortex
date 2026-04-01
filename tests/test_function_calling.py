"""Tests for the tool use / function calling system."""

from __future__ import annotations

import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from cortex.plugins.base import CommandMatch, CommandResult, CortexPlugin
from cortex.tools.function_calling import (
    FunctionCallingHandler,
    ToolRegistry,
    ToolResult,
)
from cortex.tools.plugin_adapter import (
    _build_command_match,
    _build_message,
    _make_plugin_handler,
    _TOOL_SPECS,
    adapt_plugin,
    register_all_plugins,
)


# ── Helpers ─────────────────────────────────────────────────────


class _StubPlugin(CortexPlugin):
    """Minimal weather plugin for testing."""

    plugin_id = "weather"
    display_name = "Weather (stub)"

    async def setup(self, config):
        return True

    async def health(self):
        return True

    async def match(self, message, context):
        if "weather" in message.lower():
            # Simulate extracting a location
            parts = message.lower().split("in ")
            loc = parts[-1].strip() if len(parts) > 1 else "unknown"
            return CommandMatch(matched=True, intent="weather", entities=[loc])
        return CommandMatch(matched=False)

    async def handle(self, message, match, context):
        location = match.entities[0] if match.entities else "unknown"
        return CommandResult(
            success=True,
            response=f"Sunny and 72 °F in {location}.",
            entities_used=[location],
        )


class _StubSchedulingPlugin(CortexPlugin):
    """Minimal scheduling plugin for testing multi-tool registration."""

    plugin_id = "scheduling"
    display_name = "Scheduling (stub)"

    async def setup(self, config):
        return True

    async def health(self):
        return True

    async def match(self, message, context):
        if "timer" in message.lower():
            return CommandMatch(matched=True, intent="set_timer")
        if "alarm" in message.lower():
            return CommandMatch(matched=True, intent="set_alarm")
        return CommandMatch(matched=False)

    async def handle(self, message, match, context):
        return CommandResult(success=True, response=f"Done: {match.intent}")


@pytest.fixture
def registry():
    return ToolRegistry()


@pytest.fixture
def stub_plugin():
    return _StubPlugin()


# =====================================================================
# ToolRegistry
# =====================================================================


class TestToolRegistry:
    """Core registry behaviour."""

    def test_register_and_list(self, registry):
        async def handler(args, ctx):
            return ToolResult(success=True, output="ok")

        registry.register("my_tool", "A test tool", {"type": "object", "properties": {}}, handler)
        assert "my_tool" in registry.list_tools()
        assert registry.get("my_tool") is not None
        assert registry.get("nonexistent") is None

    def test_unregister(self, registry):
        registry.register("x", "desc", {}, lambda a, c: None)
        registry.unregister("x")
        assert "x" not in registry.list_tools()

    def test_unregister_missing_is_noop(self, registry):
        registry.unregister("nope")  # should not raise

    def test_get_tool_definitions_openai_format(self, registry):
        registry.register(
            "get_weather",
            "Get weather",
            {
                "type": "object",
                "properties": {"location": {"type": "string"}},
                "required": ["location"],
            },
            lambda a, c: None,
        )
        defs = registry.get_tool_definitions()
        assert len(defs) == 1
        d = defs[0]
        assert d["type"] == "function"
        assert d["function"]["name"] == "get_weather"
        assert d["function"]["description"] == "Get weather"
        assert "location" in d["function"]["parameters"]["properties"]

    def test_multiple_tools_definitions(self, registry):
        for name in ("a", "b", "c"):
            registry.register(name, f"Tool {name}", {"type": "object", "properties": {}}, lambda a, c: None)
        defs = registry.get_tool_definitions()
        assert len(defs) == 3
        names = {d["function"]["name"] for d in defs}
        assert names == {"a", "b", "c"}

    @pytest.mark.asyncio
    async def test_execute_success(self, registry):
        async def handler(args, ctx):
            return ToolResult(success=True, output=f"Hello {args['name']}")

        registry.register("greet", "Greet", {}, handler)
        result = await registry.execute("greet", {"name": "Atlas"})
        assert result.success is True
        assert result.output == "Hello Atlas"

    @pytest.mark.asyncio
    async def test_execute_passes_context(self, registry):
        async def handler(args, ctx):
            return ToolResult(success=True, output=ctx.get("user_id", "none"))

        registry.register("ctx_tool", "Context", {}, handler)
        result = await registry.execute("ctx_tool", {}, context={"user_id": "alice"})
        assert result.output == "alice"

    @pytest.mark.asyncio
    async def test_execute_unknown_tool(self, registry):
        result = await registry.execute("nope", {})
        assert result.success is False
        assert "Unknown tool" in result.output

    @pytest.mark.asyncio
    async def test_execute_handler_exception(self, registry):
        async def broken(args, ctx):
            raise ValueError("boom")

        registry.register("broken", "Broken", {}, broken)
        result = await registry.execute("broken", {})
        assert result.success is False
        assert "boom" in result.output

    @pytest.mark.asyncio
    async def test_execute_sync_handler(self, registry):
        def sync_handler(args, ctx):
            return ToolResult(success=True, output="sync ok")

        registry.register("sync", "Sync", {}, sync_handler)
        result = await registry.execute("sync", {})
        assert result.success is True
        assert result.output == "sync ok"

    @pytest.mark.asyncio
    async def test_execute_non_toolresult_return(self, registry):
        async def handler(args, ctx):
            return "plain string"

        registry.register("plain", "Plain", {}, handler)
        result = await registry.execute("plain", {})
        assert result.success is True
        assert result.output == "plain string"


# =====================================================================
# Plugin adapter — specs & builders
# =====================================================================


class TestToolSpecs:
    """Validate the built-in tool spec list."""

    def test_unique_tool_names(self):
        names = [s.tool_name for s in _TOOL_SPECS]
        dupes = [n for n in names if names.count(n) > 1]
        assert len(names) == len(set(names)), f"Duplicate tool names: {dupes}"

    def test_all_specs_have_valid_json_schema(self):
        for spec in _TOOL_SPECS:
            assert spec.parameters.get("type") == "object", f"{spec.tool_name}: missing type"
            assert "properties" in spec.parameters, f"{spec.tool_name}: missing properties"

    def test_required_keys_exist_in_properties(self):
        for spec in _TOOL_SPECS:
            props = set(spec.parameters.get("properties", {}).keys())
            required = set(spec.parameters.get("required", []))
            missing = required - props
            assert not missing, f"{spec.tool_name}: required keys not in properties: {missing}"


class TestBuildMessage:
    def test_basic(self):
        spec = next(s for s in _TOOL_SPECS if s.tool_name == "get_weather")
        msg = _build_message(spec, {"location": "London"})
        assert "London" in msg
        assert "weather" in msg

    def test_missing_optional_key_produces_clean_string(self):
        spec = next(s for s in _TOOL_SPECS if s.tool_name == "get_news")
        msg = _build_message(spec, {})  # topic is optional
        assert isinstance(msg, str)
        assert len(msg) > 0

    def test_multiple_args(self):
        spec = next(s for s in _TOOL_SPECS if s.tool_name == "convert_units")
        msg = _build_message(spec, {"value": 5, "from_unit": "miles", "to_unit": "km"})
        assert "5" in msg
        assert "miles" in msg
        assert "km" in msg

    def test_no_double_spaces(self):
        spec = next(s for s in _TOOL_SPECS if s.tool_name == "tell_story")
        msg = _build_message(spec, {})  # genre omitted
        assert "  " not in msg


class TestBuildCommandMatch:
    def test_entities_from_entity_keys(self):
        spec = next(s for s in _TOOL_SPECS if s.tool_name == "get_weather")
        match = _build_command_match(spec, {"location": "Paris"})
        assert match.matched is True
        assert "Paris" in match.entities
        assert match.confidence == 1.0

    def test_metadata_from_metadata_keys(self):
        spec = next(s for s in _TOOL_SPECS if s.tool_name == "translate_text")
        match = _build_command_match(spec, {"text": "hello", "target_language": "Spanish"})
        assert match.metadata["text"] == "hello"
        assert match.metadata["target_language"] == "Spanish"

    def test_tool_call_marker(self):
        spec = next(s for s in _TOOL_SPECS if s.tool_name == "daily_briefing")
        match = _build_command_match(spec, {})
        assert match.metadata["_tool_call"] is True
        assert match.metadata["_tool_name"] == "daily_briefing"

    def test_dynamic_intent(self):
        spec = next(s for s in _TOOL_SPECS if s.tool_name == "control_media")
        match = _build_command_match(spec, {"action": "pause"})
        assert match.intent == "pause"

    def test_missing_entity_key_skipped(self):
        spec = next(s for s in _TOOL_SPECS if s.tool_name == "get_weather")
        match = _build_command_match(spec, {})
        assert match.entities == []


# =====================================================================
# Plugin adapter — handler wrappers
# =====================================================================


class TestPluginHandlers:
    @pytest.mark.asyncio
    async def test_handler_calls_plugin(self, stub_plugin):
        spec = next(s for s in _TOOL_SPECS if s.tool_name == "get_weather")
        handler = _make_plugin_handler(stub_plugin, spec)
        result = await handler({"location": "Tokyo"}, {})
        assert result.success is True
        assert "tokyo" in result.output.lower()

    @pytest.mark.asyncio
    async def test_handler_falls_back_on_match_failure(self):
        class NoMatchPlugin(CortexPlugin):
            plugin_id = "weather"
            display_name = "NoMatch"

            async def setup(self, config):
                return True

            async def health(self):
                return True

            async def match(self, message, context):
                return CommandMatch(matched=False)

            async def handle(self, message, match, context):
                loc = match.entities[0] if match.entities else "fallback"
                return CommandResult(success=True, response=f"Weather in {loc}")

        spec = next(s for s in _TOOL_SPECS if s.tool_name == "get_weather")
        handler = _make_plugin_handler(NoMatchPlugin(), spec)
        result = await handler({"location": "Oslo"}, {})
        assert result.success is True
        assert "Oslo" in result.output

    @pytest.mark.asyncio
    async def test_handler_exception_returns_error(self):
        class BrokenPlugin(CortexPlugin):
            plugin_id = "weather"
            display_name = "Broken"

            async def setup(self, config):
                return True

            async def health(self):
                return True

            async def match(self, message, context):
                return CommandMatch(matched=True)

            async def handle(self, message, match, context):
                raise RuntimeError("kaboom")

        spec = next(s for s in _TOOL_SPECS if s.tool_name == "get_weather")
        handler = _make_plugin_handler(BrokenPlugin(), spec)
        result = await handler({"location": "x"}, {})
        assert result.success is False
        assert "kaboom" in result.output


class TestAdaptPlugin:
    def test_registers_matching_tools(self, registry, stub_plugin):
        count = adapt_plugin(stub_plugin, registry)
        assert count == 1
        assert "get_weather" in registry.list_tools()

    def test_multiple_tools_per_plugin(self, registry):
        count = adapt_plugin(_StubSchedulingPlugin(), registry)
        assert count == 3  # set_timer, set_alarm, set_reminder

    def test_unknown_plugin_registers_nothing(self, registry):
        class UnknownPlugin(CortexPlugin):
            plugin_id = "unknown_xyz_123"
            display_name = "Unknown"

            async def setup(self, c):
                return True

            async def health(self):
                return True

            async def match(self, m, c):
                return CommandMatch(matched=False)

            async def handle(self, m, match, c):
                return CommandResult(success=True, response="")

        count = adapt_plugin(UnknownPlugin(), registry)
        assert count == 0


class TestRegisterAllPlugins:
    def test_registers_from_plugin_registry(self, registry):
        plugin_reg = MagicMock()
        plugin_reg.list_plugins.return_value = [_StubPlugin()]
        total = register_all_plugins(registry, plugin_reg)
        assert total >= 1
        assert "get_weather" in registry.list_tools()

    def test_code_sandbox_registered_when_available(self, registry):
        plugin_reg = MagicMock()
        plugin_reg.list_plugins.return_value = []
        total = register_all_plugins(registry, plugin_reg)
        # CodeSandbox exists in this repo, so execute_code should be registered
        assert "execute_code" in registry.list_tools()
        assert total >= 1

    def test_from_plugins_method(self, registry):
        plugin_reg = MagicMock()
        plugin_reg.list_plugins.return_value = [_StubPlugin()]
        registry.from_plugins(plugin_reg)
        assert "get_weather" in registry.list_tools()


# =====================================================================
# FunctionCallingHandler
# =====================================================================


class TestFunctionCallingHandlerInit:
    def test_from_provider(self, registry):
        provider = MagicMock()
        provider.base_url = "http://localhost:9999"
        provider.api_key = "test-key"
        provider.default_model = "qwen3-4b"

        handler = FunctionCallingHandler.from_provider(registry, provider)
        assert handler.base_url == "http://localhost:9999"
        assert handler.api_key == "test-key"
        assert handler.model == "qwen3-4b"

    def test_from_provider_defaults(self, registry):
        provider = object()  # no attributes
        handler = FunctionCallingHandler.from_provider(registry, provider)
        assert handler.base_url == "http://localhost:8080"

    def test_custom_max_rounds(self, registry):
        handler = FunctionCallingHandler(registry, max_rounds=10)
        assert handler.max_rounds == 10


class TestChatWithTools:
    """Test the function-calling conversation loop."""

    @pytest.mark.asyncio
    async def test_no_tool_calls_yields_text(self, registry):
        handler = FunctionCallingHandler(registry, base_url="http://fake")

        resp = {
            "choices": [{
                "message": {"role": "assistant", "content": "Hello world!"},
                "finish_reason": "stop",
            }],
        }
        with patch.object(handler, "_request_completion", return_value=resp):
            tokens = [t async for t in handler.chat_with_tools(
                [{"role": "user", "content": "hi"}],
            )]
        assert "".join(tokens) == "Hello world!"

    @pytest.mark.asyncio
    async def test_empty_content_yields_nothing(self, registry):
        handler = FunctionCallingHandler(registry, base_url="http://fake")

        resp = {
            "choices": [{
                "message": {"role": "assistant", "content": ""},
                "finish_reason": "stop",
            }],
        }
        with patch.object(handler, "_request_completion", return_value=resp):
            tokens = [t async for t in handler.chat_with_tools(
                [{"role": "user", "content": "hi"}],
            )]
        assert tokens == []

    @pytest.mark.asyncio
    async def test_single_tool_call(self, registry):
        async def greet(args, ctx):
            return ToolResult(success=True, output=f"Hi, {args['name']}!")

        registry.register("greet", "Greet", {"type": "object", "properties": {}}, greet)
        handler = FunctionCallingHandler(registry, base_url="http://fake")

        tool_resp = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "greet", "arguments": '{"name": "Atlas"}'},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
        }
        final_resp = {
            "choices": [{
                "message": {"role": "assistant", "content": "Greeted Atlas!"},
                "finish_reason": "stop",
            }],
        }

        call_count = 0

        async def mock_req(messages, **kw):
            nonlocal call_count
            call_count += 1
            return tool_resp if call_count == 1 else final_resp

        with patch.object(handler, "_request_completion", side_effect=mock_req):
            tokens = [t async for t in handler.chat_with_tools(
                [{"role": "user", "content": "greet Atlas"}],
            )]
        assert "".join(tokens) == "Greeted Atlas!"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_parallel_tool_calls(self, registry):
        async def weather(args, ctx):
            return ToolResult(success=True, output=f"Sunny in {args.get('loc', '?')}")

        async def time_tool(args, ctx):
            return ToolResult(success=True, output="3:00 PM")

        registry.register("weather", "W", {"type": "object", "properties": {}}, weather)
        registry.register("time", "T", {"type": "object", "properties": {}}, time_tool)

        handler = FunctionCallingHandler(registry, base_url="http://fake")

        tool_resp = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {"id": "c1", "type": "function", "function": {"name": "weather", "arguments": '{"loc":"NYC"}'}},
                        {"id": "c2", "type": "function", "function": {"name": "time", "arguments": "{}"}},
                    ],
                },
                "finish_reason": "tool_calls",
            }],
        }
        final_resp = {
            "choices": [{
                "message": {"role": "assistant", "content": "NYC is sunny, it's 3 PM."},
                "finish_reason": "stop",
            }],
        }

        responses = iter([tool_resp, final_resp])

        async def mock_req(messages, **kw):
            return next(responses)

        with patch.object(handler, "_request_completion", side_effect=mock_req):
            text = "".join([t async for t in handler.chat_with_tools(
                [{"role": "user", "content": "weather and time"}],
            )])
        assert "sunny" in text.lower()

    @pytest.mark.asyncio
    async def test_tool_results_sent_back(self, registry):
        """Verify tool results are appended as tool-role messages."""
        async def echo(args, ctx):
            return ToolResult(success=True, output="echo_output")

        registry.register("echo", "Echo", {"type": "object", "properties": {}}, echo)
        handler = FunctionCallingHandler(registry, base_url="http://fake")

        tool_resp = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_echo",
                        "type": "function",
                        "function": {"name": "echo", "arguments": "{}"},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
        }
        final_resp = {
            "choices": [{
                "message": {"role": "assistant", "content": "done"},
                "finish_reason": "stop",
            }],
        }

        captured_messages = None

        async def mock_req(messages, **kw):
            nonlocal captured_messages
            captured_messages = messages
            if any(m.get("role") == "tool" for m in messages):
                return final_resp
            return tool_resp

        with patch.object(handler, "_request_completion", side_effect=mock_req):
            _ = [t async for t in handler.chat_with_tools(
                [{"role": "user", "content": "test"}],
            )]

        tool_msgs = [m for m in captured_messages if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["content"] == "echo_output"
        assert tool_msgs[0]["tool_call_id"] == "call_echo"
        assert tool_msgs[0]["name"] == "echo"

    @pytest.mark.asyncio
    async def test_tool_error_forwarded(self, registry):
        async def boom(args, ctx):
            raise ValueError("service down")

        registry.register("boom", "Boom", {"type": "object", "properties": {}}, boom)
        handler = FunctionCallingHandler(registry, base_url="http://fake")

        tool_resp = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "c_err",
                        "type": "function",
                        "function": {"name": "boom", "arguments": "{}"},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
        }
        final_resp = {
            "choices": [{
                "message": {"role": "assistant", "content": "Sorry, tool error."},
                "finish_reason": "stop",
            }],
        }

        call_idx = 0

        async def mock_req(messages, **kw):
            nonlocal call_idx
            call_idx += 1
            if call_idx == 1:
                return tool_resp
            # Verify error was forwarded
            tool_msgs = [m for m in messages if m.get("role") == "tool"]
            assert any("Tool error" in m.get("content", "") for m in tool_msgs)
            return final_resp

        with patch.object(handler, "_request_completion", side_effect=mock_req):
            text = "".join([t async for t in handler.chat_with_tools(
                [{"role": "user", "content": "do it"}],
            )])
        assert "Sorry" in text

    @pytest.mark.asyncio
    async def test_max_rounds_safety(self, registry):
        async def noop(args, ctx):
            return ToolResult(success=True, output="ok")

        registry.register("noop", "Noop", {"type": "object", "properties": {}}, noop)
        handler = FunctionCallingHandler(registry, base_url="http://fake", max_rounds=2)

        loop_resp = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "cx",
                        "type": "function",
                        "function": {"name": "noop", "arguments": "{}"},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
        }

        with patch.object(handler, "_request_completion", return_value=loop_resp):
            # After max_rounds, falls through to _stream_completion
            async def fake_stream(*a, **kw):
                yield "fallback response"

            with patch.object(handler, "_stream_completion", side_effect=fake_stream):
                tokens = [t async for t in handler.chat_with_tools(
                    [{"role": "user", "content": "loop"}],
                )]
        assert "".join(tokens) == "fallback response"

    @pytest.mark.asyncio
    async def test_malformed_arguments_handled(self, registry):
        async def tool_fn(args, ctx):
            return ToolResult(success=True, output=f"got: {args}")

        registry.register("t", "T", {"type": "object", "properties": {}}, tool_fn)
        handler = FunctionCallingHandler(registry, base_url="http://fake")

        tool_resp = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "t", "arguments": "not valid json{{{"},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
        }
        final_resp = {
            "choices": [{
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }],
        }

        responses = iter([tool_resp, final_resp])

        async def mock_req(messages, **kw):
            return next(responses)

        with patch.object(handler, "_request_completion", side_effect=mock_req):
            tokens = [t async for t in handler.chat_with_tools(
                [{"role": "user", "content": "test"}],
            )]
        assert "".join(tokens) == "ok"

    @pytest.mark.asyncio
    async def test_context_passed_to_tool(self, registry):
        received_ctx = {}

        async def spy_tool(args, ctx):
            received_ctx.update(ctx)
            return ToolResult(success=True, output="ok")

        registry.register("spy", "Spy", {"type": "object", "properties": {}}, spy_tool)
        handler = FunctionCallingHandler(registry, base_url="http://fake")

        tool_resp = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "spy", "arguments": "{}"},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
        }
        final_resp = {
            "choices": [{
                "message": {"role": "assistant", "content": "done"},
                "finish_reason": "stop",
            }],
        }

        responses = iter([tool_resp, final_resp])

        async def mock_req(messages, **kw):
            return next(responses)

        with patch.object(handler, "_request_completion", side_effect=mock_req):
            _ = [t async for t in handler.chat_with_tools(
                [{"role": "user", "content": "test"}],
                context={"user_id": "bob", "room": "kitchen"},
            )]
        assert received_ctx["user_id"] == "bob"
        assert received_ctx["room"] == "kitchen"


# =====================================================================
# Integration: end-to-end plugin → tool → handler
# =====================================================================


class TestEndToEnd:
    @pytest.mark.asyncio
    async def test_weather_plugin_through_tool_system(self):
        """Register a weather plugin, call via tool handler, get result."""
        reg = ToolRegistry()
        plugin = _StubPlugin()
        adapt_plugin(plugin, reg)

        result = await reg.execute("get_weather", {"location": "Berlin"}, context={})
        assert result.success is True
        assert "berlin" in result.output.lower()
        assert "72" in result.output

    @pytest.mark.asyncio
    async def test_scheduling_plugin_multiple_tools(self):
        reg = ToolRegistry()
        plugin = _StubSchedulingPlugin()
        adapt_plugin(plugin, reg)

        assert "set_timer" in reg.list_tools()
        assert "set_alarm" in reg.list_tools()
        assert "set_reminder" in reg.list_tools()

        timer_result = await reg.execute("set_timer", {"duration": "5 minutes"}, context={})
        assert timer_result.success is True

    @pytest.mark.asyncio
    async def test_full_loop_with_weather_tool(self):
        """Simulate: user asks weather → LLM calls tool → result → final answer."""
        reg = ToolRegistry()
        adapt_plugin(_StubPlugin(), reg)

        handler = FunctionCallingHandler(reg, base_url="http://fake")

        tool_resp = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "c_weather",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": json.dumps({"location": "Seattle"}),
                        },
                    }],
                },
                "finish_reason": "tool_calls",
            }],
        }
        final_resp = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "It's sunny and 72°F in Seattle today!",
                },
                "finish_reason": "stop",
            }],
        }

        responses = iter([tool_resp, final_resp])

        async def mock_req(messages, **kw):
            return next(responses)

        with patch.object(handler, "_request_completion", side_effect=mock_req):
            text = "".join([t async for t in handler.chat_with_tools(
                [{"role": "user", "content": "What's the weather in Seattle?"}],
            )])
        assert "Seattle" in text
        assert "72" in text
