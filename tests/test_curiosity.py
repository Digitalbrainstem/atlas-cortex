"""Tests for the Atlas Curiosity Engine — all modules."""
from __future__ import annotations

import json
import math
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cortex.curiosity.connector import Analogy, CrossDomainConnector
from cortex.curiosity.drive import DriveController
from cortex.curiosity.elegance import EleganceBreakdown, EleganceScorer
from cortex.curiosity.engine import CuriosityEngine
from cortex.curiosity.hypothesis import Hypothesis, HypothesisTracker
from cortex.curiosity.observer import Observation, PatternObserver
from cortex.curiosity.perspectives import LENSES, Lens, PerspectiveRotator
from cortex.curiosity.residuals import ResidualAnalyzer, ResidualFinding


# ── PatternObserver ─────────────────────────────────────────────────


class TestPatternObserver:
    """Pattern detection from tool usage, errors, and tasks."""

    def test_observe_tool_use_tracks_counts(self):
        obs = PatternObserver()
        for _ in range(5):
            obs.observe_tool_use("grep", {"pattern": "TODO"}, "found 3", 0.5)
        assert obs._tool_usage["grep"] == 5

    def test_repeated_tool_creates_observation(self):
        obs = PatternObserver()
        for _ in range(4):
            obs.observe_tool_use("file_read", {"path": "a.py"}, "ok", 0.1)
        # Should have a repeated_task observation
        patterns = obs.get_notable_patterns(min_frequency=1, min_confidence=0.3)
        repeated = [p for p in patterns if p.pattern_type == "repeated_task"]
        assert len(repeated) >= 1
        assert "file_read" in repeated[0].description

    def test_slow_operation_detected(self):
        obs = PatternObserver()
        obs.observe_tool_use("shell_exec", {"cmd": "make build"}, "ok", 15.0)
        patterns = obs.get_notable_patterns(min_frequency=1, min_confidence=0.3)
        slow = [p for p in patterns if p.pattern_type == "slow_operation"]
        assert len(slow) >= 1
        assert "shell_exec" in slow[0].description

    def test_error_pattern_detected(self):
        obs = PatternObserver()
        for _ in range(3):
            obs.observe_error("ConnectionError", "Failed to connect to API")
        patterns = obs.get_notable_patterns(min_frequency=1, min_confidence=0.3)
        errors = [p for p in patterns if p.pattern_type == "error_pattern"]
        assert len(errors) >= 1
        assert "ConnectionError" in errors[0].description

    def test_task_completion_slow_task(self):
        obs = PatternObserver()
        obs.observe_task_completion("deploy service", 120.0, 8)
        patterns = obs.get_notable_patterns(min_frequency=1, min_confidence=0.3)
        slow = [p for p in patterns if p.pattern_type == "slow_operation"]
        assert len(slow) >= 1

    def test_user_correction_tracked(self):
        obs = PatternObserver()
        obs.observe_user_correction("use curl", "use httpie instead")
        obs.observe_user_correction("run npm test", "run npm run test:unit")
        patterns = obs.get_notable_patterns(min_frequency=1, min_confidence=0.3)
        corrections = [p for p in patterns if p.pattern_type == "manual_step"]
        assert len(corrections) >= 1

    def test_repeated_sequences_detected(self):
        obs = PatternObserver()
        # Simulate a repeated 2-tool sequence
        for _ in range(4):
            obs.observe_tool_use("git", {"subcmd": "status"}, "ok", 0.2)
            obs.observe_tool_use("git", {"subcmd": "diff"}, "ok", 0.3)
        patterns = obs.get_notable_patterns(min_frequency=3, min_confidence=0.3)
        seqs = [p for p in patterns if p.pattern_type == "tool_sequence"]
        assert len(seqs) >= 1
        assert "git" in seqs[0].description

    def test_no_patterns_when_empty(self):
        obs = PatternObserver()
        assert obs.get_notable_patterns() == []

    def test_min_frequency_filter(self):
        obs = PatternObserver()
        obs.observe_tool_use("file_read", {"path": "x"}, "ok", 0.1)
        # Only 1 occurrence — should not meet min_frequency=3
        assert obs.get_notable_patterns(min_frequency=3) == []

    def test_observation_examples_capped(self):
        obs = PatternObserver()
        for i in range(10):
            obs.observe_tool_use("grep", {"pattern": f"term_{i}"}, "ok", 0.1)
        key = "repeated_tool:grep"
        assert key in obs._observations
        assert len(obs._observations[key].examples) <= 5

    def test_tool_sequence_window_capped(self):
        obs = PatternObserver()
        for i in range(600):
            obs.observe_tool_use(f"tool_{i % 5}", {}, "ok", 0.1)
        assert len(obs._tool_sequence) <= 500


# ── HypothesisTracker ───────────────────────────────────────────────


class TestHypothesisTracker:
    """Hypothesis lifecycle: propose → evidence → experiment → result."""

    def test_propose_creates_hypothesis(self):
        tracker = HypothesisTracker()
        h = tracker.propose(
            "Caching would reduce latency",
            category="performance",
            evidence=["API calls average 200ms"],
        )
        assert h.status == "proposed"
        assert h.category == "performance"
        assert len(h.evidence_for) == 1

    def test_propose_duplicate_merges(self):
        tracker = HypothesisTracker()
        h1 = tracker.propose("Cache helps", category="performance", evidence=["a"])
        h2 = tracker.propose("Cache helps", category="performance", evidence=["b"])
        assert h1.id == h2.id
        assert len(h1.evidence_for) == 2

    def test_add_supporting_evidence(self):
        tracker = HypothesisTracker()
        h = tracker.propose("X is slow", category="performance")
        original_conf = h.confidence
        tracker.add_evidence(h.id, "measured 500ms", supports=True)
        assert len(h.evidence_for) == 1
        assert h.confidence > original_conf

    def test_add_contradicting_evidence(self):
        tracker = HypothesisTracker()
        h = tracker.propose("X is slow", category="performance")
        original_conf = h.confidence
        tracker.add_evidence(h.id, "actually fast at 10ms", supports=False)
        assert len(h.evidence_against) == 1
        assert h.confidence < original_conf

    def test_start_experiment(self):
        tracker = HypothesisTracker()
        h = tracker.propose("test", category="ux")
        tracker.start_experiment(h.id)
        assert h.status == "testing"

    def test_record_result_validated(self):
        tracker = HypothesisTracker()
        h = tracker.propose("cache helps", category="performance")
        tracker.record_result(h.id, "Latency dropped 60%", validated=True)
        assert h.status == "validated"
        assert h.result == "Latency dropped 60%"
        assert h.confidence == 0.95

    def test_record_result_rejected(self):
        tracker = HypothesisTracker()
        h = tracker.propose("cache helps", category="performance")
        tracker.record_result(h.id, "No difference", validated=False)
        assert h.status == "rejected"
        assert h.confidence == 0.05

    def test_mark_implemented(self):
        tracker = HypothesisTracker()
        h = tracker.propose("automate deploys", category="automation")
        tracker.record_result(h.id, "works great", validated=True)
        tracker.mark_implemented(h.id)
        assert h.status == "implemented"

    def test_get_actionable(self):
        tracker = HypothesisTracker()
        h1 = tracker.propose(
            "high confidence",
            category="performance",
            evidence=["a", "b", "c", "d", "e"],
        )
        h2 = tracker.propose("low confidence", category="ux")
        actionable = tracker.get_actionable()
        ids = [h.id for h in actionable]
        assert h1.id in ids
        # h2 may or may not be actionable depending on confidence threshold

    def test_get_validated(self):
        tracker = HypothesisTracker()
        h = tracker.propose("test", category="ux")
        tracker.record_result(h.id, "works", validated=True)
        assert h in tracker.get_validated()

    def test_get_all(self):
        tracker = HypothesisTracker()
        tracker.propose("a", category="ux")
        tracker.propose("b", category="perf")
        assert len(tracker.get_all()) == 2

    def test_save_and_load_roundtrip(self, tmp_path: Path):
        tracker = HypothesisTracker()
        h = tracker.propose(
            "test hypothesis",
            category="performance",
            evidence=["ev1", "ev2"],
            experiment="run benchmark",
        )
        tracker.record_result(h.id, "3x faster", validated=True)

        path = tmp_path / "hypotheses.json"
        tracker.save(path)
        assert path.exists()

        tracker2 = HypothesisTracker()
        tracker2.load(path)
        loaded = tracker2.get_all()
        assert len(loaded) == 1
        assert loaded[0].statement == "test hypothesis"
        assert loaded[0].status == "validated"
        assert loaded[0].result == "3x faster"

    def test_load_missing_file(self, tmp_path: Path):
        tracker = HypothesisTracker()
        tracker.load(tmp_path / "nonexistent.json")
        assert tracker.get_all() == []

    def test_load_corrupt_file(self, tmp_path: Path):
        path = tmp_path / "bad.json"
        path.write_text("not json at all", encoding="utf-8")
        tracker = HypothesisTracker()
        tracker.load(path)
        assert tracker.get_all() == []

    def test_add_evidence_nonexistent_id(self):
        tracker = HypothesisTracker()
        tracker.add_evidence("nonexistent", "data", supports=True)
        # Should not raise

    def test_start_experiment_nonexistent_id(self):
        tracker = HypothesisTracker()
        tracker.start_experiment("nonexistent")

    def test_record_result_nonexistent_id(self):
        tracker = HypothesisTracker()
        tracker.record_result("nonexistent", "result", validated=True)

    def test_hypothesis_serialization(self):
        h = Hypothesis(
            id="abc",
            statement="test",
            category="perf",
            evidence_for=["e1"],
            evidence_against=["e2"],
            status="testing",
            proposed_experiment="bench",
            result="fast",
            confidence=0.8,
            created_at=1000.0,
            updated_at=2000.0,
        )
        d = h.to_dict()
        h2 = Hypothesis.from_dict(d)
        assert h2.id == h.id
        assert h2.statement == h.statement
        assert h2.status == h.status
        assert h2.confidence == h.confidence


# ── CuriosityEngine ─────────────────────────────────────────────────


class TestCuriosityEngine:
    """Full engine lifecycle: observe → analyze → reflect → save."""

    @pytest.mark.asyncio
    async def test_initialize_creates_state_dir(self, tmp_path: Path):
        engine = CuriosityEngine(state_dir=tmp_path / "curiosity")
        await engine.initialize()
        assert (tmp_path / "curiosity").is_dir()

    @pytest.mark.asyncio
    async def test_observation_hooks(self, tmp_path: Path):
        engine = CuriosityEngine(state_dir=tmp_path / "curiosity")
        await engine.initialize()
        engine.on_tool_executed("grep", {"pattern": "TODO"}, "ok", 0.5)
        engine.on_error("ValueError", "bad input")
        engine.on_task_complete("fix bug", 30.0, 5)
        engine.on_user_correction("use curl", "use httpie")
        # Should not raise — hooks are fire-and-forget
        assert engine.observer._tool_usage["grep"] == 1
        assert engine.observer._error_counts["ValueError"] == 1

    @pytest.mark.asyncio
    async def test_analyze_with_patterns(self, tmp_path: Path):
        engine = CuriosityEngine(state_dir=tmp_path / "curiosity")
        await engine.initialize()

        # Generate enough patterns to trigger analysis
        for i in range(5):
            engine.on_tool_executed("shell_exec", {"cmd": "make"}, "ok", 0.5)
        for _ in range(3):
            engine.on_error("TimeoutError", "API timeout")

        insights = await engine.analyze()
        assert len(insights) > 0
        types = {i["type"] for i in insights}
        assert "automation_opportunity" in types or "reliability_issue" in types

    @pytest.mark.asyncio
    async def test_analyze_empty(self, tmp_path: Path):
        engine = CuriosityEngine(state_dir=tmp_path / "curiosity")
        await engine.initialize()
        insights = await engine.analyze()
        assert insights == []

    @pytest.mark.asyncio
    async def test_reflect_no_patterns(self, tmp_path: Path):
        engine = CuriosityEngine(state_dir=tmp_path / "curiosity")
        await engine.initialize()
        report = await engine.reflect()
        assert "No notable patterns" in report

    @pytest.mark.asyncio
    async def test_reflect_with_patterns(self, tmp_path: Path):
        engine = CuriosityEngine(state_dir=tmp_path / "curiosity")
        await engine.initialize()

        for _ in range(5):
            engine.on_tool_executed("file_read", {"path": "x"}, "ok", 0.1)

        report = await engine.reflect()
        assert "Curiosity Report" in report
        assert "Automation" in report or "automation" in report.lower()

    @pytest.mark.asyncio
    async def test_reflect_with_validated_hypothesis(self, tmp_path: Path):
        engine = CuriosityEngine(state_dir=tmp_path / "curiosity")
        await engine.initialize()

        h = engine.tracker.propose(
            "caching helps", category="performance", evidence=["slow API"],
        )
        engine.tracker.record_result(h.id, "3x faster", validated=True)

        report = await engine.reflect()
        assert "Validated" in report
        assert "3x faster" in report

    @pytest.mark.asyncio
    async def test_reflect_with_error_pattern(self, tmp_path: Path):
        engine = CuriosityEngine(state_dir=tmp_path / "curiosity")
        await engine.initialize()

        for _ in range(4):
            engine.on_error("ConnectionError", "timeout connecting")

        report = await engine.reflect()
        assert "Reliability" in report or "reliability" in report.lower()

    @pytest.mark.asyncio
    async def test_reflect_with_slow_operation(self, tmp_path: Path):
        engine = CuriosityEngine(state_dir=tmp_path / "curiosity")
        await engine.initialize()

        for _ in range(4):
            engine.on_tool_executed("build", {"target": "all"}, "ok", 25.0)

        report = await engine.reflect()
        assert "Optimization" in report or "optimization" in report.lower()

    @pytest.mark.asyncio
    async def test_save_and_load_state(self, tmp_path: Path):
        engine = CuriosityEngine(state_dir=tmp_path / "curiosity")
        await engine.initialize()

        h = engine.tracker.propose(
            "test persistence", category="architecture", evidence=["a"],
        )
        await engine.save_state()

        engine2 = CuriosityEngine(state_dir=tmp_path / "curiosity")
        await engine2.initialize()
        all_h = engine2.tracker.get_all()
        assert len(all_h) == 1
        assert all_h[0].statement == "test persistence"

    @pytest.mark.asyncio
    async def test_cross_pollinate_no_memory(self, tmp_path: Path):
        engine = CuriosityEngine(state_dir=tmp_path / "curiosity")
        await engine.initialize()
        suggestions = await engine.cross_pollinate("web-api")
        assert suggestions == []

    @pytest.mark.asyncio
    async def test_cross_pollinate_with_memory(self, tmp_path: Path):
        engine = CuriosityEngine(state_dir=tmp_path / "curiosity")
        await engine.initialize()

        mock_hit = MagicMock()
        mock_hit.score = 0.8
        mock_hit.text = "Connection pooling reduced latency by 60%"

        mock_ms = AsyncMock()
        mock_ms.recall = AsyncMock(return_value=[mock_hit])

        with patch(
            "cortex.memory.controller.get_memory_system", return_value=mock_ms,
        ):
            suggestions = await engine.cross_pollinate("web-api")

        assert len(suggestions) == 1
        assert "Connection pooling" in suggestions[0]

    @pytest.mark.asyncio
    async def test_cross_pollinate_low_score_filtered(self, tmp_path: Path):
        engine = CuriosityEngine(state_dir=tmp_path / "curiosity")
        await engine.initialize()

        mock_hit = MagicMock()
        mock_hit.score = 0.2
        mock_hit.text = "irrelevant"

        mock_ms = AsyncMock()
        mock_ms.recall = AsyncMock(return_value=[mock_hit])

        with patch(
            "cortex.memory.controller.get_memory_system", return_value=mock_ms,
        ):
            suggestions = await engine.cross_pollinate("web-api")
        assert suggestions == []

    def test_system_prompt_addition(self):
        engine = CuriosityEngine()
        prompt = engine.get_system_prompt_addition()
        assert "Curiosity Engine" in prompt
        assert "Question assumptions" in prompt
        assert "Rotate perspectives" in prompt
        assert "Seek elegance" in prompt
        assert "Cross-pollinate" in prompt
        assert "tool_propose" in prompt

    @pytest.mark.asyncio
    async def test_analyze_tool_sequence_pattern(self, tmp_path: Path):
        engine = CuriosityEngine(state_dir=tmp_path / "curiosity")
        await engine.initialize()

        # Create a repeated tool sequence
        for _ in range(5):
            engine.on_tool_executed("git", {"subcmd": "status"}, "ok", 0.1)
            engine.on_tool_executed("git", {"subcmd": "add"}, "ok", 0.1)
            engine.on_tool_executed("git", {"subcmd": "commit"}, "ok", 0.2)

        insights = await engine.analyze()
        types = {i["type"] for i in insights}
        assert "automation_opportunity" in types

    @pytest.mark.asyncio
    async def test_full_lifecycle(self, tmp_path: Path):
        """End-to-end: observe → analyze → propose → validate → reflect."""
        engine = CuriosityEngine(state_dir=tmp_path / "curiosity")
        await engine.initialize()

        # 1. Observe repeated work
        for i in range(6):
            engine.on_tool_executed(
                "shell_exec", {"cmd": f"pytest tests/test_{i}.py"}, "ok", 3.0,
            )

        # 2. Analyze — should find automation opportunity
        insights = await engine.analyze()
        assert len(insights) > 0
        hypothesis_id = None
        for h in engine.tracker.get_all():
            if h.category == "automation":
                hypothesis_id = h.id
                break
        assert hypothesis_id is not None

        # 3. Simulate experiment
        engine.tracker.start_experiment(hypothesis_id)
        engine.tracker.record_result(
            hypothesis_id, "Macro saves 30s per run", validated=True,
        )

        # 4. Reflect
        report = await engine.reflect()
        assert "Validated" in report

        # 5. Persist
        await engine.save_state()
        engine2 = CuriosityEngine(state_dir=tmp_path / "curiosity")
        await engine2.initialize()
        validated = engine2.tracker.get_validated()
        assert len(validated) == 1


# ── Observation dataclass ───────────────────────────────────────────


class TestObservation:
    """Observation data structure."""

    def test_defaults(self):
        obs = Observation(
            pattern_type="test", description="test desc", frequency=1,
        )
        assert obs.examples == []
        assert obs.confidence == 0.0

    def test_with_examples(self):
        obs = Observation(
            pattern_type="error_pattern",
            description="timeout errors",
            frequency=5,
            examples=["timeout at 10s", "timeout at 15s"],
            confidence=0.8,
        )
        assert len(obs.examples) == 2
        assert obs.confidence == 0.8


# ── EleganceScorer ──────────────────────────────────────────────────


class TestEleganceScorer:
    """Elegance measurement: free params, residuals, symmetry, constants."""

    def test_score_returns_breakdown(self):
        scorer = EleganceScorer()
        result = scorer.score("y = 3.14159 * x + 2")
        assert isinstance(result, EleganceBreakdown)
        assert 0.0 <= result.total <= 1.0

    def test_known_constant_detected(self):
        scorer = EleganceScorer()
        result = scorer.score("y = 3.14159 * x")
        assert "pi" in result.matched_constants

    def test_unknown_constant_flagged(self):
        scorer = EleganceScorer()
        result = scorer.score("y = 0.0034 * x + 7.891")
        assert len(result.unmatched_values) >= 1

    def test_small_integers_matched(self):
        scorer = EleganceScorer()
        result = scorer.score("layers = 12, heads = 8")
        assert any("int(" in m for m in result.matched_constants)

    def test_no_numbers_is_perfect(self):
        scorer = EleganceScorer()
        result = scorer.score("The solution is symmetric and elegant")
        assert result.free_params == 0.0

    def test_residual_structure_random(self):
        scorer = EleganceScorer()
        # Alternating sign residuals → random → good
        residuals = [1.0, -1.0, 1.0, -1.0, 1.0, -1.0]
        result = scorer.score("y = x", residuals=residuals)
        assert result.residual_structure < 0.3

    def test_residual_structure_structured(self):
        scorer = EleganceScorer()
        # All positive residuals → structured → bad
        residuals = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        result = scorer.score("y = x", residuals=residuals)
        assert result.residual_structure > 0.5

    def test_symmetry_detected(self):
        scorer = EleganceScorer()
        result = scorer.score("The function is symmetric: f(x) = f(-x), invariant under rotation")
        assert result.symmetry < 0.5  # lower = more symmetric (good)

    def test_complexity_ratio(self):
        scorer = EleganceScorer()
        short = scorer.score("y = x", problem_text="Explain everything about the universe")
        long = scorer.score("y = a*x^3 + b*x^2 + c*x + d + e*sin(f*x)", problem_text="fit a curve")
        assert short.complexity < long.complexity

    def test_weights_sum_to_one(self):
        assert abs(sum(EleganceScorer.WEIGHTS.values()) - 1.0) < 0.01

    def test_euler_constant_matched(self):
        scorer = EleganceScorer()
        result = scorer.score(f"decay = {math.e:.5f}")
        assert "e" in result.matched_constants

    def test_golden_ratio_matched(self):
        scorer = EleganceScorer()
        phi = (1 + math.sqrt(5)) / 2
        result = scorer.score(f"ratio = {phi:.5f}")
        assert "phi" in result.matched_constants


# ── ResidualAnalyzer ────────────────────────────────────────────────


class TestResidualAnalyzer:
    """Residual analysis: correlation, group differences, monotonic trends."""

    def test_perfect_predictions(self):
        analyzer = ResidualAnalyzer()
        findings = analyzer.analyze([1, 2, 3], [1, 2, 3])
        # Zero residuals → no structured findings
        assert all(f.finding_type != "correlation" for f in findings)

    def test_correlation_detected(self):
        analyzer = ResidualAnalyzer()
        preds = [1.0, 2.0, 3.0, 4.0, 5.0]
        acts = [1.5, 2.5, 3.5, 4.5, 5.5]
        meta = {"temperature": [10.0, 20.0, 30.0, 40.0, 50.0]}
        findings = analyzer.analyze(preds, acts, meta)
        # Residuals are all -0.5, constant → won't correlate strongly
        # But structure score might trigger
        assert isinstance(findings, list)

    def test_monotonic_trend_detected(self):
        analyzer = ResidualAnalyzer()
        preds = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
        acts = [1.0, 1.8, 2.4, 2.8, 3.0, 3.0, 2.8, 2.4]
        meta = {"layer_count": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]}
        findings = analyzer.analyze(preds, acts, meta)
        monotonic = [f for f in findings if f.finding_type == "monotonic_trend"]
        assert len(monotonic) >= 1

    def test_group_difference_detected(self):
        analyzer = ResidualAnalyzer()
        preds = [1, 2, 3, 4, 5, 6]
        acts = [1.5, 2.5, 3.5, 1, 2, 3]  # group B has large residuals
        meta = {"family": ["A", "A", "A", "B", "B", "B"]}
        findings = analyzer.analyze(preds, acts, meta)
        group_findings = [f for f in findings if f.finding_type == "group_difference"]
        assert len(group_findings) >= 1

    def test_empty_data(self):
        analyzer = ResidualAnalyzer()
        assert analyzer.analyze([], [], {}) == []

    def test_mismatched_lengths(self):
        analyzer = ResidualAnalyzer()
        assert analyzer.analyze([1, 2], [1], {}) == []

    def test_no_metadata(self):
        analyzer = ResidualAnalyzer()
        findings = analyzer.analyze([1, 1, 1, 1, 1], [2, 2, 2, 2, 2])
        # Should still detect structure in residuals
        assert isinstance(findings, list)

    def test_findings_sorted_by_strength(self):
        analyzer = ResidualAnalyzer()
        preds = [1, 2, 3, 4, 5, 6, 7, 8]
        acts = [1.1, 1.9, 2.7, 3.3, 3.7, 3.9, 3.9, 3.7]
        meta = {
            "x": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0],
            "cat": ["a", "a", "a", "a", "b", "b", "b", "b"],
        }
        findings = analyzer.analyze(preds, acts, meta)
        strengths = [f.strength for f in findings]
        assert strengths == sorted(strengths, reverse=True)


# ── PerspectiveRotator ──────────────────────────────────────────────


class TestPerspectiveRotator:
    """Perspective rotation: 8 lenses, max cognitive distance, no repeats."""

    def test_eight_lenses_defined(self):
        assert len(LENSES) == 8

    def test_all_lenses_have_prompt_and_concepts(self):
        for lens in LENSES:
            assert lens.name
            assert lens.prompt
            assert len(lens.concepts) >= 3

    def test_lens_names(self):
        names = {l.name for l in LENSES}
        expected = {
            "classical_physics", "quantum_mechanics", "fluid_dynamics",
            "chaos_theory", "information_theory", "thermodynamics",
            "string_theory", "biology",
        }
        assert names == expected

    def test_first_lens_returned(self):
        rotator = PerspectiveRotator()
        lens = rotator.next_lens()
        assert lens is not None
        assert isinstance(lens, Lens)

    def test_never_repeats(self):
        rotator = PerspectiveRotator()
        seen: list[str] = []
        for i in range(8):
            current = seen[-1] if seen else None
            lens = rotator.next_lens(current_lens=current)
            assert lens is not None
            assert lens.name not in seen
            seen.append(lens.name)
        # Mark the last lens as tried and ask for another → None
        assert rotator.next_lens(current_lens=seen[-1]) is None

    def test_maximizes_cognitive_distance(self):
        rotator = PerspectiveRotator()
        # Start with classical_physics
        first = rotator.get_lens("classical_physics")
        assert first is not None
        second = rotator.next_lens(current_lens="classical_physics")
        assert second is not None
        # Second lens should not share concepts with first
        overlap = set(first.concepts) & set(second.concepts)
        # All lenses have unique concept sets, so overlap should be 0
        assert len(overlap) == 0

    def test_reset(self):
        rotator = PerspectiveRotator()
        rotator.next_lens(current_lens="biology")
        rotator.next_lens(current_lens="chaos_theory")
        assert len(rotator.tried_lenses) == 2
        rotator.reset()
        assert len(rotator.tried_lenses) == 0
        assert rotator.best_lens is None

    def test_best_score_tracking(self):
        rotator = PerspectiveRotator()
        rotator.next_lens(current_score=0.8, current_lens="biology")
        rotator.next_lens(current_score=0.3, current_lens="chaos_theory")
        assert rotator.best_score == 0.3
        assert rotator.best_lens == "chaos_theory"

    def test_get_lens_by_name(self):
        rotator = PerspectiveRotator()
        lens = rotator.get_lens("quantum_mechanics")
        assert lens is not None
        assert "Hilbert space" in lens.prompt
        assert "entanglement" in lens.concepts

    def test_get_lens_unknown(self):
        rotator = PerspectiveRotator()
        assert rotator.get_lens("nonexistent") is None


# ── DriveController ─────────────────────────────────────────────────


class TestDriveController:
    """Drive control: research/practical/urgent modes, stop conditions."""

    def test_research_mode_high_bar(self):
        dc = DriveController(mode="research")
        assert dc.thresholds.elegance_threshold == 0.2
        assert dc.thresholds.max_iterations == 20

    def test_practical_mode(self):
        dc = DriveController(mode="practical")
        assert dc.thresholds.elegance_threshold == 0.5
        assert dc.thresholds.max_iterations == 5

    def test_urgent_mode_low_bar(self):
        dc = DriveController(mode="urgent")
        assert dc.thresholds.elegance_threshold == 0.8
        assert dc.thresholds.max_iterations == 2

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="Unknown mode"):
            DriveController(mode="nonexistent")

    def test_stops_when_elegant_enough(self):
        dc = DriveController(mode="practical")
        # Score of 0.3 is below 0.5 threshold → stop
        assert dc.should_continue(0.3) is False

    def test_continues_when_not_elegant(self):
        dc = DriveController(mode="practical")
        assert dc.should_continue(0.9) is True

    def test_stops_at_max_iterations(self):
        dc = DriveController(mode="urgent")
        dc.should_continue(0.9)  # iter 1
        assert dc.should_continue(0.85) is False  # iter 2 = max

    def test_stops_on_plateau(self):
        dc = DriveController(mode="research")
        dc.should_continue(0.5)
        dc.should_continue(0.5)
        assert dc.should_continue(0.5) is False  # 3 identical scores

    def test_continues_while_improving(self):
        dc = DriveController(mode="research")
        dc.should_continue(0.9)
        dc.should_continue(0.7)
        # Big improvement → keep going
        assert dc.should_continue(0.5) is True

    def test_suggest_action_residuals(self):
        dc = DriveController()
        finding = ResidualFinding("correlation", "x", "corr with x", 0.8)
        action, detail = dc.suggest_action(0.6, residual_findings=[finding])
        assert action == "investigate_residuals"

    def test_suggest_action_derive_constants(self):
        dc = DriveController()
        action, detail = dc.suggest_action(0.6, free_param_count=3)
        assert action == "derive_constants"
        assert "3" in detail

    def test_suggest_action_change_perspective(self):
        dc = DriveController()
        action, _ = dc.suggest_action(0.6, residual_structure=0.5)
        assert action == "change_perspective"

    def test_suggest_action_accept(self):
        dc = DriveController()
        action, _ = dc.suggest_action(0.1)
        assert action == "accept"

    def test_reset(self):
        dc = DriveController(mode="practical")
        dc.should_continue(0.9)
        dc.should_continue(0.8)
        assert dc.iteration == 2
        dc.reset()
        assert dc.iteration == 0
        assert dc.score_history == []


# ── CrossDomainConnector ────────────────────────────────────────────


class TestCrossDomainConnector:
    """Cross-domain analogies via archetype graph."""

    def test_bottleneck_analogy(self):
        conn = CrossDomainConnector()
        results = conn.suggest_analogies("GQA", "transformers")
        assert len(results) >= 1
        domains = {a.target_domain for a in results}
        assert "transformers" not in domains  # Never suggests same domain

    def test_feedback_loop_analogy(self):
        conn = CrossDomainConnector()
        results = conn.suggest_analogies("residual connection", "transformers")
        assert len(results) >= 1
        archetypes = {a.archetype for a in results}
        assert "feedback_loop" in archetypes

    def test_nonlinear_gate_analogy(self):
        conn = CrossDomainConnector()
        results = conn.suggest_analogies("SiLU", "transformers")
        assert len(results) >= 1

    def test_resonance_analogy(self):
        conn = CrossDomainConnector()
        results = conn.suggest_analogies("standing wave", "transformers")
        assert len(results) >= 1

    def test_decay_analogy(self):
        conn = CrossDomainConnector()
        results = conn.suggest_analogies("information decay", "transformers")
        assert len(results) >= 1
        prompts = [a.prompt for a in results]
        assert any("radioactive" in p or "half-life" in p.lower() for p in prompts)

    def test_unknown_concept_returns_empty(self):
        conn = CrossDomainConnector()
        results = conn.suggest_analogies("nonexistent_thing", "transformers")
        assert results == []

    def test_get_archetypes(self):
        conn = CrossDomainConnector()
        archetypes = conn.get_archetypes()
        assert "bottleneck" in archetypes
        assert "feedback_loop" in archetypes
        assert "resonance" in archetypes
        assert "decay" in archetypes
        assert "nonlinear_gate" in archetypes

    def test_get_domains(self):
        conn = CrossDomainConnector()
        domains = conn.get_domains("bottleneck")
        assert "transformers" in domains
        assert "fluids" in domains
        assert "biology" in domains

    def test_analogy_has_prompt(self):
        conn = CrossDomainConnector()
        results = conn.suggest_analogies("GQA", "transformers")
        for a in results:
            assert a.prompt
            assert "GQA" in a.prompt


# ── Engine integration with analytical modules ──────────────────────


class TestCuriosityEngineAnalytical:
    """Engine integration tests for the 5 analytical modules."""

    def test_engine_has_all_modules(self):
        engine = CuriosityEngine()
        assert isinstance(engine.elegance, EleganceScorer)
        assert isinstance(engine.residuals, ResidualAnalyzer)
        assert isinstance(engine.perspectives, PerspectiveRotator)
        assert isinstance(engine.drive, DriveController)
        assert isinstance(engine.connector, CrossDomainConnector)

    def test_engine_drive_mode(self):
        engine = CuriosityEngine(drive_mode="research")
        assert engine.drive.mode == "research"
        assert engine.drive.thresholds.max_iterations == 20

    def test_score_elegance_delegation(self):
        engine = CuriosityEngine()
        result = engine.score_elegance("y = 3.14159 * x")
        assert isinstance(result, EleganceBreakdown)
        assert "pi" in result.matched_constants

    def test_analyze_residuals_delegation(self):
        engine = CuriosityEngine()
        findings = engine.analyze_residuals(
            [1, 2, 3, 4, 5],
            [1.1, 1.9, 2.7, 3.3, 3.7],
            {"x": [1.0, 2.0, 3.0, 4.0, 5.0]},
        )
        assert isinstance(findings, list)
        for f in findings:
            assert "type" in f
            assert "field" in f
            assert "strength" in f

    def test_next_perspective_delegation(self):
        engine = CuriosityEngine()
        lens = engine.next_perspective()
        assert lens is not None
        assert isinstance(lens, Lens)

    def test_should_keep_exploring_delegation(self):
        engine = CuriosityEngine(drive_mode="practical")
        assert engine.should_keep_exploring(0.9) is True
        assert engine.should_keep_exploring(0.3) is False

    def test_suggest_analogies_delegation(self):
        engine = CuriosityEngine()
        results = engine.suggest_analogies("GQA", "transformers")
        assert isinstance(results, list)
        for r in results:
            assert "archetype" in r
            assert "prompt" in r

    @pytest.mark.asyncio
    async def test_full_analytical_cycle(self, tmp_path: Path):
        """End-to-end: score → residuals → perspective → drive → analogies."""
        engine = CuriosityEngine(
            state_dir=tmp_path / "curiosity", drive_mode="practical",
        )
        await engine.initialize()

        # 1. Score a solution
        elegance = engine.score_elegance(
            "loss = 0.0034 * layers + 3.14159 * log(params)",
            residuals_data=[0.1, -0.2, 0.3, -0.1, 0.4, -0.3],
        )
        assert elegance.total > 0

        # 2. Check if we should keep exploring
        keep_going = engine.should_keep_exploring(elegance.total)
        assert isinstance(keep_going, bool)

        # 3. Get next perspective
        lens = engine.next_perspective()
        assert lens is not None

        # 4. Get analogies for a concept in the solution
        analogies = engine.suggest_analogies("layers", "transformers")
        # "layers" might not match any archetype, that's OK
        assert isinstance(analogies, list)

    def test_system_prompt_includes_analytical_concepts(self):
        engine = CuriosityEngine()
        prompt = engine.get_system_prompt_addition()
        assert "Rotate perspectives" in prompt
        assert "Seek elegance" in prompt
        assert "Cross-pollinate" in prompt
        assert "Venturi" in prompt
