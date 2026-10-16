"""Pre-Cached Domain Prompt system.

All system prompts share a common PREFIX (Atlas personality, safety rules,
core identity) so that llama-server's prefix caching makes domain switching
virtually free.  Domain-specific SUFFIXES branch after the prefix — only the
suffix needs processing when the domain changes.

Usage::

    builder = get_prompt_builder()
    prompt  = builder.build(domain="coding", context=ctx)

Domain suffixes live in plain ``.txt`` files under ``config/prompts/`` (or the
directory pointed to by ``PROMPT_DIR``).  Drop a new file to add a domain
without touching code.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncGenerator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared prefix — identical across every domain so llama-server can cache it.
# ---------------------------------------------------------------------------

_BASE_PROMPT = (
    "You are Atlas, a personal AI assistant created by Digitalbrainstem.\n"
    "You are helpful, honest, and harmless. You follow the Atlas Principles.\n"
    "You run locally on the user's hardware. You never send data externally.\n"
    "You speak naturally and concisely unless asked for detail.\n\n"
    "IMPORTANT: Your responses will be spoken aloud via text-to-speech.\n"
    "Be concise — answer the question directly in 1-2 sentences.\n"
    "Give the specific fact asked for (a date, a name, a number) without\n"
    "extra context, background, or synopsis unless the user explicitly\n"
    "asks for more detail. Avoid lists and bullet points."
)

# ---------------------------------------------------------------------------
# Built-in domain suffixes (fallback when no file exists on disk).
# ---------------------------------------------------------------------------

_BUILTIN_SUFFIXES: dict[str, str] = {
    "coding": (
        "You are an expert programmer. Write clean, tested, production-ready code.\n"
        "Use type hints, docstrings, and follow project conventions.\n"
        "When generating code, provide complete implementations, not snippets.\n"
        "Explain your reasoning briefly before writing code.\n"
        "Prefer standard library solutions over third-party dependencies."
    ),
    "medical": (
        "You have knowledge of common medications, conditions, and first aid.\n"
        "Always recommend consulting a healthcare professional for serious concerns.\n"
        "Be precise with dosages, interactions, and contraindications.\n"
        "Never diagnose — describe possibilities and urge professional evaluation.\n"
        "Use proper medical terminology but explain it in plain language."
    ),
    "home": (
        "You control the smart home via Home Assistant.\n"
        "Available devices: lights, thermostats, locks, cameras, speakers.\n"
        "Confirm destructive actions (lock doors, arm security).\n"
        "Report device states clearly and concisely.\n"
        "When controlling multiple devices, execute them in logical order."
    ),
    "education": (
        "You are teaching young children (ages 18mo-5yr).\n"
        "Use simple language, positive reinforcement, patience.\n"
        "Celebrate effort, not just correct answers.\n"
        "Make learning fun with examples, stories, and gentle encouragement.\n"
        "Break complex ideas into tiny, digestible steps."
    ),
    "sight": (
        "You are helping a visually impaired user navigate the world.\n"
        "Describe scenes clearly and concisely. Prioritize safety information.\n"
        "Use spatial language (left, right, ahead, behind).\n"
        "Navigation warnings are always spoken first.\n"
        "Mention obstacles, terrain changes, and distances in practical terms."
    ),
    "general": "",
}

# ---------------------------------------------------------------------------
# Domain keyword map — used for auto-detection from user queries.
# ---------------------------------------------------------------------------

_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "coding": [
        "code", "function", "class", "variable", "debug", "error",
        "traceback", "compile", "syntax", "python", "javascript",
        "typescript", "refactor", "implement", "algorithm", "regex",
        "git", "commit", "merge", "pull request", "api", "endpoint",
        "database", "sql", "json", "yaml", "docker", "deploy",
        "test", "unit test", "lint", "bug", "stack trace",
    ],
    "medical": [
        "medication", "medicine", "dose", "dosage", "symptom",
        "pain", "fever", "cough", "allergy", "blood pressure",
        "prescription", "side effect", "doctor", "hospital",
        "first aid", "injury", "wound", "infection", "diagnosis",
        "treatment", "vaccine", "antibiotic", "headache", "nausea",
    ],
    "home": [
        "lights", "light", "lamp", "thermostat", "temperature",
        "lock", "door", "camera", "speaker", "turn on", "turn off",
        "brightness", "dim", "scene", "automation", "sensor",
        "motion", "security", "alarm", "garage", "fan", "heater",
        "air conditioning", "blinds", "curtains", "degrees",
        "bedroom", "living room", "kitchen", "bathroom",
    ],
    "education": [
        "teach", "learn", "abc", "alphabet", "counting", "numbers",
        "colors", "shapes", "story time", "lesson", "quiz",
        "spelling", "reading", "phonics", "math", "addition",
        "subtraction", "animal sounds", "nursery rhyme",
    ],
    "sight": [
        "describe", "see", "look", "navigate", "obstacle",
        "ahead", "behind", "around me", "what is in front",
        "read the sign", "what color", "how far", "crossing",
        "stairs", "curb", "door handle", "text on",
    ],
}

# ---------------------------------------------------------------------------
# Context injection template — appended after the domain suffix.
# ---------------------------------------------------------------------------

_CONTEXT_TEMPLATE_PARTS: dict[str, str] = {
    "user_name": "You are speaking with {user_name}.",
    "room": "The user is in the {room}.",
    "time_of_day": "Current time of day: {time_of_day}.",
}

# ---------------------------------------------------------------------------
# Pre-warm result dataclass
# ---------------------------------------------------------------------------


@dataclass
class PrewarmResult:
    """Outcome of pre-warming a single domain slot."""

    domain: str
    success: bool
    error: str = ""


# ---------------------------------------------------------------------------
# PromptBuilder
# ---------------------------------------------------------------------------


class PromptBuilder:
    """Builds system prompts from modular components with shared prefix for
    cache efficiency.

    The shared prefix is **identical** for every domain so that
    llama-server's KV-cache prefix matching keeps it resident.  Only the
    short domain suffix changes per query.
    """

    def __init__(
        self,
        *,
        prompt_dir: str | Path | None = None,
        base_prompt: str | None = None,
    ) -> None:
        self._base_prompt: str = base_prompt or _BASE_PROMPT
        self._prompt_dir: Path | None = None

        # Resolve prompt directory
        configured = prompt_dir or os.environ.get("PROMPT_DIR")
        if configured:
            self._prompt_dir = Path(configured)
        else:
            default = Path(__file__).resolve().parent.parent.parent / "config" / "prompts"
            if default.is_dir():
                self._prompt_dir = default

        # Start with built-in suffixes, then overlay disk files
        self._domains: dict[str, str] = dict(_BUILTIN_SUFFIXES)
        self._load_file_domains()

    # -- public API ---------------------------------------------------------

    def build(self, domain: str = "general", context: dict[str, Any] | None = None) -> str:
        """Return a complete system prompt for *domain* with optional context.

        Structure::

            <shared prefix>

            <domain suffix>

            <context lines>
        """
        parts: list[str] = [self._base_prompt]

        suffix = self.get_domain_suffix(domain)
        if suffix:
            parts.append(suffix)

        ctx_block = self._build_context_block(context)
        if ctx_block:
            parts.append(ctx_block)

        return "\n\n".join(parts)

    def get_base_prompt(self) -> str:
        """Return the shared prefix (cached portion)."""
        return self._base_prompt

    def get_domain_suffix(self, domain: str) -> str:
        """Return the suffix for *domain*, falling back to ``general``."""
        return self._domains.get(domain, self._domains.get("general", ""))

    def register_domain(self, name: str, suffix: str) -> None:
        """Register (or overwrite) a domain suffix at runtime."""
        self._domains[name] = suffix
        logger.info("registered domain %r (%d chars)", name, len(suffix))

    def list_domains(self) -> list[str]:
        """Return sorted list of available domain names."""
        return sorted(self._domains)

    async def prewarm_all(self, provider: Any) -> dict[str, bool]:
        """Send a dummy completion for every domain to warm the KV cache.

        Returns a mapping of ``{domain: success_bool}``.
        """
        results: dict[str, bool] = {}
        for domain in self.list_domains():
            prompt = self.build(domain)
            messages = [
                {"role": "system", "content": prompt},
                {"role": "user", "content": "warmup"},
            ]
            try:
                resp = await provider.chat(
                    messages,
                    stream=False,
                    max_tokens=1,
                    temperature=0.0,
                )
                # stream=False returns a dict; consume if generator returned
                if hasattr(resp, "__anext__"):
                    async for _ in resp:  # pragma: no cover
                        break
                results[domain] = True
                logger.debug("pre-warmed domain %r", domain)
            except Exception:
                logger.warning("failed to pre-warm domain %r", domain, exc_info=True)
                results[domain] = False
        return results

    def reload_file_domains(self) -> int:
        """Re-scan the prompt directory and load/update domain suffixes.

        Returns the number of domains loaded from files.
        """
        return self._load_file_domains()

    # -- domain detection ---------------------------------------------------

    def detect_domain(
        self,
        message: str,
        context: dict[str, Any] | None = None,
    ) -> str:
        """Classify a user message into the best-matching domain.

        Uses keyword scoring.  Returns ``"general"`` when no domain wins
        convincingly.
        """
        lower = message.lower()
        scores: dict[str, int] = {}

        for domain, keywords in _DOMAIN_KEYWORDS.items():
            score = 0
            for kw in keywords:
                if kw in lower:
                    # Multi-word keywords score higher
                    score += 2 if " " in kw else 1
            if score > 0:
                scores[domain] = score

        if not scores:
            return "general"

        best_domain = max(scores, key=lambda d: scores[d])
        best_score = scores[best_domain]

        # Require a minimum signal to avoid false positives
        if best_score < 2:
            return "general"

        return best_domain

    # -- private helpers ----------------------------------------------------

    def _load_file_domains(self) -> int:
        """Load ``.txt`` files from the prompt directory as domain suffixes."""
        if self._prompt_dir is None or not self._prompt_dir.is_dir():
            return 0

        count = 0
        for path in sorted(self._prompt_dir.glob("*.txt")):
            name = path.stem
            text = path.read_text(encoding="utf-8").strip()
            if text:
                self._domains[name] = text
                count += 1
                logger.debug("loaded domain %r from %s", name, path)
        return count

    @staticmethod
    def _build_context_block(context: dict[str, Any] | None) -> str:
        """Build the context-injection lines from the pipeline context dict."""
        if not context:
            return ""

        lines: list[str] = []
        for key, template in _CONTEXT_TEMPLATE_PARTS.items():
            value = context.get(key)
            if value:
                lines.append(template.format(**{key: value}))

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_builder: PromptBuilder | None = None


def get_prompt_builder() -> PromptBuilder:
    """Return (or create) the module-level :class:`PromptBuilder` singleton."""
    global _builder
    if _builder is None:
        _builder = PromptBuilder()
    return _builder


def reset_prompt_builder() -> None:
    """Clear the singleton — mainly for tests."""
    global _builder
    _builder = None
