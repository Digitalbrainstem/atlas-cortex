"""Git operations use isolated repositories; source inspection uses the checkout."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from cortex.coding.git_context import (
    DiffInfo,
    EditContext,
    GitContext,
    GitStatus,
    NewFileContext,
    _extract_imports,
    _extract_signatures,
    _run_git,
)

REPO_ROOT = str(Path(__file__).resolve().parent.parent)


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "repository"
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    (repo / "sample.py").write_text("value = 1\n")
    subprocess.run(["git", "add", "sample.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "Synthetic fixture"], cwd=repo, check=True, capture_output=True)
    return str(repo)


# ---------------------------------------------------------------------------
# Helper: _run_git
# ---------------------------------------------------------------------------


class TestRunGit:
    def test_returns_stdout(self, git_repo) -> None:
        out = _run_git(["rev-parse", "--is-inside-work-tree"], git_repo)
        assert out is not None
        assert out.strip() == "true"

    def test_returns_none_on_failure(self) -> None:
        out = _run_git(["log", "--oneline", "-1", "nonexistent_ref_xyz"], REPO_ROOT)
        assert out is None

    def test_returns_none_when_git_missing(self) -> None:
        with patch("cortex.coding.git_context.subprocess.run", side_effect=FileNotFoundError):
            assert _run_git(["status"], REPO_ROOT) is None

    def test_returns_none_on_timeout(self) -> None:
        with patch(
            "cortex.coding.git_context.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="git", timeout=1),
        ):
            assert _run_git(["status"], REPO_ROOT) is None


# ---------------------------------------------------------------------------
# Helper: _extract_signatures
# ---------------------------------------------------------------------------


class TestExtractSignatures:
    def test_class_and_methods(self) -> None:
        source = (
            "class Foo(Bar):\n"
            "    def hello(self, x: int) -> str:\n"
            "        return str(x)\n"
        )
        sigs = _extract_signatures(source)
        assert any("class Foo(Bar):" in s for s in sigs)
        assert any("def hello" in s for s in sigs)

    def test_top_level_function(self) -> None:
        source = "def compute(a: int, b: int) -> int:\n    return a + b\n"
        sigs = _extract_signatures(source)
        assert any("def compute" in s for s in sigs)

    def test_async_function(self) -> None:
        source = "async def fetch(url: str) -> bytes:\n    ...\n"
        sigs = _extract_signatures(source)
        assert any("async def fetch" in s for s in sigs)

    def test_syntax_error_returns_empty(self) -> None:
        assert _extract_signatures("def broken(") == []

    def test_empty_source(self) -> None:
        assert _extract_signatures("") == []


# ---------------------------------------------------------------------------
# Helper: _extract_imports
# ---------------------------------------------------------------------------


class TestExtractImports:
    def test_from_import(self) -> None:
        source = "from os.path import join\n"
        imports = _extract_imports(source)
        assert "from os.path import join" in imports

    def test_plain_import(self) -> None:
        source = "import json\n"
        imports = _extract_imports(source)
        assert "import json" in imports

    def test_relative_import(self) -> None:
        source = "from ..utils import helper\n"
        imports = _extract_imports(source)
        assert any("..utils" in i for i in imports)

    def test_syntax_error(self) -> None:
        assert _extract_imports("from import") == []


# ---------------------------------------------------------------------------
# GitContext — initialisation
# ---------------------------------------------------------------------------


class TestGitContextInit:
    def test_detects_git_repo(self, git_repo) -> None:
        gc = GitContext(git_repo)
        assert gc._is_git is True

    def test_non_git_directory(self, tmp_path: Path) -> None:
        gc = GitContext(str(tmp_path))
        assert gc._is_git is False


# ---------------------------------------------------------------------------
# GitContext.get_status
# ---------------------------------------------------------------------------


class TestGetStatus:
    @pytest.fixture(autouse=True)
    def context(self, git_repo) -> None:
        self.gc = GitContext(git_repo)

    def test_returns_git_status_dataclass(self) -> None:
        status = self.gc.get_status()
        assert isinstance(status, GitStatus)
        assert status.is_git_repo is True

    def test_branch_is_populated(self) -> None:
        status = self.gc.get_status()
        assert len(status.branch) > 0

    def test_non_git_repo(self, tmp_path: Path) -> None:
        gc = GitContext(str(tmp_path))
        status = gc.get_status()
        assert status.is_git_repo is False
        assert status.branch == ""

    def test_status_in_test_repo(self, tmp_path: Path) -> None:
        """Create a small git repo with known state and verify parsing."""
        repo = str(tmp_path / "repo")
        os.makedirs(repo)
        subprocess.run(["git", "init", repo], capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True, check=True)

        # Create and commit a file
        Path(repo, "hello.py").write_text("print('hi')\n")
        subprocess.run(["git", "add", "hello.py"], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True)

        # Modify file (unstaged), add a new untracked file
        Path(repo, "hello.py").write_text("print('hello')\n")
        Path(repo, "new.py").write_text("x = 1\n")

        gc = GitContext(repo)
        status = gc.get_status()
        assert "hello.py" in status.modified
        assert "new.py" in status.untracked


# ---------------------------------------------------------------------------
# GitContext.get_recent_diffs
# ---------------------------------------------------------------------------


class TestGetRecentDiffs:
    @pytest.fixture(autouse=True)
    def context(self, git_repo) -> None:
        self.gc = GitContext(git_repo)

    def test_returns_list_of_diff_info(self) -> None:
        diffs = self.gc.get_recent_diffs(3)
        assert isinstance(diffs, list)
        assert len(diffs) == 1
        assert isinstance(diffs[0], DiffInfo)
        assert len(diffs[0].sha) > 0
        assert diffs[0].message == "Synthetic fixture"

    def test_non_git_repo(self, tmp_path: Path) -> None:
        gc = GitContext(str(tmp_path))
        assert gc.get_recent_diffs() == []


# ---------------------------------------------------------------------------
# GitContext.get_file_content
# ---------------------------------------------------------------------------


class TestGetFileContent:
    def setup_method(self) -> None:
        self.gc = GitContext(REPO_ROOT)

    def test_reads_existing_file(self) -> None:
        content = self.gc.get_file_content("cortex/coding/git_context.py")
        assert "class GitContext" in content

    def test_missing_file_returns_empty(self) -> None:
        assert self.gc.get_file_content("does_not_exist.py") == ""


# ---------------------------------------------------------------------------
# GitContext.get_file_signatures
# ---------------------------------------------------------------------------


class TestGetFileSignatures:
    def setup_method(self) -> None:
        self.gc = GitContext(REPO_ROOT)

    def test_specific_file(self) -> None:
        sigs = self.gc.get_file_signatures(["cortex/coding/git_context.py"])
        assert "cortex/coding/git_context.py" in sigs
        sig_list = sigs["cortex/coding/git_context.py"]
        assert any("class GitContext" in s for s in sig_list)
        assert any("class GitStatus" in s for s in sig_list)

    def test_none_scans_all(self) -> None:
        sigs = self.gc.get_file_signatures(None)
        assert len(sigs) > 10  # atlas-cortex has many .py files

    def test_nonexistent_file_skipped(self) -> None:
        sigs = self.gc.get_file_signatures(["no_such_file.py"])
        assert sigs == {}


# ---------------------------------------------------------------------------
# GitContext.get_imports_for_file
# ---------------------------------------------------------------------------


class TestGetImportsForFile:
    def setup_method(self) -> None:
        self.gc = GitContext(REPO_ROOT)

    def test_returns_imports(self) -> None:
        imports = self.gc.get_imports_for_file("cortex/coding/git_context.py")
        assert any("ast" in i for i in imports)
        assert any("subprocess" in i for i in imports)

    def test_missing_file(self) -> None:
        assert self.gc.get_imports_for_file("nope.py") == []


# ---------------------------------------------------------------------------
# GitContext.get_importers_of
# ---------------------------------------------------------------------------


class TestGetImportersOf:
    def setup_method(self) -> None:
        self.gc = GitContext(REPO_ROOT)

    def test_finds_importers(self) -> None:
        # The coding __init__.py imports from dep_graph.py
        importers = self.gc.get_importers_of("cortex/coding/dep_graph.py")
        assert any("__init__.py" in f for f in importers) or \
               any("pipeline.py" in f for f in importers)

    def test_no_importers_for_unknown(self) -> None:
        importers = self.gc.get_importers_of("cortex/coding/git_context.py")
        # git_context.py is brand new — only __init__.py imports it now
        assert isinstance(importers, list)


# ---------------------------------------------------------------------------
# GitContext.get_project_structure
# ---------------------------------------------------------------------------


class TestGetProjectStructure:
    def setup_method(self) -> None:
        self.gc = GitContext(REPO_ROOT)

    def test_returns_dict(self) -> None:
        tree = self.gc.get_project_structure(max_depth=1)
        assert isinstance(tree, dict)
        assert "cortex" in tree

    def test_skips_hidden_dirs(self) -> None:
        tree = self.gc.get_project_structure(max_depth=1)
        assert ".git" not in tree

    def test_depth_limit(self) -> None:
        shallow = self.gc.get_project_structure(max_depth=1)
        deep = self.gc.get_project_structure(max_depth=3)
        # Deep tree has more nested dicts
        assert _count_keys(deep) >= _count_keys(shallow)


# ---------------------------------------------------------------------------
# GitContext.build_context_for_edit
# ---------------------------------------------------------------------------


class TestBuildContextForEdit:
    def setup_method(self) -> None:
        self.gc = GitContext(REPO_ROOT)

    def test_existing_file(self) -> None:
        ctx = self.gc.build_context_for_edit("cortex/coding/dep_graph.py")
        assert isinstance(ctx, EditContext)
        assert ctx.target_file == "cortex/coding/dep_graph.py"
        assert "class DepGraph" in ctx.file_content
        assert any("ast" in i for i in ctx.imports)
        assert isinstance(ctx.project_structure, dict)
        assert ctx.git_status is not None

    def test_import_signatures_populated(self) -> None:
        ctx = self.gc.build_context_for_edit("cortex/coding/pipeline.py")
        # pipeline.py imports from dep_graph, spec_parser, etc.
        assert isinstance(ctx.import_signatures, dict)


# ---------------------------------------------------------------------------
# GitContext.build_context_for_new_file
# ---------------------------------------------------------------------------


class TestBuildContextForNewFile:
    def setup_method(self) -> None:
        self.gc = GitContext(REPO_ROOT)

    def test_new_file_in_existing_dir(self) -> None:
        ctx = self.gc.build_context_for_new_file("cortex/coding/new_tool.py")
        assert isinstance(ctx, NewFileContext)
        assert ctx.target_path == "cortex/coding/new_tool.py"
        # nearby_signatures should include other files in cortex/coding/
        assert len(ctx.nearby_signatures) > 0
        assert isinstance(ctx.project_structure, dict)
        assert ctx.git_status is not None

    def test_conventions_detected(self) -> None:
        ctx = self.gc.build_context_for_new_file("cortex/coding/new_tool.py")
        assert any("__future__" in c for c in ctx.conventions)

    def test_nonexistent_dir(self) -> None:
        ctx = self.gc.build_context_for_new_file("cortex/brand_new_pkg/widget.py")
        assert isinstance(ctx, NewFileContext)
        # No nearby files, but should still have project structure
        assert ctx.nearby_signatures == {}
        assert isinstance(ctx.project_structure, dict)


# ---------------------------------------------------------------------------
# Graceful degradation (non-git directory)
# ---------------------------------------------------------------------------


class TestNonGitFallback:
    def test_all_methods_work(self, tmp_path: Path) -> None:
        """Every method should return empty/default results, never crash."""
        # Create a small Python file so AST methods have something to parse
        (tmp_path / "hello.py").write_text(
            "from __future__ import annotations\n\ndef greet() -> str:\n    return 'hi'\n"
        )
        gc = GitContext(str(tmp_path))
        assert gc._is_git is False

        status = gc.get_status()
        assert status.is_git_repo is False

        assert gc.get_recent_diffs() == []

        content = gc.get_file_content("hello.py")
        assert "greet" in content

        sigs = gc.get_file_signatures(["hello.py"])
        assert "hello.py" in sigs

        imports = gc.get_imports_for_file("hello.py")
        assert any("__future__" in i for i in imports)

        tree = gc.get_project_structure()
        assert isinstance(tree, dict)

        edit_ctx = gc.build_context_for_edit("hello.py")
        assert isinstance(edit_ctx, EditContext)

        new_ctx = gc.build_context_for_new_file("new.py")
        assert isinstance(new_ctx, NewFileContext)


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _count_keys(d: dict) -> int:
    """Recursively count keys in nested dicts."""
    n = len(d)
    for v in d.values():
        if isinstance(v, dict):
            n += _count_keys(v)
    return n
