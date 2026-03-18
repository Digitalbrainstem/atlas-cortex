"""Tests for Part 9 — Self-evolution engine, conversation analysis, model registry."""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta

import pytest

from cortex.db import get_db, init_db, set_db_path
from cortex.evolution.analysis import ConversationAnalyzer
from cortex.evolution.engine import EvolutionEngine
from cortex.evolution.registry import ModelRegistry


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _fresh_db(tmp_path):
    """Give every test an isolated in-memory database with full schema."""
    db_path = str(tmp_path / "test.db")
    set_db_path(db_path)
    init_db()
    yield


def _insert_interaction(
    *,
    message: str = "hello",
    matched_layer: str = "llm",
    intent: str = "",
    sentiment: str = "neutral",
    sentiment_score: float = 0.0,
    response: str = "hi there",
    response_time_ms: int = 500,
    confidence_score: float = 0.9,
    resolved_area: str = "",
    user_id: str = "user-1",
    minutes_ago: int = 0,
) -> None:
    conn = get_db()
    ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    conn.execute(
        "INSERT INTO interactions "
        "(user_id, message, matched_layer, intent, sentiment, sentiment_score, "
        " response, response_time_ms, confidence_score, resolved_area, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, message, matched_layer, intent, sentiment, sentiment_score,
         response, response_time_ms, confidence_score, resolved_area, ts),
    )
    conn.commit()


# ── ConversationAnalyzer: quality gap detection ──────────────────────

class TestAnalyzeQualityGaps:

    def test_empty_history(self):
        analyzer = ConversationAnalyzer()
        result = analyzer.analyze_quality_gaps()
        assert result == {}

    def test_detects_slow_responses(self):
        _insert_interaction(response_time_ms=5000, resolved_area="home")
        analyzer = ConversationAnalyzer()
        gaps = analyzer.analyze_quality_gaps(days=1)
        assert "home" in gaps
        assert gaps["home"]["issue_count"] >= 1
        assert any("slow" in ex["issues"][0] for ex in gaps["home"]["examples"])

    def test_detects_low_confidence(self):
        _insert_interaction(confidence_score=0.2, resolved_area="weather")
        analyzer = ConversationAnalyzer()
        gaps = analyzer.analyze_quality_gaps(days=1)
        assert "weather" in gaps
        assert gaps["weather"]["issue_count"] >= 1

    def test_detects_negative_sentiment(self):
        _insert_interaction(sentiment="frustrated", resolved_area="music")
        analyzer = ConversationAnalyzer()
        gaps = analyzer.analyze_quality_gaps(days=1)
        assert "music" in gaps
        assert gaps["music"]["issue_count"] >= 1

    def test_detects_plugin_fallthrough(self):
        _insert_interaction(matched_layer="llm", intent="turn_on_lights")
        analyzer = ConversationAnalyzer()
        gaps = analyzer.analyze_quality_gaps(days=1)
        # Falls through to LLM — the domain key is the matched_layer
        assert "llm" in gaps
        assert gaps["llm"]["issue_count"] >= 1

    def test_score_degrades_with_issues(self):
        for _ in range(5):
            _insert_interaction(
                response_time_ms=6000, confidence_score=0.1, resolved_area="lists",
            )
        analyzer = ConversationAnalyzer()
        gaps = analyzer.analyze_quality_gaps(days=1)
        assert gaps["lists"]["score"] < 1.0

    def test_no_issues_for_healthy_interactions(self):
        _insert_interaction(
            response_time_ms=200, confidence_score=0.95,
            sentiment="positive", resolved_area="greetings",
        )
        analyzer = ConversationAnalyzer()
        gaps = analyzer.analyze_quality_gaps(days=1)
        # A healthy interaction should have zero issues
        if "greetings" in gaps:
            assert gaps["greetings"]["issue_count"] == 0


# ── ConversationAnalyzer: weak domain identification ─────────────────

class TestIdentifyWeakDomains:

    def test_empty_returns_empty(self):
        analyzer = ConversationAnalyzer()
        assert analyzer.identify_weak_domains() == []

    def test_returns_sorted_weakest_first(self):
        # Two domains: home has issues, greetings is fine
        for _ in range(3):
            _insert_interaction(
                response_time_ms=6000, confidence_score=0.1,
                resolved_area="home",
            )
        _insert_interaction(
            response_time_ms=200, confidence_score=0.95,
            sentiment="positive", resolved_area="greetings",
        )
        analyzer = ConversationAnalyzer()
        weak = analyzer.identify_weak_domains(days=1)
        assert len(weak) >= 1
        # home should be weaker than greetings
        domains = [d["domain"] for d in weak]
        if "greetings" in domains and "home" in domains:
            assert domains.index("home") < domains.index("greetings")


# ── ConversationAnalyzer: training candidate generation ──────────────

class TestGenerateTrainingCandidates:

    def test_empty_domain(self):
        analyzer = ConversationAnalyzer()
        assert analyzer.generate_training_candidates("nonexistent") == []

    def test_returns_positive_neutral_pairs(self):
        _insert_interaction(
            message="turn on kitchen lights",
            response="Done! Kitchen lights are on.",
            sentiment="positive",
            resolved_area="home",
        )
        _insert_interaction(
            message="what's the weather",
            response="Cloudy with a chance of rain",
            sentiment="neutral",
            resolved_area="home",
        )
        # Negative sentiment should be excluded
        _insert_interaction(
            message="that's wrong",
            response="Sorry about that",
            sentiment="negative",
            resolved_area="home",
        )
        analyzer = ConversationAnalyzer()
        candidates = analyzer.generate_training_candidates("home")
        assert len(candidates) == 2
        for c in candidates:
            assert "prompt" in c
            assert "ideal_response" in c
            assert c["domain"] == "home"

    def test_respects_limit(self):
        for i in range(10):
            _insert_interaction(
                message=f"msg {i}", response=f"resp {i}",
                sentiment="positive", resolved_area="home",
            )
        analyzer = ConversationAnalyzer()
        assert len(analyzer.generate_training_candidates("home", limit=3)) == 3


# ── ConversationAnalyzer: usage stats ────────────────────────────────

class TestGetUsageStats:

    def test_empty_stats(self):
        analyzer = ConversationAnalyzer()
        stats = analyzer.get_usage_stats()
        assert stats["total"] == 0
        assert stats["satisfaction"] == 0.0

    def test_counts_and_satisfaction(self):
        _insert_interaction(sentiment="positive")
        _insert_interaction(sentiment="neutral")
        _insert_interaction(sentiment="negative")
        analyzer = ConversationAnalyzer()
        stats = analyzer.get_usage_stats(days=1)
        assert stats["total"] == 3
        assert stats["satisfaction"] == pytest.approx(2 / 3, abs=0.01)


# ── ModelRegistry ────────────────────────────────────────────────────

class TestModelRegistry:

    def test_register_and_list(self):
        reg = ModelRegistry()
        mid = reg.register_model("qwen2.5:7b", source="ollama")
        assert mid is not None and mid > 0

        models = reg.list_models()
        assert len(models) == 1
        assert models[0]["model_name"] == "qwen2.5:7b"

    def test_filter_by_status(self):
        reg = ModelRegistry()
        reg.register_model("model-a")
        m2 = reg.register_model("model-b", model_type="lora")
        reg.promote_model(m2)

        available = reg.list_models(status="available")
        active = reg.list_models(status="active")
        assert len(available) == 1
        assert len(active) == 1
        assert active[0]["model_name"] == "model-b"

    def test_filter_by_type(self):
        reg = ModelRegistry()
        reg.register_model("base-model", model_type="base")
        reg.register_model("lora-model", model_type="lora")
        loras = reg.list_models(model_type="lora")
        assert len(loras) == 1
        assert loras[0]["model_type"] == "lora"

    def test_get_active_model_none(self):
        reg = ModelRegistry()
        assert reg.get_active_model() is None

    def test_promote_and_retire(self):
        reg = ModelRegistry()
        m1 = reg.register_model("old-model")
        m2 = reg.register_model("new-model", model_type="candidate")

        reg.promote_model(m1)
        assert reg.get_active_model()["model_name"] == "old-model"

        # Promoting m2 should retire m1
        reg.promote_model(m2)
        active = reg.get_active_model()
        assert active is not None
        assert active["model_name"] == "new-model"
        assert active["promoted_at"] is not None

        retired = reg.list_models(status="retired")
        assert any(m["model_name"] == "old-model" for m in retired)

    def test_retire_model(self):
        reg = ModelRegistry()
        mid = reg.register_model("temp-model")
        assert reg.retire_model(mid) is True
        assert reg.list_models(status="retired")[0]["model_name"] == "temp-model"

    def test_update_scores(self):
        reg = ModelRegistry()
        mid = reg.register_model("scored-model")
        reg.update_scores(mid, eval_score=0.85, safety_score=0.92, personality_score=0.78)
        models = reg.list_models()
        m = models[0]
        assert m["eval_score"] == pytest.approx(0.85)
        assert m["safety_score"] == pytest.approx(0.92)
        assert m["personality_score"] == pytest.approx(0.78)

    def test_unique_constraint(self):
        reg = ModelRegistry()
        reg.register_model("dup-model", model_type="base")
        with pytest.raises(Exception):
            reg.register_model("dup-model", model_type="base")


# ── EvolutionEngine ──────────────────────────────────────────────────

class TestEvolutionEngine:

    @pytest.mark.asyncio
    async def test_run_analysis_empty(self):
        engine = EvolutionEngine()
        report = await engine.run_analysis()
        assert "error" not in report
        assert "quality_gaps" in report
        assert "usage_stats" in report

    @pytest.mark.asyncio
    async def test_run_analysis_with_data(self):
        _insert_interaction(
            response_time_ms=6000, confidence_score=0.1,
            sentiment="frustrated", resolved_area="home",
        )
        engine = EvolutionEngine()
        report = await engine.run_analysis()
        assert report["usage_stats"]["total"] >= 1
        # Should have recorded a run
        history = engine.get_evolution_history()
        assert len(history) == 1
        assert history[0]["run_type"] == "analysis"
        assert history[0]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_schedule_training(self):
        _insert_interaction(
            message="turn on lights", response="OK",
            sentiment="positive", resolved_area="home",
        )
        engine = EvolutionEngine()
        run_id = await engine.schedule_training("home")
        assert run_id > 0
        history = engine.get_evolution_history()
        assert any(h["run_type"] == "training" for h in history)

    @pytest.mark.asyncio
    async def test_check_for_improvements_none(self):
        engine = EvolutionEngine()
        result = await engine.check_for_improvements()
        assert result == []

    @pytest.mark.asyncio
    async def test_check_for_improvements_triggers(self):
        # Create enough bad interactions to trigger retraining
        for _ in range(5):
            _insert_interaction(
                response_time_ms=6000, confidence_score=0.1,
                sentiment="frustrated", resolved_area="home",
            )
        engine = EvolutionEngine()
        actionable = await engine.check_for_improvements()
        assert len(actionable) >= 1
        assert actionable[0]["domain"] == "home"

    @pytest.mark.asyncio
    async def test_nightly_evolution(self):
        # Create degraded domain
        for _ in range(5):
            _insert_interaction(
                response_time_ms=6000, confidence_score=0.1,
                sentiment="frustrated", resolved_area="home",
            )
        engine = EvolutionEngine()
        report = await engine.run_nightly_evolution()
        assert "error" not in report
        assert "scheduled_training_runs" in report

    def test_get_evolution_history_empty(self):
        engine = EvolutionEngine()
        assert engine.get_evolution_history() == []

    @pytest.mark.asyncio
    async def test_evolution_history_limit(self):
        engine = EvolutionEngine()
        for _ in range(5):
            await engine.run_analysis()
        history = engine.get_evolution_history(limit=3)
        assert len(history) == 3


# ── Edge cases ───────────────────────────────────────────────────────

class TestEdgeCases:

    def test_analyzer_with_no_interactions_table(self, tmp_path):
        """Analyzer should not crash if the interactions table is missing."""
        db_path = str(tmp_path / "empty.db")
        set_db_path(db_path)
        # Deliberately do NOT call init_db — no tables exist
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.close()

        analyzer = ConversationAnalyzer()
        assert analyzer.analyze_quality_gaps() == {}
        assert analyzer.identify_weak_domains() == []
        assert analyzer.generate_training_candidates("home") == []
        stats = analyzer.get_usage_stats()
        assert stats["total"] == 0

    def test_old_interactions_excluded(self):
        """Interactions older than the window should not appear."""
        _insert_interaction(minutes_ago=60 * 24 * 30)  # 30 days ago
        analyzer = ConversationAnalyzer()
        gaps = analyzer.analyze_quality_gaps(days=1)
        # The old interaction should not be in the 1-day window
        assert gaps == {}


# ── LoRATrainer ──────────────────────────────────────────────────────────────

class TestLoRATrainer:

    def test_get_training_config(self):
        from cortex.evolution.training import LoRATrainer
        trainer = LoRATrainer(output_dir="/tmp/test-lora")
        config = trainer.get_training_config("home")
        assert config["domain"] == "home"
        assert config["rank"] == 16
        assert config["lora_alpha"] == 32
        assert config["batch_size"] == 2
        assert config["gradient_checkpointing"] is True
        assert config["epochs"] == 3
        assert config["learning_rate"] == 2e-4
        assert "output_dir" in config

    def test_estimate_training_time_short(self):
        from cortex.evolution.training import LoRATrainer
        trainer = LoRATrainer()
        result = trainer.estimate_training_time(5)
        assert "seconds" in result

    def test_estimate_training_time_medium(self):
        from cortex.evolution.training import LoRATrainer
        trainer = LoRATrainer()
        result = trainer.estimate_training_time(500)
        assert "minutes" in result

    def test_estimate_training_time_long(self):
        from cortex.evolution.training import LoRATrainer
        trainer = LoRATrainer()
        result = trainer.estimate_training_time(50000)
        assert "hours" in result

    async def test_prepare_training_data_empty(self, tmp_path):
        from cortex.evolution.training import LoRATrainer
        trainer = LoRATrainer(output_dir=str(tmp_path / "lora"))
        path = await trainer.prepare_training_data("home", limit=10)
        assert path.endswith("home.jsonl")
        # File should exist but be empty (no interactions)
        import os
        assert os.path.exists(path)

    async def test_prepare_training_data_with_interactions(self, tmp_path):
        from cortex.evolution.training import LoRATrainer
        _insert_interaction(
            message="turn on lights", response="Done!",
            sentiment="positive", resolved_area="home",
        )
        _insert_interaction(
            message="kitchen status", response="All clear",
            sentiment="neutral", resolved_area="home",
        )
        trainer = LoRATrainer(output_dir=str(tmp_path / "lora"))
        path = await trainer.prepare_training_data("home", limit=10)
        import json as json_mod
        with open(path) as f:
            lines = f.readlines()
        assert len(lines) == 2
        entry = json_mod.loads(lines[0])
        assert "instruction" in entry
        assert "output" in entry
        assert entry["domain"] == "home"

    async def test_validate_adapter_missing_dir(self):
        from cortex.evolution.training import LoRATrainer
        trainer = LoRATrainer()
        result = await trainer.validate_adapter("/nonexistent/path")
        assert result["eval_score"] == 0.0
        assert "error" in result

    async def test_validate_adapter_missing_files(self, tmp_path):
        from cortex.evolution.training import LoRATrainer
        adapter_dir = str(tmp_path / "adapter")
        import os
        os.makedirs(adapter_dir)
        trainer = LoRATrainer()
        result = await trainer.validate_adapter(adapter_dir)
        assert result["eval_score"] == 0.0
        assert "Missing files" in result.get("error", "")

    def test_generate_training_script(self):
        from cortex.evolution.training import LoRATrainer
        trainer = LoRATrainer()
        config = trainer.get_training_config("home")
        script = trainer._generate_training_script("/data/train.jsonl", "/output", config)
        assert "QLoRA" in script
        assert "ROCm" in script
        assert "HSA_OVERRIDE_GFX_VERSION" in script
        assert "LoraConfig" in script


# ── ModelScout ───────────────────────────────────────────────────────────────

class TestModelScout:

    async def test_scan_ollama_no_server(self):
        """Scan should return empty list when Ollama is not available."""
        from cortex.evolution.scout import ModelScout
        scout = ModelScout()
        scout._ollama_base = "http://localhost:1"  # Unreachable
        result = await scout.scan_ollama_library()
        assert result == []

    async def test_scan_registry_empty(self):
        from cortex.evolution.scout import ModelScout
        scout = ModelScout()
        result = await scout.scan_registry()
        assert result == []

    async def test_scan_registry_with_candidates(self):
        from cortex.evolution.scout import ModelScout
        reg = ModelRegistry()
        reg.register_model("candidate-model", model_type="candidate")
        # Mark status as candidate (register defaults to available)
        conn = get_db()
        conn.execute(
            "UPDATE model_registry SET status = 'candidate' WHERE model_name = 'candidate-model'"
        )
        conn.commit()

        scout = ModelScout()
        result = await scout.scan_registry()
        assert len(result) == 1
        assert result[0]["model_name"] == "candidate-model"

    async def test_recommend_promotion_empty(self):
        from cortex.evolution.scout import ModelScout
        scout = ModelScout()
        result = await scout.recommend_promotion()
        assert result == []

    async def test_recommend_promotion_eligible(self):
        from cortex.evolution.scout import ModelScout
        reg = ModelRegistry()
        mid = reg.register_model("good-model", model_type="candidate")
        conn = get_db()
        conn.execute(
            "UPDATE model_registry SET status = 'candidate', "
            "eval_score = 0.9, safety_score = 1.0, personality_score = 0.8 "
            "WHERE id = ?",
            (mid,),
        )
        conn.commit()

        scout = ModelScout()
        result = await scout.recommend_promotion()
        assert len(result) == 1
        assert result[0]["model_name"] == "good-model"

    async def test_recommend_promotion_fails_safety(self):
        """Model with low safety score should not be recommended."""
        from cortex.evolution.scout import ModelScout
        reg = ModelRegistry()
        mid = reg.register_model("unsafe-model", model_type="candidate")
        conn = get_db()
        conn.execute(
            "UPDATE model_registry SET status = 'candidate', "
            "eval_score = 0.9, safety_score = 0.5, personality_score = 0.8 "
            "WHERE id = ?",
            (mid,),
        )
        conn.commit()

        scout = ModelScout()
        result = await scout.recommend_promotion()
        assert result == []


# ── DriftMonitor ─────────────────────────────────────────────────────────────

class TestDriftMonitor:

    def test_record_response_metrics(self):
        from cortex.evolution.scout import DriftMonitor
        monitor = DriftMonitor()
        monitor.record_response_metrics("Hello, how can I help you today?", "general")
        conn = get_db()
        run_id = monitor._ensure_drift_run()
        rows = conn.execute(
            "SELECT * FROM evolution_metrics WHERE run_id = ?", (run_id,)
        ).fetchall()
        assert len(rows) == 2  # response_length and word_count
        names = {dict(r)["metric_name"] for r in rows}
        assert "response_length" in names
        assert "word_count" in names

    def test_check_drift_no_data(self):
        from cortex.evolution.scout import DriftMonitor
        monitor = DriftMonitor()
        result = monitor.check_drift()
        assert result["drift_score"] == 0.0
        assert result["alert"] is False

    def test_check_drift_stable(self):
        from cortex.evolution.scout import DriftMonitor
        monitor = DriftMonitor()
        # Record baseline and recent data that are similar
        conn = get_db()
        run_id = monitor._ensure_drift_run()
        now = datetime.now(timezone.utc)
        for i in range(20):
            ts_old = (now - timedelta(days=10, hours=i)).isoformat()
            ts_new = (now - timedelta(hours=i)).isoformat()
            conn.execute(
                "INSERT INTO evolution_metrics (run_id, metric_name, metric_value, domain, created_at) "
                "VALUES (?, 'response_length', ?, 'general', ?)",
                (run_id, 100.0 + (i % 5), ts_old),
            )
            conn.execute(
                "INSERT INTO evolution_metrics (run_id, metric_name, metric_value, domain, created_at) "
                "VALUES (?, 'response_length', ?, 'general', ?)",
                (run_id, 101.0 + (i % 5), ts_new),
            )
        conn.commit()

        result = monitor.check_drift(window_days=7)
        assert result["drift_score"] < 0.3
        assert result["alert"] is False

    def test_check_drift_significant(self):
        from cortex.evolution.scout import DriftMonitor
        monitor = DriftMonitor()
        conn = get_db()
        run_id = monitor._ensure_drift_run()
        now = datetime.now(timezone.utc)
        for i in range(20):
            ts_old = (now - timedelta(days=10, hours=i)).isoformat()
            ts_new = (now - timedelta(hours=i)).isoformat()
            conn.execute(
                "INSERT INTO evolution_metrics (run_id, metric_name, metric_value, domain, created_at) "
                "VALUES (?, 'response_length', 50.0, 'general', ?)",
                (run_id, ts_old),
            )
            conn.execute(
                "INSERT INTO evolution_metrics (run_id, metric_name, metric_value, domain, created_at) "
                "VALUES (?, 'response_length', 200.0, 'general', ?)",
                (run_id, ts_new),
            )
        conn.commit()

        result = monitor.check_drift(window_days=7)
        assert result["drift_score"] > 0.3
        assert result["alert"] is True

    def test_get_personality_timeline_empty(self):
        from cortex.evolution.scout import DriftMonitor
        monitor = DriftMonitor()
        result = monitor.get_personality_timeline()
        assert result == []

    def test_get_personality_timeline_with_data(self):
        from cortex.evolution.scout import DriftMonitor
        monitor = DriftMonitor()
        monitor.record_response_metrics("Hello!", "general")
        monitor.record_response_metrics("I can help with that.", "general")
        result = monitor.get_personality_timeline(days=1)
        assert len(result) >= 2


# ── ABTester ─────────────────────────────────────────────────────────────────

class TestABTester:

    async def test_start_test(self):
        from cortex.evolution.scout import ABTester
        tester = ABTester()
        run_id = await tester.start_test("qwen2.5:14b", duration_hours=12)
        assert run_id > 0
        conn = get_db()
        row = conn.execute("SELECT * FROM evolution_runs WHERE id = ?", (run_id,)).fetchone()
        run = dict(row)
        assert run["run_type"] == "ab_test"
        assert run["status"] == "running"

    async def test_get_results(self):
        from cortex.evolution.scout import ABTester
        tester = ABTester()
        run_id = await tester.start_test("test-model")
        result = await tester.get_results(run_id)
        assert result["test_id"] == run_id
        assert result["status"] == "running"
        assert result["candidate_model"] == "test-model"

    async def test_get_results_not_found(self):
        from cortex.evolution.scout import ABTester
        tester = ABTester()
        result = await tester.get_results(99999)
        assert "error" in result

    async def test_stop_test(self):
        from cortex.evolution.scout import ABTester
        tester = ABTester()
        run_id = await tester.start_test("stop-model", duration_hours=1)
        result = await tester.stop_test(run_id)
        assert result["status"] == "completed"

    async def test_stop_test_not_found(self):
        from cortex.evolution.scout import ABTester
        tester = ABTester()
        result = await tester.stop_test(99999)
        assert "error" in result
