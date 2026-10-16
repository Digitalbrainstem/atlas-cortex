"""Generate tests before code — tests define the contract."""
from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field

import httpx

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_TEST_GEN_SYSTEM = """\
You are an expert test engineer. Given a specification and interface contract,
write comprehensive pytest tests BEFORE any implementation exists.

Rules:
- Use pytest (not unittest)
- Use async def for async tests (pytest-asyncio auto mode)
- Start every file with: from __future__ import annotations
- Use descriptive test names: test_<function>_<scenario>_<expected>
- Cover: happy path, edge cases, error cases, boundary values
- Use @pytest.fixture for shared setup
- Mock external dependencies (httpx, APIs, file I/O)
- Each test should be independent and deterministic
- Import from the module being tested (even though it doesn't exist yet)

Return ONLY the test file content, no explanation."""

_TEST_GEN_USER = """\
## Specification
{spec}

## Interface Contract
{contract}

## File to test: {source_file}

Generate a complete pytest test file for this module. The test file should be
named {test_file} and test all public functions/classes defined in the contract
for this module."""


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


class CodeTestGenerator:
    """Generate tests before code — tests define the contract."""

    def __init__(self, timeout: float = 120.0) -> None:
        self.timeout = timeout

    async def generate_tests(
        self,
        spec: str,
        contract: str,
        generator_url: str,
        source_files: list[str] | None = None,
        model: str | None = None,
    ) -> dict[str, str]:
        """Generate test files for each source file in the project.

        Returns ``{test_path: test_code}``.
        """
        if not source_files:
            source_files = self._infer_files_from_contract(contract)

        results: dict[str, str] = {}
        for src in source_files:
            test_path = self._test_path_for(src)
            code = await self._generate_one(
                spec=spec,
                contract=contract,
                source_file=src,
                test_file=test_path,
                generator_url=generator_url,
                model=model,
            )
            if code:
                results[test_path] = code

        return results

    def validate_tests_runnable(self, test_files: dict[str, str]) -> list[str]:
        """Syntax-check all test files. Returns list of errors (empty = OK)."""
        errors: list[str] = []
        for path, code in test_files.items():
            try:
                ast.parse(code)
            except SyntaxError as exc:
                errors.append(f"{path}: SyntaxError at line {exc.lineno}: {exc.msg}")
        return errors

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _generate_one(
        self,
        spec: str,
        contract: str,
        source_file: str,
        test_file: str,
        generator_url: str,
        model: str | None = None,
    ) -> str | None:
        user_msg = _TEST_GEN_USER.format(
            spec=spec,
            contract=contract,
            source_file=source_file,
            test_file=test_file,
        )
        payload: dict = {
            "messages": [
                {"role": "system", "content": _TEST_GEN_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            "stream": False,
            "temperature": 0.2,
            "max_tokens": 4096,
        }
        if model:
            payload["model"] = model
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{generator_url.rstrip('/')}/v1/chat/completions",
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                return self._extract_code(content)
        except Exception as exc:
            log.error("Test generation failed for %s: %s", source_file, exc)
            return None

    @staticmethod
    def _extract_code(response: str) -> str:
        """Strip markdown fences from LLM response."""
        import re

        text = response.strip()
        # Remove ```python ... ``` wrapper
        m = re.search(r"```python\n(.*?)```", text, re.DOTALL)
        if m:
            return m.group(1).strip()
        # Remove generic ``` wrapper
        m = re.search(r"```\n(.*?)```", text, re.DOTALL)
        if m:
            return m.group(1).strip()
        return text

    @staticmethod
    def _test_path_for(source_file: str) -> str:
        """Derive test file path from source file path."""
        parts = source_file.rsplit("/", 1)
        if len(parts) == 2:
            directory, filename = parts
            return f"tests/test_{filename}"
        return f"tests/test_{source_file}"

    @staticmethod
    def _infer_files_from_contract(contract: str) -> list[str]:
        """Extract file names mentioned in the contract text."""
        import re

        files: list[str] = []
        for m in re.finditer(r"[`'\"]?([\w/]+\.py)[`'\"]?", contract):
            path = m.group(1)
            if not path.startswith("test_"):
                files.append(path)
        return files or ["main.py"]
