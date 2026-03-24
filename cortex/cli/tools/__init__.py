"""Agent tool registry and base class.

All tools implement the AgentTool ABC and register via the ToolRegistry.
"""

# Module ownership: Agent tool infrastructure
from __future__ import annotations

import abc
import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class ToolResult:
    """Result returned by every tool execution."""

    success: bool
    output: str
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class AgentTool(abc.ABC):
    """Base class for all agent tools."""

    tool_id: str = ""
    description: str = ""
    parameters_schema: dict[str, Any] = {}
    requires_confirmation: bool = False

    @abc.abstractmethod
    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        """Execute the tool with given parameters."""
        ...

    def to_function_schema(self) -> dict[str, Any]:
        """Convert to OpenAI-compatible function calling schema."""
        return {
            "type": "function",
            "function": {
                "name": self.tool_id,
                "description": self.description,
                "parameters": self.parameters_schema,
            },
        }


class ToolRegistry:
    """Registry of available agent tools."""

    def __init__(self) -> None:
        self._tools: dict[str, AgentTool] = {}

    def register(self, tool: AgentTool) -> None:
        if not tool.tool_id:
            raise ValueError(f"Tool {type(tool).__name__} has no tool_id")
        if tool.tool_id in self._tools:
            log.warning("Overwriting tool %s", tool.tool_id)
        self._tools[tool.tool_id] = tool

    def unregister(self, tool_id: str) -> bool:
        """Remove a tool from the registry. Returns True if it existed."""
        return self._tools.pop(tool_id, None) is not None

    def get(self, tool_id: str) -> AgentTool | None:
        return self._tools.get(tool_id)

    def list_tools(self) -> list[AgentTool]:
        return list(self._tools.values())

    def get_function_schemas(self) -> list[dict[str, Any]]:
        """Return OpenAI-compatible function schemas for all registered tools."""
        return [t.to_function_schema() for t in self._tools.values()]

    async def execute(
        self,
        tool_id: str,
        params: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Execute a tool by ID."""
        tool = self._tools.get(tool_id)
        if tool is None:
            return ToolResult(success=False, output="", error=f"Unknown tool: {tool_id}")
        if tool.requires_confirmation:
            log.info("Tool %s requires confirmation (destructive)", tool_id)
        return await tool.execute(params, context)


def get_default_registry() -> ToolRegistry:
    """Create a registry with all built-in tools registered."""
    from cortex.cli.tools.database import (
        MigrationGenerateTool,
        QueryExplainTool,
        SchemaInspectTool,
    )
    from cortex.cli.tools.dev import (
        BenchmarkTool,
        BuildTool,
        ChangelogGenerateTool,
        CodeAnalyzeTool,
        DBQueryTool,
        DiffPreviewTool,
        DocGenerateTool,
        DockerTool,
        EnvManageTool,
        LintTool,
        PackageManageTool,
        ProcessRunTool,
        RefactorTool,
        TestRunTool,
    )
    from cortex.cli.tools.devops import (
        IncidentTimelineTool,
        LogAnalyzeTool,
        MetricsQueryTool,
    )
    from cortex.cli.tools.files import (
        FileEditTool,
        FileListTool,
        FileReadTool,
        FileWriteTool,
    )
    from cortex.cli.tools.git import GitTool
    from cortex.cli.tools.network import (
        APICallTool,
        SSHTool,
        WebFetchTool,
        WebSearchTool,
    )
    from cortex.cli.tools.network_ops import (
        ContainerLogsTool,
        FirewallReadTool,
        HTTPDebugTool,
        NetworkScanTool,
        SSLCheckTool,
    )
    from cortex.cli.tools.project import (
        RiskAssessTool,
        TaskTrackTool,
        TimeEstimateTool,
    )
    from cortex.cli.tools.security import (
        PermissionAuditTool,
        SecretScanTool,
        VulnScanTool,
    )
    from cortex.cli.tools.shell import GlobTool, GrepTool, ShellExecTool

    registry = ToolRegistry()
    for tool_cls in [
        FileReadTool,
        FileWriteTool,
        FileEditTool,
        FileListTool,
        ShellExecTool,
        GrepTool,
        GlobTool,
        GitTool,
        # Network tools
        WebSearchTool,
        WebFetchTool,
        APICallTool,
        SSHTool,
        # Dev tools
        TestRunTool,
        BuildTool,
        LintTool,
        DockerTool,
        DBQueryTool,
        PackageManageTool,
        RefactorTool,
        DiffPreviewTool,
        ProcessRunTool,
        EnvManageTool,
        CodeAnalyzeTool,
        BenchmarkTool,
        DocGenerateTool,
        ChangelogGenerateTool,
        # Network engineering tools
        NetworkScanTool,
        HTTPDebugTool,
        ContainerLogsTool,
        SSLCheckTool,
        FirewallReadTool,
        # Database tools
        SchemaInspectTool,
        MigrationGenerateTool,
        QueryExplainTool,
        # Security tools
        SecretScanTool,
        VulnScanTool,
        PermissionAuditTool,
        # DevOps tools
        LogAnalyzeTool,
        MetricsQueryTool,
        IncidentTimelineTool,
        # Project management tools
        TaskTrackTool,
        TimeEstimateTool,
        RiskAssessTool,
    ]:
        registry.register(tool_cls())

    # Atlas integration tools (optional — depend on cortex modules)
    try:
        from cortex.cli.tools.atlas import (
            HAControlTool,
            MemoryStoreTool,
            MemoryTool,
            NotifyTool,
            ReminderTool,
            RoutineTool,
            TimerTool,
        )

        for cls in [
            HAControlTool,
            TimerTool,
            ReminderTool,
            RoutineTool,
            NotifyTool,
            MemoryTool,
            MemoryStoreTool,
        ]:
            registry.register(cls())
    except ImportError:
        pass

    # Diagram & architecture tools
    from cortex.cli.tools.diagrams import (
        APISpecTool,
        ArchitectureDocTool,
        DependencyGraphTool,
        MermaidGenerateTool,
    )

    for cls in [MermaidGenerateTool, ArchitectureDocTool, DependencyGraphTool, APISpecTool]:
        registry.register(cls())

    # Multi-modal tools (optional)
    try:
        from cortex.cli.tools.multimodal import (
            EmbedTextTool,
            ImageGenerateTool,
            OCRTool,
            SpeechToTextTool,
            TextToSpeechTool,
            VisionAnalyzeTool,
        )

        for cls in [
            VisionAnalyzeTool,
            ImageGenerateTool,
            EmbedTextTool,
            OCRTool,
            SpeechToTextTool,
            TextToSpeechTool,
        ]:
            registry.register(cls())
    except ImportError:
        pass

    # Self-learning tools (dynamic tool lifecycle)
    try:
        from cortex.cli.tools.learned import (
            ToolCleanupTool,
            ToolForgetTool,
            ToolLearnTool,
            ToolListLearnedTool,
            ToolProposeTool,
            get_tool_loader,
        )

        for cls in [
            ToolLearnTool,
            ToolForgetTool,
            ToolListLearnedTool,
            ToolCleanupTool,
            ToolProposeTool,
        ]:
            registry.register(cls())

        # Load and register user-taught / auto-discovered tools
        loader = get_tool_loader()
        for tool in loader.load_all():
            registry.register(tool)
    except ImportError:
        pass

    # Sandbox tool (isolated experimentation)
    from cortex.cli.sandbox import SandboxTool

    registry.register(SandboxTool())

    return registry
