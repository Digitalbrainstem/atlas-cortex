"""Build and query file dependency graphs."""
from __future__ import annotations

import ast
import logging
import os
from collections import defaultdict
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class FileSpec:
    """Specification for a single file in a project."""

    path: str
    description: str = ""
    imports: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Dependency graph
# ---------------------------------------------------------------------------


class DepGraph:
    """Build and query file dependency graphs.

    Supports both spec-declared dependencies and AST-inferred imports.
    """

    def __init__(self) -> None:
        self._edges: dict[str, set[str]] = defaultdict(set)  # file -> set of deps
        self._reverse: dict[str, set[str]] = defaultdict(set)  # file -> dependents

    # ------------------------------------------------------------------
    # Build from spec
    # ------------------------------------------------------------------

    def plan(self, files: list[FileSpec]) -> list[str]:
        """Topological sort of files based on declared dependencies.

        Returns file paths in safe generation order (dependencies first).
        Raises ``ValueError`` on circular dependency.
        """
        self._edges.clear()
        self._reverse.clear()

        all_paths: set[str] = set()
        for f in files:
            all_paths.add(f.path)
            for dep in f.depends_on:
                self._edges[f.path].add(dep)
                self._reverse[dep].add(f.path)

        return self._topo_sort(all_paths)

    # ------------------------------------------------------------------
    # Build from existing code
    # ------------------------------------------------------------------

    def build_from_code(self, project_dir: str) -> None:
        """Parse existing Python code to build the import graph via AST."""
        self._edges.clear()
        self._reverse.clear()

        py_files: dict[str, str] = {}
        for root, _dirs, filenames in os.walk(project_dir):
            for name in filenames:
                if name.endswith(".py"):
                    full = os.path.join(root, name)
                    rel = os.path.relpath(full, project_dir)
                    try:
                        with open(full, encoding="utf-8") as fh:
                            py_files[rel] = fh.read()
                    except OSError:
                        continue

        # Map module names to file paths
        mod_to_file: dict[str, str] = {}
        for rel in py_files:
            mod = rel.replace(os.sep, ".").removesuffix(".py")
            if mod.endswith(".__init__"):
                mod = mod.removesuffix(".__init__")
            mod_to_file[mod] = rel

        for rel, source in py_files.items():
            imports = self._extract_imports(source)
            for imp in imports:
                dep_file = mod_to_file.get(imp)
                if dep_file and dep_file != rel:
                    self._edges[rel].add(dep_file)
                    self._reverse[dep_file].add(rel)

    def build_from_source(self, files: dict[str, str]) -> None:
        """Build the graph from in-memory source dict ``{path: code}``."""
        self._edges.clear()
        self._reverse.clear()

        mod_to_file: dict[str, str] = {}
        for rel in files:
            mod = rel.replace(os.sep, "/").replace("/", ".").removesuffix(".py")
            if mod.endswith(".__init__"):
                mod = mod.removesuffix(".__init__")
            mod_to_file[mod] = rel

        for rel, source in files.items():
            imports = self._extract_imports(source)
            for imp in imports:
                dep_file = mod_to_file.get(imp)
                if dep_file and dep_file != rel:
                    self._edges[rel].add(dep_file)
                    self._reverse[dep_file].add(rel)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_dependents(self, file_path: str) -> list[str]:
        """Which files depend on (import from) *file_path*?"""
        return sorted(self._reverse.get(file_path, set()))

    def get_dependencies(self, file_path: str) -> list[str]:
        """Which files does *file_path* depend on?"""
        return sorted(self._edges.get(file_path, set()))

    def generation_order(self) -> list[str]:
        """Return all known files in topological order."""
        all_paths = set(self._edges.keys()) | set(self._reverse.keys())
        return self._topo_sort(all_paths)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_imports(source: str) -> list[str]:
        """Extract top-level module names from Python source via AST."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []

        modules: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    modules.append(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    modules.append(node.module.split(".")[0])
        return modules

    def _topo_sort(self, nodes: set[str]) -> list[str]:
        """Kahn's algorithm for topological sort."""
        in_degree: dict[str, int] = {n: 0 for n in nodes}
        for node in nodes:
            for dep in self._edges.get(node, set()):
                if dep in in_degree:
                    in_degree[node] = in_degree.get(node, 0)  # ensure key

        # Recount properly
        in_degree = {n: 0 for n in nodes}
        for node in nodes:
            for dep in self._edges.get(node, set()):
                if dep in nodes:
                    in_degree[node] += 1

        # Wait — in_degree should count how many deps *I* still need, i.e.
        # for each node, how many of its _edges targets are in the set.
        # Actually let's reconsider: _edges[A] = {B} means A depends on B.
        # So B should come before A. In Kahn's, in_degree[A] = number of
        # nodes that A depends on (that are still in the set).
        queue: list[str] = [n for n, d in in_degree.items() if d == 0]
        queue.sort()  # deterministic
        result: list[str] = []

        while queue:
            node = queue.pop(0)
            result.append(node)
            for dependent in sorted(self._reverse.get(node, set())):
                if dependent not in in_degree:
                    continue
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        if len(result) != len(nodes):
            missing = nodes - set(result)
            raise ValueError(f"Circular dependency detected among: {sorted(missing)}")

        return result
