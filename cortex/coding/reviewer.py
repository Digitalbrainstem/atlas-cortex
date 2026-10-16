"""Cross-family code review using a different model."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

import httpx

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class ReviewIssue:
    """A single issue found during review."""

    line: int | None = None
    severity: str = "warning"  # "bug" | "warning" | "suggestion"
    description: str = ""
    suggested_fix: str = ""


@dataclass
class ReviewResult:
    """Review outcome for one file."""

    file_path: str
    issues: list[ReviewIssue] = field(default_factory=list)
    approved: bool = True


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_REVIEW_SYSTEM = """\
You are a senior code reviewer. You are reviewing code written by another AI.
Check for:
- Logic bugs and off-by-one errors
- Spec violations (compare against the provided spec/contract)
- Missing edge cases and error handling
- Incorrect library usage or deprecated APIs
- Security issues (injection, path traversal, secrets in code)
- Performance pitfalls (N+1 queries, unbounded loops)
- Type errors and None-safety

Be specific.  Return a JSON array of issues.  Each issue is an object:
{"line": <int or null>, "severity": "bug"|"warning"|"suggestion", "description": "...", "suggested_fix": "..."}

If the code is correct, return an empty array: []
Only output the JSON array, no surrounding text."""

_REVIEW_USER = """\
## Spec / Contract
{contract}

## File: {file_path}
```python
{code}
```

Review this file. Return ONLY a JSON array of issues."""

_PROJECT_REVIEW_USER = """\
## Contract
{contract}

## Project Files
{files_section}

Review the ENTIRE project for cross-file issues:
- Import consistency (do files import from each other correctly?)
- Interface conformance (do implementations match the contract?)
- Test coverage gaps (are there untested code paths?)
- Missing error handling across module boundaries

Return ONLY a JSON array of issues. Each issue must include "file_path" as an additional field."""


# ---------------------------------------------------------------------------
# Reviewer
# ---------------------------------------------------------------------------


class CodeReviewer:
    """Cross-family code review using a different model.

    Sends generated code to a *different* model (e.g. Omnicoder-9B running
    on a separate port) so that blind spots of the generator are caught by
    a model with different training data.
    """

    def __init__(
        self,
        reviewer_url: str,
        reviewer_model: str = "omnicoder",
        timeout: float = 120.0,
    ) -> None:
        self.url = reviewer_url.rstrip("/")
        self.model = reviewer_model
        self.timeout = timeout

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def review_file(
        self,
        code: str,
        spec_context: str,
        file_path: str = "<unknown>",
    ) -> ReviewResult:
        """Review a single file against the spec/contract."""
        user_msg = _REVIEW_USER.format(
            contract=spec_context,
            file_path=file_path,
            code=code,
        )
        raw = await self._call_llm(user_msg)
        issues = self._parse_issues(raw)
        return ReviewResult(
            file_path=file_path,
            issues=issues,
            approved=not any(i.severity == "bug" for i in issues),
        )

    async def review_project(
        self,
        files: dict[str, str],
        contract: str,
    ) -> list[ReviewResult]:
        """Review entire project for cross-file issues."""
        files_section = "\n\n".join(
            f"### {path}\n```python\n{code}\n```"
            for path, code in files.items()
        )
        user_msg = _PROJECT_REVIEW_USER.format(
            contract=contract,
            files_section=files_section,
        )
        raw = await self._call_llm(user_msg)
        issues = self._parse_issues(raw)

        # Group by file_path
        by_file: dict[str, list[ReviewIssue]] = {}
        for issue in issues:
            fp = "<project>"  # default for cross-file issues
            by_file.setdefault(fp, []).append(issue)

        return [
            ReviewResult(
                file_path=fp,
                issues=file_issues,
                approved=not any(i.severity == "bug" for i in file_issues),
            )
            for fp, file_issues in by_file.items()
        ]

    # ------------------------------------------------------------------
    # LLM communication
    # ------------------------------------------------------------------

    async def _call_llm(self, user_message: str) -> str:
        """Send a review request to the reviewer model."""
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _REVIEW_SYSTEM},
                {"role": "user", "content": user_message},
            ],
            "stream": False,
            "temperature": 0.1,
            "max_tokens": 4096,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.url}/v1/chat/completions",
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
        except Exception as exc:
            log.error("Reviewer LLM call failed: %s", exc)
            return "[]"

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_issues(raw: str) -> list[ReviewIssue]:
        """Parse the JSON array of issues from the LLM response."""
        # Strip markdown fences if present
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```\w*\n?", "", cleaned)
            cleaned = re.sub(r"\n?```$", "", cleaned)
            cleaned = cleaned.strip()

        try:
            items = json.loads(cleaned)
        except json.JSONDecodeError:
            # Try to extract JSON array from the response
            m = re.search(r"\[.*\]", cleaned, re.DOTALL)
            if m:
                try:
                    items = json.loads(m.group(0))
                except json.JSONDecodeError:
                    log.warning("Could not parse reviewer response as JSON")
                    return []
            else:
                return []

        if not isinstance(items, list):
            return []

        issues: list[ReviewIssue] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            issues.append(
                ReviewIssue(
                    line=item.get("line"),
                    severity=item.get("severity", "warning"),
                    description=item.get("description", ""),
                    suggested_fix=item.get("suggested_fix", ""),
                )
            )
        return issues
