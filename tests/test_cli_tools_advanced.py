"""Tests for CLI diagram and architecture tools."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from cortex.cli.tools import ToolResult, get_default_registry
from cortex.cli.tools.diagrams import (
    APISpecTool,
    ArchitectureDocTool,
    DependencyGraphTool,
    MermaidGenerateTool,
    _detect_cycles,
    _extract_routes_from_source,
    _parse_imports,
)


# ===================================================================
# Registration
# ===================================================================


async def test_diagram_tools_registered():
    registry = get_default_registry()
    for tid in ["mermaid_generate", "architecture_doc", "dependency_graph", "api_spec"]:
        assert registry.get(tid) is not None, f"{tid} not registered"


# ===================================================================
# MermaidGenerateTool
# ===================================================================


class TestMermaidGenerate:
    async def test_flowchart_to_stdout(self):
        tool = MermaidGenerateTool()
        result = await tool.execute(
            {"diagram_type": "flowchart", "content": "flowchart TD\n    A --> B"}
        )
        assert result.success
        assert "A --> B" in result.output
        assert result.metadata["diagram_type"] == "flowchart"

    async def test_sequence_diagram_to_stdout(self):
        tool = MermaidGenerateTool()
        content = "sequenceDiagram\n    Alice->>Bob: Hello"
        result = await tool.execute(
            {"diagram_type": "sequence", "content": content}
        )
        assert result.success
        assert "Alice->>Bob" in result.output

    async def test_writes_to_file(self, tmp_path: Path):
        tool = MermaidGenerateTool()
        out = tmp_path / "diagram.mmd"
        result = await tool.execute(
            {
                "diagram_type": "flowchart",
                "content": "flowchart LR\n    X --> Y",
                "output_path": str(out),
            }
        )
        assert result.success
        assert out.exists()
        assert "X --> Y" in out.read_text()

    async def test_auto_prefix(self):
        tool = MermaidGenerateTool()
        result = await tool.execute(
            {"diagram_type": "pie", "content": '"Dogs" : 40\n"Cats" : 60'}
        )
        assert result.success
        assert result.output.startswith("```mermaid\npie")

    async def test_render_without_mmdc(self, tmp_path: Path):
        tool = MermaidGenerateTool()
        out = tmp_path / "d.mmd"
        result = await tool.execute(
            {
                "diagram_type": "flowchart",
                "content": "flowchart TD\n    A-->B",
                "output_path": str(out),
                "render": True,
            }
        )
        assert result.success
        # mmdc almost certainly not installed in CI
        assert "mermaid.live" in result.output or "SVG" in result.output

    async def test_er_diagram(self):
        tool = MermaidGenerateTool()
        content = "erDiagram\n    USER ||--o{ ORDER : places"
        result = await tool.execute(
            {"diagram_type": "erDiagram", "content": content}
        )
        assert result.success
        assert "USER" in result.output

    async def test_function_schema(self):
        tool = MermaidGenerateTool()
        schema = tool.to_function_schema()
        assert schema["function"]["name"] == "mermaid_generate"
        assert "diagram_type" in schema["function"]["parameters"]["properties"]


# ===================================================================
# ArchitectureDocTool
# ===================================================================


class TestArchitectureDoc:
    @pytest.fixture()
    def sample_project(self, tmp_path: Path) -> Path:
        """Create a minimal project tree for scanning."""
        pkg = tmp_path / "myapp"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "server.py").write_text(
            textwrap.dedent("""\
                from fastapi import FastAPI
                app = FastAPI()
                @app.get("/health")
                async def health():
                    return {"ok": True}
            """)
        )
        sub = pkg / "models"
        sub.mkdir()
        (sub / "__init__.py").write_text("")
        (sub / "user.py").write_text(
            "from dataclasses import dataclass\n@dataclass\nclass User:\n    name: str\n"
        )
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_app.py").write_text("def test_ok(): pass\n")
        (tmp_path / "requirements.txt").write_text("fastapi\n")
        return tmp_path

    async def test_markdown_output(self, sample_project: Path):
        tool = ArchitectureDocTool()
        result = await tool.execute({"path": str(sample_project)})
        assert result.success
        assert "myapp" in result.output
        assert "Python Packages" in result.output
        assert result.metadata["packages"] >= 2  # myapp, myapp/models

    async def test_mermaid_output(self, sample_project: Path):
        tool = ArchitectureDocTool()
        result = await tool.execute(
            {"path": str(sample_project), "format": "mermaid"}
        )
        assert result.success
        assert "flowchart" in result.output

    async def test_detects_fastapi_routes(self, sample_project: Path):
        tool = ArchitectureDocTool()
        result = await tool.execute({"path": str(sample_project)})
        assert result.success
        assert "FastAPI routes" in result.output

    async def test_detects_dataclasses(self, sample_project: Path):
        tool = ArchitectureDocTool()
        result = await tool.execute({"path": str(sample_project)})
        assert result.success
        assert "dataclasses" in result.output

    async def test_writes_to_file(self, sample_project: Path, tmp_path: Path):
        tool = ArchitectureDocTool()
        out = tmp_path / "arch.md"
        result = await tool.execute(
            {"path": str(sample_project), "output_path": str(out)}
        )
        assert result.success
        assert out.exists()
        assert "Architecture" in out.read_text()

    async def test_nonexistent_path(self):
        tool = ArchitectureDocTool()
        result = await tool.execute({"path": "/nonexistent/path/xyz"})
        assert not result.success
        assert "Not a directory" in result.error

    async def test_detects_test_dirs(self, sample_project: Path):
        tool = ArchitectureDocTool()
        result = await tool.execute({"path": str(sample_project)})
        assert "Test Directories" in result.output

    async def test_detects_config_files(self, sample_project: Path):
        tool = ArchitectureDocTool()
        result = await tool.execute({"path": str(sample_project)})
        assert "requirements.txt" in result.output


# ===================================================================
# DependencyGraphTool
# ===================================================================


class TestDependencyGraph:
    @pytest.fixture()
    def dep_project(self, tmp_path: Path) -> Path:
        """Project with known internal dependencies."""
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "core.py").write_text("import os\n")
        (pkg / "utils.py").write_text("from pkg import core\n")
        (pkg / "api.py").write_text("from pkg import utils\nfrom pkg import core\n")
        return tmp_path

    @pytest.fixture()
    def circular_project(self, tmp_path: Path) -> Path:
        """Project with a circular import."""
        pkg = tmp_path / "cyc"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "a.py").write_text("from cyc import b\n")
        (pkg / "b.py").write_text("from cyc import a\n")
        return tmp_path

    async def test_text_output(self, dep_project: Path):
        tool = DependencyGraphTool()
        result = await tool.execute({"path": str(dep_project)})
        assert result.success
        assert "pkg.api" in result.output
        assert "pkg.utils" in result.output
        assert result.metadata["modules"] >= 3

    async def test_mermaid_output(self, dep_project: Path):
        tool = DependencyGraphTool()
        result = await tool.execute(
            {"path": str(dep_project), "format": "mermaid"}
        )
        assert result.success
        assert "flowchart LR" in result.output
        assert "-->" in result.output

    async def test_dot_output(self, dep_project: Path):
        tool = DependencyGraphTool()
        result = await tool.execute(
            {"path": str(dep_project), "format": "dot"}
        )
        assert result.success
        assert "digraph" in result.output

    async def test_detects_circular_imports(self, circular_project: Path):
        tool = DependencyGraphTool()
        result = await tool.execute({"path": str(circular_project)})
        assert result.success
        assert result.metadata["cycles"] > 0
        assert "Circular" in result.output or "circular" in result.output

    async def test_no_circular_imports(self, dep_project: Path):
        tool = DependencyGraphTool()
        result = await tool.execute({"path": str(dep_project)})
        assert result.success
        assert "No circular" in result.output

    async def test_nonexistent_path(self):
        tool = DependencyGraphTool()
        result = await tool.execute({"path": "/nonexistent/xyz"})
        assert not result.success


# ===================================================================
# _parse_imports helper
# ===================================================================


class TestParseImports:
    def test_import_statement(self):
        assert "os" in _parse_imports("import os")

    def test_from_import(self):
        assert "os.path" in _parse_imports("from os.path import join")

    def test_multiple_imports(self):
        source = "import os\nimport sys\nfrom pathlib import Path\n"
        result = _parse_imports(source)
        assert "os" in result
        assert "sys" in result
        assert "pathlib" in result

    def test_syntax_error_returns_empty(self):
        assert _parse_imports("def foo(:\n") == []


# ===================================================================
# _detect_cycles helper
# ===================================================================


class TestDetectCycles:
    def test_no_cycle(self):
        graph = {"a": {"b"}, "b": {"c"}, "c": set()}
        assert _detect_cycles(graph) == []

    def test_simple_cycle(self):
        graph = {"a": {"b"}, "b": {"a"}}
        cycles = _detect_cycles(graph)
        assert len(cycles) >= 1

    def test_self_loop(self):
        graph = {"a": {"a"}}
        cycles = _detect_cycles(graph)
        assert len(cycles) >= 1


# ===================================================================
# APISpecTool
# ===================================================================


class TestAPISpec:
    @pytest.fixture()
    def fastapi_file(self, tmp_path: Path) -> Path:
        f = tmp_path / "routes.py"
        f.write_text(
            textwrap.dedent("""\
                from fastapi import APIRouter

                router = APIRouter()

                @router.get("/users")
                async def list_users():
                    \"\"\"List all users.\"\"\"
                    return []

                @router.post("/users")
                async def create_user(name: str):
                    \"\"\"Create a new user.\"\"\"
                    return {"name": name}

                @router.get("/users/{user_id}")
                async def get_user(user_id: int):
                    return {}

                @router.delete("/users/{user_id}")
                async def delete_user(user_id: int):
                    \"\"\"Delete a user.\"\"\"
                    return {}
            """)
        )
        return f

    async def test_markdown_output(self, fastapi_file: Path):
        tool = APISpecTool()
        result = await tool.execute({"path": str(fastapi_file)})
        assert result.success
        assert result.metadata["routes"] == 4
        assert "GET" in result.output
        assert "POST" in result.output
        assert "/users" in result.output
        assert "List all users" in result.output

    async def test_json_output(self, fastapi_file: Path):
        tool = APISpecTool()
        result = await tool.execute(
            {"path": str(fastapi_file), "format": "json"}
        )
        assert result.success
        spec = json.loads(result.output)
        assert spec["openapi"] == "3.0.3"
        assert "/users" in spec["paths"]

    async def test_yaml_output(self, fastapi_file: Path):
        tool = APISpecTool()
        result = await tool.execute(
            {"path": str(fastapi_file), "format": "yaml"}
        )
        assert result.success
        assert "openapi:" in result.output

    async def test_writes_to_file(self, fastapi_file: Path, tmp_path: Path):
        tool = APISpecTool()
        out = tmp_path / "api.md"
        result = await tool.execute(
            {"path": str(fastapi_file), "output_path": str(out)}
        )
        assert result.success
        assert out.exists()
        assert "Endpoints" in out.read_text()

    async def test_directory_scan(self, fastapi_file: Path):
        tool = APISpecTool()
        result = await tool.execute({"path": str(fastapi_file.parent)})
        assert result.success
        assert result.metadata["routes"] == 4

    async def test_no_routes_found(self, tmp_path: Path):
        (tmp_path / "empty.py").write_text("x = 1\n")
        tool = APISpecTool()
        result = await tool.execute({"path": str(tmp_path)})
        assert result.success
        assert "No FastAPI routes" in result.output

    async def test_extracts_function_params(self, fastapi_file: Path):
        tool = APISpecTool()
        result = await tool.execute(
            {"path": str(fastapi_file), "format": "json"}
        )
        spec = json.loads(result.output)
        # create_user has 'name' param
        post_op = spec["paths"]["/users"]["post"]
        param_names = [p["name"] for p in post_op.get("parameters", [])]
        assert "name" in param_names

    async def test_nonexistent_path(self):
        tool = APISpecTool()
        result = await tool.execute({"path": "/nonexistent/file.py"})
        assert not result.success


# ===================================================================
# _extract_routes_from_source helper
# ===================================================================


class TestExtractRoutes:
    def test_basic_route(self):
        source = textwrap.dedent("""\
            from fastapi import APIRouter
            router = APIRouter()
            @router.get("/items")
            async def get_items():
                return []
        """)
        routes = _extract_routes_from_source(source, "test.py")
        assert len(routes) == 1
        assert routes[0]["method"] == "GET"
        assert routes[0]["path"] == "/items"

    def test_multiple_routes(self):
        source = textwrap.dedent("""\
            from fastapi import FastAPI
            app = FastAPI()
            @app.get("/a")
            def a(): pass
            @app.post("/b")
            def b(): pass
        """)
        routes = _extract_routes_from_source(source, "test.py")
        assert len(routes) == 2
        methods = {r["method"] for r in routes}
        assert methods == {"GET", "POST"}
