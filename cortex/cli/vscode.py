"""VS Code integration bridge for Atlas CLI.

Provides a JSON-RPC server over a Unix socket that a VS Code extension
(or any JSON-RPC client) can use to interact with Atlas.

Two integration modes:
1. **Terminal** — run ``atlas`` in VS Code's integrated terminal.
2. **Extension API** — connect to the Unix socket from a VS Code extension
   to get chat, code-explain, test-generation, etc.

Module ownership: CLI VS Code integration
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SOCKET_PATH = Path(os.environ.get(
    "ATLAS_VSCODE_SOCKET",
    Path.home() / ".atlas" / "vscode.sock",
))


class VSCodeBridge:
    """JSON-RPC server that VS Code extensions connect to."""

    def __init__(self) -> None:
        self._server: asyncio.AbstractServer | None = None
        self._provider: Any = None
        self._running = False

    async def start_server(self, provider: Any = None) -> None:
        """Start the Unix-socket JSON-RPC server."""
        self._provider = provider
        SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
        if SOCKET_PATH.exists():
            SOCKET_PATH.unlink()

        self._server = await asyncio.start_unix_server(
            self._handle_client, str(SOCKET_PATH),
        )
        self._running = True
        logger.info("VS Code bridge listening on %s", SOCKET_PATH)

        async with self._server:
            await self._server.serve_forever()

    async def stop(self) -> None:
        """Shut down the bridge."""
        self._running = False
        if self._server:
            self._server.close()
        if SOCKET_PATH.exists():
            SOCKET_PATH.unlink()

    # ── Client handling ─────────────────────────────────────────────

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a single client connection (line-delimited JSON-RPC)."""
        logger.info("VS Code client connected")
        try:
            while self._running:
                line = await reader.readline()
                if not line:
                    break
                try:
                    request = json.loads(line.decode())
                except json.JSONDecodeError:
                    continue

                response = await self._dispatch(request)
                writer.write((json.dumps(response) + "\n").encode())
                await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            logger.info("VS Code client disconnected")

    async def _dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        """Route a JSON-RPC request to the appropriate handler."""
        method = request.get("method", "")
        params = request.get("params", {})
        req_id = request.get("id")

        handlers: dict[str, Any] = {
            "chat": self._handle_chat,
            "explain_code": self._handle_explain_code,
            "fix_code": self._handle_fix_code,
            "generate_tests": self._handle_generate_tests,
            "status": self._handle_status,
        }

        handler = handlers.get(method)
        if handler is None:
            return {
                "id": req_id,
                "error": {"code": -32601, "message": f"Unknown method: {method}"},
            }

        try:
            result = await handler(params)
            return {"id": req_id, "result": result}
        except Exception as exc:
            return {
                "id": req_id,
                "error": {"code": -32000, "message": str(exc)},
            }

    # ── RPC method handlers ─────────────────────────────────────────

    async def _handle_chat(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle a chat message."""
        message = params.get("message", "")
        if not self._provider:
            return {"text": "LLM provider not available."}

        response = ""
        async for chunk in self._provider.chat(
            [{"role": "user", "content": message}], stream=True,
        ):
            response += chunk
        return {"text": response}

    async def _handle_explain_code(self, params: dict[str, Any]) -> dict[str, Any]:
        """Explain a code snippet."""
        code = params.get("code", "")
        language = params.get("language", "")
        prompt = f"Explain this {language} code concisely:\n\n```{language}\n{code}\n```"
        return await self._handle_chat({"message": prompt})

    async def _handle_fix_code(self, params: dict[str, Any]) -> dict[str, Any]:
        """Suggest a fix for code with an error."""
        code = params.get("code", "")
        error = params.get("error", "")
        language = params.get("language", "")
        prompt = (
            f"Fix this {language} code. Error: {error}\n\n"
            f"```{language}\n{code}\n```\n\n"
            "Return only the fixed code."
        )
        return await self._handle_chat({"message": prompt})

    async def _handle_generate_tests(self, params: dict[str, Any]) -> dict[str, Any]:
        """Generate tests for a code snippet."""
        code = params.get("code", "")
        language = params.get("language", "")
        prompt = (
            f"Generate unit tests for this {language} code:\n\n"
            f"```{language}\n{code}\n```"
        )
        return await self._handle_chat({"message": prompt})

    async def _handle_status(self, _params: dict[str, Any]) -> dict[str, Any]:
        """Return bridge status."""
        return {
            "running": self._running,
            "provider": type(self._provider).__name__ if self._provider else None,
        }
