"""Atlas Coding Pipeline — 14-stage spec-to-working-committed-code system.

Two models run simultaneously on the same GPU:
- Generator (Qwen3.5-4B, port 8080): Writes code at 110 tok/s
- Reviewer (Omnicoder-9B, port 8081): Cross-family review — different blind spots
"""

# Module ownership: Coding Pipeline: spec parsing, code generation, cross-family review

from __future__ import annotations

from cortex.coding.pipeline import CodingPipeline, ProjectResult
from cortex.coding.reviewer import CodeReviewer, ReviewResult, ReviewIssue
from cortex.coding.spec_parser import SpecParser, ParsedSpec, Violation
from cortex.coding.doc_fetcher import DocFetcher
from cortex.coding.dep_graph import DepGraph, FileSpec
from cortex.coding.known_bugs import KnownBugs, BugPattern, BugWarning
from cortex.coding.error_memory import ErrorMemory, ErrorFix
from cortex.coding.test_first import CodeTestGenerator as TestFirstGenerator

__all__ = [
    "CodingPipeline",
    "ProjectGenerator",
    "CodeReviewer",
    "SpecParser",
    "DocFetcher",
    "DepGraph",
    "KnownBugs",
    "ErrorMemory",
    "ProjectResult",
    "ReviewResult",
    "ReviewIssue",
    "ParsedSpec",
    "Violation",
    "FileSpec",
    "BugPattern",
    "BugWarning",
    "ErrorFix",
    "TestFirstGenerator",
]

# Alias for backward compatibility
ProjectGenerator = CodingPipeline
