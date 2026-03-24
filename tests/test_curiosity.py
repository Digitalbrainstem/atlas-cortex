"""Tests for the Atlas Curiosity Engine — observer, hypothesis tracker, and engine."""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cortex.curiosity.observer import Observation, PatternObserver
from cortex.curiosity.hypothesis import Hypothesis, HypothesisTracker
from cortex.curiosity.engine import CuriosityEngine


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
        assert "scientist" in prompt.lower() or "Scientific" in prompt
        assert "Question assumptions" in prompt
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
