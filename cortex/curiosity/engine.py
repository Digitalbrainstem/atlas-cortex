"""Core curiosity engine — thinks like an analytical scientist.

Architecture based on Derek Thomas's original design.  Five sub-modules
collaborate inside the engine:

1. **EleganceScorer**      — measures solution beauty
2. **ResidualAnalyzer**    — examines what models get wrong
3. **PerspectiveRotator**  — 8 scientific lenses, max cognitive distance
4. **DriveController**     — research/practical/urgent exploration depth
5. **CrossDomainConnector** — archetype-based analogy graph

The *PatternObserver* and *HypothesisTracker* from the first version are
retained — they handle the operational side (tool usage patterns, workflow
improvements) while the five scientific modules handle analytical depth.

Module ownership: Curiosity engine — autonomous improvement
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Sequence

from cortex.curiosity.connector import CrossDomainConnector
from cortex.curiosity.drive import DriveController
from cortex.curiosity.elegance import EleganceBreakdown, EleganceScorer
from cortex.curiosity.hypothesis import HypothesisTracker
from cortex.curiosity.observer import PatternObserver
from cortex.curiosity.perspectives import Lens, PerspectiveRotator
from cortex.curiosity.residuals import ResidualAnalyzer

logger = logging.getLogger(__name__)

_DEFAULT_STATE_DIR = os.path.expanduser("~/.atlas/curiosity")


class CuriosityEngine:
    """Atlas's inner scientist.  Observes, hypothesizes, experiments, learns.

    The engine has two complementary halves:

    **Operational side** (PatternObserver + HypothesisTracker):
      Watches tool usage, errors, task durations, user corrections.
      Proposes workflow improvements and automation.

    **Analytical side** (EleganceScorer + ResidualAnalyzer +
    PerspectiveRotator + DriveController + CrossDomainConnector):
      Applies the scientific method to any analysis task.
      Scores elegance, examines residuals, rotates perspectives,
      and suggests cross-domain analogies.

    Cycle:
      1. OBSERVE        — Watch behaviour / score solutions
      2. HYPOTHESIZE    — Form theories about improvements
      3. EXPERIMENT     — Propose and run controlled tests
      4. LEARN          — Store validated insights to memory
      5. PROPOSE        — Surface actionable improvements
      6. CROSS-POLLINATE — Apply learnings across domains
    """

    def __init__(
        self,
        state_dir: str | Path | None = None,
        drive_mode: str = "practical",
    ) -> None:
        # Operational modules
        self.observer = PatternObserver()
        self.tracker = HypothesisTracker()

        # Analytical modules (Derek Thomas design)
        self.elegance = EleganceScorer()
        self.residuals = ResidualAnalyzer()
        self.perspectives = PerspectiveRotator()
        self.drive = DriveController(mode=drive_mode)
        self.connector = CrossDomainConnector()

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

    # ── Analytical pipeline (scientific method) ───────────────────

    def score_elegance(
        self,
        solution_text: str,
        residuals_data: Sequence[float] | None = None,
        problem_text: str = "",
    ) -> EleganceBreakdown:
        """Score a solution's elegance.  Lower total = more beautiful."""
        return self.elegance.score(solution_text, residuals_data, problem_text)

    def analyze_residuals(
        self,
        predictions: Sequence[float],
        actuals: Sequence[float],
        metadata: dict[str, Sequence[Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Examine prediction errors for missing physics.

        Returns a list of findings as dicts.
        """
        findings = self.residuals.analyze(predictions, actuals, metadata)
        return [
            {
                "type": f.finding_type,
                "field": f.field,
                "detail": f.detail,
                "strength": f.strength,
            }
            for f in findings
        ]

    def next_perspective(
        self,
        current_score: float = 0.0,
        current_lens: str | None = None,
    ) -> Lens | None:
        """Get the next scientific lens to try (maximises cognitive distance)."""
        return self.perspectives.next_lens(current_score, current_lens)

    def should_keep_exploring(self, current_score: float) -> bool:
        """Ask the drive controller if we should keep going."""
        return self.drive.should_continue(current_score)

    def suggest_analogies(
        self, concept: str, domain: str = "transformers",
    ) -> list[dict[str, str]]:
        """Get cross-domain analogies for a concept."""
        analogies = self.connector.suggest_analogies(concept, domain)
        return [
            {
                "source": a.source_domain,
                "target": a.target_domain,
                "archetype": a.archetype,
                "analogy": a.analogy,
                "prompt": a.prompt,
            }
            for a in analogies
        ]

    # ── Operational analysis (pattern-based) ──────────────────────

    async def analyze(self) -> list[dict[str, Any]]:
        """Run a full operational analysis cycle.  Returns actionable insights."""
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
        """Look for insights from other domains via persistent memory.

        Complements ``suggest_analogies()`` which uses the static archetype
        graph — this method searches Atlas's long-term memory for insights
        from past sessions.
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
            "You have an inner scientist — the Curiosity Engine. As you work:\n"
            "- Question assumptions: "
            '"Is this the best approach? What alternatives exist?"\n'
            "- Notice patterns: "
            '"I\'ve done this 3 times — should this be automated?"\n'
            "- Propose experiments: "
            '"I think caching would help. Let me benchmark before and after."\n'
            "- Learn from errors: "
            '"This failed because X. I\'ll remember this for next time."\n'
            "- Rotate perspectives: "
            '"What if I view this as a fluid dynamics problem? An information '
            'theory problem?"\n'
            "- Seek elegance: "
            '"Can I reduce the number of arbitrary constants? '
            'Are the residuals random?"\n'
            "- Cross-pollinate: "
            '"This bottleneck pattern maps to Venturi constriction in fluids."\n'
            "\n"
            "When you notice an improvement opportunity, "
            "use tool_propose to create automation.\n"
            "When you validate an approach works better, "
            "store it to memory for future use.\n"
        )

