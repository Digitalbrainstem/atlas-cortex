"""Atlas workspace TUI — thin client for the workspace daemon.

Module ownership: CLI workspace TUI client
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

SOCKET_PATH = Path(
    os.environ.get("ATLAS_SOCKET", os.path.expanduser("~/.atlas/atlas.sock"))
)


class WorkspaceClient:
    """Thin client that connects to the daemon via Unix socket."""

    def __init__(self) -> None:
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self) -> bool:
        """Connect to the daemon."""
        try:
            self._reader, self._writer = await asyncio.open_unix_connection(
                str(SOCKET_PATH)
            )
            self._connected = True
            return True
        except (ConnectionRefusedError, FileNotFoundError):
            return False

    async def disconnect(self) -> None:
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
        self._connected = False

    async def send(self, msg: dict[str, Any]) -> None:
        """Send a length-prefixed JSON message to the daemon."""
        if not self._writer:
            return
        data = json.dumps(msg).encode()
        self._writer.write(len(data).to_bytes(4, "big") + data)
        await self._writer.drain()

    async def receive(self) -> dict[str, Any] | None:
        """Receive a length-prefixed JSON message from the daemon."""
        if not self._reader:
            return None
        try:
            length_bytes = await self._reader.readexactly(4)
            length = int.from_bytes(length_bytes, "big")
            data = await self._reader.readexactly(length)
            return json.loads(data.decode())
        except (asyncio.IncompleteReadError, ConnectionResetError):
            self._connected = False
            return None

    async def send_message(self, text: str) -> None:
        """Send a chat message."""
        await self.send({"type": "message", "text": text})

    async def send_command(self, cmd: str, **kwargs: Any) -> None:
        """Send a command."""
        await self.send({"type": "command", "cmd": cmd, **kwargs})

    async def ping(self) -> bool:
        """Ping the daemon and wait for pong."""
        await self.send({"type": "ping"})
        msg = await self.receive()
        return msg is not None and msg.get("type") == "pong"


async def run_workspace_tui() -> int:
    """Run the workspace TUI client."""
    # Try Textual first for rich TUI
    try:
        from cortex.cli.workspace_tui import AtlasWorkspaceApp

        app = AtlasWorkspaceApp()
        await app.run_async()
        return 0
    except ImportError:
        pass

    # Fallback: basic readline client
    return await _run_basic_client()


async def _run_basic_client() -> int:
    """Basic readline-based workspace client (fallback when Textual not available)."""
    client = WorkspaceClient()

    if not await client.connect():
        print("❌ Cannot connect to Atlas daemon.")
        print("   Start it with: atlas daemon start")
        return 1

    # Receive welcome message
    welcome = await client.receive()
    if welcome and welcome.get("type") == "welcome":
        history = welcome.get("history", [])
        tools = welcome.get("tools", 0)
        uptime = welcome.get("uptime", 0)
        print(f"🧠 Atlas Workspace — {tools} tools, uptime {uptime / 3600:.1f}h")
        if history:
            print(f"   {len(history)} messages in history")
        print(f"   📂 {welcome.get('cwd', os.getcwd())}")
        print("   Type /help for commands, /quit to disconnect\n")

        for msg in history[-6:]:
            role = msg.get("role", "")
            content = msg.get("content", "")[:200]
            if role == "user":
                print(f"  You: {content}")
            elif role == "assistant":
                print(f"  Atlas: {content}")
        if history:
            print()

    # Start receiver task
    async def _receive_loop() -> None:
        while client.connected:
            msg = await client.receive()
            if not msg:
                break

            msg_type = msg.get("type", "")
            if msg_type == "token":
                print(msg.get("text", ""), end="", flush=True)
            elif msg_type == "response_end":
                print()
            elif msg_type == "tool_start":
                print(
                    f"\n  🔧 {msg['tool']}"
                    f"({json.dumps(msg.get('params', {}))[:60]})"
                )
            elif msg_type == "tool_result":
                icon = "✅" if msg.get("success") else "❌"
                output = msg.get("output", "")[:200]
                print(f"  {icon} {output}")
            elif msg_type == "info":
                print(msg.get("text", ""))
            elif msg_type == "status":
                print(
                    f"  Uptime: {msg['uptime'] / 3600:.1f}h"
                    f" | Messages: {msg['messages']}"
                    f" | Clients: {msg['clients']}"
                    f" | Tools: {msg['tools']}"
                )
            elif msg_type == "error":
                print(f"  ❌ {msg.get('message', 'Unknown error')}")
            elif msg_type == "dispatch_started":
                print(f"  🚀 Dispatched {len(msg['tasks'])} tasks")
            elif msg_type == "dispatch_complete":
                for r in msg.get("results", []):
                    icon = "✅" if r["status"] == "completed" else "❌"
                    print(f"  {icon} {r['id']}: {r['status']} ({r['duration']:.1f}s)")
            elif msg_type == "cleared":
                print("  🧹 Conversation cleared")
            elif msg_type == "cwd_changed":
                print(f"  📂 {msg.get('path', '')}")
            elif msg_type == "disconnect":
                break
            elif msg_type == "shutdown":
                print(f"\n⚠️  {msg.get('message', 'Daemon shutting down')}")
                break

    receiver = asyncio.create_task(_receive_loop())

    # Input loop
    try:
        loop = asyncio.get_event_loop()
        while client.connected:
            try:
                text = await loop.run_in_executor(None, lambda: input("atlas> "))
            except EOFError:
                break

            text = text.strip()
            if not text:
                continue

            if text == "/quit":
                break
            else:
                await client.send_message(text)
    except KeyboardInterrupt:
        print("\n")
    finally:
        receiver.cancel()
        await client.disconnect()

    return 0
