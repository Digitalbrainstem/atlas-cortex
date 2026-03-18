# Atlas Cortex CLI Tool System & Module APIs - Complete Reference

## 1. CLI Tool System (`cortex/cli/tools/`)

### 1.1 Core Classes (`cortex/cli/tools/__init__.py`)

#### `ToolResult` (dataclass)
```python
@dataclass
class ToolResult:
    success: bool              # True if execution succeeded
    output: str                # Command output or result
    error: str = ""            # Error message (if any)
    metadata: dict[str, Any] = field(default_factory=dict)  # Additional info
```

#### `AgentTool` (abstract base class)
```python
class AgentTool(abc.ABC):
    tool_id: str = ""                                    # Unique tool identifier
    description: str = ""                                # Human-readable description
    parameters_schema: dict[str, Any] = {}              # OpenAI-compatible schema
    requires_confirmation: bool = False                  # If True, needs user approval

    @abc.abstractmethod
    async def execute(
        self, 
        params: dict[str, Any], 
        context: dict[str, Any] | None = None
    ) -> ToolResult:
        """Execute the tool with given parameters."""
        ...

    def to_function_schema(self) -> dict[str, Any]:
        """Convert to OpenAI-compatible function calling schema."""
        # Returns {"type": "function", "function": {...}}
```

**Context dict keys (optional):**
- `"cwd"` — current working directory for file/command operations
- `"variables"` — variables dict for templating

#### `ToolRegistry`
```python
class ToolRegistry:
    def __init__(self) -> None: ...
    
    def register(self, tool: AgentTool) -> None:
        """Register a tool. Raises ValueError if tool has no tool_id."""
    
    def get(self, tool_id: str) -> AgentTool | None:
        """Retrieve a tool by ID."""
    
    def list_tools(self) -> list[AgentTool]:
        """List all registered tools."""
    
    def get_function_schemas(self) -> list[dict[str, Any]]:
        """Get OpenAI-compatible function schemas for all tools."""
    
    async def execute(
        self,
        tool_id: str,
        params: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Execute a tool by ID."""

def get_default_registry() -> ToolRegistry:
    """Create a registry with all built-in tools registered."""
    # Registers: FileReadTool, FileWriteTool, FileEditTool, FileListTool,
    #           ShellExecTool, GrepTool, GlobTool, GitTool
```

---

### 1.2 File Tools (`cortex/cli/tools/files.py`)

#### `FileReadTool`
- **tool_id:** `"file_read"`
- **requires_confirmation:** False
- **Parameters:**
  - `path` (str, required) — File path
  - `start_line` (int, optional) — 1-indexed start line
  - `end_line` (int, optional) — 1-indexed end line (inclusive)
- **Returns:** ToolResult with line-numbered output, metadata with `path` and `total_lines`

#### `FileWriteTool`
- **tool_id:** `"file_write"`
- **requires_confirmation:** True
- **Parameters:**
  - `path` (str, required) — Path to new file
  - `content` (str, required) — File content
- **Returns:** ToolResult; fails if file exists (use `file_edit` instead)
- **Behavior:** Creates parent directories automatically

#### `FileEditTool`
- **tool_id:** `"file_edit"`
- **requires_confirmation:** False
- **Parameters:**
  - `path` (str, required) — Path to existing file
  - `old_str` (str, required) — Exact text to find
  - `new_str` (str, required) — Replacement text
- **Returns:** ToolResult with context snippet around the edit
- **Validation:** 
  - Fails if `old_str` not found
  - Fails if `old_str` matches >1 location (must be unambiguous)

#### `FileListTool`
- **tool_id:** `"file_list"`
- **requires_confirmation:** False
- **Parameters:**
  - `path` (str, optional, default ".") — Directory to list
  - `max_depth` (int, optional, default 2) — Max nesting depth
- **Returns:** ToolResult with tree-formatted listing
- **Behavior:** Skips hidden files (starting with `.`)

---

### 1.3 Shell Tools (`cortex/cli/tools/shell.py`)

#### `ShellExecTool`
- **tool_id:** `"shell_exec"`
- **requires_confirmation:** True
- **Parameters:**
  - `command` (str, required) — Shell command to execute
  - `timeout` (int, optional, default 30) — Timeout in seconds
  - `cwd` (str, optional) — Working directory
- **Returns:** ToolResult with stdout, stderr, and returncode in metadata
- **Safety:** Blocks dangerous patterns (rm -rf /, mkfs, dd, fork bombs, etc.)

#### `GrepTool`
- **tool_id:** `"grep"`
- **requires_confirmation:** False
- **Parameters:**
  - `pattern` (str, required) — Regex pattern to search for
  - `path` (str, optional, default ".") — File/directory to search
  - `glob_filter` (str, optional) — Glob to filter files (e.g., "*.py")
  - `case_insensitive` (bool, optional) — Case-insensitive search
  - `context_lines` (int, optional) — Lines of context around matches
  - `max_results` (int, optional) — Max matching lines to return
- **Returns:** ToolResult with matching lines, no-matches is success
- **Implementation:** Uses `rg` (ripgrep) if available, falls back to grep

#### `GlobTool`
- **tool_id:** `"glob"`
- **requires_confirmation:** False
- **Parameters:**
  - `pattern` (str, required) — Glob pattern (e.g., "**/*.py")
  - `path` (str, optional, default ".") — Base directory
- **Returns:** ToolResult with sorted paths, one per line
- **Metadata:** Includes `count` of matches

---

### 1.4 Git Tool (`cortex/cli/tools/git.py`)

#### `GitTool`
- **tool_id:** `"git"`
- **requires_confirmation:** False (checked per-operation)
- **Parameters:**
  - `operation` (str, required, enum) — Git operation
  - `args` (str, optional) — Additional arguments (space-separated)
- **Allowed operations:** `status`, `diff`, `log`, `blame`, `add`, `commit`, `branch`, `show`, `stash`
- **Mutating ops** (logged as warning): `add`, `commit`, `stash`
- **Returns:** ToolResult with git output
- **Behavior:** Uses `git --no-pager` to avoid interactive pagers

---

## 2. Scheduling Engines (`cortex/scheduling/`)

### 2.1 Imports
```python
from cortex.scheduling import (
    TimerEngine,
    AlarmEngine,
    ReminderEngine,
    parse_time,      # Function for NLP time parsing
    ParsedTime,      # Result type
)
```

### 2.2 `TimerEngine` (`cortex/scheduling/timers.py`)

```python
class TimerEngine:
    async def start_timer(
        self,
        duration_seconds: int,
        label: str = "",
        user_id: str = "",
        room: str = "",
    ) -> int:
        """Start a timer. Returns timer_id."""
    
    async def pause_timer(self, timer_id: int) -> bool:
        """Pause a running timer. Returns success."""
    
    async def resume_timer(self, timer_id: int) -> bool:
        """Resume a paused timer. Returns success."""
    
    async def cancel_timer(self, timer_id: int) -> bool:
        """Cancel a timer. Returns success."""
    
    async def list_timers(self, user_id: str = "") -> list[dict[str, Any]]:
        """List timers, optionally filtered by user_id."""
    
    async def get_timer(self, timer_id: int) -> dict[str, Any] | None:
        """Get timer details. Computes live remaining_seconds for running timers."""
    
    def on_expire(self, callback: Callable[..., Any]) -> None:
        """Register callback: callback(timer_id, label, user_id, room)."""
    
    async def restore_from_db(self) -> None:
        """Restore running/paused timers from DB on startup."""
```

**Timer dict fields:**
- `id`, `label`, `duration_seconds`, `remaining_seconds`, `state` (running|paused|finished|cancelled)
- `user_id`, `room`, `expires_at` (ISO string)

---

### 2.3 `AlarmEngine` (`cortex/scheduling/alarms.py`)

```python
class AlarmEngine:
    async def create_alarm(
        self,
        cron_expression: str,      # "0 7 * * 1-5" (5-field cron)
        label: str = "",
        sound: str = "default",
        tts_message: str = "",
        user_id: str = "",
        room: str = "",
    ) -> int:
        """Create a cron-based alarm. Returns alarm_id."""
    
    async def delete_alarm(self, alarm_id: int) -> bool:
        """Delete an alarm."""
    
    async def enable_alarm(self, alarm_id: int) -> bool:
        """Enable an alarm (re-calculates next_fire)."""
    
    async def disable_alarm(self, alarm_id: int) -> bool:
        """Disable an alarm."""
    
    async def list_alarms(self, user_id: str = "") -> list[dict[str, Any]]:
        """List alarms, optionally filtered by user_id."""
    
    async def snooze_alarm(self, alarm_id: int, minutes: int = 5) -> bool:
        """Snooze an alarm by N minutes."""
    
    def on_trigger(self, callback: Callable[..., Any]) -> None:
        """Register callback: callback(alarm_id, label, sound, tts_message, user_id, room)."""
    
    async def start(self) -> None:
        """Start the background alarm checker loop."""
    
    async def stop(self) -> None:
        """Stop the background alarm checker."""

# Helper functions
def cron_matches(expression: str, dt: datetime) -> bool:
    """Check if datetime matches 5-field cron expression."""

def next_cron_time(
    expression: str, 
    after: datetime | None = None
) -> datetime | None:
    """Find next datetime matching cron expression (up to 366 days ahead)."""
```

**Alarm dict fields:**
- `id`, `label`, `cron_expression`, `sound`, `tts_message`
- `user_id`, `room`, `enabled` (0|1)
- `next_fire` (ISO string), `last_fired` (ISO string)

---

### 2.4 `ReminderEngine` (`cortex/scheduling/reminders.py`)

```python
class ReminderEngine:
    async def create_reminder(
        self,
        message: str,
        trigger_at: datetime | None = None,        # One-time reminder
        cron_expression: str | None = None,        # Recurring reminder
        event_condition: str | None = None,        # Event-based
        user_id: str = "",
        room: str = "",
    ) -> int:
        """Create a reminder. Returns reminder_id.
        
        Trigger type is auto-detected: time|recurring|event based on parameters.
        """
    
    async def delete_reminder(self, reminder_id: int) -> bool:
        """Delete a reminder."""
    
    async def list_reminders(
        self,
        user_id: str = "",
        include_fired: bool = False,
    ) -> list[dict[str, Any]]:
        """List reminders."""
    
    async def check_event(
        self,
        event_name: str,
        context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Fire event-based reminders matching event_name. Returns fired reminders."""
    
    def on_trigger(self, callback: Callable[..., Any]) -> None:
        """Register callback: callback(reminder_id, message, user_id, room)."""
    
    async def start(self) -> None:
        """Start the background reminder checker loop."""
    
    async def stop(self) -> None:
        """Stop the background reminder checker."""
```

**Reminder dict fields:**
- `id`, `message`, `trigger_type` (time|recurring|event)
- `trigger_at` (ISO string, for time-based)
- `cron_expression` (for recurring)
- `event_condition` (for event-based)
- `user_id`, `room`, `fired` (0|1)

---

## 3. Routines Engine (`cortex/routines/`)

### 3.1 Imports
```python
from cortex.routines import (
    RoutineEngine,
    TriggerManager,
    ActionExecutor,
    ActionResult,
    TTSAnnounceAction,
    HAServiceAction,
    DelayAction,
    ConditionAction,
    SetVariableAction,
    TEMPLATES,
    instantiate_template,
)
```

### 3.2 `RoutineEngine` (`cortex/routines/engine.py`)

```python
class RoutineEngine:
    # ── CRUD ──
    async def create_routine(
        self,
        name: str,
        description: str = "",
        user_id: str = "",
        template_id: str = "",
    ) -> int:
        """Create routine. Returns routine_id."""
    
    async def delete_routine(self, routine_id: int) -> bool: ...
    async def enable_routine(self, routine_id: int) -> bool: ...
    async def disable_routine(self, routine_id: int) -> bool: ...
    
    async def list_routines(self, user_id: str = "") -> list[dict]:
        """List routines, optionally filtered by user_id."""
    
    async def get_routine(self, routine_id: int) -> dict | None:
        """Get routine with steps and triggers.
        
        Returns dict with:
          id, name, description, user_id, enabled, template_id, ...
          steps: [dict with id, routine_id, step_order, action_type, action_config, condition, on_error]
          triggers: [dict with id, routine_id, trigger_type, trigger_config]
        """
    
    # ── Step management ──
    async def add_step(
        self,
        routine_id: int,
        action_type: str,
        action_config: dict,
        step_order: int | None = None,
        condition: str = "",
        on_error: str = "continue",  # "continue" | "stop" | "skip_rest"
    ) -> int:
        """Add step to routine. Returns step_id."""
    
    async def remove_step(self, step_id: int) -> bool: ...
    
    async def reorder_steps(self, routine_id: int, step_ids: list[int]) -> bool:
        """Reorder steps by providing desired order of IDs."""
    
    # ── Trigger management ──
    async def add_trigger(
        self,
        routine_id: int,
        trigger_type: str,  # "voice_phrase" | "ha_event" | ...
        trigger_config: dict,
    ) -> int:
        """Add trigger. Returns trigger_id."""
    
    async def remove_trigger(self, trigger_id: int) -> bool: ...
    
    # ── Execution ──
    async def run_routine(
        self, 
        routine_id: int, 
        context: dict | None = None
    ) -> int:
        """Execute routine immediately. Returns run_id.
        
        Context dict keys:
          variables: dict of template variables ({{var}} substitution)
        """
    
    async def cancel_run(self, run_id: int) -> bool: ...
    
    # ── Trigger matching ──
    async def match_voice_trigger(self, phrase: str) -> int | None:
        """Match voice phrase to routine. Returns routine_id or None.
        
        Uses fuzzy matching (0.75 similarity threshold).
        """
    
    async def match_ha_event(
        self, 
        entity_id: str, 
        new_state: str, 
        old_state: str
    ) -> list[int]:
        """Match HA state change to routine triggers. Returns list of routine_ids."""
```

**Routine dict:**
- `id`, `name`, `description`, `user_id`, `enabled` (0|1)
- `template_id`, `created_at`, `updated_at`, `last_run`, `run_count`
- `steps`: list of step dicts
- `triggers`: list of trigger dicts

**Step dict:**
- `id`, `routine_id`, `step_order`, `action_type`, `action_config` (JSON)
- `condition`, `on_error`

**Run dict:**
- `id`, `routine_id`, `started_at`, `finished_at`
- `status` (running|completed|failed|cancelled)
- `steps_completed`, `error_message`

---

## 4. Notifications System (`cortex/notifications/`)

### 4.1 Core Classes (`cortex/notifications/channels.py`)

```python
@dataclass
class Notification:
    level: str                                  # "info", "warning", "critical"
    title: str
    message: str
    source: str = ""                           # e.g., "safety", "system", "learning"
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=...)

class NotificationChannel(abc.ABC):
    @abc.abstractmethod
    async def send(self, notification: Notification) -> bool:
        """Deliver notification. Return True on success."""

class LogChannel(NotificationChannel):
    """Logs notifications to database and Python logger."""

# Global registry
def register_channel(channel: NotificationChannel) -> None:
    """Add a notification channel to the registry."""

async def send_notification(
    level: str,
    title: str,
    message: str,
    source: str = "",
    metadata: dict[str, Any] | None = None,
) -> int:
    """Send notification to all registered channels. Returns count of successful deliveries."""
```

### 4.2 Satellite Channel (`cortex/notifications/satellite.py`)

```python
class SatelliteChannel(NotificationChannel):
    def set_tts_callback(
        self, 
        callback: Callable[[str, str], Coroutine[Any, Any, bool]]
    ) -> None:
        """Set TTS delivery: async callback(satellite_id, text) -> bool."""
    
    async def send(self, notification: Notification) -> bool:
        """Route notification via TTS to satellites.
        
        Resolution strategy:
          1. metadata['room'] → satellites in that room
          2. metadata['user_id'] → user's last known room
          3. All connected satellites
          4. Fall back to log channel
        """

# Convenience functions
async def notify_timer_expired(
    timer_label: str,
    room: str = "",
    user_id: str = "",
) -> None: ...

async def notify_alarm_triggered(
    alarm_label: str,
    tts_message: str = "",
    room: str = "",
    user_id: str = "",
) -> None: ...

async def notify_reminder_fired(
    reminder_message: str,
    room: str = "",
    user_id: str = "",
) -> None: ...

def register_satellite_channel() -> SatelliteChannel:
    """Create and register the satellite channel."""

def wire_scheduling_callbacks(
    timer_engine: TimerEngine,
    alarm_engine: AlarmEngine,
    reminder_engine: ReminderEngine,
) -> None:
    """Wire scheduling engine callbacks to notification system."""
```

---

## 5. Memory System (`cortex/memory/`)

### 5.1 Hot Path (`cortex/memory/hot.py`)

```python
@dataclass
class MemoryHit:
    doc_id: str
    user_id: str
    text: str
    score: float
    source: str  # "fts5", "vector", "rrf"

def hot_query(
    query: str,
    user_id: str,
    conn: Any,
    top_k: int = 8,
    embedding: list[float] | None = None,
    vector_store: VectorStore | None = None,
) -> list[MemoryHit]:
    """Retrieve memories using BM25 (FTS5).
    
    When embedding + vector_store provided:
      - Also performs vector search
      - Fuses results via Reciprocal Rank Fusion (RRF)
    
    Returns up to top_k results, sorted by score.
    """

def format_memory_context(
    hits: list[MemoryHit], 
    max_chars: int = 1000
) -> str:
    """Format memory hits as context string for LLM prompt."""
```

### 5.2 Controller (`cortex/memory/controller.py`)

```python
class MemorySystem:
    def __init__(
        self,
        conn: Any,
        data_dir: str = "./data",
        provider: Any = None,
    ) -> None: ...
    
    async def recall(
        self,
        query: str,
        user_id: str,
        top_k: int = 8,
    ) -> list[MemoryHit]:
        """Retrieve memories (HOT path, <50ms target)."""
    
    async def remember(
        self,
        text: str,
        user_id: str,
        tags: list[str] | None = None,
    ) -> None:
        """Store a memory (COLD path, non-blocking, async write)."""
    
    async def start(self) -> None: ...
    async def stop(self) -> None: ...

# Module-level singleton
def get_memory_system() -> MemorySystem | None:
    """Return the initialized MemorySystem singleton, or None."""

def set_memory_system(ms: MemorySystem) -> None:
    """Set the module-level MemorySystem singleton."""
```

---

## 6. LLM Providers (`cortex/providers/`)

### 6.1 Base Provider (`cortex/providers/base.py`)

```python
class LLMProvider(abc.ABC):
    @abc.abstractmethod
    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        stream: bool = True,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[str, None] | dict[str, Any]:
        """Send chat completion request.
        
        Returns:
          - AsyncGenerator[str, None] when stream=True (chunks)
          - dict[str, Any] when stream=False (full response)
        """
    
    @abc.abstractmethod
    async def embed(
        self, 
        text: str, 
        model: str | None = None
    ) -> list[float]:
        """Generate embeddings for text. Returns embedding vector."""
    
    @abc.abstractmethod
    async def list_models(self) -> list[dict[str, Any]]:
        """List available models.
        
        Each entry contains at minimum:
          name (str), size_bytes (int)
        """
    
    @abc.abstractmethod
    async def health(self) -> bool:
        """Check if backend is reachable."""
    
    def supports_embeddings(self) -> bool:
        """Whether this provider can generate embeddings."""
        return False
    
    def supports_thinking(self) -> bool:
        """Whether models support extended thinking."""
        return False
```

### 6.2 Provider Factory (`cortex/providers/__init__.py`)

```python
def get_provider(
    provider_name: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    **kwargs: Any,
) -> LLMProvider:
    """Get configured LLMProvider.
    
    If provider_name is None, reads LLM_PROVIDER from environment (default: "ollama").
    
    Environment variables:
      LLM_PROVIDER — provider name ("ollama", "openai_compatible")
      LLM_URL — base URL (overridable by base_url parameter)
      LLM_API_KEY — API key (overridable by api_key parameter)
    
    Available providers:
      - "ollama"
      - "openai_compatible"
    """

# Available providers
class OllamaProvider(LLMProvider): ...
class OpenAICompatibleProvider(LLMProvider): ...
```

---

## 7. Home Assistant Integration (`cortex/integrations/ha/`)

### 7.1 HA Client (`cortex/integrations/ha/client.py`)

```python
class HAClientError(Exception): ...
class HAConnectionError(HAClientError): ...
class HAAuthError(HAClientError): ...

class HAClient:
    def __init__(
        self, 
        base_url: str, 
        token: str, 
        timeout: float = 10.0
    ) -> None: ...
    
    async def aclose(self) -> None:
        """Close HTTP client and free resources."""
    
    async def __aenter__(self) -> "HAClient": ...
    async def __aexit__(self, *_: object) -> None: ...
    
    async def health(self) -> bool:
        """Check if HA API is reachable."""
    
    async def get_states(self) -> list[dict]:
        """Return all entity states (GET /api/states)."""
    
    async def get_areas(self) -> list[dict]:
        """Return all area registry entries."""
    
    async def call_service(
        self, 
        domain: str, 
        service: str, 
        data: dict
    ) -> dict:
        """Call a HA service (POST /api/services/{domain}/{service})."""
```

---

## 8. Test Patterns (`tests/test_cli_tools.py`)

The test file demonstrates:

```python
# ToolRegistry tests
reg = ToolRegistry()
tool = FileReadTool()
reg.register(tool)
assert reg.get("file_read") is tool
schemas = reg.get_function_schemas()

# File tool tests
result = await FileReadTool().execute(
    {"path": str(f), "start_line": 2, "end_line": 4}
)
assert result.success
assert "2. b" in result.output

# Shell tool tests
result = await ShellExecTool().execute(
    {"command": "echo hello", "timeout": 30}
)
assert result.success

# Grep tests
result = await GrepTool().execute(
    {"pattern": "hello", "path": str(tmp_path), "glob_filter": "*.py"}
)

# Glob tests
result = await GlobTool().execute(
    {"pattern": "**/*.py", "path": str(tmp_path)}
)

# Git tests (requires `git init` setup)
result = await GitTool().execute(
    {"operation": "status"}, 
    context={"cwd": str(tmp_path)}
)

# Default registry
reg = get_default_registry()
expected_tools = {
    "file_read", "file_write", "file_edit", "file_list",
    "shell_exec", "grep", "glob", "git"
}
```

---

## Summary: Creating New Tools

To wrap Atlas modules as tools, extend `AgentTool`:

```python
from cortex.cli.tools import AgentTool, ToolResult
from typing import Any

class YourModuleTool(AgentTool):
    tool_id = "your_module_function"  # Unique ID
    description = "Human-readable description"
    parameters_schema = {
        "type": "object",
        "properties": {
            "param1": {"type": "string", "description": "..."},
            "param2": {"type": "integer", "description": "..."},
        },
        "required": ["param1"],
    }
    requires_confirmation = True  # If destructive

    async def execute(
        self, 
        params: dict[str, Any], 
        context: dict[str, Any] | None = None
    ) -> ToolResult:
        try:
            result = await some_atlas_module_call(params["param1"])
            return ToolResult(
                success=True, 
                output=str(result),
                metadata={"key": "value"}
            )
        except Exception as exc:
            return ToolResult(
                success=False, 
                output="", 
                error=str(exc)
            )

# Register it
registry = ToolRegistry()
registry.register(YourModuleTool())
```

