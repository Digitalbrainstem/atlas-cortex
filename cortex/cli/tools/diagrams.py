"""Diagram and architecture documentation tools.

Generate Mermaid diagrams, architecture docs, dependency graphs, and API specs
from the codebase — all without mandatory external dependencies.
"""

# Module ownership: CLI diagram and architecture tools
from __future__ import annotations

import ast
import json
import logging
import os
import re
import shutil
import asyncio
from collections import defaultdict
from pathlib import Path
from typing import Any

from cortex.cli.tools import AgentTool, ToolResult

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_IGNORED_DIRS = {
    "__pycache__",
    ".git",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    "egg-info",
}


def _resolve_path(raw: str, context: dict[str, Any] | None = None) -> Path:
    """Resolve a user-supplied path relative to context cwd."""
    cwd = (context or {}).get("cwd") or os.getcwd()
    p = Path(raw)
    if not p.is_absolute():
        p = Path(cwd) / p
    return p.resolve()


def _walk(root: Path, depth: int = 3) -> list[tuple[Path, int]]:
    """Walk directory up to *depth* levels, skipping ignored dirs."""
    results: list[tuple[Path, int]] = []

    def _recurse(directory: Path, level: int) -> None:
        if level > depth:
            return
        try:
            entries = sorted(directory.iterdir(), key=lambda e: (not e.is_dir(), e.name))
        except PermissionError:
            return
        for entry in entries:
            if entry.name.startswith(".") or entry.name in _IGNORED_DIRS:
                continue
            results.append((entry, level))
            if entry.is_dir():
                _recurse(entry, level + 1)

    _recurse(root, 1)
    return results


# ---------------------------------------------------------------------------
# 1. MermaidGenerateTool
# ---------------------------------------------------------------------------


class MermaidGenerateTool(AgentTool):
    """Generate a Mermaid diagram and optionally render it."""

    tool_id = "mermaid_generate"
    description = (
        "Generate a Mermaid diagram (flowchart, sequence, ER, class, state, gantt, pie)"
    )
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "diagram_type": {
                "type": "string",
                "enum": [
                    "flowchart",
                    "sequence",
                    "erDiagram",
                    "classDiagram",
                    "stateDiagram",
                    "gantt",
                    "pie",
                ],
                "description": "Type of diagram",
            },
            "content": {
                "type": "string",
                "description": "Mermaid diagram source code",
            },
            "output_path": {
                "type": "string",
                "description": "Path to save the .mmd file (optional, defaults to stdout)",
            },
            "render": {
                "type": "boolean",
                "description": "Try to render to SVG/PNG using mmdc CLI (if installed)",
                "default": False,
            },
        },
        "required": ["diagram_type", "content"],
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        diagram_type: str = params["diagram_type"]
        content: str = params["content"]
        output_path: str | None = params.get("output_path")
        render: bool = params.get("render", False)

        # Ensure content starts with the diagram directive
        trimmed = content.strip()
        valid_prefixes = (
            "flowchart",
            "graph",
            "sequenceDiagram",
            "erDiagram",
            "classDiagram",
            "stateDiagram",
            "gantt",
            "pie",
        )
        if not trimmed.split("\n", 1)[0].strip().startswith(valid_prefixes):
            content = f"{diagram_type}\n{content}"

        if output_path:
            dest = _resolve_path(output_path, context)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
            msg = f"Mermaid source saved to {dest}"
        else:
            msg = f"```mermaid\n{content}\n```"

        # Optional render via mmdc
        rendered_path: str | None = None
        if render and output_path:
            mmdc = shutil.which("mmdc")
            if mmdc:
                svg_path = str(dest.with_suffix(".svg"))
                try:
                    proc = await asyncio.create_subprocess_exec(
                        mmdc,
                        "-i",
                        str(dest),
                        "-o",
                        svg_path,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
                    if proc.returncode == 0:
                        rendered_path = svg_path
                        msg += f"\nRendered SVG: {svg_path}"
                    else:
                        msg += f"\nmmdc render failed: {stderr.decode(errors='replace')}"
                except Exception as exc:  # noqa: BLE001
                    msg += f"\nmmdc render error: {exc}"
            else:
                msg += (
                    "\nmmdc not installed — paste the source into "
                    "https://mermaid.live/ to render."
                )

        return ToolResult(
            success=True,
            output=msg,
            metadata={
                "diagram_type": diagram_type,
                "output_path": output_path,
                "rendered_path": rendered_path,
            },
        )


# ---------------------------------------------------------------------------
# 2. ArchitectureDocTool
# ---------------------------------------------------------------------------

_FASTAPI_ROUTE_RE = re.compile(
    r"@(?:app|router)\.(get|post|put|patch|delete|options|head)\s*\(",
    re.IGNORECASE,
)


def _detect_patterns(source: str) -> list[str]:
    """Detect notable Python patterns in source text."""
    patterns: list[str] = []
    if _FASTAPI_ROUTE_RE.search(source):
        patterns.append("FastAPI routes")
    if re.search(r"class\s+\w+\(.*\bABC\b", source):
        patterns.append("ABC classes")
    if "@dataclass" in source:
        patterns.append("dataclasses")
    if "BaseModel" in source and "pydantic" in source.lower():
        patterns.append("Pydantic models")
    if re.search(r"@pytest\.fixture", source):
        patterns.append("pytest fixtures")
    return patterns


class ArchitectureDocTool(AgentTool):
    """Scan a codebase and generate architecture documentation."""

    tool_id = "architecture_doc"
    description = "Generate architecture documentation by scanning the codebase"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Root directory to scan",
                "default": ".",
            },
            "output_path": {
                "type": "string",
                "description": "Path to save the doc",
            },
            "format": {
                "type": "string",
                "enum": ["markdown", "mermaid"],
                "default": "markdown",
            },
            "depth": {
                "type": "integer",
                "description": "Directory scan depth",
                "default": 3,
            },
        },
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        root = _resolve_path(params.get("path", "."), context)
        fmt = params.get("format", "markdown")
        depth = params.get("depth", 3)
        output_path: str | None = params.get("output_path")

        if not root.is_dir():
            return ToolResult(success=False, output="", error=f"Not a directory: {root}")

        entries = _walk(root, depth)

        # Categorise
        packages: list[str] = []
        modules: list[tuple[str, int, list[str]]] = []  # (rel path, size, patterns)
        config_files: list[str] = []
        test_dirs: list[str] = []

        config_names = {
            "pyproject.toml",
            "setup.cfg",
            "setup.py",
            "package.json",
            "Dockerfile",
            "docker-compose.yml",
            "Makefile",
            ".env",
            "requirements.txt",
            "pytest.ini",
        }

        for entry, _lvl in entries:
            rel = str(entry.relative_to(root))
            if entry.is_dir():
                if (entry / "__init__.py").exists():
                    packages.append(rel)
                if entry.name in ("tests", "test", "spec"):
                    test_dirs.append(rel)
            elif entry.is_file():
                if entry.name in config_names:
                    config_files.append(rel)
                if entry.suffix == ".py" and entry.name != "__init__.py":
                    try:
                        source = entry.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                    pats = _detect_patterns(source)
                    modules.append((rel, len(source), pats))

        # Sort modules by size descending to highlight key ones
        modules.sort(key=lambda m: m[1], reverse=True)

        if fmt == "mermaid":
            doc = self._build_mermaid(root.name, packages)
        else:
            doc = self._build_markdown(root.name, packages, modules, config_files, test_dirs)

        if output_path:
            dest = _resolve_path(output_path, context)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(doc, encoding="utf-8")
            return ToolResult(
                success=True,
                output=f"Architecture doc written to {dest}",
                metadata={"packages": len(packages), "modules": len(modules)},
            )

        return ToolResult(
            success=True,
            output=doc,
            metadata={"packages": len(packages), "modules": len(modules)},
        )

    # -- Formatters -----------------------------------------------------------

    @staticmethod
    def _build_markdown(
        project: str,
        packages: list[str],
        modules: list[tuple[str, int, list[str]]],
        config_files: list[str],
        test_dirs: list[str],
    ) -> str:
        lines = [f"# Architecture — {project}\n"]

        if packages:
            lines.append("## Python Packages\n")
            for pkg in sorted(packages):
                lines.append(f"- `{pkg}`")
            lines.append("")

        if modules:
            lines.append("## Key Modules (by size)\n")
            lines.append("| Module | Size | Patterns |")
            lines.append("|--------|------|----------|")
            for rel, size, pats in modules[:30]:
                pat_str = ", ".join(pats) if pats else "—"
                lines.append(f"| `{rel}` | {size:,} B | {pat_str} |")
            lines.append("")

        if config_files:
            lines.append("## Config Files\n")
            for cf in sorted(config_files):
                lines.append(f"- `{cf}`")
            lines.append("")

        if test_dirs:
            lines.append("## Test Directories\n")
            for td in sorted(test_dirs):
                lines.append(f"- `{td}`")
            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def _build_mermaid(project: str, packages: list[str]) -> str:
        lines = [f"flowchart TD"]
        root_id = "ROOT"
        lines.append(f"    {root_id}[{project}]")
        seen: dict[str, str] = {}
        for pkg in sorted(packages):
            parts = Path(pkg).parts
            parent_id = root_id
            for i, part in enumerate(parts):
                key = "/".join(parts[: i + 1])
                if key not in seen:
                    node_id = key.replace("/", "_").replace("-", "_")
                    seen[key] = node_id
                    lines.append(f"    {parent_id} --> {node_id}[{part}]")
                parent_id = seen[key]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 3. DependencyGraphTool
# ---------------------------------------------------------------------------


def _parse_imports(source: str) -> list[str]:
    """Use the ast module to extract all imported module names from source."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
                # `from pkg import sub` may refer to pkg.sub module
                for alias in node.names:
                    imports.append(f"{node.module}.{alias.name}")
    return imports


def _detect_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """Find all simple cycles using DFS-based cycle detection."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {n: WHITE for n in graph}
    cycles: list[list[str]] = []
    path: list[str] = []

    def _dfs(node: str) -> None:
        color[node] = GRAY
        path.append(node)
        for nbr in graph.get(node, set()):
            if nbr not in color:
                continue
            if color[nbr] == GRAY:
                idx = path.index(nbr)
                cycles.append(path[idx:] + [nbr])
            elif color[nbr] == WHITE:
                _dfs(nbr)
        path.pop()
        color[node] = BLACK

    for node in list(graph):
        if color.get(node) == WHITE:
            _dfs(node)
    return cycles


class DependencyGraphTool(AgentTool):
    """Map module dependencies and detect circular imports."""

    tool_id = "dependency_graph"
    description = "Map module dependencies and find circular imports"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Root directory",
                "default": ".",
            },
            "format": {
                "type": "string",
                "enum": ["text", "mermaid", "dot"],
                "default": "text",
            },
            "show_external": {
                "type": "boolean",
                "description": "Include external package deps",
                "default": False,
            },
        },
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        root = _resolve_path(params.get("path", "."), context)
        fmt = params.get("format", "text")
        show_external = params.get("show_external", False)

        if not root.is_dir():
            return ToolResult(success=False, output="", error=f"Not a directory: {root}")

        # Collect all .py files and map them to module names
        py_files: list[tuple[Path, str]] = []
        for entry, _ in _walk(root, depth=10):
            if entry.is_file() and entry.suffix == ".py":
                try:
                    rel = entry.relative_to(root)
                except ValueError:
                    continue
                parts = list(rel.parts)
                if parts[-1] == "__init__.py":
                    parts.pop()
                else:
                    parts[-1] = parts[-1].removesuffix(".py")
                mod_name = ".".join(parts) if parts else ""
                if mod_name:
                    py_files.append((entry, mod_name))

        known_modules = {m for _, m in py_files}
        # Also include parent packages
        for m in list(known_modules):
            parts = m.split(".")
            for i in range(1, len(parts)):
                known_modules.add(".".join(parts[:i]))

        graph: dict[str, set[str]] = defaultdict(set)

        for fpath, mod_name in py_files:
            try:
                source = fpath.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for imp in _parse_imports(source):
                # Determine if internal
                is_internal = any(
                    imp == km or imp.startswith(km + ".") for km in known_modules
                )
                if is_internal:
                    # Normalise to top-level known module
                    target = imp
                    while target and target not in known_modules:
                        target = target.rsplit(".", 1)[0] if "." in target else ""
                    if target and target != mod_name:
                        graph[mod_name].add(target)
                elif show_external:
                    top = imp.split(".")[0]
                    graph[mod_name].add(top)

        # Ensure all modules in graph values are also keys (for cycle detection)
        all_nodes = set(graph.keys())
        for deps in graph.values():
            all_nodes.update(deps)
        for node in all_nodes:
            graph.setdefault(node, set())

        cycles = _detect_cycles(graph)

        output = self._format(graph, cycles, fmt)
        return ToolResult(
            success=True,
            output=output,
            metadata={
                "modules": len(all_nodes),
                "edges": sum(len(v) for v in graph.values()),
                "cycles": len(cycles),
            },
        )

    @staticmethod
    def _format(
        graph: dict[str, set[str]], cycles: list[list[str]], fmt: str
    ) -> str:
        if fmt == "mermaid":
            lines = ["flowchart LR"]
            for mod, deps in sorted(graph.items()):
                safe_mod = mod.replace(".", "_")
                for dep in sorted(deps):
                    safe_dep = dep.replace(".", "_")
                    lines.append(f"    {safe_mod} --> {safe_dep}")
            if cycles:
                lines.append("")
                lines.append("%% Circular imports detected:")
                for cyc in cycles:
                    lines.append(f"%%   {' -> '.join(cyc)}")
            return "\n".join(lines)

        if fmt == "dot":
            lines = ["digraph dependencies {", "    rankdir=LR;"]
            for mod, deps in sorted(graph.items()):
                for dep in sorted(deps):
                    lines.append(f'    "{mod}" -> "{dep}";')
            lines.append("}")
            if cycles:
                lines.append("")
                lines.append("// Circular imports detected:")
                for cyc in cycles:
                    lines.append(f"//   {' -> '.join(cyc)}")
            return "\n".join(lines)

        # text
        lines: list[str] = ["Module Dependencies", "=" * 40]
        for mod in sorted(graph):
            deps = sorted(graph[mod])
            if deps:
                lines.append(f"  {mod}")
                for dep in deps:
                    lines.append(f"    → {dep}")
        if cycles:
            lines.append("")
            lines.append("⚠ Circular imports detected:")
            for cyc in cycles:
                lines.append(f"  {' → '.join(cyc)}")
        else:
            lines.append("")
            lines.append("✓ No circular imports detected.")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 4. APISpecTool
# ---------------------------------------------------------------------------

_ROUTE_RE = re.compile(
    r"""@(?:app|router)\.\s*                # decorator prefix
    (get|post|put|patch|delete|options|head) # HTTP method
    \s*\(\s*                                 # opening paren
    ["']([^"']+)["']                         # route path string
    """,
    re.VERBOSE | re.IGNORECASE,
)


def _extract_routes_from_source(source: str, filepath: str) -> list[dict[str, Any]]:
    """Extract FastAPI routes using AST + regex fallback."""
    routes: list[dict[str, Any]] = []

    # AST approach: look for decorated async/sync functions
    try:
        tree = ast.parse(source)
    except SyntaxError:
        tree = None

    if tree:
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                # Handle @router.get("/path") or @app.post("/path")
                if not isinstance(dec, ast.Call):
                    continue
                func = dec.func if isinstance(dec, ast.Call) else None
                if not isinstance(func, ast.Attribute):
                    continue
                method = func.attr.upper()
                if method not in {
                    "GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD",
                }:
                    continue
                # Extract path from first positional arg
                path_str = ""
                if dec.args and isinstance(dec.args[0], ast.Constant):
                    path_str = str(dec.args[0].value)
                # Docstring
                docstring = ast.get_docstring(node) or ""
                # Parameters from function signature
                func_params = [
                    a.arg
                    for a in node.args.args
                    if a.arg not in ("self", "cls", "request", "response")
                ]
                routes.append(
                    {
                        "method": method,
                        "path": path_str,
                        "function": node.name,
                        "docstring": docstring,
                        "parameters": func_params,
                        "file": filepath,
                    }
                )

    # Regex fallback if AST found nothing (handles dynamic decorator patterns)
    if not routes:
        for match in _ROUTE_RE.finditer(source):
            routes.append(
                {
                    "method": match.group(1).upper(),
                    "path": match.group(2),
                    "function": "",
                    "docstring": "",
                    "parameters": [],
                    "file": filepath,
                }
            )

    return routes


class APISpecTool(AgentTool):
    """Parse FastAPI routes and generate API specifications."""

    tool_id = "api_spec"
    description = "Parse FastAPI routes and generate OpenAPI specification"
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to Python file or directory with FastAPI routes",
            },
            "output_path": {
                "type": "string",
                "description": "Output path for spec",
            },
            "format": {
                "type": "string",
                "enum": ["yaml", "json", "markdown"],
                "default": "markdown",
            },
        },
    }

    async def execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolResult:
        target = _resolve_path(params.get("path", "."), context)
        fmt = params.get("format", "markdown")
        output_path: str | None = params.get("output_path")

        py_files: list[Path] = []
        if target.is_file():
            py_files.append(target)
        elif target.is_dir():
            for entry, _ in _walk(target, depth=10):
                if entry.is_file() and entry.suffix == ".py":
                    py_files.append(entry)
        else:
            return ToolResult(success=False, output="", error=f"Path not found: {target}")

        all_routes: list[dict[str, Any]] = []
        for fpath in py_files:
            try:
                source = fpath.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rel = str(fpath.relative_to(target)) if target.is_dir() else fpath.name
            all_routes.extend(_extract_routes_from_source(source, rel))

        if not all_routes:
            return ToolResult(
                success=True,
                output="No FastAPI routes found.",
                metadata={"routes": 0},
            )

        if fmt == "json":
            spec = self._build_openapi(all_routes)
            doc = json.dumps(spec, indent=2)
        elif fmt == "yaml":
            spec = self._build_openapi(all_routes)
            doc = self._dict_to_yaml(spec)
        else:
            doc = self._build_markdown(all_routes)

        if output_path:
            dest = _resolve_path(output_path, context)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(doc, encoding="utf-8")
            return ToolResult(
                success=True,
                output=f"API spec written to {dest} ({len(all_routes)} routes)",
                metadata={"routes": len(all_routes)},
            )

        return ToolResult(
            success=True,
            output=doc,
            metadata={"routes": len(all_routes)},
        )

    # -- Formatters -----------------------------------------------------------

    @staticmethod
    def _build_markdown(routes: list[dict[str, Any]]) -> str:
        lines = ["# API Endpoints\n"]
        lines.append("| Method | Path | Function | Description | File |")
        lines.append("|--------|------|----------|-------------|------|")
        for r in routes:
            desc = r["docstring"].split("\n")[0] if r["docstring"] else "—"
            func = r["function"] or "—"
            lines.append(
                f"| `{r['method']}` | `{r['path']}` | `{func}` | {desc} | `{r['file']}` |"
            )
        return "\n".join(lines)

    @staticmethod
    def _build_openapi(routes: list[dict[str, Any]]) -> dict[str, Any]:
        paths: dict[str, Any] = {}
        for r in routes:
            path = r["path"] or "/"
            method = r["method"].lower()
            entry: dict[str, Any] = {
                "summary": r["docstring"].split("\n")[0] if r["docstring"] else "",
                "operationId": r["function"] or f"{method}_{path.replace('/', '_')}",
            }
            if r["parameters"]:
                entry["parameters"] = [
                    {"name": p, "in": "query", "schema": {"type": "string"}}
                    for p in r["parameters"]
                ]
            paths.setdefault(path, {})[method] = entry
        return {
            "openapi": "3.0.3",
            "info": {"title": "API Spec", "version": "1.0.0"},
            "paths": paths,
        }

    @staticmethod
    def _dict_to_yaml(data: dict[str, Any], indent: int = 0) -> str:
        """Minimal YAML serialiser (no PyYAML dependency)."""
        lines: list[str] = []
        prefix = "  " * indent
        for key, value in data.items():
            if isinstance(value, dict):
                lines.append(f"{prefix}{key}:")
                lines.append(APISpecTool._dict_to_yaml(value, indent + 1))
            elif isinstance(value, list):
                lines.append(f"{prefix}{key}:")
                for item in value:
                    if isinstance(item, dict):
                        lines.append(f"{prefix}- ")
                        lines.append(APISpecTool._dict_to_yaml(item, indent + 2))
                    else:
                        lines.append(f"{prefix}  - {item}")
            else:
                val = f'"{value}"' if isinstance(value, str) and value else value
                lines.append(f"{prefix}{key}: {val}")
        return "\n".join(lines)
