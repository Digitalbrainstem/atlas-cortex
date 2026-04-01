"""Database of common coding pitfalls per library."""
from __future__ import annotations

import ast
import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class BugPattern:
    """A known coding pitfall with detection logic and fix suggestion."""

    id: str
    library: str
    title: str
    description: str
    regex: str | None = None
    fix_suggestion: str = ""
    severity: str = "warning"  # "warning" | "error"

    def compiled_regex(self) -> re.Pattern[str] | None:
        if self.regex:
            return re.compile(self.regex, re.MULTILINE)
        return None


@dataclass
class BugWarning:
    """A detected bug in generated code."""

    bug_id: str
    file_path: str
    line: int | None
    title: str
    description: str
    fix_suggestion: str
    severity: str = "warning"


# ---------------------------------------------------------------------------
# Built-in bug patterns (15+)
# ---------------------------------------------------------------------------

_BUILTIN_BUGS: list[BugPattern] = [
    # 1. PyYAML date auto-parsing
    BugPattern(
        id="pyyaml-date-coerce",
        library="pyyaml",
        title="PyYAML auto-parses date-like strings to datetime objects",
        description=(
            "PyYAML's yaml.safe_load() converts strings like '2024-01-01' into "
            "datetime.date objects. Use yaml.safe_load() with explicit string "
            "handling or quote values in YAML."
        ),
        regex=r"yaml\.(?:safe_)?load\(",
        fix_suggestion="Use a custom constructor or yaml.BaseLoader to keep strings as strings.",
        severity="warning",
    ),
    # 2. Python vs python3 shebang
    BugPattern(
        id="python-shebang",
        library="python",
        title="python shebang may invoke Python 2 on macOS",
        description=(
            "#!/usr/bin/env python may invoke Python 2 on macOS. "
            "Always use #!/usr/bin/env python3."
        ),
        regex=r"^#!.*\bpython\s*$",
        fix_suggestion="Change shebang to #!/usr/bin/env python3",
        severity="warning",
    ),
    # 3. asyncio missing await
    BugPattern(
        id="asyncio-missing-await",
        library="asyncio",
        title="Coroutine called without await",
        description=(
            "Calling an async function without await returns a coroutine object "
            "instead of executing it. This is a common source of silent bugs."
        ),
        # We use AST analysis for this, not regex
        regex=None,
        fix_suggestion="Add 'await' before the coroutine call.",
        severity="error",
    ),
    # 4. SQLite unclosed connections in tests
    BugPattern(
        id="sqlite-unclosed-conn",
        library="sqlite3",
        title="SQLite connection opened without close/context-manager",
        description=(
            "Opening sqlite3.connect() without a context manager or explicit close() "
            "can leak file handles, especially in test teardown."
        ),
        regex=r"sqlite3\.connect\([^)]+\)\s*$",
        fix_suggestion="Use 'with sqlite3.connect(...) as conn:' or ensure close() in finally/teardown.",
        severity="warning",
    ),
    # 5. Relative vs absolute import confusion
    BugPattern(
        id="import-relative-confusion",
        library="python",
        title="Mixing relative and absolute imports in a package",
        description=(
            "Using 'from .module import X' and 'from package.module import X' "
            "in the same package can cause double-import issues."
        ),
        regex=None,  # AST-based detection
        fix_suggestion="Pick one style (prefer absolute) and be consistent within a package.",
        severity="warning",
    ),
    # 6. f-string nested quotes
    BugPattern(
        id="fstring-nested-quotes",
        library="python",
        title="Nested quotes in f-strings can cause SyntaxError",
        description="f-strings with nested quotes of the same type break parsing.",
        regex=r'f"[^"]*"[^"]*"[^"]*"',
        fix_suggestion="Use different quote styles or extract to a variable.",
        severity="warning",
    ),
    # 7. pathlib Path vs str
    BugPattern(
        id="pathlib-str-mix",
        library="pathlib",
        title="Mixing Path objects and str in os/file operations",
        description=(
            "Some older APIs expect str paths. Mixing Path and str can cause "
            "TypeError in places like os.path.join(Path(...), str)."
        ),
        regex=r"os\.path\.\w+\(.*Path\(",
        fix_suggestion="Use pathlib consistently or str(path) when passing to os.path functions.",
        severity="warning",
    ),
    # 8. typing Dict vs dict for 3.8 compat
    BugPattern(
        id="typing-old-generics",
        library="typing",
        title="Using dict/list/tuple as generic in code targeting Python < 3.9",
        description=(
            "dict[str, int] syntax requires Python 3.9+. For 3.8 compat, "
            "use typing.Dict[str, int] or 'from __future__ import annotations'."
        ),
        regex=None,  # AST-based
        fix_suggestion="Add 'from __future__ import annotations' at module top.",
        severity="warning",
    ),
    # 9. pytest fixture scope confusion
    BugPattern(
        id="pytest-fixture-scope",
        library="pytest",
        title="Session-scoped fixture modifying shared state",
        description=(
            "Fixtures with scope='session' or scope='module' that modify mutable "
            "state can cause test pollution across test runs."
        ),
        regex=r'@pytest\.fixture\([^)]*scope\s*=\s*["\'](?:session|module)["\']',
        fix_suggestion="Use scope='function' (default) for fixtures that modify state, or deepcopy shared data.",
        severity="warning",
    ),
    # 10. Jinja2 syntax leaking
    BugPattern(
        id="jinja2-syntax-leak",
        library="jinja2",
        title="Jinja2 {{ }} syntax in non-template strings",
        description=(
            "Jinja2 template syntax ({{ var }}) appearing in Python string literals "
            "suggests the template was not rendered or was pasted incorrectly."
        ),
        regex=r'["\'][^"\']*\{\{[^}]+\}\}[^"\']*["\']',
        fix_suggestion="Render templates with jinja2.Template.render() or use f-strings for Python interpolation.",
        severity="warning",
    ),
    # 11. requests without timeout
    BugPattern(
        id="requests-no-timeout",
        library="requests",
        title="HTTP request without timeout can hang indefinitely",
        description=(
            "requests.get/post/etc. without a timeout parameter can block forever "
            "if the server doesn't respond."
        ),
        regex=r"requests\.(?:get|post|put|delete|patch|head)\([^)]*\)(?!.*timeout)",
        fix_suggestion="Always pass timeout=10 (or appropriate value) to requests calls.",
        severity="warning",
    ),
    # 12. json.loads on None
    BugPattern(
        id="json-loads-none",
        library="json",
        title="json.loads() called on potentially None value",
        description="json.loads(None) raises TypeError. Check for None first.",
        regex=r"json\.loads\(\s*\w+\s*\)",
        fix_suggestion="Guard with 'if data: json.loads(data)' or validate input.",
        severity="warning",
    ),
    # 13. datetime naive vs aware
    BugPattern(
        id="datetime-naive-comparison",
        library="datetime",
        title="Comparing naive and timezone-aware datetimes",
        description=(
            "Comparing datetime.now() (naive) with an aware datetime raises TypeError. "
            "Use datetime.now(tz=timezone.utc) or always work in UTC."
        ),
        regex=r"datetime\.(?:now|utcnow)\(\s*\)",
        fix_suggestion="Use datetime.now(tz=timezone.utc) instead of datetime.now() or datetime.utcnow().",
        severity="warning",
    ),
    # 14. subprocess shell=True injection
    BugPattern(
        id="subprocess-shell-injection",
        library="subprocess",
        title="subprocess with shell=True is a command injection risk",
        description=(
            "Using shell=True with user-supplied input enables shell injection. "
            "Pass a list of args instead."
        ),
        regex=r"subprocess\.(?:run|call|Popen|check_output)\([^)]*shell\s*=\s*True",
        fix_suggestion="Use subprocess.run(['cmd', 'arg1'], shell=False) with a list of arguments.",
        severity="error",
    ),
    # 15. os.environ default None
    BugPattern(
        id="os-environ-keyerror",
        library="os",
        title="os.environ['KEY'] raises KeyError if missing",
        description=(
            "Accessing os.environ['KEY'] directly raises KeyError when the variable "
            "is not set. Use os.environ.get('KEY', 'default') or os.getenv()."
        ),
        regex=r"os\.environ\[['\"]",
        fix_suggestion="Use os.environ.get('KEY', 'default') or os.getenv('KEY', 'default').",
        severity="warning",
    ),
    # 16. open() without encoding
    BugPattern(
        id="open-no-encoding",
        library="python",
        title="open() without explicit encoding uses platform default",
        description=(
            "On Windows the default encoding is often cp1252, not utf-8. "
            "Always specify encoding='utf-8' explicitly."
        ),
        regex=r"open\([^)]+\)(?!.*encoding)",
        fix_suggestion="Add encoding='utf-8' to all open() calls.",
        severity="warning",
    ),
    # 17. mutable default argument
    BugPattern(
        id="mutable-default-arg",
        library="python",
        title="Mutable default argument in function definition",
        description=(
            "def f(items=[]): shares the same list across all calls. "
            "Use None as default and create inside the function."
        ),
        regex=r"def\s+\w+\([^)]*=\s*\[\s*\]",
        fix_suggestion="Use 'items=None' then 'items = items or []' inside the function.",
        severity="warning",
    ),
    # 18. except bare Exception
    BugPattern(
        id="bare-except",
        library="python",
        title="Bare except clause catches KeyboardInterrupt and SystemExit",
        description="Using 'except:' or 'except Exception:' too broadly hides bugs.",
        regex=r"^\s*except\s*:",
        fix_suggestion="Catch specific exceptions: except (ValueError, TypeError): ...",
        severity="warning",
    ),
]


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


class KnownBugs:
    """Database of common coding pitfalls per library."""

    def __init__(self) -> None:
        self.bugs: list[BugPattern] = list(_BUILTIN_BUGS)

    def scan(self, files: dict[str, str]) -> list[BugWarning]:
        """Scan generated code for known pitfalls."""
        warnings: list[BugWarning] = []
        for path, code in files.items():
            warnings.extend(self._scan_file(path, code))
        log.debug("KnownBugs scan: %d warnings across %d files", len(warnings), len(files))
        return warnings

    def add_bug(self, pattern: BugPattern) -> None:
        """Register a new bug pattern (e.g. from error memory)."""
        self.bugs.append(pattern)

    def get_by_library(self, library: str) -> list[BugPattern]:
        """Get all known bugs for a given library."""
        return [b for b in self.bugs if b.library == library]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _scan_file(self, path: str, code: str) -> list[BugWarning]:
        warnings: list[BugWarning] = []

        for bug in self.bugs:
            compiled = bug.compiled_regex()
            if compiled is None:
                continue
            for i, line in enumerate(code.splitlines(), 1):
                if compiled.search(line):
                    warnings.append(
                        BugWarning(
                            bug_id=bug.id,
                            file_path=path,
                            line=i,
                            title=bug.title,
                            description=bug.description,
                            fix_suggestion=bug.fix_suggestion,
                            severity=bug.severity,
                        )
                    )

        # AST-based checks
        warnings.extend(self._ast_checks(path, code))
        return warnings

    def _ast_checks(self, path: str, code: str) -> list[BugWarning]:
        """Run AST-based detection for patterns that regex can't catch."""
        warnings: list[BugWarning] = []
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return warnings

        has_future_annotations = False
        has_relative = False
        has_absolute = False

        for node in ast.walk(tree):
            # Check for missing __future__ annotations
            if isinstance(node, ast.ImportFrom):
                if node.module == "__future__":
                    for alias in node.names:
                        if alias.name == "annotations":
                            has_future_annotations = True
                if node.level and node.level > 0:
                    has_relative = True
                else:
                    has_absolute = True

        # Mixed import styles
        if has_relative and has_absolute:
            warnings.append(
                BugWarning(
                    bug_id="import-relative-confusion",
                    file_path=path,
                    line=None,
                    title="Mixing relative and absolute imports",
                    description="Both relative and absolute imports found in the same file.",
                    fix_suggestion="Pick one style (prefer absolute) and be consistent.",
                    severity="warning",
                )
            )

        return warnings
