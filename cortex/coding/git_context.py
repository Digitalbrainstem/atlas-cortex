"""Git-aware code context for the coding pipeline.

Reads actual codebase state (git status, diffs, file structure, AST
signatures) so the coding agent generates/edits code that fits the
real project — not a hallucinated version of it.

Uses subprocess to call git commands directly (no gitpython dependency).
Falls back gracefully when not inside a git repository.
"""
from __future__ import annotations

import ast
import logging
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class GitStatus:
    """Snapshot of ``git status`` output."""

    modified: list[str] = field(default_factory=list)
    staged: list[str] = field(default_factory=list)
    untracked: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    branch: str = ""
    is_git_repo: bool = True


@dataclass
class DiffInfo:
    """One commit's diff summary."""

    sha: str = ""
    author: str = ""
    date: str = ""
    message: str = ""
    files_changed: list[str] = field(default_factory=list)
    diff_text: str = ""


@dataclass
class EditContext:
    """Everything the coding agent needs to edit an existing file."""

    target_file: str = ""
    file_content: str = ""
    imports: list[str] = field(default_factory=list)
    imported_by: list[str] = field(default_factory=list)
    import_signatures: dict[str, list[str]] = field(default_factory=dict)
    importer_signatures: dict[str, list[str]] = field(default_factory=dict)
    recent_diffs: list[DiffInfo] = field(default_factory=list)
    project_structure: dict[str, object] = field(default_factory=dict)
    git_status: GitStatus | None = None


@dataclass
class NewFileContext:
    """Everything the coding agent needs to create a new file."""

    target_path: str = ""
    project_structure: dict[str, object] = field(default_factory=dict)
    nearby_signatures: dict[str, list[str]] = field(default_factory=dict)
    similar_files: dict[str, list[str]] = field(default_factory=dict)
    conventions: list[str] = field(default_factory=list)
    git_status: GitStatus | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SKIP_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "node_modules",
    ".tox", ".eggs", "*.egg-info", "dist", "build",
}


def _run_git(args: list[str], cwd: str, *, timeout: float = 15.0) -> str | None:
    """Run a git command and return stdout, or *None* on failure."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode != 0:
            return None
        return proc.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        log.debug("git %s failed: %s", " ".join(args), exc)
        return None


def _extract_signatures(source: str) -> list[str]:
    """Return class/function signature lines from Python source via AST."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    # Build a set of function/method node ids owned by classes so we can
    # distinguish top-level functions from methods.
    class_method_ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    class_method_ids.add(id(item))

    sigs: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            bases = ", ".join(
                ast.unparse(b) for b in node.bases
            )
            base_str = f"({bases})" if bases else ""
            sigs.append(f"class {node.name}{base_str}:")
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    prefix = "async def" if isinstance(item, ast.AsyncFunctionDef) else "def"
                    args_str = ast.unparse(item.args)
                    ret = f" -> {ast.unparse(item.returns)}" if item.returns else ""
                    sigs.append(f"    {prefix} {item.name}({args_str}){ret}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if id(node) not in class_method_ids:
                prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
                args_str = ast.unparse(node.args)
                ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""
                sigs.append(f"{prefix} {node.name}({args_str}){ret}")

    return sigs


def _extract_imports(source: str) -> list[str]:
    """Return raw import lines from Python source via AST."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    imports: list[str] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = ", ".join(a.name for a in node.names)
            level = "." * node.level
            imports.append(f"from {level}{module} import {names}")
    return imports


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------


class GitContext:
    """Read git state to provide real codebase context for code generation.

    All methods are synchronous (git and filesystem calls are fast).
    Gracefully degrades when the working directory is not a git repo.
    """

    def __init__(self, repo_path: str = ".") -> None:
        self.repo_path = str(Path(repo_path).resolve())
        self._is_git = self._check_git()

    # ------------------------------------------------------------------
    # Git queries
    # ------------------------------------------------------------------

    def get_status(self) -> GitStatus:
        """``git status`` — modified, staged, untracked, deleted files."""
        status = GitStatus(is_git_repo=self._is_git)
        if not self._is_git:
            return status

        # Current branch
        branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], self.repo_path)
        if branch:
            status.branch = branch.strip()

        # Porcelain v1 gives stable, parseable output
        raw = _run_git(["status", "--porcelain"], self.repo_path)
        if raw is None:
            return status

        for line in raw.splitlines():
            if len(line) < 4:
                continue
            index_flag = line[0]
            work_flag = line[1]
            path = line[3:]

            # Handle renames: "R  old -> new"
            if " -> " in path:
                path = path.split(" -> ", 1)[1]

            if index_flag in "MARC":
                status.staged.append(path)
            if work_flag == "M":
                status.modified.append(path)
            if work_flag == "D" or index_flag == "D":
                status.deleted.append(path)
            if index_flag == "?" and work_flag == "?":
                status.untracked.append(path)

        return status

    def get_recent_diffs(self, n_commits: int = 5) -> list[DiffInfo]:
        """Recent commit diffs — what changed recently."""
        if not self._is_git:
            return []

        raw = _run_git(
            ["log", f"-{n_commits}", "--pretty=format:%H|%an|%ai|%s", "--name-only"],
            self.repo_path,
        )
        if not raw:
            return []

        diffs: list[DiffInfo] = []
        current: DiffInfo | None = None

        for line in raw.splitlines():
            if "|" in line and len(line.split("|")) >= 4:
                parts = line.split("|", 3)
                current = DiffInfo(
                    sha=parts[0],
                    author=parts[1],
                    date=parts[2],
                    message=parts[3],
                )
                diffs.append(current)
            elif line.strip() and current is not None:
                current.files_changed.append(line.strip())

        # Fetch actual diff text per commit (truncated for context)
        for d in diffs:
            diff_raw = _run_git(
                ["diff", f"{d.sha}~1", d.sha, "--stat"],
                self.repo_path,
            )
            if diff_raw:
                d.diff_text = diff_raw[:2000]

        return diffs

    def get_file_content(self, path: str) -> str:
        """Read a specific file relative to the repo root."""
        full = os.path.join(self.repo_path, path)
        try:
            with open(full, encoding="utf-8") as fh:
                return fh.read()
        except (OSError, UnicodeDecodeError) as exc:
            log.debug("Cannot read %s: %s", full, exc)
            return ""

    # ------------------------------------------------------------------
    # AST queries
    # ------------------------------------------------------------------

    def get_file_signatures(
        self, file_paths: list[str] | None = None,
    ) -> dict[str, list[str]]:
        """Extract class/function signatures from Python files via AST.

        If *file_paths* is ``None``, scans every ``.py`` file in the repo.
        """
        if file_paths is None:
            file_paths = self._find_py_files()

        result: dict[str, list[str]] = {}
        for fp in file_paths:
            source = self.get_file_content(fp)
            if source:
                sigs = _extract_signatures(source)
                if sigs:
                    result[fp] = sigs
        return result

    def get_imports_for_file(self, path: str) -> list[str]:
        """What does this file import? AST-based."""
        source = self.get_file_content(path)
        if not source:
            return []
        return _extract_imports(source)

    def get_importers_of(self, module_path: str) -> list[str]:
        """Which files import from *module_path*? Reverse dependency search.

        *module_path* is a file path like ``cortex/coding/dep_graph.py``.
        We convert it to a module name and search imports in all ``.py`` files.
        """
        # Build target module names (both dotted and bare)
        mod = module_path.replace(os.sep, ".").replace("/", ".").removesuffix(".py")
        if mod.endswith(".__init__"):
            mod = mod.removesuffix(".__init__")
        targets = {mod}
        # Also match any sub-component, e.g. "cortex.coding.dep_graph" matches
        # "from cortex.coding.dep_graph import DepGraph"
        parts = mod.split(".")
        if len(parts) > 1:
            targets.add(parts[-1])  # bare module name

        importers: list[str] = []
        for fp in self._find_py_files():
            if fp == module_path:
                continue
            source = self.get_file_content(fp)
            if not source:
                continue
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            for node in ast.iter_child_nodes(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if any(alias.name == t or alias.name.startswith(t + ".") for t in targets):
                            importers.append(fp)
                            break
                elif isinstance(node, ast.ImportFrom):
                    full_mod = ("." * node.level) + (node.module or "")
                    if any(full_mod == t or full_mod.startswith(t + ".") for t in targets):
                        importers.append(fp)
                        break
            else:
                continue
            # If inner break fired, continue outer loop already appended
        return sorted(set(importers))

    # ------------------------------------------------------------------
    # Project structure
    # ------------------------------------------------------------------

    def get_project_structure(self, max_depth: int = 4) -> dict[str, object]:
        """Directory tree of the project (dict of nested dicts / file lists)."""
        return self._walk_tree(self.repo_path, max_depth, 0)

    # ------------------------------------------------------------------
    # Composite context builders
    # ------------------------------------------------------------------

    def build_context_for_edit(self, target_file: str) -> EditContext:
        """Build full context needed to edit an existing file."""
        ctx = EditContext(target_file=target_file)
        ctx.file_content = self.get_file_content(target_file)
        ctx.imports = self.get_imports_for_file(target_file)
        ctx.git_status = self.get_status()

        # Files this file imports from (resolve to repo-relative paths)
        dep_paths = self._resolve_import_paths(ctx.imports)
        ctx.imported_by = dep_paths
        ctx.import_signatures = self.get_file_signatures(dep_paths) if dep_paths else {}

        # Files that import this file
        importers = self.get_importers_of(target_file)
        ctx.importer_signatures = self.get_file_signatures(importers) if importers else {}

        # Recent diffs touching this file
        all_diffs = self.get_recent_diffs(10)
        ctx.recent_diffs = [
            d for d in all_diffs if target_file in d.files_changed
        ]

        ctx.project_structure = self.get_project_structure(max_depth=2)
        return ctx

    def build_context_for_new_file(self, target_path: str) -> NewFileContext:
        """Build context for generating a brand-new file."""
        ctx = NewFileContext(target_path=target_path)
        ctx.project_structure = self.get_project_structure(max_depth=2)
        ctx.git_status = self.get_status()

        # Nearby files — same directory
        parent = str(Path(target_path).parent)
        nearby: list[str] = []
        full_parent = os.path.join(self.repo_path, parent)
        if os.path.isdir(full_parent):
            for name in sorted(os.listdir(full_parent)):
                if name.endswith(".py"):
                    nearby.append(os.path.join(parent, name))
        ctx.nearby_signatures = self.get_file_signatures(nearby) if nearby else {}

        # Similar files — same filename stem elsewhere in the repo
        stem = Path(target_path).stem
        similar_paths = [
            fp for fp in self._find_py_files()
            if stem in Path(fp).stem and fp != target_path
        ][:10]
        ctx.similar_files = self.get_file_signatures(similar_paths) if similar_paths else {}

        # Conventions from nearby __init__.py and existing files
        ctx.conventions = self._detect_conventions(nearby)
        return ctx

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_git(self) -> bool:
        out = _run_git(["rev-parse", "--is-inside-work-tree"], self.repo_path)
        return out is not None and out.strip() == "true"

    def _find_py_files(self) -> list[str]:
        """All .py files in the repo (relative paths), respecting skip dirs."""
        result: list[str] = []
        for root, dirs, files in os.walk(self.repo_path):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for name in files:
                if name.endswith(".py"):
                    full = os.path.join(root, name)
                    result.append(os.path.relpath(full, self.repo_path))
        return sorted(result)

    def _walk_tree(self, base: str, max_depth: int, depth: int) -> dict[str, object]:
        """Recursive directory walk returning nested structure."""
        tree: dict[str, object] = {}
        try:
            entries = sorted(os.listdir(base))
        except OSError:
            return tree

        dirs: list[str] = []
        file_list: list[str] = []
        for entry in entries:
            if entry.startswith(".") or entry in _SKIP_DIRS:
                continue
            full = os.path.join(base, entry)
            if os.path.isdir(full):
                dirs.append(entry)
            else:
                file_list.append(entry)

        if file_list:
            tree["_files"] = file_list
        for d in dirs:
            if depth + 1 >= max_depth:
                tree[d] = {}  # record directory name without recursing
            else:
                subtree = self._walk_tree(os.path.join(base, d), max_depth, depth + 1)
                tree[d] = subtree
        return tree

    def _resolve_import_paths(self, imports: list[str]) -> list[str]:
        """Convert import strings to repo-relative file paths (best-effort)."""
        paths: list[str] = []
        for imp in imports:
            # Parse "from cortex.coding.dep_graph import DepGraph" → "cortex/coding/dep_graph"
            parts = imp.split()
            if parts[0] == "from" and len(parts) >= 4:
                module = parts[1]
            elif parts[0] == "import" and len(parts) >= 2:
                module = parts[1].split(",")[0]
            else:
                continue

            # Skip stdlib / third-party (heuristic: first component exists as dir)
            top = module.split(".")[0]
            if not os.path.isdir(os.path.join(self.repo_path, top)):
                continue

            # Try as a file
            candidate = module.replace(".", os.sep) + ".py"
            if os.path.isfile(os.path.join(self.repo_path, candidate)):
                paths.append(candidate)
                continue

            # Try as a package
            candidate_init = os.path.join(module.replace(".", os.sep), "__init__.py")
            if os.path.isfile(os.path.join(self.repo_path, candidate_init)):
                paths.append(candidate_init)

        return sorted(set(paths))

    def _detect_conventions(self, file_paths: list[str]) -> list[str]:
        """Detect coding conventions from a sample of files."""
        conventions: list[str] = []
        seen_future = False
        seen_dataclass = False
        seen_logging = False
        seen_abc = False

        for fp in file_paths[:5]:
            source = self.get_file_content(fp)
            if not source:
                continue
            if "from __future__ import annotations" in source:
                seen_future = True
            if "@dataclass" in source:
                seen_dataclass = True
            if "logging.getLogger" in source:
                seen_logging = True
            if "abc.ABC" in source or "abc.abstractmethod" in source:
                seen_abc = True

        if seen_future:
            conventions.append("from __future__ import annotations")
        if seen_dataclass:
            conventions.append("@dataclass for data types")
        if seen_logging:
            conventions.append("logging.getLogger(__name__) for module logger")
        if seen_abc:
            conventions.append("abc.ABC + @abc.abstractmethod for interfaces")
        return conventions
