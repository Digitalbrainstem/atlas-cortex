"""Tests for the Atlas Coding Pipeline."""
from __future__ import annotations

import ast
import json
import textwrap
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cortex.coding.dep_graph import DepGraph, FileSpec
from cortex.coding.doc_fetcher import DocFetcher
from cortex.coding.error_memory import ErrorMemory, ErrorFix
from cortex.coding.known_bugs import BugPattern, BugWarning, KnownBugs
from cortex.coding.pipeline import CodingPipeline, ProjectResult, _strip_fences
from cortex.coding.reviewer import CodeReviewer, ReviewIssue, ReviewResult
from cortex.coding.spec_parser import ParsedSpec, SpecParser, Violation
from cortex.coding.test_first import CodeTestGenerator as TestFirstGenerator


# =========================================================================
# SpecParser
# =========================================================================


class TestSpecParser:
    def setup_method(self) -> None:
        self.parser = SpecParser()

    def test_extract_libraries(self) -> None:
        spec = "Build a YAML parser using pyyaml and markdown. Also import requests."
        parsed = self.parser.parse(spec)
        assert "pyyaml" in parsed.required_libraries
        assert "markdown" in parsed.required_libraries
        assert "requests" in parsed.required_libraries

    def test_extract_libraries_from_backticks(self) -> None:
        spec = "Use `httpx` for HTTP and `pydantic` for validation."
        parsed = self.parser.parse(spec)
        assert "httpx" in parsed.required_libraries
        assert "pydantic" in parsed.required_libraries

    def test_stdlib_excluded(self) -> None:
        spec = "Import json, os, and sys. Also use requests."
        parsed = self.parser.parse(spec)
        assert "json" not in parsed.required_libraries
        assert "os" not in parsed.required_libraries
        assert "requests" in parsed.required_libraries

    def test_extract_constraints_stdlib_only(self) -> None:
        spec = "Build this using stdlib only, no external packages."
        parsed = self.parser.parse(spec)
        assert any("stdlib" in c for c in parsed.constraints)

    def test_extract_constraints_async(self) -> None:
        spec = "All operations must be async."
        parsed = self.parser.parse(spec)
        assert any("async" in c for c in parsed.constraints)

    def test_extract_file_structure(self) -> None:
        spec = "Files: `models.py`, `engine.py`, `tests/test_engine.py`."
        parsed = self.parser.parse(spec)
        assert "models.py" in parsed.file_structure
        assert "engine.py" in parsed.file_structure

    def test_extract_features(self) -> None:
        spec = textwrap.dedent("""\
            Features:
            - Parse YAML files into Python dicts
            - Support multi-document YAML streams
            - Handle unicode correctly
        """)
        parsed = self.parser.parse(spec)
        assert len(parsed.features) >= 3

    def test_extract_test_requirements(self) -> None:
        spec = textwrap.dedent("""\
            Tests:
            - round-trip parsing
            - error handling for malformed input
            - unicode support
        """)
        parsed = self.parser.parse(spec)
        assert len(parsed.test_requirements) >= 2

    def test_check_adherence_stdlib_violation(self) -> None:
        files = {"main.py": "import requests\nimport json\n"}
        spec = ParsedSpec(raw="", constraints=["stdlib only"])
        violations = self.parser.check_adherence(files, spec)
        assert any("stdlib" in v.description.lower() for v in violations)

    def test_check_adherence_undeclared_dep(self) -> None:
        files = {"main.py": "import pandas\n"}
        spec = ParsedSpec(raw="", required_libraries=["numpy"])
        violations = self.parser.check_adherence(files, spec)
        assert any("pandas" in v.description for v in violations)

    def test_check_adherence_missing_file(self) -> None:
        files = {"main.py": "x = 1\n"}
        spec = ParsedSpec(raw="", file_structure={"main.py": "", "utils.py": ""})
        violations = self.parser.check_adherence(files, spec)
        assert any("utils.py" in v.description for v in violations)

    def test_check_adherence_clean(self) -> None:
        files = {"main.py": "import json\nx = json.loads('{}')\n"}
        spec = ParsedSpec(raw="", constraints=["stdlib only"])
        violations = self.parser.check_adherence(files, spec)
        errors = [v for v in violations if v.severity == "error"]
        assert len(errors) == 0

    def test_library_alias_yaml(self) -> None:
        spec = "import yaml to parse config"
        parsed = self.parser.parse(spec)
        assert "pyyaml" in parsed.required_libraries

    def test_empty_spec(self) -> None:
        parsed = self.parser.parse("")
        assert parsed.required_libraries == []
        assert parsed.constraints == []
        assert parsed.features == []


# =========================================================================
# DepGraph
# =========================================================================


class TestDepGraph:
    def test_topological_sort_simple(self) -> None:
        graph = DepGraph()
        files = [
            FileSpec(path="models.py"),
            FileSpec(path="db.py", depends_on=["models.py"]),
            FileSpec(path="engine.py", depends_on=["db.py"]),
        ]
        order = graph.plan(files)
        assert order.index("models.py") < order.index("db.py")
        assert order.index("db.py") < order.index("engine.py")

    def test_topological_sort_no_deps(self) -> None:
        graph = DepGraph()
        files = [FileSpec(path="a.py"), FileSpec(path="b.py"), FileSpec(path="c.py")]
        order = graph.plan(files)
        assert set(order) == {"a.py", "b.py", "c.py"}

    def test_circular_dependency_raises(self) -> None:
        graph = DepGraph()
        files = [
            FileSpec(path="a.py", depends_on=["b.py"]),
            FileSpec(path="b.py", depends_on=["a.py"]),
        ]
        with pytest.raises(ValueError, match="Circular"):
            graph.plan(files)

    def test_get_dependents(self) -> None:
        graph = DepGraph()
        files = [
            FileSpec(path="models.py"),
            FileSpec(path="db.py", depends_on=["models.py"]),
            FileSpec(path="api.py", depends_on=["models.py"]),
        ]
        graph.plan(files)
        dependents = graph.get_dependents("models.py")
        assert "db.py" in dependents
        assert "api.py" in dependents

    def test_get_dependencies(self) -> None:
        graph = DepGraph()
        files = [
            FileSpec(path="models.py"),
            FileSpec(path="db.py", depends_on=["models.py"]),
        ]
        graph.plan(files)
        deps = graph.get_dependencies("db.py")
        assert "models.py" in deps

    def test_build_from_source(self) -> None:
        graph = DepGraph()
        code = {
            "models.py": "class User: pass\n",
            "db.py": "from models import User\n\ndef save(u: User): ...\n",
        }
        graph.build_from_source(code)
        deps = graph.get_dependencies("db.py")
        assert "models.py" in deps

    def test_extract_imports_from_ast(self) -> None:
        source = textwrap.dedent("""\
            import os
            from pathlib import Path
            import json
            from . import utils
        """)
        imports = DepGraph._extract_imports(source)
        assert "os" in imports
        assert "pathlib" in imports
        assert "json" in imports

    def test_generation_order_after_build(self) -> None:
        graph = DepGraph()
        code = {
            "base.py": "class Base: pass\n",
            "child.py": "from base import Base\nclass Child(Base): pass\n",
        }
        graph.build_from_source(code)
        order = graph.generation_order()
        assert order.index("base.py") < order.index("child.py")


# =========================================================================
# KnownBugs
# =========================================================================


class TestKnownBugs:
    def setup_method(self) -> None:
        self.scanner = KnownBugs()

    def test_detect_pyyaml_date(self) -> None:
        code = 'data = yaml.safe_load("2024-01-01")\n'
        warnings = self.scanner.scan({"config.py": code})
        assert any(w.bug_id == "pyyaml-date-coerce" for w in warnings)

    def test_detect_subprocess_shell_true(self) -> None:
        code = 'subprocess.run("ls -la", shell=True)\n'
        warnings = self.scanner.scan({"run.py": code})
        assert any(w.bug_id == "subprocess-shell-injection" for w in warnings)

    def test_detect_requests_no_timeout(self) -> None:
        code = 'resp = requests.get("http://example.com")\n'
        warnings = self.scanner.scan({"fetch.py": code})
        assert any(w.bug_id == "requests-no-timeout" for w in warnings)

    def test_detect_mutable_default(self) -> None:
        code = "def append_item(items=[]):\n    items.append(1)\n"
        warnings = self.scanner.scan({"funcs.py": code})
        assert any(w.bug_id == "mutable-default-arg" for w in warnings)

    def test_detect_bare_except(self) -> None:
        code = "try:\n    x()\nexcept:\n    pass\n"
        warnings = self.scanner.scan({"handler.py": code})
        assert any(w.bug_id == "bare-except" for w in warnings)

    def test_detect_datetime_naive(self) -> None:
        code = "from datetime import datetime\nnow = datetime.now()\n"
        warnings = self.scanner.scan({"time.py": code})
        assert any(w.bug_id == "datetime-naive-comparison" for w in warnings)

    def test_detect_os_environ_keyerror(self) -> None:
        code = "key = os.environ['SECRET_KEY']\n"
        warnings = self.scanner.scan({"config.py": code})
        assert any(w.bug_id == "os-environ-keyerror" for w in warnings)

    def test_clean_code_no_warnings(self) -> None:
        code = textwrap.dedent("""\
            from __future__ import annotations
            import json
            
            def parse(data: str) -> dict:
                return json.loads(data)
        """)
        warnings = self.scanner.scan({"clean.py": code})
        # Should not trigger pyyaml/subprocess/etc
        severe = [w for w in warnings if w.severity == "error"]
        assert len(severe) == 0

    def test_add_custom_bug(self) -> None:
        custom = BugPattern(
            id="custom-test",
            library="test",
            title="Test bug",
            description="A test bug pattern",
            regex=r"NEVER_DO_THIS",
            fix_suggestion="Don't do it.",
        )
        self.scanner.add_bug(custom)
        warnings = self.scanner.scan({"bad.py": "NEVER_DO_THIS()\n"})
        assert any(w.bug_id == "custom-test" for w in warnings)

    def test_get_by_library(self) -> None:
        python_bugs = self.scanner.get_by_library("python")
        assert len(python_bugs) >= 3  # shebang, fstring, open, mutable default, bare except

    def test_scan_multiple_files(self) -> None:
        files = {
            "a.py": 'subprocess.run("ls", shell=True)\n',
            "b.py": "x = 1\n",
        }
        warnings = self.scanner.scan(files)
        assert all(w.file_path == "a.py" for w in warnings if w.bug_id == "subprocess-shell-injection")

    def test_builtin_count(self) -> None:
        """At least 15 built-in bug patterns as required."""
        assert len(self.scanner.bugs) >= 15


# =========================================================================
# ErrorMemory
# =========================================================================


class TestErrorMemory:
    def test_record_and_retrieve(self) -> None:
        mem = ErrorMemory()
        mem.record("ImportError: no module named foo", "pip install foo", library="foo")
        fix = mem.get_known_fix("ImportError: no module named foo")
        assert fix == "pip install foo"

    def test_unknown_error_returns_none(self) -> None:
        mem = ErrorMemory()
        assert mem.get_known_fix("never seen this") is None

    def test_common_errors_by_library(self) -> None:
        mem = ErrorMemory()
        mem.record("err1", "fix1", library="requests")
        mem.record("err2", "fix2", library="requests")
        mem.record("err3", "fix3", library="pandas")
        common = mem.get_common_errors("requests")
        assert len(common) == 2

    def test_count_increments(self) -> None:
        mem = ErrorMemory()
        mem.record("same error", "fix1", library="x")
        mem.record("same error", "fix2", library="x")
        all_errs = mem.get_all()
        entry = [e for e in all_errs if e.error == "same error"][0]
        assert entry.count == 2
        assert entry.fix == "fix2"  # latest fix wins

    def test_sqlite_persistence(self, tmp_path) -> None:
        db_path = str(tmp_path / "test_errors.db")
        mem1 = ErrorMemory(db_path=db_path)
        mem1.record("SegFault", "check pointers", library="ctypes")
        mem1.close()

        mem2 = ErrorMemory(db_path=db_path)
        fix = mem2.get_known_fix("SegFault")
        assert fix == "check pointers"
        mem2.close()

    def test_get_all(self) -> None:
        mem = ErrorMemory()
        mem.record("e1", "f1", library="a")
        mem.record("e2", "f2", library="b")
        assert len(mem.get_all()) == 2


# =========================================================================
# CodeReviewer
# =========================================================================


class TestCodeReviewer:
    @pytest.fixture()
    def mock_reviewer_response(self) -> str:
        return json.dumps([
            {
                "line": 5,
                "severity": "bug",
                "description": "Division by zero possible",
                "suggested_fix": "Add zero check before division",
            },
            {
                "line": 12,
                "severity": "warning",
                "description": "Missing error handling",
                "suggested_fix": "Wrap in try/except",
            },
        ])

    async def test_review_file(self, mock_reviewer_response: str) -> None:
        with patch("cortex.coding.reviewer.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "choices": [{"message": {"content": mock_reviewer_response}}],
            }
            mock_resp.raise_for_status = MagicMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            reviewer = CodeReviewer("http://localhost:8081")
            result = await reviewer.review_file("def div(a, b): return a / b\n", "spec")
            assert len(result.issues) == 2
            assert result.issues[0].severity == "bug"
            assert not result.approved  # has a bug

    async def test_review_file_clean(self) -> None:
        with patch("cortex.coding.reviewer.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "choices": [{"message": {"content": "[]"}}],
            }
            mock_resp.raise_for_status = MagicMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            reviewer = CodeReviewer("http://localhost:8081")
            result = await reviewer.review_file("x = 1", "spec")
            assert result.approved
            assert len(result.issues) == 0

    def test_parse_issues_json(self) -> None:
        raw = json.dumps([{"line": 1, "severity": "bug", "description": "bad", "suggested_fix": "fix"}])
        issues = CodeReviewer._parse_issues(raw)
        assert len(issues) == 1
        assert issues[0].severity == "bug"

    def test_parse_issues_with_fences(self) -> None:
        raw = '```json\n[{"severity": "warning", "description": "check"}]\n```'
        issues = CodeReviewer._parse_issues(raw)
        assert len(issues) == 1

    def test_parse_issues_malformed(self) -> None:
        issues = CodeReviewer._parse_issues("This is not JSON at all")
        assert issues == []

    def test_parse_issues_empty(self) -> None:
        issues = CodeReviewer._parse_issues("[]")
        assert issues == []

    async def test_review_project(self) -> None:
        with patch("cortex.coding.reviewer.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "choices": [{"message": {"content": "[]"}}],
            }
            mock_resp.raise_for_status = MagicMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            reviewer = CodeReviewer("http://localhost:8081")
            results = await reviewer.review_project(
                {"main.py": "x = 1", "utils.py": "y = 2"},
                contract="no contract",
            )
            # Empty issues means no ReviewResult entries
            assert isinstance(results, list)


# =========================================================================
# TestFirstGenerator
# =========================================================================


class TestTestFirstGenerator:
    def test_validate_tests_runnable_valid(self) -> None:
        gen = TestFirstGenerator()
        tests = {
            "tests/test_main.py": textwrap.dedent("""\
                from __future__ import annotations
                def test_add():
                    assert 1 + 1 == 2
            """),
        }
        errors = gen.validate_tests_runnable(tests)
        assert errors == []

    def test_validate_tests_runnable_syntax_error(self) -> None:
        gen = TestFirstGenerator()
        tests = {"tests/test_bad.py": "def test_bad(\n"}
        errors = gen.validate_tests_runnable(tests)
        assert len(errors) == 1
        assert "SyntaxError" in errors[0]

    def test_test_path_for(self) -> None:
        assert TestFirstGenerator._test_path_for("engine.py") == "tests/test_engine.py"
        assert TestFirstGenerator._test_path_for("src/models.py") == "tests/test_models.py"

    def test_infer_files_from_contract(self) -> None:
        contract = "Files: `models.py`, `engine.py`, `utils.py`"
        files = TestFirstGenerator._infer_files_from_contract(contract)
        assert "models.py" in files
        assert "engine.py" in files

    def test_infer_files_default(self) -> None:
        files = TestFirstGenerator._infer_files_from_contract("")
        assert files == ["main.py"]

    def test_extract_code_with_fences(self) -> None:
        response = '```python\ndef hello():\n    pass\n```'
        code = TestFirstGenerator._extract_code(response)
        assert code == "def hello():\n    pass"

    def test_extract_code_plain(self) -> None:
        response = "def hello():\n    pass"
        code = TestFirstGenerator._extract_code(response)
        assert code == "def hello():\n    pass"

    async def test_generate_tests_mocked(self) -> None:
        gen = TestFirstGenerator()
        test_code = "def test_something(): assert True"
        with patch("cortex.coding.test_first.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "choices": [{"message": {"content": test_code}}],
            }
            mock_resp.raise_for_status = MagicMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            results = await gen.generate_tests(
                spec="Build a calculator",
                contract="def add(a, b): ...",
                generator_url="http://localhost:8080",
                source_files=["calc.py"],
            )
            assert "tests/test_calc.py" in results


# =========================================================================
# DocFetcher
# =========================================================================


class TestDocFetcher:
    def setup_method(self) -> None:
        self.fetcher = DocFetcher()

    async def test_cache_hit(self) -> None:
        # Pre-populate cache
        self.fetcher._cache["testlib"] = type(
            "DocEntry", (), {"library": "testlib", "content": "cached docs", "source": "test", "fetched_at": __import__("time").time()}
        )()
        result = await self.fetcher.fetch_one("testlib")
        assert result == "cached docs"

    async def test_installed_package(self) -> None:
        # json is always installed
        result = await self.fetcher.fetch_one("json")
        assert "json" in result.lower()
        assert self.fetcher.cache_size >= 1

    async def test_stub_fallback(self) -> None:
        result = await self.fetcher.fetch_one("nonexistent_library_xyz_12345")
        assert "No documentation found" in result

    async def test_fetch_multiple(self) -> None:
        docs = await self.fetcher.fetch(["json", "os"])
        assert "json" in docs
        assert "os" in docs

    def test_clear_cache(self) -> None:
        self.fetcher._cache["x"] = MagicMock()
        self.fetcher.clear_cache()
        assert self.fetcher.cache_size == 0

    async def test_pypi_fallback_on_network_error(self) -> None:
        """When both installed and PyPI fail, we get stub."""
        with patch.object(self.fetcher, "_from_installed", return_value=None):
            with patch.object(self.fetcher, "_from_pypi", return_value=None):
                result = await self.fetcher.fetch_one("fake_lib")
                assert "No documentation found" in result


# =========================================================================
# Pipeline helpers
# =========================================================================


class TestStripFences:
    def test_python_fences(self) -> None:
        text = '```python\nx = 1\n```'
        assert _strip_fences(text) == "x = 1"

    def test_generic_fences(self) -> None:
        text = '```\nx = 1\n```'
        assert _strip_fences(text) == "x = 1"

    def test_no_fences(self) -> None:
        text = "x = 1"
        assert _strip_fences(text) == "x = 1"


class TestParseMultiFileResponse:
    def test_basic(self) -> None:
        raw = textwrap.dedent("""\
            Here are the fixes:
            ### main.py
            ```python
            x = 1
            ```
            ### utils.py
            ```python
            y = 2
            ```
        """)
        result = CodingPipeline._parse_multi_file_response(raw)
        assert "main.py" in result
        assert "utils.py" in result
        assert result["main.py"] == "x = 1"

    def test_empty(self) -> None:
        result = CodingPipeline._parse_multi_file_response("no files here")
        assert result == {}


# =========================================================================
# Integration: CodingPipeline (mocked LLM endpoints)
# =========================================================================


class TestCodingPipelineIntegration:
    def _mock_llm_call(self, return_value: str):
        """Patch _llm_call to return a fixed value."""
        return patch("cortex.coding.pipeline._llm_call", new_callable=AsyncMock, return_value=return_value)

    async def test_pipeline_instantiation(self) -> None:
        pipeline = CodingPipeline(
            generator_url="http://localhost:8080",
            reviewer_url="http://localhost:8081",
        )
        assert pipeline.generator == "http://localhost:8080"
        assert pipeline._reviewer is not None

    async def test_pipeline_without_reviewer(self) -> None:
        pipeline = CodingPipeline(generator_url="http://localhost:8080")
        assert pipeline._reviewer is None

    async def test_pipeline_stages_run(self, tmp_path) -> None:
        contract = textwrap.dedent("""\
            from __future__ import annotations
            def greet(name: str) -> str: ...
        """)
        code = textwrap.dedent("""\
            from __future__ import annotations
            def greet(name: str) -> str:
                return f"Hello, {name}!"
        """)
        test_code = textwrap.dedent("""\
            from __future__ import annotations
            def test_greet():
                assert True
        """)

        with self._mock_llm_call(contract) as mock_llm:
            # The LLM will be called for contract, tests, code gen, and fix loop
            mock_llm.side_effect = [
                contract,   # Stage 4: contract
                test_code,  # Stage 5: tests (via test_first httpx call)
                code,       # Stage 6: code gen
            ]
            # Patch test_first to avoid httpx calls
            with patch.object(TestFirstGenerator, "generate_tests", new_callable=AsyncMock, return_value={"tests/test_main.py": test_code}):
                # Patch codex validation
                with patch("cortex.coding.pipeline.CodexPipeline.validate", new_callable=AsyncMock) as mock_codex:
                    mock_codex.return_value = MagicMock(valid=True, stages_passed=["syntax"], stages_failed=[])
                    # Patch pytest run
                    with patch.object(CodingPipeline, "_run_pytest", return_value=(True, "1 passed")):
                        pipeline = CodingPipeline(generator_url="http://localhost:8080")
                        spec = "Build a greeting function in main.py"
                        result = await pipeline.generate_project(spec, output_dir=str(tmp_path))

                        assert isinstance(result, ProjectResult)
                        assert len(result.stages) == 12
                        assert result.tests_passed

    async def test_pipeline_minimal_no_output_dir(self) -> None:
        contract = "def main(): ..."
        code = "def main(): pass"

        with self._mock_llm_call(contract):
            with patch.object(TestFirstGenerator, "generate_tests", new_callable=AsyncMock, return_value={}):
                with patch("cortex.coding.pipeline.CodexPipeline.validate", new_callable=AsyncMock) as mock_codex:
                    mock_codex.return_value = MagicMock(valid=True, stages_passed=["syntax"], stages_failed=[])
                    pipeline = CodingPipeline(generator_url="http://localhost:8080")
                    result = await pipeline.generate_project("Build something")
                    assert isinstance(result, ProjectResult)
                    # Without output_dir, fix loop returns 0 iterations
                    assert result.fix_iterations == 0
