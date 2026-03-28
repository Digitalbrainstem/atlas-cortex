# Atlas CLI Guide

Atlas Cortex CLI is a persistent, unified development platform for
interacting with the Atlas AI from your terminal.

## Installation

```bash
# From the atlas-cortex repo
pip install -e ".[cli]"

# Or install the CLI dependencies directly
pip install rich prompt_toolkit textual
```

This installs the `atlas` command globally.

## Quick Start

```bash
# Start an interactive REPL (default)
atlas

# Resume the last session
atlas --session <id>

# One-shot question
atlas ask "what time is it?"

# Autonomous agent
atlas agent "add auth to the API"

# List saved sessions
atlas sessions

# System status
atlas status
```

## Sessions

Every conversation is a **session** — a JSON file in `~/.atlas/sessions/`
that survives terminal disconnects, sleep, and reboots.

### Automatic persistence

Sessions auto-save after every exchange. Closing the terminal does
**not** lose your conversation.

### Managing sessions

| Command                      | Description                  |
|------------------------------|------------------------------|
| `atlas sessions`             | List all sessions            |
| `atlas --session <id>`       | Resume a specific session    |
| `atlas --new`                | Force a new session          |
| `/session list`              | List sessions inside REPL    |
| `/session new [name]`        | Start a new session          |
| `/session resume [id]`       | Resume a session             |
| `/session name <name>`       | Rename the current session   |
| `/session info`              | Show current session details |

### File format

```json
{
  "id": "20250716-143022-001-chat",
  "name": "auth-refactor",
  "mode": "chat",
  "created_at": 1752678622.0,
  "updated_at": 1752679000.0,
  "metadata": {},
  "messages": [
    {"role": "user", "content": "...", "timestamp": 1752678622.0},
    {"role": "assistant", "content": "...", "timestamp": 1752678625.0}
  ]
}
```

## Slash Commands

Inside the REPL, type `/` to access commands:

### General
| Command         | Description                         |
|-----------------|-------------------------------------|
| `/help`         | Show all available commands         |
| `/quit`, `/exit`| Exit CLI (session auto-saved)       |
| `/clear`        | Clear screen (session preserved)    |
| `/status`       | Show system status                  |
| `/history`      | Show conversation history           |
| `/context`      | Show context window size (tokens)   |

### Sessions
| Command                   | Description              |
|---------------------------|--------------------------|
| `/session list`           | List all sessions        |
| `/session new [name]`     | Start a new session      |
| `/session resume [id]`    | Resume a session         |
| `/session name <name>`    | Rename current session   |
| `/session info`           | Current session details  |

### Memory
| Command                 | Description                |
|-------------------------|----------------------------|
| `/memory search <query>`| Search persistent memory   |
| `/memory recall`        | Show recall for last msg   |
| `/memory forget <id>`   | Remove a memory entry      |

### Model
| Command       | Description                        |
|---------------|------------------------------------|
| `/model`      | Show current model                 |
| `/model fast` | Switch to fast model               |
| `/model think`| Switch to thinking model           |
| `/model list` | List available models              |
| `/model auto` | Auto-select based on query         |

### Tools
| Command                    | Description           |
|----------------------------|-----------------------|
| `/tools`                   | List available tools  |
| `/tools run <tool> [args]` | Run a tool directly   |

### Input
| Command         | Description                           |
|-----------------|---------------------------------------|
| `/code <lang>`  | Enter multi-line code block mode      |
| `/file <path>`  | Read file and include in context      |
| `/diff`         | Include git diff in context           |
| `/stream on|off`| Toggle streaming output               |
| `"""`           | Start/end a multi-line input block    |

### Background Tasks
| Command              | Description              |
|----------------------|--------------------------|
| `/bg list`           | List background tasks    |
| `/bg cancel <id>`    | Cancel a running task    |

## VS Code Integration

Atlas provides a VS Code bridge via a Unix socket JSON-RPC server.

### Setup

1. Start the bridge:
   ```bash
   atlas vscode-bridge
   ```

2. Install the extension from `cortex/cli/vscode-extension/`:
   ```bash
   cd cortex/cli/vscode-extension
   # Package with vsce, or load as an unpacked extension in VS Code
   ```

3. In VS Code, use the command palette:
   - **Atlas: Chat** — ask a question
   - **Atlas: Explain Selection** — explain highlighted code
   - **Atlas: Fix Selection** — suggest a fix
   - **Atlas: Generate Tests** — generate tests for selection
   - **Atlas: Show Status** — check bridge status

### Configuration

In VS Code settings:
```json
{
  "atlas.socketPath": "/home/user/.atlas/vscode.sock"
}
```

## Configuration

Atlas CLI reads `~/.atlas/config.yaml` on startup:

```yaml
# Atlas CLI configuration
model:
  fast: qwen2.5:14b        # Quick responses
  thinking: qwen3:30b-a3b  # Complex reasoning
  provider: ollama

server:
  url: http://localhost:5100
  ollama_url: http://localhost:11434

memory:
  auto_recall: true       # Search memory before each LLM call
  auto_archive: true      # Archive old turns when context grows
  max_recall_results: 5

cli:
  streaming: true          # Token-by-token output
  syntax_highlight: true   # Code highlighting
  max_context_messages: 50
  prompt_style: "atlas"    # "atlas", "minimal", or "verbose"

tools:
  enabled: true
  auto_approve: false      # Ask before shell commands
```

Environment variables override config values:
- `MODEL_FAST` → `model.fast`
- `MODEL_THINKING` → `model.thinking`
- `OLLAMA_BASE_URL` → `server.ollama_url`

## Multi-Model Routing

The CLI automatically picks the right model for each query:

| Query type                        | Model used |
|-----------------------------------|------------|
| Quick questions, translations     | Fast       |
| Code generation, refactoring      | Thinking   |
| Analysis, step-by-step reasoning  | Thinking   |
| Tool use                          | Fast       |
| Long sessions (20+ messages)      | Thinking   |

Override with `/model fast`, `/model think`, or `/model auto`.

## Memory System

Atlas remembers across sessions using persistent memory:

- **Auto-recall**: Before each LLM call, relevant memories are searched
  and injected into context.
- **Auto-archive**: When the conversation grows long, old turns are
  summarised and stored in memory.
- **Manual search**: `/memory search <query>` searches the full memory
  store.

## Tool System

The CLI agent has 31+ built-in tools. See them with `/tools`. Key tools:

- `shell_exec` — Run shell commands
- `file_read` / `file_write` / `file_edit` — File operations
- `code_search` / `regex_search` — Search code
- `web_search` — Search the web
- `git_*` — Git operations

## Streaming with Interrupt

Responses stream token-by-token. Press **Ctrl+C** to:
- **During streaming**: Stop generation (partial response is kept)
- **At the prompt**: Print a hint to use `/quit`

This means Ctrl+C never kills the CLI — it only interrupts the
current generation.

## Background Tasks

Long-running operations can execute in the background:
- Tasks are tracked with IDs (`bg-xxxxxxxx`)
- Check status with `/bg list`
- Cancel with `/bg cancel <id>`
- Completion notifications appear in the REPL

## Agent Mode

For autonomous task execution:

```bash
# Single task
atlas agent "add authentication to the API"

# With file context
atlas agent --file spec.png "implement this design"

# Multi-task dispatch
atlas agent --dispatch "task one" "task two"
```

The agent uses the ReAct (Think → Act → Observe) loop with all
available tools.

## Workspace Daemon

For persistent background operation:

```bash
# Start the daemon
atlas daemon start

# Connect to it
atlas workspace

# Send a message
atlas send "check the build"

# Stop the daemon
atlas daemon stop
```

The daemon survives terminal disconnects and maintains full state
(conversation, tools, memory, curiosity engine).

## Examples

### Code review workflow
```
atlas
atlas> /file src/auth.py
atlas> review this code for security issues
atlas> /diff
atlas> explain what changed in this diff
```

### Research session
```
atlas --new
atlas> /session name research-llm-quantization
atlas> explain GPTQ vs AWQ quantization tradeoffs
atlas> /memory search quantization
atlas> summarize what we discussed about quantization
```

### Multi-model usage
```
atlas
atlas> /model fast
atlas> what's the capital of France?
atlas> /model think
atlas> analyze the time complexity of merge sort with a formal proof
atlas> /model auto
```
