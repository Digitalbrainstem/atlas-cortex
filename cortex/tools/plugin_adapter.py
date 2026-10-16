"""Bridge existing CortexPlugin instances to tool definitions.

Each Atlas plugin exposes one or more tools that the LLM can invoke
via function calling.  This module defines the tool schemas and creates
handler wrappers that translate structured tool arguments into the
plugin's ``match()`` / ``handle()`` interface.

Strategy
--------
For every tool call the adapter:

1. Reconstructs a natural-language *message* from the arguments so the
   plugin's existing regex-based ``match()`` can parse it.
2. Calls ``match()`` — if it succeeds, the resulting ``CommandMatch``
   carries properly-extracted entities/metadata.
3. If ``match()`` fails (the synthetic message didn't hit a regex), a
   ``CommandMatch`` is built directly from the tool spec.
4. ``handle()`` runs with the message + match + context.

This hybrid approach maximises compatibility with the 21 existing
plugins without requiring any changes to them.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable

from cortex.plugins.base import CommandMatch, CortexPlugin
from cortex.tools.function_calling import ToolRegistry, ToolResult

logger = logging.getLogger(__name__)


# ── Declarative tool-to-plugin mapping ────────────────────────────


@dataclass
class _ToolSpec:
    """Maps a single tool to an Atlas plugin."""

    tool_name: str
    description: str
    parameters: dict[str, Any]
    plugin_id: str
    intent: str = ""
    entity_keys: list[str] = field(default_factory=list)
    metadata_keys: list[str] = field(default_factory=list)
    message_template: str = "{_raw}"


# fmt: off
_TOOL_SPECS: list[_ToolSpec] = [
    # ── Weather ──────────────────────────────────────────────────
    _ToolSpec(
        tool_name="get_weather",
        description="Get current weather and forecast for a location.",
        parameters={
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "City or location name"},
            },
            "required": ["location"],
        },
        plugin_id="weather",
        intent="weather",
        entity_keys=["location"],
        message_template="what's the weather in {location}",
    ),

    # ── Dictionary ───────────────────────────────────────────────
    _ToolSpec(
        tool_name="define_word",
        description="Look up the definition, pronunciation, and usage of a word.",
        parameters={
            "type": "object",
            "properties": {
                "word": {"type": "string", "description": "The word to define"},
            },
            "required": ["word"],
        },
        plugin_id="dictionary",
        intent="define",
        entity_keys=["word"],
        message_template="define {word}",
    ),

    # ── Wikipedia ────────────────────────────────────────────────
    _ToolSpec(
        tool_name="search_wikipedia",
        description="Search Wikipedia for information about a topic.",
        parameters={
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Topic to look up"},
            },
            "required": ["topic"],
        },
        plugin_id="wikipedia",
        intent="wikipedia",
        entity_keys=["topic"],
        message_template="tell me about {topic}",
    ),

    # ── Unit Conversion ──────────────────────────────────────────
    _ToolSpec(
        tool_name="convert_units",
        description="Convert a value between measurement units (length, weight, volume, temperature).",
        parameters={
            "type": "object",
            "properties": {
                "value":     {"type": "number",  "description": "Numeric value to convert"},
                "from_unit": {"type": "string",  "description": "Source unit (e.g. miles, kg, fahrenheit)"},
                "to_unit":   {"type": "string",  "description": "Target unit (e.g. kilometers, pounds, celsius)"},
            },
            "required": ["value", "from_unit", "to_unit"],
        },
        plugin_id="conversions",
        intent="convert",
        entity_keys=["value", "from_unit", "to_unit"],
        message_template="convert {value} {from_unit} to {to_unit}",
    ),

    # ── Timer ────────────────────────────────────────────────────
    _ToolSpec(
        tool_name="set_timer",
        description="Set a countdown timer for a specific duration.",
        parameters={
            "type": "object",
            "properties": {
                "duration": {"type": "string", "description": "Duration (e.g. '5 minutes', '1 hour 30 minutes')"},
                "label":    {"type": "string", "description": "Optional label (e.g. 'eggs', 'laundry')"},
            },
            "required": ["duration"],
        },
        plugin_id="scheduling",
        intent="set_timer",
        metadata_keys=["duration", "label"],
        message_template="set a timer for {duration}",
    ),

    # ── Alarm ────────────────────────────────────────────────────
    _ToolSpec(
        tool_name="set_alarm",
        description="Set an alarm for a specific time.",
        parameters={
            "type": "object",
            "properties": {
                "time":  {"type": "string", "description": "Alarm time (e.g. '7:30 AM', 'tomorrow at 6')"},
                "label": {"type": "string", "description": "Optional alarm label"},
            },
            "required": ["time"],
        },
        plugin_id="scheduling",
        intent="set_alarm",
        metadata_keys=["time", "label"],
        message_template="set an alarm for {time}",
    ),

    # ── Reminder ─────────────────────────────────────────────────
    _ToolSpec(
        tool_name="set_reminder",
        description="Set a reminder with a message at a specific time.",
        parameters={
            "type": "object",
            "properties": {
                "time":    {"type": "string", "description": "When to remind (e.g. 'in 2 hours', 'tomorrow 3pm')"},
                "message": {"type": "string", "description": "The reminder message"},
            },
            "required": ["time", "message"],
        },
        plugin_id="scheduling",
        intent="set_reminder",
        metadata_keys=["time", "message"],
        message_template="remind me to {message} {time}",
    ),

    # ── Translation ──────────────────────────────────────────────
    _ToolSpec(
        tool_name="translate_text",
        description="Translate text to another language.",
        parameters={
            "type": "object",
            "properties": {
                "text":            {"type": "string", "description": "Text to translate"},
                "target_language": {"type": "string", "description": "Target language (e.g. Spanish, fr, Japanese)"},
                "source_language": {"type": "string", "description": "Source language (default: auto-detect)"},
            },
            "required": ["text", "target_language"],
        },
        plugin_id="translation",
        intent="translate",
        metadata_keys=["text", "target_language", "source_language"],
        message_template="translate '{text}' to {target_language}",
    ),

    # ── News ─────────────────────────────────────────────────────
    _ToolSpec(
        tool_name="get_news",
        description="Get the latest news headlines, optionally for a topic.",
        parameters={
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Optional topic (e.g. technology, sports)"},
            },
            "required": [],
        },
        plugin_id="news",
        intent="news",
        metadata_keys=["topic"],
        message_template="latest news",
    ),

    # ── Stocks ───────────────────────────────────────────────────
    _ToolSpec(
        tool_name="get_stock_price",
        description="Get the current stock price for a ticker symbol.",
        parameters={
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Ticker symbol (e.g. AAPL, GOOGL, TSLA)"},
            },
            "required": ["symbol"],
        },
        plugin_id="stocks",
        intent="stock_price",
        entity_keys=["symbol"],
        metadata_keys=["symbol"],
        message_template="stock price of {symbol}",
    ),

    # ── Cooking ──────────────────────────────────────────────────
    _ToolSpec(
        tool_name="cooking_help",
        description="Get cooking temperatures, times, substitutions, or measurement info.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Cooking question (e.g. 'chicken internal temp')"},
            },
            "required": ["query"],
        },
        plugin_id="cooking",
        intent="cooking",
        entity_keys=["query"],
        message_template="{query}",
    ),

    # ── Movie & TV ───────────────────────────────────────────────
    _ToolSpec(
        tool_name="movie_info",
        description="Get information about a movie or TV show.",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Movie or TV show title"},
            },
            "required": ["title"],
        },
        plugin_id="movie",
        intent="movie_info",
        entity_keys=["title"],
        message_template="tell me about the movie {title}",
    ),

    # ── Sports ───────────────────────────────────────────────────
    _ToolSpec(
        tool_name="get_sports_scores",
        description="Get latest scores and standings for a sport.",
        parameters={
            "type": "object",
            "properties": {
                "sport": {"type": "string", "description": "Sport or league (NFL, NBA, soccer, etc.)"},
                "team":  {"type": "string", "description": "Optional team name to filter"},
            },
            "required": ["sport"],
        },
        plugin_id="sports",
        intent="scores",
        entity_keys=["team"],
        metadata_keys=["sport", "team"],
        message_template="{sport} scores",
    ),

    # ── Stories ──────────────────────────────────────────────────
    _ToolSpec(
        tool_name="tell_story",
        description="Start an interactive story (adventure, fantasy, science, bedtime, mystery).",
        parameters={
            "type": "object",
            "properties": {
                "genre": {"type": "string", "description": "Story genre"},
            },
            "required": [],
        },
        plugin_id="stories",
        intent="story_new",
        metadata_keys=["genre"],
        message_template="tell me a {genre} story",
    ),

    # ── Routines ─────────────────────────────────────────────────
    _ToolSpec(
        tool_name="run_routine",
        description="Execute a smart-home routine by name.",
        parameters={
            "type": "object",
            "properties": {
                "routine_name": {"type": "string", "description": "Routine name (e.g. bedtime, good morning)"},
            },
            "required": ["routine_name"],
        },
        plugin_id="routines",
        intent="run_routine",
        entity_keys=["routine_name"],
        metadata_keys=["routine_name"],
        message_template="run the {routine_name} routine",
    ),

    # ── Home-Assistant device control (via routines plugin) ──────
    _ToolSpec(
        tool_name="control_device",
        description="Control a smart-home device (lights, switches, etc.).",
        parameters={
            "type": "object",
            "properties": {
                "entity_id":  {"type": "string",  "description": "HA entity ID (e.g. light.living_room)"},
                "action":     {"type": "string",  "description": "Action: turn_on, turn_off, toggle"},
                "brightness": {"type": "integer", "description": "Brightness 0-255 (lights only)"},
            },
            "required": ["entity_id", "action"],
        },
        plugin_id="routines",
        intent="ha_control",
        metadata_keys=["entity_id", "action", "brightness"],
        message_template="turn {action} {entity_id}",
    ),

    # ── Intercom ─────────────────────────────────────────────────
    _ToolSpec(
        tool_name="intercom_announce",
        description="Announce a message to a room or broadcast to all rooms.",
        parameters={
            "type": "object",
            "properties": {
                "message":     {"type": "string", "description": "Message to announce"},
                "target_room": {"type": "string", "description": "Room name (omit for broadcast)"},
            },
            "required": ["message"],
        },
        plugin_id="intercom",
        intent="intercom_announce",
        metadata_keys=["message", "target_room"],
        message_template="announce {message}",
    ),

    # ── Sound Library ────────────────────────────────────────────
    _ToolSpec(
        tool_name="play_sound",
        description="Play an animal or nature sound effect.",
        parameters={
            "type": "object",
            "properties": {
                "sound_name": {"type": "string", "description": "Sound name (e.g. cat, thunder, ocean)"},
            },
            "required": ["sound_name"],
        },
        plugin_id="sound_library",
        intent="play_sound",
        entity_keys=["sound_name"],
        message_template="play a {sound_name} sound",
    ),

    # ── Media — play ─────────────────────────────────────────────
    _ToolSpec(
        tool_name="play_media",
        description="Play music, podcast, or audiobook by search query.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to play (song, artist, album, playlist, …)"},
            },
            "required": ["query"],
        },
        plugin_id="media",
        intent="play",
        entity_keys=["query"],
        metadata_keys=["query"],
        message_template="play {query}",
    ),

    # ── Media — playback control ─────────────────────────────────
    _ToolSpec(
        tool_name="control_media",
        description="Control media playback (pause, resume, skip, etc.).",
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "Playback action",
                    "enum": ["pause", "resume", "skip", "next", "previous", "stop"],
                },
            },
            "required": ["action"],
        },
        plugin_id="media",
        intent="{action}",
        metadata_keys=["action"],
        message_template="{action}",
    ),

    # ── Media — volume ───────────────────────────────────────────
    _ToolSpec(
        tool_name="set_volume",
        description="Set media playback volume (0-100).",
        parameters={
            "type": "object",
            "properties": {
                "level": {"type": "integer", "description": "Volume level 0-100"},
            },
            "required": ["level"],
        },
        plugin_id="media",
        intent="volume_set",
        metadata_keys=["level"],
        message_template="volume {level}",
    ),

    # ── STEM Games ───────────────────────────────────────────────
    _ToolSpec(
        tool_name="start_game",
        description="Start a STEM learning game (number_quest, science_safari, word_wizard).",
        parameters={
            "type": "object",
            "properties": {
                "game_type": {
                    "type": "string",
                    "description": "Which game to start",
                    "enum": ["number_quest", "science_safari", "word_wizard"],
                },
            },
            "required": ["game_type"],
        },
        plugin_id="stem_games",
        intent="start_game",
        metadata_keys=["game_type"],
        message_template="let's play {game_type}",
    ),

    # ── Daily Briefing ───────────────────────────────────────────
    _ToolSpec(
        tool_name="daily_briefing",
        description="Generate a personalized daily briefing (weather, calendar, news).",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        plugin_id="daily_briefing",
        intent="briefing",
        message_template="give me my daily briefing",
    ),

    # ── Code Sandbox (not a CortexPlugin — special-cased) ───────
    _ToolSpec(
        tool_name="execute_code",
        description="Execute Python code in a sandboxed environment.",
        parameters={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python code to execute"},
            },
            "required": ["code"],
        },
        plugin_id="_code_sandbox",
        intent="execute",
    ),
]
# fmt: on


def get_tool_specs() -> list[_ToolSpec]:
    """Return a copy of the built-in tool specs (for testing / introspection)."""
    return list(_TOOL_SPECS)


# ── Message / match builders ──────────────────────────────────────


def _build_message(spec: _ToolSpec, arguments: dict[str, Any]) -> str:
    """Reconstruct a natural-language message from tool arguments."""
    safe: dict[str, str] = defaultdict(str, {k: str(v) for k, v in arguments.items()})
    safe.setdefault(
        "_raw",
        " ".join(str(v) for v in arguments.values()) if arguments else spec.tool_name,
    )
    try:
        msg = spec.message_template.format_map(safe)
    except (KeyError, ValueError, IndexError):
        msg = safe.get("_raw", spec.tool_name)
    # Collapse whitespace left by empty optional placeholders
    return " ".join(msg.split())


def _build_command_match(spec: _ToolSpec, arguments: dict[str, Any]) -> CommandMatch:
    """Build a ``CommandMatch`` directly from the tool spec and arguments."""
    entities = [str(arguments[k]) for k in spec.entity_keys if k in arguments]

    metadata: dict[str, Any] = {k: arguments[k] for k in spec.metadata_keys if k in arguments}

    intent = spec.intent
    if "{" in intent:
        try:
            intent = intent.format_map(defaultdict(str, {k: str(v) for k, v in arguments.items()}))
        except (KeyError, ValueError):
            pass

    metadata["_tool_call"] = True
    metadata["_tool_name"] = spec.tool_name

    return CommandMatch(
        matched=True,
        intent=intent,
        entities=entities,
        confidence=1.0,
        metadata=metadata,
    )


# ── Handler factories ─────────────────────────────────────────────


def _make_plugin_handler(plugin: CortexPlugin, spec: _ToolSpec) -> Callable:
    """Create an async handler that bridges tool arguments → plugin.handle()."""

    async def handler(arguments: dict[str, Any], context: dict[str, Any]) -> ToolResult:
        message = _build_message(spec, arguments)

        # Try the plugin's own match() first — it knows how to parse best
        try:
            match = await plugin.match(message, context)
        except Exception:
            match = CommandMatch(matched=False)

        if not match.matched:
            match = _build_command_match(spec, arguments)

        try:
            result = await plugin.handle(message, match, context)
            return ToolResult(
                success=result.success,
                output=result.response,
                metadata=result.metadata,
            )
        except Exception as exc:
            logger.exception("Plugin %s.handle() failed: %s", plugin.plugin_id, exc)
            return ToolResult(success=False, output=f"Plugin error: {exc}")

    return handler


def _make_code_sandbox_handler() -> Callable:
    """Create a handler for the built-in code sandbox tool."""

    async def handler(arguments: dict[str, Any], _context: dict[str, Any]) -> ToolResult:
        try:
            from cortex.tools.code_sandbox import CodeSandbox
        except ImportError:
            return ToolResult(success=False, output="Code sandbox not available")

        sandbox = CodeSandbox()
        code = arguments.get("code", "")
        result = await sandbox.execute(code)

        parts: list[str] = []
        if result.stdout:
            parts.append(result.stdout)
        if result.stderr:
            parts.append(f"STDERR: {result.stderr}")
        if result.return_value is not None:
            parts.append(f"Return value: {result.return_value}")

        return ToolResult(
            success=result.success,
            output="\n".join(parts) if parts else "(no output)",
            metadata={"elapsed": result.elapsed, "timed_out": result.timed_out},
        )

    return handler


# ── Public registration helpers ───────────────────────────────────


def adapt_plugin(plugin: CortexPlugin, tool_registry: ToolRegistry) -> int:
    """Convert a single plugin into tool definitions and register them.

    Returns the number of tools registered.
    """
    count = 0
    for spec in _TOOL_SPECS:
        if spec.plugin_id != plugin.plugin_id:
            continue
        tool_registry.register(
            name=spec.tool_name,
            description=spec.description,
            parameters=spec.parameters,
            handler=_make_plugin_handler(plugin, spec),
        )
        count += 1
    return count


def register_all_plugins(tool_registry: ToolRegistry, plugin_registry: Any) -> int:
    """Register all plugins from a ``PluginRegistry`` as callable tools.

    Also registers the code sandbox if available.
    Returns the total number of tools registered.
    """
    total = 0

    for plugin in plugin_registry.list_plugins():
        n = adapt_plugin(plugin, tool_registry)
        if n:
            logger.info("Plugin %s → %d tool(s)", plugin.plugin_id, n)
        total += n

    # Code sandbox (standalone tool, not a CortexPlugin)
    sandbox_spec = next((s for s in _TOOL_SPECS if s.plugin_id == "_code_sandbox"), None)
    if sandbox_spec is not None:
        try:
            from cortex.tools.code_sandbox import CodeSandbox  # noqa: F401

            tool_registry.register(
                name=sandbox_spec.tool_name,
                description=sandbox_spec.description,
                parameters=sandbox_spec.parameters,
                handler=_make_code_sandbox_handler(),
            )
            total += 1
            logger.info("Code sandbox → 1 tool")
        except ImportError:
            logger.debug("Code sandbox unavailable, skipping")

    logger.info("Total tools registered from plugins: %d", total)
    return total
