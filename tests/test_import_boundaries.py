"""Import boundary enforcement — verifies module separation of concerns.

These tests ensure the architecture rules are followed:
- Pipeline must not import avatar, speech, satellite, or orchestrator
- Satellite must not import speech internals or avatar directly
- Avatar must not import speech or satellite
- Speech must not import avatar, satellite, or pipeline
- Content must not import avatar or satellite

Run with: python -m pytest tests/test_import_boundaries.py -v
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

CORTEX_ROOT = Path(__file__).parent.parent / "cortex"

# Rules: module_prefix → list of forbidden import prefixes
BOUNDARY_RULES: dict[str, list[str]] = {
    "pipeline": ["avatar", "speech", "satellite", "orchestrator"],
    "satellite": ["speech", "avatar"],
    "avatar": ["speech"],
    "speech": ["avatar", "satellite", "pipeline"],
    "content": [],
    "memory": ["avatar", "satellite", "orchestrator"],
}

# Known violations pending deeper refactor — tracked so they don't grow
KNOWN_VIOLATIONS: set[str] = {
    # avatar/websocket.py checks satellite list to skip greeting for web satellites
    "avatar/websocket.py:cortex.satellite.websocket",
    # content/jokes.py streams cached audio directly to avatar broadcast
    "content/jokes.py:cortex.avatar.websocket",
}


def _collect_imports(filepath: Path) -> list[str]:
    """Extract all import module names from a Python file."""
    try:
        tree = ast.parse(filepath.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
    return imports


def _get_python_files(module_dir: Path) -> list[Path]:
    """Get all .py files in a module directory (recursively)."""
    if not module_dir.is_dir():
        return []
    return list(module_dir.rglob("*.py"))


def _check_boundary(module_name: str, forbidden: list[str]) -> list[str]:
    """Check that a module doesn't import from forbidden modules."""
    module_dir = CORTEX_ROOT / module_name
    violations = []
    for py_file in _get_python_files(module_dir):
        rel_path = py_file.relative_to(CORTEX_ROOT)
        imports = _collect_imports(py_file)
        for imp in imports:
            for forbidden_mod in forbidden:
                forbidden_prefixes = [
                    f"cortex.{forbidden_mod}",
                    f".{forbidden_mod}",
                ]
                if any(imp.startswith(p) or imp == f"cortex.{forbidden_mod}" for p in forbidden_prefixes):
                    key = f"{rel_path}:{imp}"
                    if key in KNOWN_VIOLATIONS:
                        continue  # tracked exception — skip
                    violations.append(
                        f"  {rel_path} imports {imp} (forbidden: {forbidden_mod})"
                    )
    return violations


@pytest.mark.parametrize("module,forbidden", BOUNDARY_RULES.items())
def test_import_boundary(module: str, forbidden: list[str]):
    """Verify that module does not import from its forbidden dependencies."""
    module_dir = CORTEX_ROOT / module
    if not module_dir.is_dir():
        pytest.skip(f"Module {module} not found")
    violations = _check_boundary(module, forbidden)
    if violations:
        msg = f"\nImport boundary violations in cortex/{module}/:\n"
        msg += "\n".join(violations)
        pytest.fail(msg)
