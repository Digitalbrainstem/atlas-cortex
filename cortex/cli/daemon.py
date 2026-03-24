"""Atlas workspace daemon — persistent background agent.

Holds conversation state, tools, memory, and curiosity engine.
Clients connect via Unix socket. Daemon survives disconnects.

Module ownership: CLI workspace daemon
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SOCKET_PATH = Path(
    os.environ.get("ATLAS_SOCKET", os.path.expanduser("~/.atlas/atlas.sock"))
)
PID_FILE = Path(os.path.expanduser("~/.atlas/daemon.pid"))


class WorkspaceState:
    """Holds all persistent state for the workspace."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.provider: Any = None
        self.memory_bridge: Any = None
        self.curiosity_engine: Any = None
        self.tool_registry: Any = None
        self.active_tasks: list[dict[str, Any]] = []
        self.connected_clients: list[asyncio.StreamWriter] = []
        self.started_at: float = time.time()
        self.cwd: str = os.getcwd()

    async def initialize(self, model: str | None = None) -> None:
        """Set up all subsystems."""
        # Provider
        try:
            from cortex.providers import get_provider
            self.provider = get_provider()
        except Exception as e:
            logger.warning("Provider init failed: %s", e)

        # Memory bridge
        try:
            from cortex.cli.memory_bridge import MemoryBridge
            self.memory_bridge = MemoryBridge(user_id="workspace")
            await self.memory_bridge.initialize()
            if self.provider:
                self.memory_bridge.set_provider(self.provider)
        except Exception as e:
            logger.warning("Memory bridge init failed: %s", e)

        # Curiosity engine
        try:
            from cortex.curiosity import CuriosityEngine
            self.curiosity_engine = CuriosityEngine()
            await self.curiosity_engine.initialize()
        except Exception as e:
            logger.warning("Curiosity engine init failed: %s", e)

        # Tool registry
        try:
            from cortex.cli.tools import get_default_registry
            self.tool_registry = get_default_registry()
        except Exception as e:
            logger.warning("Tool registry init failed: %s", e)

        logger.info(
            "Workspace initialized: %d tools, provider=%s",
            len(self.tool_registry.list_tools()) if self.tool_registry else 0,
            type(self.provider).__name__ if self.provider else "None",
        )


class DaemonServer:
    """Unix socket server that accepts client connections."""

    def __init__(self) -> None:
        self.state = WorkspaceState()
        self._server: asyncio.AbstractServer | None = None
        self._running = False

    async def start(self, model: str | None = None) -> None:
        """Start the daemon."""
        await self.state.initialize(model)

        # Remove stale socket
        SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
        if SOCKET_PATH.exists():
            SOCKET_PATH.unlink()

        self._server = await asyncio.start_unix_server(
            self._handle_client, str(SOCKET_PATH)
        )
        self._running = True

        PID_FILE.write_text(str(os.getpid()))

        logger.info("Daemon listening on %s (PID %d)", SOCKET_PATH, os.getpid())

        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.stop()))

        async with self._server:
            await self._server.serve_forever()

    async def stop(self) -> None:
        """Graceful shutdown."""
        logger.info("Daemon shutting down...")
        self._running = False

        for writer in list(self.state.connected_clients):
            try:
                await _send(writer, {"type": "shutdown", "message": "Daemon shutting down"})
                writer.close()
            except Exception:
                pass

        if self.state.memory_bridge:
            try:
                await self.state.memory_bridge.shutdown()
            except Exception:
                pass
        if self.state.curiosity_engine:
            try:
                await self.state.curiosity_engine.save_state()
            except Exception:
                pass

        if self._server:
            self._server.close()
        if SOCKET_PATH.exists():
            SOCKET_PATH.unlink()
        if PID_FILE.exists():
            PID_FILE.unlink()

        logger.info("Daemon stopped")

    # ── Client handling ──────────────────────────────────────────

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle a new client connection."""
        self.state.connected_clients.append(writer)
        client_id = id(writer)
        logger.info(
            "Client connected: %d (total: %d)",
            client_id,
            len(self.state.connected_clients),
        )

        await _send(writer, {
            "type": "welcome",
            "history": self.state.messages[-50:],
            "tools": (
                len(self.state.tool_registry.list_tools())
                if self.state.tool_registry
                else 0
            ),
            "memory_available": self.state.memory_bridge is not None,
            "uptime": time.time() - self.state.started_at,
            "cwd": self.state.cwd,
        })

        try:
            while self._running:
                length_bytes = await reader.readexactly(4)
                length = int.from_bytes(length_bytes, "big")
                data = await reader.readexactly(length)
                msg = json.loads(data.decode())
                await self._process_client_message(msg, writer)
        except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
            logger.info("Client disconnected: %d", client_id)
        except Exception as e:
            logger.error("Client error: %s", e)
        finally:
            if writer in self.state.connected_clients:
                self.state.connected_clients.remove(writer)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _process_client_message(
        self, msg: dict[str, Any], writer: asyncio.StreamWriter
    ) -> None:
        """Process a message from a client."""
        msg_type = msg.get("type", "")

        if msg_type == "message":
            text = msg.get("text", "")
            await self._handle_chat(text, writer)

        elif msg_type == "command":
            cmd = msg.get("cmd", "")
            if cmd == "dispatch":
                tasks = msg.get("tasks", [])
                await self._handle_dispatch(tasks, writer)
            elif cmd == "reflect":
                await self._handle_reflect(writer)
            elif cmd == "status":
                await self._handle_status(writer)
            elif cmd == "clear":
                self.state.messages.clear()
                await _send(writer, {"type": "cleared"})
            elif cmd == "cd":
                new_dir = msg.get("path", "")
                if new_dir and os.path.isdir(new_dir):
                    os.chdir(new_dir)
                    self.state.cwd = new_dir
                    await _send(writer, {"type": "cwd_changed", "path": new_dir})

        elif msg_type == "ping":
            await _send(writer, {"type": "pong"})

    # ── Chat ─────────────────────────────────────────────────────

    async def _handle_chat(self, text: str, writer: asyncio.StreamWriter) -> None:
        """Handle a chat message — run through pipeline with memory."""
        self.state.messages.append({"role": "user", "content": text})

        if text.startswith("/"):
            await self._handle_slash(text, writer)
            return

        try:
            if self.state.memory_bridge and self.state.provider:
                from cortex.cli.agent import build_system_prompt

                system_prompt = (
                    build_system_prompt(self.state.tool_registry)
                    if self.state.tool_registry
                    else "You are Atlas, a helpful AI assistant."
                )

                if self.state.curiosity_engine:
                    system_prompt += self.state.curiosity_engine.get_system_prompt_addition()

                messages = await self.state.memory_bridge.build_messages_with_memory(
                    system_prompt=system_prompt,
                    current_message=text,
                    history=self.state.messages[:-1],
                )
            else:
                messages = [{"role": "user", "content": text}]

            full_response = ""
            if self.state.provider:
                async for chunk in self.state.provider.chat(messages, stream=True):
                    full_response += chunk
                    await _send(writer, {"type": "token", "text": chunk})
                    await self._broadcast(
                        {"type": "token", "text": chunk}, exclude=writer
                    )
            else:
                full_response = (
                    "No LLM provider available. Start Ollama and restart the daemon."
                )
                await _send(writer, {"type": "token", "text": full_response})

            # Parse and execute tool calls
            from cortex.cli.agent import parse_tool_calls

            tool_calls = parse_tool_calls(full_response)

            if tool_calls and self.state.tool_registry:
                for tc in tool_calls:
                    tool_id = tc.get("tool", "")
                    params = tc.get("params", {})
                    tool = self.state.tool_registry.get(tool_id)
                    if tool:
                        await self._send_all(
                            {"type": "tool_start", "tool": tool_id, "params": params}
                        )

                        start = time.time()
                        result = await tool.execute(
                            params, {"registry": self.state.tool_registry}
                        )
                        duration = time.time() - start

                        await self._send_all({
                            "type": "tool_result",
                            "tool": tool_id,
                            "output": result.output[:5000],
                            "success": result.success,
                        })

                        if self.state.curiosity_engine:
                            self.state.curiosity_engine.on_tool_executed(
                                tool_id, params, result, duration
                            )

                        if self.state.memory_bridge:
                            await self.state.memory_bridge.remember_tool_result(
                                tool_id, params, result.output
                            )

            self.state.messages.append({"role": "assistant", "content": full_response})
            await self._send_all({"type": "response_end"})

        except Exception as e:
            logger.error("Chat error: %s", e)
            await _send(writer, {"type": "error", "message": str(e)})

    # ── Slash commands ───────────────────────────────────────────

    async def _handle_slash(self, text: str, writer: asyncio.StreamWriter) -> None:
        """Handle slash commands."""
        parts = text.split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd == "/help":
            help_text = (
                "Commands:\n"
                "  /help              Show this help\n"
                "  /clear             Clear conversation\n"
                "  /memory <query>    Search memory\n"
                "  /status            Show daemon status\n"
                "  /reflect           Curiosity engine insights\n"
                "  /tools             List available tools\n"
                "  /dispatch t1; t2   Dispatch multiple tasks\n"
                "  /cd <path>         Change working directory\n"
                "  /quit              Disconnect (daemon keeps running)\n"
            )
            await _send(writer, {"type": "info", "text": help_text})
        elif cmd == "/memory":
            if self.state.memory_bridge and arg:
                result = await self.state.memory_bridge.recall(arg)
                await _send(writer, {"type": "info", "text": result or "No memories found."})
            else:
                await _send(writer, {"type": "info", "text": "Memory not available or no query given."})
        elif cmd == "/status":
            await self._handle_status(writer)
        elif cmd == "/reflect":
            await self._handle_reflect(writer)
        elif cmd == "/tools":
            if self.state.tool_registry:
                tools = self.state.tool_registry.list_tools()
                tool_text = f"{len(tools)} tools:\n" + "\n".join(
                    f"  {t.tool_id:25s} {t.description[:50]}" for t in tools
                )
                await _send(writer, {"type": "info", "text": tool_text})
            else:
                await _send(writer, {"type": "info", "text": "No tools loaded."})
        elif cmd == "/dispatch":
            tasks = [t.strip() for t in arg.split(";") if t.strip()]
            if tasks:
                await self._handle_dispatch(tasks, writer)
        elif cmd == "/clear":
            self.state.messages.clear()
            await _send(writer, {"type": "cleared"})
        elif cmd == "/cd":
            if arg and os.path.isdir(arg):
                os.chdir(arg)
                self.state.cwd = arg
                await _send(writer, {"type": "cwd_changed", "path": arg})
            else:
                await _send(writer, {"type": "error", "message": f"Not a directory: {arg}"})
        elif cmd == "/quit":
            await _send(writer, {"type": "disconnect"})
            raise ConnectionResetError("Client requested disconnect")
        else:
            await _send(writer, {"type": "error", "message": f"Unknown command: {cmd}"})

    # ── Command handlers ─────────────────────────────────────────

    async def _handle_reflect(self, writer: asyncio.StreamWriter) -> None:
        if self.state.curiosity_engine:
            report = await self.state.curiosity_engine.reflect()
            await _send(writer, {"type": "info", "text": report})
        else:
            await _send(writer, {"type": "info", "text": "Curiosity engine not available."})

    async def _handle_status(self, writer: asyncio.StreamWriter) -> None:
        status = {
            "type": "status",
            "uptime": time.time() - self.state.started_at,
            "messages": len(self.state.messages),
            "clients": len(self.state.connected_clients),
            "tools": (
                len(self.state.tool_registry.list_tools())
                if self.state.tool_registry
                else 0
            ),
            "provider": (
                type(self.state.provider).__name__
                if self.state.provider
                else "None"
            ),
            "memory": self.state.memory_bridge is not None,
            "curiosity": self.state.curiosity_engine is not None,
            "cwd": self.state.cwd,
            "active_tasks": len(self.state.active_tasks),
        }
        await _send(writer, status)

    async def _handle_dispatch(
        self, tasks: list[str], writer: asyncio.StreamWriter
    ) -> None:
        async def _dispatch() -> None:
            try:
                from cortex.cli.dispatch import AgentDispatcher

                dispatcher = AgentDispatcher()
                await dispatcher.initialize()
                results = await dispatcher.dispatch(tasks, strategy="auto")
                await self._send_all({
                    "type": "dispatch_complete",
                    "results": [
                        {"id": t.id, "status": t.status, "duration": t.duration}
                        for t in results
                    ],
                })
            except Exception as e:
                logger.error("Dispatch error: %s", e)
                await self._send_all({
                    "type": "error",
                    "message": f"Dispatch failed: {e}",
                })

        asyncio.create_task(_dispatch())
        await _send(writer, {"type": "dispatch_started", "tasks": tasks})

    # ── Transport helpers ────────────────────────────────────────

    async def _send_all(self, msg: dict[str, Any]) -> None:
        """Broadcast to all connected clients."""
        for writer in list(self.state.connected_clients):
            await _send(writer, msg)

    async def _broadcast(
        self, msg: dict[str, Any], exclude: asyncio.StreamWriter | None = None
    ) -> None:
        """Broadcast to all clients except one."""
        for writer in list(self.state.connected_clients):
            if writer != exclude:
                await _send(writer, msg)


# ── Wire protocol ────────────────────────────────────────────────

async def _send(writer: asyncio.StreamWriter, msg: dict[str, Any]) -> None:
    """Send a length-prefixed JSON message."""
    try:
        data = json.dumps(msg).encode()
        writer.write(len(data).to_bytes(4, "big") + data)
        await writer.drain()
    except Exception:
        pass


async def _receive(reader: asyncio.StreamReader) -> dict[str, Any] | None:
    """Receive a length-prefixed JSON message."""
    try:
        length_bytes = await reader.readexactly(4)
        length = int.from_bytes(length_bytes, "big")
        data = await reader.readexactly(length)
        return json.loads(data.decode())
    except (asyncio.IncompleteReadError, ConnectionResetError):
        return None


# ── CLI commands for daemon management ───────────────────────────

async def start_daemon(model: str | None = None) -> None:
    """Start the daemon in the foreground (daemonize with systemd or nohup)."""
    server = DaemonServer()
    await server.start(model)


def is_daemon_running() -> bool:
    """Check if the daemon is running."""
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, ProcessLookupError, PermissionError):
        return False


def get_daemon_pid() -> int | None:
    """Get the daemon PID if running."""
    if not PID_FILE.exists():
        return None
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)
        return pid
    except Exception:
        return None


def stop_daemon() -> bool:
    """Stop the daemon by sending SIGTERM."""
    pid = get_daemon_pid()
    if pid:
        os.kill(pid, signal.SIGTERM)
        return True
    return False
