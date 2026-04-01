"""Extract structured requirements from natural-language specs."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class ParsedSpec:
    """Structured representation of a project specification."""

    raw: str
    required_libraries: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    file_structure: dict[str, str] = field(default_factory=dict)
    features: list[str] = field(default_factory=list)
    test_requirements: list[str] = field(default_factory=list)
    language: str = "python"


@dataclass
class Violation:
    """A spec-adherence violation found in generated code."""

    file_path: str
    description: str
    severity: str = "error"  # "error" | "warning"
    line: int | None = None


# ---------------------------------------------------------------------------
# Well-known library identifiers
# ---------------------------------------------------------------------------

_STDLIB_MODULES: set[str] = {
    "abc", "argparse", "ast", "asyncio", "base64", "bisect",
    "calendar", "collections", "contextlib", "copy", "csv",
    "dataclasses", "datetime", "decimal", "difflib", "enum",
    "errno", "fnmatch", "fractions", "functools", "getpass",
    "glob", "gzip", "hashlib", "heapq", "html", "http",
    "importlib", "inspect", "io", "itertools", "json",
    "logging", "math", "mimetypes", "multiprocessing", "operator",
    "os", "pathlib", "pickle", "platform", "pprint",
    "queue", "random", "re", "secrets", "shlex", "shutil",
    "signal", "socket", "sqlite3", "statistics", "string",
    "struct", "subprocess", "sys", "tempfile", "textwrap",
    "threading", "time", "timeit", "traceback", "typing",
    "unittest", "urllib", "uuid", "venv", "warnings",
    "weakref", "xml", "zipfile", "zlib",
    # testing
    "pytest", "unittest",
    # common builtins people forget
    "__future__", "builtins", "types",
}

_LIBRARY_ALIASES: dict[str, str] = {
    "yaml": "pyyaml",
    "PIL": "pillow",
    "cv2": "opencv-python",
    "sklearn": "scikit-learn",
    "bs4": "beautifulsoup4",
    "gi": "pygobject",
    "attr": "attrs",
    "dotenv": "python-dotenv",
}

# Regex helpers
_LIB_MENTION = re.compile(
    r"\b(?:use|using|import|require|depends?\s+on|with|via|library|package)\s+"
    r"[`'\"]?([a-zA-Z][a-zA-Z0-9_.-]+)[`'\"]?"
    r"(?:\s*(?:,|and)\s+[`'\"]?([a-zA-Z][a-zA-Z0-9_.-]+)[`'\"]?)*",
    re.IGNORECASE,
)

# Secondary pattern: captures individual library-like tokens after keywords
_LIB_LIST_ITEM = re.compile(
    r"\b(?:use|using|import|require|depends?\s+on|with|via|library|package)\s+"
    r"((?:[`'\"]?[a-zA-Z][a-zA-Z0-9_.-]+[`'\"]?(?:\s*(?:,|and)\s+)?)+)",
    re.IGNORECASE,
)
_LIB_TOKEN = re.compile(r"[`'\"]?([a-zA-Z][a-zA-Z0-9_.-]+)[`'\"]?")
_CONSTRAINT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bstdlib[- ]?only\b", re.I), "stdlib only"),
    (re.compile(r"\bno external\b", re.I), "no external deps"),
    (re.compile(r"\bno third[- ]?party\b", re.I), "no third-party deps"),
    (re.compile(r"\bpython\s*3\.(\d+)\+?\b", re.I), "python version constraint"),
    (re.compile(r"\btype[- ]?hints?\b", re.I), "type hints required"),
    (re.compile(r"\basync\b", re.I), "async required"),
    (re.compile(r"\bno\s+globals?\b", re.I), "no global state"),
    (re.compile(r"\bthread[- ]?safe\b", re.I), "thread-safe required"),
    (re.compile(r"\bimmutable\b", re.I), "immutable data structures"),
]
_FILE_PATTERN = re.compile(
    r"[`'\"]?([\w/]+\.py)[`'\"]?",
)
_FEATURE_BULLETS = re.compile(
    r"^[\s]*[-*•]\s+(.+)$", re.MULTILINE,
)
_BACKTICK_LIBS = re.compile(r"`([a-zA-Z][a-zA-Z0-9_.-]+)`")

# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class SpecParser:
    """Extract structured requirements from natural-language specs."""

    def parse(self, spec: str) -> ParsedSpec:
        """Parse a free-form spec into structured requirements."""
        parsed = ParsedSpec(raw=spec)

        # --- Libraries ---
        libs: set[str] = set()
        for m in _LIB_LIST_ITEM.finditer(spec):
            group_text = m.group(1)
            for tok in _LIB_TOKEN.finditer(group_text):
                libs.add(self._normalise_lib(tok.group(1)))
        for m in _BACKTICK_LIBS.finditer(spec):
            candidate = m.group(1)
            # Skip file names
            if "." in candidate and not candidate.endswith((".py", ".js")):
                libs.add(self._normalise_lib(candidate))
            elif "." not in candidate:
                libs.add(self._normalise_lib(candidate))
        # Drop stdlib modules
        libs -= _STDLIB_MODULES
        # Drop common false positives
        libs -= {"the", "a", "an", "it", "this", "that", "and", "or", "for"}
        parsed.required_libraries = sorted(libs)

        # --- Constraints ---
        for pattern, label in _CONSTRAINT_PATTERNS:
            if pattern.search(spec):
                parsed.constraints.append(label)

        # --- File structure ---
        for m in _FILE_PATTERN.finditer(spec):
            path = m.group(1)
            parsed.file_structure[path] = ""

        # --- Features (bullet points) ---
        for m in _FEATURE_BULLETS.finditer(spec):
            feat = m.group(1).strip()
            if len(feat) > 5:
                parsed.features.append(feat)

        # --- Test requirements ---
        test_section = False
        for line in spec.splitlines():
            low = line.lower().strip()
            if "test" in low and (":" in low or low.startswith("#")):
                test_section = True
                continue
            if test_section and low.startswith(("-", "*", "•")):
                parsed.test_requirements.append(low.lstrip("-*• ").strip())
            elif test_section and low == "":
                test_section = False

        log.debug(
            "Parsed spec: libs=%s constraints=%s files=%d features=%d",
            parsed.required_libraries,
            parsed.constraints,
            len(parsed.file_structure),
            len(parsed.features),
        )
        return parsed

    # ------------------------------------------------------------------
    # Adherence checking
    # ------------------------------------------------------------------

    def check_adherence(
        self,
        files: dict[str, str],
        spec: ParsedSpec,
    ) -> list[Violation]:
        """Scan generated code for spec violations."""
        violations: list[Violation] = []

        stdlib_only = any("stdlib" in c for c in spec.constraints)

        for path, code in files.items():
            violations.extend(self._check_imports(path, code, spec, stdlib_only))
            violations.extend(self._check_missing_features(path, code, spec))

        # Check required file structure
        if spec.file_structure:
            for required_file in spec.file_structure:
                if required_file not in files:
                    violations.append(
                        Violation(
                            file_path=required_file,
                            description=f"Required file missing: {required_file}",
                            severity="error",
                        )
                    )
        return violations

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _normalise_lib(self, name: str) -> str:
        name = name.lower().strip("`'\".,;:!?")
        name = name.split(".")[0]
        return _LIBRARY_ALIASES.get(name, name)

    def _check_imports(
        self,
        path: str,
        code: str,
        spec: ParsedSpec,
        stdlib_only: bool,
    ) -> list[Violation]:
        violations: list[Violation] = []
        import_re = re.compile(
            r"^\s*(?:from|import)\s+([a-zA-Z_][a-zA-Z0-9_.]*)", re.MULTILINE,
        )
        allowed: set[str] = set(spec.required_libraries) | _STDLIB_MODULES
        for i, line in enumerate(code.splitlines(), 1):
            m = import_re.match(line)
            if not m:
                continue
            top = m.group(1).split(".")[0].lower()
            top = _LIBRARY_ALIASES.get(top, top)
            if stdlib_only and top not in _STDLIB_MODULES:
                violations.append(
                    Violation(
                        file_path=path,
                        description=f"Non-stdlib import '{top}' violates stdlib-only constraint",
                        severity="error",
                        line=i,
                    )
                )
            elif not stdlib_only and top not in allowed and top not in _STDLIB_MODULES:
                violations.append(
                    Violation(
                        file_path=path,
                        description=f"Undeclared dependency '{top}' not in spec",
                        severity="warning",
                        line=i,
                    )
                )
        return violations

    def _check_missing_features(
        self,
        path: str,
        code: str,
        spec: ParsedSpec,
    ) -> list[Violation]:
        """Basic heuristic: feature keywords should appear somewhere in code."""
        # This is intentionally lightweight — real check is in cross-review
        return []
