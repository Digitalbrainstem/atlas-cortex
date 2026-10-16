"""Tool use / function calling system for Atlas Cortex.

Lets the LLM call Atlas plugins (and custom tools) via the OpenAI
function-calling protocol supported by llama-server, vLLM, and other
OpenAI-compatible backends.

Flow:
  1. ToolRegistry holds tool definitions + handlers.
  2. FunctionCallingHandler sends messages + tool defs to the LLM.
  3. If the LLM response contains tool_calls, execute each tool.
  4. Send tool results back as tool-role messages.
  5. LLM generates the final user-facing response.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable

import httpx

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 5


@dataclass
class ToolResult:
    """Result of executing a tool."""

    success: bool
    output: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolDefinition:
    """Internal representation of a registered tool."""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Any]


class ToolRegistry:
    """Registry of tools the LLM can call via function calling."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: Callable[..., Any],
    ) -> None:
        """Register a tool that the LLM can invoke."""
        self._tools[name] = ToolDefinition(
            name=name,
            description=description,
            parameters=parameters,
            handler=handler,
        )
        logger.info("Tool registered: %s", name)

    def unregister(self, name: str) -> None:
        """Remove a tool from the registry."""
        self._tools.pop(name, None)

    def get(self, name: str) -> ToolDefinition | None:
        """Look up a tool by name."""
        return self._tools.get(name)

    def list_tools(self) -> list[str]:
        """Return the names of all registered tools."""
        return list(self._tools)

    # ------------------------------------------------------------------
    # OpenAI format
    # ------------------------------------------------------------------

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """Return tool definitions in OpenAI function-calling format."""
        return [
            {
                "type": "function",
                "function": {
                    "name": td.name,
                    "description": td.description,
                    "parameters": td.parameters,
                },
            }
            for td in self._tools.values()
        ]

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Execute a tool by name with the given arguments."""
        tool = self._tools.get(tool_name)
        if tool is None:
            return ToolResult(success=False, output=f"Unknown tool: {tool_name}")
        try:
            result = tool.handler(arguments, context or {})
            if asyncio.iscoroutine(result) or asyncio.isfuture(result):
                result = await result
            if not isinstance(result, ToolResult):
                return ToolResult(success=True, output=str(result))
            return result
        except Exception as exc:
            logger.exception("Tool %s raised: %s", tool_name, exc)
            return ToolResult(success=False, output=f"Tool error: {exc}")

    # ------------------------------------------------------------------
    # Bulk import from plugin system
    # ------------------------------------------------------------------

    def from_plugins(self, plugin_registry: Any) -> None:
        """Auto-register tools from the existing Atlas plugin registry.

        Delegates to :func:`cortex.tools.plugin_adapter.register_all_plugins`.
        """
        from cortex.tools.plugin_adapter import register_all_plugins

        register_all_plugins(self, plugin_registry)


class FunctionCallingHandler:
    """Handles the multi-turn function-calling flow with an OpenAI-compatible LLM.

    Speaks the ``/v1/chat/completions`` protocol directly so it can
    inspect ``tool_calls`` in the response before deciding whether to
    stream the final answer.
    """

    def __init__(
        self,
        tool_registry: ToolRegistry,
        *,
        base_url: str = "http://localhost:8080",
        api_key: str = "sk-no-key",
        model: str | None = None,
        max_rounds: int = MAX_TOOL_ROUNDS,
    ) -> None:
        self.registry = tool_registry
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.max_rounds = max_rounds
        self._client: httpx.AsyncClient | None = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=None,
            )
        return self._client

    @classmethod
    def from_provider(
        cls,
        tool_registry: ToolRegistry,
        provider: Any,
        **kwargs: Any,
    ) -> FunctionCallingHandler:
        """Create a handler reusing an existing provider's connection info."""
        base_url = getattr(provider, "base_url", "http://localhost:8080")
        api_key = getattr(provider, "api_key", "sk-no-key")
        model = getattr(provider, "default_model", None)
        return cls(tool_registry, base_url=base_url, api_key=api_key, model=model, **kwargs)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        temperature: float = 0.7,
        max_tokens: int | None = None,
        context: dict[str, Any] | None = None,
    ) -> AsyncGenerator[str, None]:
        """Run the full function-calling loop, yielding final text tokens.

        1. Send messages + tool definitions (non-streaming).
        2. While the LLM wants to call tools (up to *max_rounds*):
           a. Execute each requested tool via the registry.
           b. Append tool-role result messages.
           c. Re-send (non-streaming).
        3. Yield the final text response.
        """
        effective_model = model or self.model or "default"
        effective_tools = tools if tools is not None else self.registry.get_tool_definitions()
        ctx = context or {}

        working_messages: list[dict[str, Any]] = list(messages)
        rounds = 0

        while rounds < self.max_rounds:
            rounds += 1

            response = await self._request_completion(
                working_messages,
                tools=effective_tools,
                tool_choice=tool_choice,
                model=effective_model,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            choice = (response.get("choices") or [{}])[0]
            message = choice.get("message", {})
            tool_calls = message.get("tool_calls")

            if not tool_calls:
                content = message.get("content") or ""
                if content:
                    yield content
                return

            # Append the assistant message (including tool_calls) to history
            working_messages.append(message)

            # Execute each tool call and append results
            for tc in tool_calls:
                fn = tc.get("function", {})
                tc_id = tc.get("id", "")
                name = fn.get("name", "")
                raw_args = fn.get("arguments", "{}")

                try:
                    arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except json.JSONDecodeError:
                    arguments = {}

                logger.info("Tool call: %s(%s)", name, arguments)
                result = await self.registry.execute(name, arguments, ctx)

                working_messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "name": name,
                    "content": result.output,
                })

        # Exhausted max_rounds — stream a final response without tools
        logger.warning("Hit max tool-calling rounds (%d), generating final response", self.max_rounds)
        async for token in self._stream_completion(
            working_messages,
            model=effective_model,
            temperature=temperature,
            max_tokens=max_tokens,
        ):
            yield token

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    async def _request_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        model: str = "default",
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Non-streaming chat completion with optional tool definitions."""
        client = self._ensure_client()
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice

        resp = await client.post("/v1/chat/completions", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def _stream_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str = "default",
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[str, None]:
        """Streaming chat completion (no tools — for final response only)."""
        client = self._ensure_client()
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        async with client.stream("POST", "/v1/chat/completions", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                raw = line[6:].strip()
                if raw == "[DONE]":
                    break
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                delta = (data.get("choices") or [{}])[0].get("delta", {})
                content = delta.get("content", "")
                if content:
                    yield content

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
