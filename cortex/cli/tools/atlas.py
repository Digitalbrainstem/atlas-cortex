"""Atlas integration tools: HA, timers, reminders, routines, notifications, memory.

Wraps existing cortex modules for use by the CLI agent.  All dependencies are
imported inside ``execute()`` so the tool file can be imported even when the
backing engine is not installed.
"""

# Module ownership: Agent tool infrastructure — Atlas integrations
from __future__ import annotations

import logging
from typing import Any

from cortex.cli.tools import AgentTool, ToolResult

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Home Assistant
# ---------------------------------------------------------------------------


class HAControlTool(AgentTool):
    """Control Home Assistant entities (lights, switches, climate, etc.)."""

    tool_id = "ha_control"
    description = "Control Home Assistant entities (lights, switches, climate, etc.)"
    requires_confirmation = True
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["turn_on", "turn_off", "toggle", "set"],
                "description": "Action to perform on the entity",
            },
            "entity_id": {
                "type": "string",
                "description": "Home Assistant entity ID (e.g. light.living_room)",
            },
            "data": {
                "type": "object",
                "description": "Optional service-call data (brightness, temperature, etc.)",
            },
        },
        "required": ["action", "entity_id"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        action: str = params["action"]
        entity_id: str = params["entity_id"]
        data: dict[str, Any] = params.get("data") or {}

        try:
            import os

            from cortex.integrations.ha.client import HAClient
        except ImportError:
            return ToolResult(
                success=False,
                output="",
                error="Home Assistant integration not available (cortex.integrations.ha not installed)",
            )

        import os as _os

        base_url = _os.environ.get("HA_URL", "")
        token = _os.environ.get("HA_TOKEN", "")
        if not base_url or not token:
            return ToolResult(
                success=False,
                output="",
                error="Home Assistant not configured — set HA_URL and HA_TOKEN environment variables",
            )

        # Map action → HA domain/service
        domain = entity_id.split(".")[0] if "." in entity_id else "homeassistant"
        service_map = {
            "turn_on": "turn_on",
            "turn_off": "turn_off",
            "toggle": "toggle",
            "set": "turn_on",  # 'set' sends data with turn_on
        }
        service = service_map.get(action, action)
        payload = {"entity_id": entity_id, **data}

        try:
            async with HAClient(base_url, token) as client:
                await client.call_service(domain, service, payload)
        except Exception as exc:
            return ToolResult(
                success=False,
                output="",
                error=f"HA call failed: {exc}",
            )

        return ToolResult(
            success=True,
            output=f"OK — {action} {entity_id}",
            metadata={"entity_id": entity_id, "action": action},
        )


# ---------------------------------------------------------------------------
# Timers
# ---------------------------------------------------------------------------


class TimerTool(AgentTool):
    """Set, list, or cancel timers."""

    tool_id = "timer"
    description = "Set, list, or cancel timers"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["set", "list", "cancel", "pause", "resume"],
                "description": "Timer action to perform",
            },
            "duration_seconds": {
                "type": "integer",
                "description": "Duration in seconds (required for 'set')",
            },
            "label": {
                "type": "string",
                "description": "Human-readable label for the timer",
            },
            "timer_id": {
                "type": "integer",
                "description": "Timer ID (required for cancel/pause/resume)",
            },
        },
        "required": ["action"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        action: str = params["action"]
        try:
            from cortex.scheduling import TimerEngine
        except ImportError:
            return ToolResult(
                success=False,
                output="",
                error="Scheduling module not available",
            )

        try:
            from cortex.db import get_db
            conn = get_db()
        except Exception as exc:
            return ToolResult(success=False, output="", error=f"Database unavailable: {exc}")

        engine = TimerEngine(conn)

        if action == "set":
            duration = params.get("duration_seconds")
            if not duration:
                return ToolResult(success=False, output="", error="duration_seconds is required for 'set'")
            label = params.get("label", "")
            user_id = (context or {}).get("user_id", "")
            room = (context or {}).get("room", "")
            tid = engine.start_timer(duration, label=label, user_id=user_id, room=room)
            return ToolResult(
                success=True,
                output=f"Timer {tid} set for {duration}s — {label}" if label else f"Timer {tid} set for {duration}s",
                metadata={"timer_id": tid},
            )

        if action == "list":
            user_id = (context or {}).get("user_id", "")
            timers = engine.list_timers(user_id=user_id)
            if not timers:
                return ToolResult(success=True, output="No active timers")
            lines = [f"  #{t['id']}  {t.get('label', '')}  {t.get('remaining', '?')}s remaining" for t in timers]
            return ToolResult(success=True, output="Active timers:\n" + "\n".join(lines), metadata={"count": len(timers)})

        if action in ("cancel", "pause", "resume"):
            tid = params.get("timer_id")
            if tid is None:
                return ToolResult(success=False, output="", error=f"timer_id is required for '{action}'")
            fn = {"cancel": engine.cancel_timer, "pause": engine.pause_timer, "resume": engine.resume_timer}[action]
            ok = fn(tid)
            verb = {"cancel": "cancelled", "pause": "paused", "resume": "resumed"}[action]
            if ok:
                return ToolResult(success=True, output=f"Timer {tid} {verb}")
            return ToolResult(success=False, output="", error=f"Could not {action} timer {tid}")

        return ToolResult(success=False, output="", error=f"Unknown timer action: {action}")


# ---------------------------------------------------------------------------
# Reminders
# ---------------------------------------------------------------------------


class ReminderTool(AgentTool):
    """Set or list reminders."""

    tool_id = "reminder"
    description = "Set or list reminders"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["set", "list", "delete"],
                "description": "Reminder action to perform",
            },
            "message": {
                "type": "string",
                "description": "Reminder message (required for 'set')",
            },
            "trigger_at": {
                "type": "string",
                "description": "ISO-8601 datetime for when the reminder fires",
            },
            "reminder_id": {
                "type": "integer",
                "description": "Reminder ID (required for 'delete')",
            },
        },
        "required": ["action"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        action: str = params["action"]
        try:
            from cortex.scheduling import ReminderEngine
        except ImportError:
            return ToolResult(success=False, output="", error="Scheduling module not available")

        try:
            from cortex.db import get_db
            conn = get_db()
        except Exception as exc:
            return ToolResult(success=False, output="", error=f"Database unavailable: {exc}")

        engine = ReminderEngine(conn)

        if action == "set":
            message = params.get("message")
            if not message:
                return ToolResult(success=False, output="", error="message is required for 'set'")
            trigger_at_str = params.get("trigger_at")
            trigger_at = None
            if trigger_at_str:
                from datetime import datetime, timezone

                try:
                    trigger_at = datetime.fromisoformat(trigger_at_str)
                    if trigger_at.tzinfo is None:
                        trigger_at = trigger_at.replace(tzinfo=timezone.utc)
                except ValueError:
                    return ToolResult(success=False, output="", error=f"Invalid datetime format: {trigger_at_str}")
            user_id = (context or {}).get("user_id", "")
            room = (context or {}).get("room", "")
            rid = engine.create_reminder(message, trigger_at=trigger_at, user_id=user_id, room=room)
            return ToolResult(
                success=True,
                output=f"Reminder {rid} set: {message}",
                metadata={"reminder_id": rid},
            )

        if action == "list":
            user_id = (context or {}).get("user_id", "")
            reminders = engine.list_reminders(user_id=user_id)
            if not reminders:
                return ToolResult(success=True, output="No active reminders")
            lines = [f"  #{r['id']}  {r.get('message', '')}  @ {r.get('trigger_at', '?')}" for r in reminders]
            return ToolResult(
                success=True,
                output="Active reminders:\n" + "\n".join(lines),
                metadata={"count": len(reminders)},
            )

        if action == "delete":
            rid = params.get("reminder_id")
            if rid is None:
                return ToolResult(success=False, output="", error="reminder_id is required for 'delete'")
            ok = engine.delete_reminder(rid)
            if ok:
                return ToolResult(success=True, output=f"Reminder {rid} deleted")
            return ToolResult(success=False, output="", error=f"Could not delete reminder {rid}")

        return ToolResult(success=False, output="", error=f"Unknown reminder action: {action}")


# ---------------------------------------------------------------------------
# Routines
# ---------------------------------------------------------------------------


class RoutineTool(AgentTool):
    """Run, list, or manage automation routines."""

    tool_id = "routine"
    description = "Run, list, or manage automation routines"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["run", "list", "create", "delete"],
                "description": "Routine action to perform",
            },
            "routine_name": {
                "type": "string",
                "description": "Name for a new routine (used with 'create')",
            },
            "routine_id": {
                "type": "integer",
                "description": "Routine ID (required for run/delete)",
            },
        },
        "required": ["action"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        action: str = params["action"]
        try:
            from cortex.routines.engine import RoutineEngine
        except ImportError:
            return ToolResult(success=False, output="", error="Routines module not available")

        try:
            from cortex.db import get_db
            conn = get_db()
        except Exception as exc:
            return ToolResult(success=False, output="", error=f"Database unavailable: {exc}")

        engine = RoutineEngine(conn)

        if action == "list":
            user_id = (context or {}).get("user_id", "")
            routines = await engine.list_routines(user_id=user_id)
            if not routines:
                return ToolResult(success=True, output="No routines defined")
            lines = [f"  #{r['id']}  {r.get('name', '')}  (enabled={r.get('enabled', '?')})" for r in routines]
            return ToolResult(
                success=True,
                output="Routines:\n" + "\n".join(lines),
                metadata={"count": len(routines)},
            )

        if action == "run":
            rid = params.get("routine_id")
            if rid is None:
                return ToolResult(success=False, output="", error="routine_id is required for 'run'")
            try:
                run_id = await engine.run_routine(rid, context=context)
            except Exception as exc:
                return ToolResult(success=False, output="", error=f"Routine execution failed: {exc}")
            return ToolResult(
                success=True,
                output=f"Routine {rid} started (run_id={run_id})",
                metadata={"routine_id": rid, "run_id": run_id},
            )

        if action == "create":
            name = params.get("routine_name")
            if not name:
                return ToolResult(success=False, output="", error="routine_name is required for 'create'")
            user_id = (context or {}).get("user_id", "")
            rid = await engine.create_routine(name, user_id=user_id)
            return ToolResult(
                success=True,
                output=f"Routine {rid} created: {name}",
                metadata={"routine_id": rid},
            )

        if action == "delete":
            rid = params.get("routine_id")
            if rid is None:
                return ToolResult(success=False, output="", error="routine_id is required for 'delete'")
            ok = await engine.delete_routine(rid)
            if ok:
                return ToolResult(success=True, output=f"Routine {rid} deleted")
            return ToolResult(success=False, output="", error=f"Could not delete routine {rid}")

        return ToolResult(success=False, output="", error=f"Unknown routine action: {action}")


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


class NotifyTool(AgentTool):
    """Send a notification to satellites or log."""

    tool_id = "notify"
    description = "Send a notification to satellites or log"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Notification title",
            },
            "message": {
                "type": "string",
                "description": "Notification body",
            },
            "level": {
                "type": "string",
                "enum": ["info", "warning", "critical"],
                "description": "Notification severity level (default: info)",
            },
        },
        "required": ["title", "message"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        title: str = params["title"]
        message: str = params["message"]
        level: str = params.get("level", "info")

        try:
            from cortex.notifications.channels import send_notification
        except ImportError:
            return ToolResult(
                success=False,
                output="",
                error="Notifications module not available",
            )

        try:
            delivered = await send_notification(
                level=level,
                title=title,
                message=message,
                source="cli_agent",
            )
        except Exception as exc:
            return ToolResult(success=False, output="", error=f"Notification failed: {exc}")

        return ToolResult(
            success=True,
            output=f"Notification sent ({delivered} channel(s)): {title}",
            metadata={"delivered": delivered, "level": level},
        )


# ---------------------------------------------------------------------------
# Memory Search
# ---------------------------------------------------------------------------


class MemoryTool(AgentTool):
    """Search Atlas memory for relevant past information."""

    tool_id = "memory_search"
    description = "Search Atlas memory for relevant past information"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results to return (default: 5)",
            },
        },
        "required": ["query"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        query: str = params["query"]
        top_k: int = params.get("max_results", 5)

        try:
            from cortex.memory.hot import hot_query
        except ImportError:
            return ToolResult(success=False, output="", error="Memory module not available")

        try:
            from cortex.db import get_db
            conn = get_db()
        except Exception as exc:
            return ToolResult(success=False, output="", error=f"Database unavailable: {exc}")

        user_id = (context or {}).get("user_id", "")

        try:
            hits = hot_query(query, user_id=user_id, conn=conn, top_k=top_k)
        except Exception as exc:
            return ToolResult(success=False, output="", error=f"Memory search failed: {exc}")

        if not hits:
            return ToolResult(success=True, output="No memories found", metadata={"count": 0})

        lines: list[str] = []
        for h in hits:
            lines.append(f"[{h.score:.2f}] {h.text}")
        return ToolResult(
            success=True,
            output="\n".join(lines),
            metadata={"count": len(hits)},
        )


# ---------------------------------------------------------------------------
# Memory Store
# ---------------------------------------------------------------------------


class MemoryStoreTool(AgentTool):
    """Store a fact or learning for future recall."""

    tool_id = "memory_store"
    description = "Store a fact or learning for future recall"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The fact or information to store",
            },
            "category": {
                "type": "string",
                "description": "Optional category tag (e.g. 'preference', 'fact', 'learning')",
            },
        },
        "required": ["content"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        content: str = params["content"]
        category: str = params.get("category", "")

        try:
            from cortex.memory.controller import get_memory_system
        except ImportError:
            return ToolResult(success=False, output="", error="Memory module not available")

        ms = get_memory_system()
        if ms is None:
            return ToolResult(
                success=False,
                output="",
                error="Memory system not initialised — cannot store memories",
            )

        user_id = (context or {}).get("user_id", "")
        tags = [category] if category else None

        try:
            await ms.remember(content, user_id=user_id, tags=tags)
        except Exception as exc:
            return ToolResult(success=False, output="", error=f"Memory store failed: {exc}")

        return ToolResult(
            success=True,
            output=f"Stored: {content[:80]}{'…' if len(content) > 80 else ''}",
            metadata={"category": category},
        )


# ---------------------------------------------------------------------------
# Satellite display
# ---------------------------------------------------------------------------


class DisplayTool(AgentTool):
    """Show content on a satellite tablet display (video, recipe, weather, etc.)."""

    tool_id = "display"
    description = (
        "Show content on a satellite tablet display "
        "(video, recipe, weather, dashboard, timer, list, photos)"
    )
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": [
                    "avatar", "video", "recipe", "dashboard", "weather",
                    "timer", "list", "photos", "media_player",
                ],
                "description": "What to display",
            },
            "room": {
                "type": "string",
                "description": "Target room (empty = all tablets)",
                "default": "",
            },
            "content": {
                "type": "object",
                "description": "Content data (varies by mode)",
            },
            "duration": {
                "type": "integer",
                "description": "Seconds to show before returning to avatar (0 = until changed)",
                "default": 0,
            },
        },
        "required": ["mode"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        mode: str = params["mode"]
        room: str = params.get("room", "")
        content: dict[str, Any] = params.get("content", {})
        duration: int = params.get("duration", 0)

        try:
            from cortex.satellite.display_protocol import send_display_command
        except ImportError:
            return ToolResult(
                success=False, output="", error="Display module not available"
            )

        try:
            ok = await send_display_command(room, mode, content, duration)
        except Exception as exc:
            return ToolResult(
                success=False, output="", error=f"Display command failed: {exc}"
            )

        if not ok:
            return ToolResult(
                success=False,
                output="",
                error=f"Display command rejected for mode={mode}",
            )
        target = room or "all tablets"
        return ToolResult(
            success=True,
            output=f"Showing {mode} on {target}",
            metadata={"mode": mode, "room": room},
        )
