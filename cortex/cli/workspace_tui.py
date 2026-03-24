"""Atlas workspace Textual TUI — rich terminal interface.

Module ownership: CLI workspace rich TUI
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Vertical
    from textual.widgets import Footer, Header, Input, RichLog

    _HAS_TEXTUAL = True
except ImportError:
    _HAS_TEXTUAL = False

if _HAS_TEXTUAL:

    class AtlasWorkspaceApp(App):  # type: ignore[misc]
        """Rich TUI for the Atlas workspace."""

        TITLE = "Atlas Workspace"
        CSS = """
        #chat-log {
            height: 1fr;
            border: solid green;
        }
        #tool-log {
            height: 30%;
            border: solid blue;
        }
        #input-area {
            dock: bottom;
            height: 3;
        }
        """

        BINDINGS = [
            Binding("f1", "show_help", "Help"),
            Binding("f2", "show_tools", "Tools"),
            Binding("f3", "search_memory", "Memory"),
            Binding("f5", "show_status", "Status"),
            Binding("ctrl+c", "quit", "Quit"),
        ]

        def compose(self) -> ComposeResult:
            yield Header()
            yield Vertical(
                RichLog(id="chat-log", wrap=True, markup=True),
                RichLog(id="tool-log", wrap=True, markup=True),
            )
            yield Input(placeholder="atlas> ", id="input-area")
            yield Footer()

        async def on_mount(self) -> None:
            """Connect to daemon on startup."""
            from cortex.cli.workspace import WorkspaceClient

            self.client = WorkspaceClient()
            connected = await self.client.connect()

            chat = self.query_one("#chat-log", RichLog)
            if connected:
                welcome = await self.client.receive()
                if welcome:
                    tools = welcome.get("tools", 0)
                    chat.write(f"[green]🧠 Connected — {tools} tools ready[/green]")
                    for msg in welcome.get("history", [])[-10:]:
                        role = msg.get("role", "")
                        content = msg.get("content", "")[:300]
                        if role == "user":
                            chat.write(f"[cyan]You:[/cyan] {content}")
                        elif role == "assistant":
                            chat.write(f"[green]Atlas:[/green] {content}")

                self._receiver_task = asyncio.create_task(self._receive_loop())
            else:
                chat.write(
                    "[red]❌ Cannot connect to daemon."
                    " Start with: atlas daemon start[/red]"
                )

        async def _receive_loop(self) -> None:
            chat = self.query_one("#chat-log", RichLog)
            tools_log = self.query_one("#tool-log", RichLog)

            while True:
                msg = await self.client.receive()
                if not msg:
                    chat.write("[red]Disconnected from daemon[/red]")
                    break

                msg_type = msg.get("type", "")
                if msg_type == "token":
                    chat.write(msg.get("text", ""), end="")
                elif msg_type == "response_end":
                    chat.write("")
                elif msg_type == "tool_start":
                    tools_log.write(
                        f"[blue]🔧 {msg['tool']}[/blue]"
                        f"({msg.get('params', {})})"
                    )
                elif msg_type == "tool_result":
                    icon = "✅" if msg.get("success") else "❌"
                    tools_log.write(f"{icon} {msg.get('output', '')[:200]}")
                elif msg_type == "info":
                    chat.write(f"[dim]{msg.get('text', '')}[/dim]")
                elif msg_type == "error":
                    chat.write(f"[red]❌ {msg.get('message', '')}[/red]")
                elif msg_type == "shutdown":
                    chat.write(
                        f"[red]⚠️  {msg.get('message', 'Daemon shutting down')}[/red]"
                    )
                    break

        async def on_input_submitted(self, event: Input.Submitted) -> None:
            text = event.value.strip()
            if not text:
                return

            chat = self.query_one("#chat-log", RichLog)
            input_widget = self.query_one("#input-area", Input)
            input_widget.value = ""

            chat.write(f"[cyan]You:[/cyan] {text}")
            await self.client.send_message(text)

        async def action_show_help(self) -> None:
            await self.client.send_message("/help")

        async def action_show_tools(self) -> None:
            await self.client.send_message("/tools")

        async def action_search_memory(self) -> None:
            await self.client.send_message("/memory recent")

        async def action_show_status(self) -> None:
            await self.client.send_message("/status")

        def action_quit(self) -> None:
            self.exit()
