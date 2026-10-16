"""Codex validation pipeline — multi-stage code validation with LLM self-correction.

Every piece of code Atlas generates flows through this pipeline before being
presented to the user:

    LLM generates code
        ↓
    Stage 1: Syntax Check (ast.parse)
        ↓ fail → feed error back to LLM → regenerate
    Stage 2: Import Check (verify all imports available)
        ↓ fail → feed error back to LLM → regenerate
    Stage 3: Static Analysis (lightweight lint checks)
        ↓ warnings → note for review
    Stage 4: Sandbox Execute (if safe/requested)
        ↓ fail → feed error + traceback back to LLM → fix
    Stage 5: LLM Review Pass (ask LLM to review its own code)
        ↓ changes → apply → re-run stages 1-4
    Stage 6: Return validated code to user
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field
from typing import Any

from cortex.tools.code_sandbox import CodeSandbox

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class CodexResult:
    """Outcome of the full Codex validation pipeline."""

    code: str
    valid: bool
    stages_passed: list[str] = field(default_factory=list)
    stages_failed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    rounds: int = 1
    original_code: str = ""
    changes_made: list[str] = field(default_factory=list)


@dataclass
class CodexReview:
    """Outcome of the LLM review pass (stage 5)."""

    reviewed_code: str
    suggestions: list[str] = field(default_factory=list)
    changed: bool = False


# ---------------------------------------------------------------------------
# Static analysis helpers (stage 3)
# ---------------------------------------------------------------------------

_BUILTIN_NAMES: frozenset[str] = frozenset({
    "print", "len", "range", "int", "float", "str", "bool", "list", "dict",
    "set", "tuple", "type", "isinstance", "issubclass", "id", "hash", "abs",
    "round", "min", "max", "sum", "sorted", "reversed", "enumerate", "zip",
    "map", "filter", "any", "all", "next", "iter", "open", "input", "repr",
    "chr", "ord", "hex", "oct", "bin", "format", "vars", "dir", "getattr",
    "setattr", "hasattr", "delattr", "callable", "super", "property",
    "staticmethod", "classmethod", "object", "Exception", "ValueError",
    "TypeError", "KeyError", "IndexError", "AttributeError", "RuntimeError",
    "StopIteration", "NotImplementedError", "FileNotFoundError", "OSError",
    "IOError", "ImportError", "ModuleNotFoundError", "NameError",
    "ZeroDivisionError", "OverflowError", "AssertionError", "True", "False",
    "None", "Ellipsis", "NotImplemented", "__name__", "__file__",
    "__import__", "breakpoint", "exec", "eval", "compile", "globals",
    "locals", "memoryview", "bytearray", "bytes", "complex", "frozenset",
    "slice", "pow", "divmod", "ascii",
})


def _collect_defined_names(tree: ast.Module) -> set[str]:
    """Collect names defined at module scope (variables, functions, classes, imports)."""
    names: set[str] = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
                elif isinstance(target, ast.Tuple):
                    for elt in target.elts:
                        if isinstance(elt, ast.Name):
                            names.add(elt.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname if alias.asname else alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname if alias.asname else alias.name)
        elif isinstance(node, ast.For):
            if isinstance(node.target, ast.Name):
                names.add(node.target.id)
        elif isinstance(node, ast.With):
            for item in node.items:
                if item.optional_vars and isinstance(item.optional_vars, ast.Name):
                    names.add(item.optional_vars.id)
    return names


def _check_unused_imports(tree: ast.Module) -> list[str]:
    """Return warnings for imports that are never referenced in the code body."""
    imported: dict[str, int] = {}
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname if alias.asname else alias.name.split(".")[0]
                imported[local] = node.lineno
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    continue
                local = alias.asname if alias.asname else alias.name
                imported[local] = node.lineno

    if not imported:
        return []

    # Collect every Name reference in the rest of the tree
    referenced: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in imported:
            referenced.add(node.id)
        elif isinstance(node, ast.Attribute):
            val = node.value
            if isinstance(val, ast.Name) and val.id in imported:
                referenced.add(val.id)

    # Imports only appear as Name in their own import statement, so subtract
    # those that are used elsewhere. A name referenced at least twice (once in
    # the import, once in usage) is fine — but our walk counts every reference
    # including the import node itself. To be safe, just check if the name
    # appears *anywhere* outside of import nodes.
    used_outside: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Name) and node.id in imported:
            used_outside.add(node.id)
        elif isinstance(node, ast.Attribute):
            val = node.value
            if isinstance(val, ast.Name) and val.id in imported:
                used_outside.add(val.id)

    warnings: list[str] = []
    for name, lineno in sorted(imported.items(), key=lambda kv: kv[1]):
        if name not in used_outside:
            warnings.append(f"Line {lineno}: import '{name}' appears unused")
    return warnings


def _check_unreachable_after_return(tree: ast.Module) -> list[str]:
    """Detect obvious unreachable code after return/raise/break/continue."""
    warnings: list[str] = []
    terminators = (ast.Return, ast.Raise, ast.Break, ast.Continue)

    for node in ast.walk(tree):
        body: list[ast.stmt] | None = None
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
        elif isinstance(node, (ast.For, ast.While, ast.If, ast.With)):
            body = node.body

        if body is None:
            continue

        for i, stmt in enumerate(body):
            if isinstance(stmt, terminators) and i < len(body) - 1:
                next_stmt = body[i + 1]
                lineno = getattr(next_stmt, "lineno", None)
                warnings.append(
                    f"Line {lineno}: unreachable code after "
                    f"{type(stmt).__name__.lower()}"
                )
    return warnings


def _check_stub_functions(tree: ast.Module) -> list[str]:
    """Detect functions whose body is only ``pass`` or ``...`` (Ellipsis)."""
    warnings: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        stmts = [
            s for s in node.body
            if not isinstance(s, ast.Expr) or not isinstance(
                getattr(s, "value", None), ast.Constant
            ) or not isinstance(s.value.value, str)  # type: ignore[union-attr]
        ]
        if len(stmts) == 1:
            s = stmts[0]
            is_pass = isinstance(s, ast.Pass)
            is_ellipsis = (
                isinstance(s, ast.Expr)
                and isinstance(s.value, ast.Constant)
                and s.value.value is ...
            )
            if is_pass or is_ellipsis:
                warnings.append(
                    f"Line {node.lineno}: function '{node.name}' is a stub (only pass/...)"
                )
    return warnings


def _check_missing_return_type_hints(tree: ast.Module) -> list[str]:
    """Warn on module-level functions missing return type annotations."""
    warnings: list[str] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.returns is None:
                warnings.append(
                    f"Line {node.lineno}: function '{node.name}' has no return type hint"
                )
    return warnings


def run_static_analysis(code: str) -> list[str]:
    """Run lightweight static analysis checks on *code*. Returns warnings."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []

    warnings: list[str] = []
    warnings.extend(_check_unused_imports(tree))
    warnings.extend(_check_unreachable_after_return(tree))
    warnings.extend(_check_stub_functions(tree))
    warnings.extend(_check_missing_return_type_hints(tree))
    return warnings


# ---------------------------------------------------------------------------
# Code extraction helper
# ---------------------------------------------------------------------------

_CODE_BLOCK_MARKERS = ("```python", "```py")


def extract_python_code(text: str) -> str:
    """Pull the first Python fenced code block out of *text*."""
    lower = text.lower()
    start = -1
    for marker in _CODE_BLOCK_MARKERS:
        idx = lower.find(marker)
        if idx != -1 and (start == -1 or idx < start):
            start = idx

    if start == -1:
        return ""

    code_start = text.index("\n", start) + 1
    end = text.find("```", code_start)
    if end == -1:
        return text[code_start:].strip()
    return text[code_start:end].strip()


# ---------------------------------------------------------------------------
# LLM review prompt
# ---------------------------------------------------------------------------

_REVIEW_SYSTEM = (
    "You are a senior Python code reviewer. Review the code below for bugs, "
    "logic errors, security issues, and correctness. If the code is correct, "
    "respond with exactly 'LGTM'. Otherwise, return ONLY the corrected code "
    "inside a ```python ... ``` block followed by a brief list of changes."
)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class CodexPipeline:
    """Multi-stage code validation with LLM self-correction."""

    def __init__(self, *, sandbox: CodeSandbox | None = None) -> None:
        self._sandbox = sandbox or CodeSandbox()

    # -- public API ---------------------------------------------------------

    async def validate(
        self,
        code: str,
        context: str = "",
    ) -> CodexResult:
        """Run stages 1-4 (no LLM) and return a :class:`CodexResult`."""
        result = CodexResult(
            code=code,
            valid=False,
            original_code=code,
        )

        # Stage 1 — Syntax
        syn = CodeSandbox.validate_syntax(code)
        if not syn.valid:
            result.stages_failed.append("syntax")
            result.warnings.append(
                f"Syntax error at line {syn.line}: {syn.error}"
            )
            return result
        result.stages_passed.append("syntax")

        # Stage 2 — Imports
        imp = CodeSandbox.check_imports(code)
        if not imp.allowed:
            result.stages_failed.append("imports")
            result.warnings.append(
                f"Blocked imports: {', '.join(imp.blocked)}"
            )
            return result
        if imp.unknown:
            result.warnings.append(
                f"Unknown imports (may fail at runtime): {', '.join(imp.unknown)}"
            )
        result.stages_passed.append("imports")

        # Stage 3 — Static analysis
        sa_warnings = run_static_analysis(code)
        if sa_warnings:
            result.warnings.extend(sa_warnings)
        result.stages_passed.append("static")

        # Stage 4 — Sandbox execute (only if looks like a complete script)
        if _should_execute(code):
            exe = await self._sandbox.execute(code)
            if exe.success:
                result.stages_passed.append("execute")
            else:
                result.stages_failed.append("execute")
                result.warnings.append(f"Execution failed: {exe.stderr}")
                return result

        result.valid = True
        result.code = code
        return result

    async def validate_and_fix(
        self,
        code: str,
        provider: Any,
        context: str = "",
        *,
        max_rounds: int = 3,
    ) -> CodexResult:
        """Run the full pipeline with LLM self-correction.

        Stages 1-4 are checked; on failure the error is fed back to the LLM
        for a fix.  After all rounds, stage 5 (LLM review) runs once and its
        result is re-validated through stages 1-4.
        """
        original_code = code
        changes: list[str] = []
        current_code = code
        final_result: CodexResult | None = None

        for rnd in range(1, max_rounds + 1):
            result = await self.validate(current_code, context=context)
            result.original_code = original_code
            result.rounds = rnd
            result.changes_made = list(changes)

            if result.valid:
                final_result = result
                break

            # Ask LLM to fix ------------------------------------------------
            error_desc = "; ".join(
                result.warnings[:5]
            )
            fix_messages = _fix_messages(current_code, error_desc, context)

            response = await provider.chat(
                fix_messages, stream=False, temperature=0.3,
            )
            content: str = (
                response.get("content", "")
                if isinstance(response, dict)
                else str(response)
            )

            fixed = extract_python_code(content)
            if not fixed:
                # LLM didn't return a code block — keep the current code
                final_result = result
                break

            changes.append(
                f"Round {rnd}: fixed {', '.join(result.stages_failed)}"
            )
            current_code = fixed
            final_result = None  # will be set next loop or after

        if final_result is None:
            # Last fix round — validate once more
            final_result = await self.validate(current_code, context=context)
            final_result.original_code = original_code
            final_result.rounds = max_rounds
            final_result.changes_made = list(changes)

        # Stage 5 — LLM review pass -----------------------------------------
        review = await self.review_with_llm(
            final_result.code, provider, context=context,
        )
        if review.changed:
            changes.append(
                f"LLM review: {'; '.join(review.suggestions[:3])}"
            )
            # Re-validate reviewed code through stages 1-4
            reviewed = await self.validate(review.reviewed_code, context=context)
            if reviewed.valid:
                reviewed.stages_passed.append("review")
                reviewed.original_code = original_code
                reviewed.rounds = final_result.rounds
                reviewed.changes_made = list(changes)
                return reviewed
            # Review introduced problems — keep the pre-review version
            final_result.warnings.append(
                "LLM review suggested changes that failed validation; "
                "keeping pre-review version"
            )
        else:
            final_result.stages_passed.append("review")

        final_result.changes_made = list(changes)
        return final_result

    async def review_with_llm(
        self,
        code: str,
        provider: Any,
        context: str = "",
    ) -> CodexReview:
        """Stage 5: ask the LLM to review its own code."""
        user_content = f"Review this code:\n\n```python\n{code}\n```"
        if context:
            user_content = f"Context: {context}\n\n{user_content}"

        messages: list[dict[str, str]] = [
            {"role": "system", "content": _REVIEW_SYSTEM},
            {"role": "user", "content": user_content},
        ]

        response = await provider.chat(
            messages, stream=False, temperature=0.3,
        )
        content: str = (
            response.get("content", "")
            if isinstance(response, dict)
            else str(response)
        )

        if not content or content.strip().upper().startswith("LGTM"):
            return CodexReview(reviewed_code=code, changed=False)

        reviewed = extract_python_code(content)
        if not reviewed or reviewed.strip() == code.strip():
            return CodexReview(reviewed_code=code, changed=False)

        # Extract suggestion lines (everything after the code block)
        suggestions: list[str] = []
        code_end = content.rfind("```")
        if code_end != -1:
            after = content[code_end + 3:].strip()
            suggestions = [
                ln.lstrip("- •*").strip()
                for ln in after.splitlines()
                if ln.strip() and not ln.strip().startswith("```")
            ]

        return CodexReview(
            reviewed_code=reviewed,
            suggestions=suggestions,
            changed=True,
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _should_execute(code: str) -> bool:
    """Decide whether the code looks like a runnable script."""
    if 'if __name__' in code:
        return True
    # Also execute if it's purely top-level statements (no function/class defs
    # dominating the body) — i.e., a simple script.
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False
    top_level = list(ast.iter_child_nodes(tree))
    if not top_level:
        return False
    defs = sum(
        1 for n in top_level
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    )
    # If more than half the top-level nodes are defs it's a library module
    return defs <= len(top_level) // 2


def _fix_messages(
    code: str,
    error: str,
    context: str,
) -> list[dict[str, str]]:
    """Build the message list for asking the LLM to fix broken code."""
    system = (
        "You are a Python expert. The user's code failed validation. "
        "Fix the code and return ONLY the corrected version inside a "
        "```python ... ``` block."
    )
    user = f"This code failed:\n\n```python\n{code}\n```\n\nErrors: {error}"
    if context:
        user = f"Context: {context}\n\n{user}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
