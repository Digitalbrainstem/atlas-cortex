"""Core curiosity engine — thinks like an analytical scientist.

Module ownership: Curiosity engine — autonomous improvement
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from cortex.curiosity.hypothesis import HypothesisTracker
from cortex.curiosity.observer import PatternObserver

logger = logging.getLogger(__name__)

_DEFAULT_STATE_DIR = os.path.expanduser("~/.atlas/curiosity")


class CuriosityEngine:
    """Atlas's inner scientist.  Observes, hypothesizes, experiments, learns.

    The engine runs as a background process (or is called periodically)
    and surfaces improvements to the user/system.

    Inspiration: the scientific method applied to AI self-improvement.

    Cycle:
      1. OBSERVE      — Watch tool usage, errors, task durations, user corrections
      2. HYPOTHESIZE   — Form theories about what could be improved
      3. EXPERIMENT    — Propose and run controlled tests
      4. LEARN         — Store validated insights to memory
      5. PROPOSE       — Surface actionable improvements
      6. CROSS-POLLINATE — Apply learnings from one domain to another
    """

    def __init__(self, state_dir: str | Path | None = None) -> None:
        self.observer = PatternObserver()
        self.tracker = HypothesisTracker()
        self._insights: list[dict[str, Any]] = []
        self._state_path = Path(state_dir or _DEFAULT_STATE_DIR)

    async def initialize(self) -> None:
        """Load saved state."""
        self._state_path.mkdir(parents=True, exist_ok=True)
        self.tracker.load(self._state_path / "hypotheses.json")

    # ── Observation hooks (called by CLI agent / REPL) ────────────

    def on_tool_executed(
        self,
        tool_id: str,
        params: dict[str, Any],
        result: Any,
        duration: float,
    ) -> None:
        """Hook: called after every tool execution."""
        self.observer.observe_tool_use(tool_id, params, result, duration)

    def on_error(self, error_type: str, context: str) -> None:
        """Hook: called on errors."""
        self.observer.observe_error(error_type, context)

    def on_task_complete(
        self, task: str, duration: float, iterations: int,
    ) -> None:
        """Hook: called when an agent task completes."""
        self.observer.observe_task_completion(task, duration, iterations)

    def on_user_correction(self, original: str, correction: str) -> None:
        """Hook: called when user corrects Atlas."""
        self.observer.observe_user_correction(original, correction)

    # ── Analysis (run periodically or on-demand) ──────────────────

    async def analyze(self) -> list[dict[str, Any]]:
        """Run a full analysis cycle.  Returns actionable insights."""
        insights: list[dict[str, Any]] = []

        patterns = self.observer.get_notable_patterns()

        for pattern in patterns:
            if pattern.pattern_type == "repeated_task":
                h = self.tracker.propose(
                    statement=f"Automating '{pattern.description}' would save time",
                    category="automation",
                    evidence=[
                        f"Seen {pattern.frequency} times: {e}"
                        for e in pattern.examples[:3]
                    ],
                    experiment="Create a learned tool and measure time saved",
                )
                insights.append({
                    "type": "automation_opportunity",
                    "description": pattern.description,
                    "hypothesis": h.statement,
                    "action": (
                        f"Use tool_propose to create a tool for: "
                        f"{pattern.description}"
                    ),
                })

            elif pattern.pattern_type == "error_pattern":
                h = self.tracker.propose(
                    statement=f"Error '{pattern.description}' has a systematic cause",
                    category="reliability",
                    evidence=pattern.examples[:3],
                    experiment="Analyze error context for common factors",
                )
                insights.append({
                    "type": "reliability_issue",
                    "description": pattern.description,
                    "hypothesis": h.statement,
                })

            elif pattern.pattern_type == "slow_operation":
                h = self.tracker.propose(
                    statement=f"'{pattern.description}' can be optimized",
                    category="performance",
                    evidence=pattern.examples[:3],
                    experiment="Profile and benchmark alternative approaches",
                )
                insights.append({
                    "type": "optimization_opportunity",
                    "description": pattern.description,
                    "hypothesis": h.statement,
                })

            elif pattern.pattern_type == "tool_sequence":
                h = self.tracker.propose(
                    statement=(
                        f"Sequence '{pattern.description}' should be a macro"
                    ),
                    category="automation",
                    evidence=[
                        f"Repeated {pattern.frequency} times: {e}"
                        for e in pattern.examples[:3]
                    ],
                    experiment="Create a combined tool and measure usability",
                )
                insights.append({
                    "type": "automation_opportunity",
                    "description": pattern.description,
                    "hypothesis": h.statement,
                    "action": (
                        f"Use tool_propose to combine: {pattern.description}"
                    ),
                })

        # Validated hypotheses that should be implemented
        for h in self.tracker.get_validated():
            insights.append({
                "type": "validated_improvement",
                "description": h.statement,
                "result": h.result,
                "action": "Implement this improvement",
            })

        self._insights = insights
        return insights

    async def reflect(self) -> str:
        """Generate a reflection on recent work — the scientist's journal.

        Returns a human-readable summary of observations, hypotheses,
        and learnings that Atlas can use to improve its own performance.
        """
        insights = await self.analyze()
        if not insights:
            return (
                "No notable patterns observed yet. "
                "Keep working — I'm watching for improvements."
            )

        parts = ["## Atlas Curiosity Report\n"]

        for insight in insights:
            itype = insight["type"]
            if itype == "automation_opportunity":
                parts.append(
                    f"🔄 **Automation opportunity**: {insight['description']}",
                )
                action = insight.get("action", "")
                if action:
                    parts.append(f"   → {action}\n")
            elif itype == "reliability_issue":
                parts.append(
                    f"⚠️ **Reliability pattern**: {insight['description']}\n",
                )
            elif itype == "optimization_opportunity":
                parts.append(
                    f"⚡ **Optimization**: {insight['description']}\n",
                )
            elif itype == "validated_improvement":
                parts.append(
                    f"✅ **Validated**: {insight['description']}",
                )
                result = insight.get("result", "")
                if result:
                    parts.append(f"   Result: {result}\n")

        return "\n".join(parts)

    async def cross_pollinate(self, current_domain: str) -> list[str]:
        """Look for insights from other domains that might apply here.

        Example: "In the web scraping project, we found that connection pooling
        reduced API latency by 60%.  The current project also makes many HTTP
        calls — should we try pooling here too?"
        """
        suggestions: list[str] = []
        try:
            from cortex.memory.controller import get_memory_system

            ms = get_memory_system()
            if ms:
                hits = await ms.recall(
                    f"optimization improvement pattern {current_domain}",
                    user_id="cli_user",
                    top_k=5,
                )
                for hit in hits:
                    if hit.score > 0.5:
                        suggestions.append(
                            f"From previous work: {hit.text[:200]}",
                        )
        except Exception:  # noqa: BLE001
            logger.debug("Cross-pollinate: memory system unavailable")
        return suggestions

    async def save_state(self) -> None:
        """Persist curiosity state to disk."""
        self.tracker.save(self._state_path / "hypotheses.json")

    # ── Integration with agent system prompt ──────────────────────

    def get_system_prompt_addition(self) -> str:
        """Return text to append to the agent's system prompt."""
        return (
            "\n## Scientific Thinking\n\n"
            "You have an inner scientist. As you work:\n"
            "- Question assumptions: "
            '"Is this the best approach? What alternatives exist?"\n'
            "- Notice patterns: "
            '"I\'ve done this 3 times — should this be automated?"\n'
            "- Propose experiments: "
            '"I think caching would help. Let me benchmark before and after."\n'
            "- Learn from errors: "
            '"This failed because X. I\'ll remember this for next time."\n'
            "- Cross-reference: "
            '"This is similar to a pattern I saw in another project."\n'
            "\n"
            "When you notice an improvement opportunity, "
            "use tool_propose to create automation.\n"
            "When you validate an approach works better, "
            "store it to memory for future use.\n"
        )
