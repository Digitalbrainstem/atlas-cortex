"""12-stage coding pipeline: spec → working tested code.

Stages:
 1. Parse spec
 2. Fetch documentation
 3. Build dependency graph
 4. Generate interface contract
 5. Generate tests first
 6. Generate code (cumulative context, dependency order)
 7. Known bugs check
 8. Spec adherence check
 9. Codex validation (syntax, imports, execute)
10. Cross-family review (Omnicoder)
11. Fix loop (pytest-driven)
12. Error memory
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import textwrap
from dataclasses import dataclass, field

import httpx

from cortex.coding.dep_graph import DepGraph, FileSpec
from cortex.coding.doc_fetcher import DocFetcher
from cortex.coding.error_memory import ErrorMemory
from cortex.coding.known_bugs import BugWarning, KnownBugs
from cortex.coding.reviewer import CodeReviewer, ReviewResult
from cortex.coding.spec_parser import ParsedSpec, SpecParser, Violation
from cortex.coding.test_first import CodeTestGenerator as TestFirstGenerator
from cortex.tools.codex_pipeline import CodexPipeline

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class StageResult:
    """Outcome of a single pipeline stage."""

    name: str
    passed: bool
    detail: str = ""
    data: dict = field(default_factory=dict)


@dataclass
class ProjectResult:
    """Final outcome of the full 12-stage pipeline."""

    files: dict[str, str] = field(default_factory=dict)
    test_files: dict[str, str] = field(default_factory=dict)
    tests_passed: bool = False
    stages: list[StageResult] = field(default_factory=list)
    bug_warnings: list[BugWarning] = field(default_factory=list)
    violations: list[Violation] = field(default_factory=list)
    reviews: list[ReviewResult] = field(default_factory=list)
    fix_iterations: int = 0
    contract: str = ""


# ---------------------------------------------------------------------------
# LLM calling helpers
# ---------------------------------------------------------------------------

_CONTRACT_SYSTEM = """\
You are a software architect. Given a project specification and library docs,
produce a Python interface contract: shared types (dataclasses), ABCs, function
signatures, constants. This contract is the single source of truth that all
files will import from.

Rules:
- Start with: from __future__ import annotations
- Use @dataclass for data types
- Use abc.ABC + @abc.abstractmethod for interfaces
- Include complete type hints
- No implementation bodies — only signatures, docstrings, and pass
- Return ONLY Python code, no markdown fences or explanation."""

_CODE_GEN_SYSTEM = """\
You are an expert Python developer. Generate production-quality code that:
- Strictly follows the provided interface contract
- Uses the documented library APIs (docs provided)
- Handles errors gracefully
- Includes docstrings for public API
- Starts with: from __future__ import annotations

You will be given the spec, contract, library docs, and any previously generated
files for context. Generate ONLY the requested file — no explanation, no fences."""


async def _llm_call(
    url: str,
    system: str,
    user: str,
    *,
    model: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 8192,
    timeout: float = 180.0,
) -> str:
    """Send a chat completion request to an OpenAI-compatible endpoint."""
    payload: dict = {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if model:
        payload["model"] = model
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{url.rstrip('/')}/v1/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


def _strip_fences(text: str) -> str:
    """Remove markdown code fences from LLM output."""
    text = text.strip()
    m = re.search(r"```python\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    m = re.search(r"```\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class CodingPipeline:
    """Full 12-stage coding pipeline: spec → working tested code.

    Two models run simultaneously:
    - generator_url: Qwen3.5-4B (port 8080) — writes code at 110 tok/s
    - reviewer_url:  Omnicoder-9B (port 8081) — cross-family review
    """

    def __init__(
        self,
        generator_url: str,
        reviewer_url: str | None = None,
        spec: str | None = None,
        *,
        generator_model: str | None = None,
        reviewer_model: str = "omnicoder",
    ) -> None:
        self.generator = generator_url.rstrip("/")
        self.reviewer_url = reviewer_url
        self._gen_model = generator_model
        self._rev_model = reviewer_model

        self.spec_parser = SpecParser()
        self.doc_fetcher = DocFetcher()
        self.dep_graph = DepGraph()
        self.known_bugs = KnownBugs()
        self.error_memory = ErrorMemory()
        self.codex = CodexPipeline()
        self.test_gen = TestFirstGenerator()
        self._reviewer: CodeReviewer | None = None
        if reviewer_url:
            self._reviewer = CodeReviewer(reviewer_url, reviewer_model)

        self._spec_text = spec or ""

    # ==================================================================
    # Main entry point
    # ==================================================================

    async def generate_project(
        self,
        spec: str,
        output_dir: str | None = None,
        *,
        max_fix_iterations: int = 15,
    ) -> ProjectResult:
        """Run the full 12-stage pipeline."""
        result = ProjectResult()

        # ---- Stage 1: Parse spec ----
        parsed = self.spec_parser.parse(spec)
        result.stages.append(StageResult("parse_spec", True, f"libs={parsed.required_libraries}"))
        log.info("Stage 1/12 — Spec parsed: %d libs, %d constraints", len(parsed.required_libraries), len(parsed.constraints))

        # ---- Stage 2: Fetch documentation ----
        docs = await self._stage_fetch_docs(parsed)
        result.stages.append(StageResult("fetch_docs", True, f"{len(docs)} libraries documented"))
        log.info("Stage 2/12 — Docs fetched for %d libraries", len(docs))

        # ---- Stage 3: Build dependency graph ----
        dep_order = self._stage_dep_graph(parsed)
        result.stages.append(StageResult("dep_graph", True, f"order={dep_order}"))
        log.info("Stage 3/12 — Generation order: %s", dep_order)

        # ---- Stage 4: Generate interface contract ----
        contract = await self._stage_contract(spec, docs)
        result.contract = contract
        result.stages.append(StageResult("contract", bool(contract), f"{len(contract)} chars"))
        log.info("Stage 4/12 — Contract generated (%d chars)", len(contract))

        # ---- Stage 5: Generate tests first ----
        test_files = await self._stage_tests(spec, contract, dep_order)
        result.test_files = test_files
        result.stages.append(StageResult("tests_first", bool(test_files), f"{len(test_files)} test files"))
        log.info("Stage 5/12 — %d test files generated", len(test_files))

        # ---- Stage 6: Generate code ----
        files = await self._stage_generate(spec, contract, dep_order, docs)
        result.files = files
        result.stages.append(StageResult("generate", bool(files), f"{len(files)} files"))
        log.info("Stage 6/12 — %d source files generated", len(files))

        # ---- Stage 7: Known bugs check ----
        bugs = self.known_bugs.scan(files)
        result.bug_warnings = bugs
        result.stages.append(StageResult("known_bugs", True, f"{len(bugs)} warnings"))
        log.info("Stage 7/12 — %d known-bug warnings", len(bugs))

        # ---- Stage 8: Spec adherence check ----
        violations = self.spec_parser.check_adherence(files, parsed)
        result.violations = violations
        errors = [v for v in violations if v.severity == "error"]
        result.stages.append(StageResult("spec_adherence", len(errors) == 0, f"{len(violations)} violations"))
        log.info("Stage 8/12 — %d spec violations (%d errors)", len(violations), len(errors))

        # ---- Stage 9: Codex validation ----
        codex_ok = await self._stage_codex(files)
        result.stages.append(StageResult("codex", codex_ok, ""))
        log.info("Stage 9/12 — Codex validation: %s", "PASS" if codex_ok else "FAIL")

        # ---- Stage 10: Cross-family review ----
        if self._reviewer:
            reviews = await self._stage_review(files, contract)
            result.reviews = reviews
            bugs_found = sum(1 for r in reviews for i in r.issues if i.severity == "bug")
            result.stages.append(StageResult("cross_review", bugs_found == 0, f"{bugs_found} bugs"))
            log.info("Stage 10/12 — Cross-review: %d bugs found", bugs_found)
        else:
            result.stages.append(StageResult("cross_review", True, "skipped (no reviewer)"))
            log.info("Stage 10/12 — Cross-review skipped (no reviewer URL)")

        # ---- Stage 11: Fix loop ----
        final_files, iterations, tests_ok = await self._stage_fix_loop(
            files, test_files, output_dir, max_iterations=max_fix_iterations,
        )
        result.files = final_files
        result.fix_iterations = iterations
        result.tests_passed = tests_ok
        result.stages.append(StageResult("fix_loop", tests_ok, f"{iterations} iterations"))
        log.info("Stage 11/12 — Fix loop: %d iterations, tests_passed=%s", iterations, tests_ok)

        # ---- Stage 12: Error memory ----
        self._stage_error_memory(result)
        result.stages.append(StageResult("error_memory", True, "recorded"))
        log.info("Stage 12/12 — Error memory updated")

        # Write files to disk if requested
        if output_dir:
            self._write_project(result, output_dir)

        return result

    # ==================================================================
    # Stage implementations
    # ==================================================================

    async def _stage_fetch_docs(self, parsed: ParsedSpec) -> dict[str, str]:
        return await self.doc_fetcher.fetch(parsed.required_libraries)

    def _stage_dep_graph(self, parsed: ParsedSpec) -> list[str]:
        if not parsed.file_structure:
            return []
        specs = [FileSpec(path=p) for p in parsed.file_structure]
        try:
            return self.dep_graph.plan(specs)
        except ValueError as exc:
            log.warning("Dep graph cycle: %s — using alphabetical order", exc)
            return sorted(parsed.file_structure)

    async def _stage_contract(self, spec: str, docs: dict[str, str]) -> str:
        docs_section = "\n\n".join(
            f"### {lib}\n{doc[:1000]}" for lib, doc in docs.items()
        )
        user_msg = f"## Project Specification\n{spec}\n\n## Library Docs\n{docs_section}"
        raw = await _llm_call(self.generator, _CONTRACT_SYSTEM, user_msg, model=self._gen_model)
        return _strip_fences(raw)

    async def _stage_tests(
        self, spec: str, contract: str, dep_order: list[str],
    ) -> dict[str, str]:
        source_files = dep_order if dep_order else ["main.py"]
        tests = await self.test_gen.generate_tests(
            spec=spec,
            contract=contract,
            generator_url=self.generator,
            source_files=source_files,
            model=self._gen_model,
        )
        # Validate syntax
        errors = self.test_gen.validate_tests_runnable(tests)
        for err in errors:
            log.warning("Test syntax error: %s", err)
        return tests

    async def _stage_generate(
        self,
        spec: str,
        contract: str,
        dep_order: list[str],
        docs: dict[str, str],
    ) -> dict[str, str]:
        files: dict[str, str] = {}
        file_list = dep_order if dep_order else ["main.py"]
        docs_section = "\n\n".join(f"### {lib}\n{doc[:800]}" for lib, doc in docs.items())

        for path in file_list:
            # Cumulative context: all previously generated files
            prev_section = ""
            if files:
                prev_section = "\n\n".join(
                    f"### {p}\n```python\n{c}\n```" for p, c in files.items()
                )

            user_msg = textwrap.dedent(f"""\
                ## Specification
                {spec}

                ## Interface Contract
                ```python
                {contract}
                ```

                ## Library Documentation
                {docs_section}

                ## Previously Generated Files
                {prev_section}

                ## Task
                Generate the file: {path}
                Follow the contract exactly. Use documented APIs. Handle errors.""")

            raw = await _llm_call(self.generator, _CODE_GEN_SYSTEM, user_msg, model=self._gen_model)
            files[path] = _strip_fences(raw)

        return files

    async def _stage_codex(self, files: dict[str, str]) -> bool:
        all_ok = True
        for path, code in files.items():
            result = await self.codex.validate(code)
            if not result.valid:
                log.warning("Codex failed for %s: %s", path, result.stages_failed)
                all_ok = False
        return all_ok

    async def _stage_review(
        self, files: dict[str, str], contract: str,
    ) -> list[ReviewResult]:
        assert self._reviewer is not None
        results: list[ReviewResult] = []
        # Per-file reviews
        for path, code in files.items():
            r = await self._reviewer.review_file(code, contract, file_path=path)
            results.append(r)
        # Project-level cross-file review
        project_reviews = await self._reviewer.review_project(files, contract)
        results.extend(project_reviews)
        return results

    async def _stage_fix_loop(
        self,
        files: dict[str, str],
        test_files: dict[str, str],
        output_dir: str | None,
        *,
        max_iterations: int = 15,
    ) -> tuple[dict[str, str], int, bool]:
        """Run pytest, feed errors back to the generator, fix, repeat."""
        if not output_dir:
            return files, 0, False

        # Write initial files
        all_files = {**files, **test_files}
        self._write_files(all_files, output_dir)

        for iteration in range(1, max_iterations + 1):
            passed, error_output = self._run_pytest(output_dir)
            if passed:
                log.info("Fix loop: tests passed on iteration %d", iteration)
                return files, iteration, True

            # Check error memory for known fix
            known_fix = self.error_memory.get_known_fix(error_output[:200])
            if known_fix:
                log.info("Fix loop: applying known fix from error memory")

            # Ask the generator to fix
            fix_prompt = textwrap.dedent(f"""\
                The following pytest errors occurred:

                ```
                {error_output[:3000]}
                ```

                {"Known fix hint: " + known_fix if known_fix else ""}

                Here are the current source files:
                {self._files_as_context(files)}

                Fix the code to make the tests pass. Return ALL fixed files as:
                ### filename.py
                ```python
                <code>
                ```

                Only include files that need changes.""")

            try:
                raw = await _llm_call(
                    self.generator, _CODE_GEN_SYSTEM, fix_prompt,
                    model=self._gen_model, max_tokens=8192,
                )
                patched = self._parse_multi_file_response(raw)
                if patched:
                    files.update(patched)
                    all_files = {**files, **test_files}
                    self._write_files(all_files, output_dir)
            except Exception as exc:
                log.warning("Fix iteration %d failed: %s", iteration, exc)

        # Final check
        passed, _ = self._run_pytest(output_dir)
        return files, max_iterations, passed

    def _stage_error_memory(self, result: ProjectResult) -> None:
        for bug in result.bug_warnings:
            self.error_memory.record(
                error=bug.title,
                fix=bug.fix_suggestion,
                library=bug.bug_id.split("-")[0],
            )

    # ==================================================================
    # Helpers
    # ==================================================================

    @staticmethod
    def _write_files(files: dict[str, str], output_dir: str) -> None:
        for path, content in files.items():
            full = os.path.join(output_dir, path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as f:
                f.write(content)

    @staticmethod
    def _write_project(result: ProjectResult, output_dir: str) -> None:
        all_files = {**result.files, **result.test_files}
        for path, content in all_files.items():
            full = os.path.join(output_dir, path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as f:
                f.write(content)

    @staticmethod
    def _run_pytest(project_dir: str) -> tuple[bool, str]:
        """Run pytest in the project directory. Returns (passed, output)."""
        try:
            proc = subprocess.run(
                ["python", "-m", "pytest", "-x", "-q", "--tb=short"],
                capture_output=True,
                text=True,
                cwd=project_dir,
                timeout=120,
            )
            return proc.returncode == 0, proc.stdout + proc.stderr
        except Exception as exc:
            return False, str(exc)

    @staticmethod
    def _files_as_context(files: dict[str, str]) -> str:
        parts: list[str] = []
        for path, code in files.items():
            parts.append(f"### {path}\n```python\n{code}\n```")
        return "\n\n".join(parts)

    @staticmethod
    def _parse_multi_file_response(raw: str) -> dict[str, str]:
        """Parse a multi-file LLM response with ### headers and code blocks."""
        result: dict[str, str] = {}
        parts = re.split(r"###\s+(\S+\.py)\s*\n", raw)
        # parts[0] is preamble, then pairs of (filename, content)
        for i in range(1, len(parts) - 1, 2):
            filename = parts[i].strip()
            content = parts[i + 1].strip()
            code = _strip_fences(content)
            if code:
                result[filename] = code
        return result
