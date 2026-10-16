"""Tests for the Simple Self-Distillation (SSD) module."""

from __future__ import annotations

import ast
import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cortex.db import init_db, set_db_path
from cortex.evolution.self_distillation import (
    SelfDistillation,
    SSDConfig,
    SSDSample,
)


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def db_conn():
    """In-memory SQLite DB with full schema for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    set_db_path(db_path)
    init_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    yield conn
    conn.close()


@pytest.fixture
def config():
    return SSDConfig(
        enabled=True,
        samples_per_problem=4,
        temperatures=[0.8],
        min_improvement=0.02,
        max_iterations=100,
        problems_dir="data/ssd_problems",
        lora_dir="data/ssd_loras",
    )


@pytest.fixture
def ssd(config):
    return SelfDistillation(
        llm_url="http://localhost:8080",
        model_path="test-model",
        config=config,
    )


@pytest.fixture
def sample_problems():
    return [
        {
            "id": "test_001",
            "prompt": "Write a function `add(a, b)` that returns the sum.",
            "entry_point": "add",
            "difficulty": "easy",
            "category": "math",
        },
        {
            "id": "test_002",
            "prompt": "Write a function `reverse_string(s)` that reverses a string.",
            "entry_point": "reverse_string",
            "difficulty": "easy",
            "category": "string_manipulation",
        },
    ]


# ── should_run ───────────────────────────────────────────────────────


class TestShouldRun:
    async def test_returns_false_when_disabled(self, ssd, db_conn):
        ssd.config.enabled = False
        assert await ssd.should_run() is False

    async def test_returns_false_when_max_iterations_reached(self, ssd, db_conn):
        ssd.iteration = 100
        assert await ssd.should_run() is False

    async def test_returns_false_when_evolution_jobs_active(self, ssd, db_conn):
        db_conn.execute(
            "INSERT INTO evolution_runs (run_type, status, created_at) "
            "VALUES ('training', 'running', datetime('now'))"
        )
        db_conn.commit()
        assert await ssd.should_run() is False

    async def test_returns_true_when_idle(self, ssd, db_conn):
        assert await ssd.should_run() is True

    async def test_returns_true_when_only_completed_jobs(self, ssd, db_conn):
        db_conn.execute(
            "INSERT INTO evolution_runs (run_type, status, created_at) "
            "VALUES ('training', 'completed', datetime('now'))"
        )
        db_conn.commit()
        assert await ssd.should_run() is True


# ── generate_samples ─────────────────────────────────────────────────


class TestGenerateSamples:
    async def test_calls_api_for_each_problem(self, ssd, sample_problems):
        mock_samples = [
            SSDSample(
                problem_id="test_001",
                prompt="Write a function `add(a, b)` that returns the sum.",
                solution="def add(a, b):\n    return a + b",
                temperature=0.8,
                tokens=10,
            ),
        ]

        with patch.object(ssd, "_sample_batch", new_callable=AsyncMock, return_value=mock_samples):
            samples = await ssd.generate_samples(sample_problems)

        assert len(samples) > 0
        assert all(isinstance(s, SSDSample) for s in samples)

    async def test_skips_failed_api_calls(self, ssd, sample_problems):
        with patch.object(ssd, "_sample_batch", new_callable=AsyncMock, return_value=[]):
            samples = await ssd.generate_samples(sample_problems)

        assert len(samples) == 0


# ── filter_samples ───────────────────────────────────────────────────


class TestFilterSamples:
    def test_keeps_valid_python(self, ssd):
        samples = [
            SSDSample(
                problem_id="t1",
                prompt="test",
                solution="def foo():\n    return 42",
                temperature=0.8,
            ),
        ]
        valid = ssd.filter_samples(samples)
        assert len(valid) == 1
        assert valid[0].problem_id == "t1"

    def test_rejects_invalid_python(self, ssd):
        samples = [
            SSDSample(
                problem_id="t1",
                prompt="test",
                solution="def foo(\n    broken syntax!!!",
                temperature=0.8,
            ),
        ]
        valid = ssd.filter_samples(samples)
        assert len(valid) == 0

    def test_mixed_valid_and_invalid(self, ssd):
        samples = [
            SSDSample(problem_id="ok", prompt="x", solution="x = 1", temperature=0.8),
            SSDSample(problem_id="bad", prompt="x", solution="def (:", temperature=0.8),
            SSDSample(problem_id="ok2", prompt="x", solution="print('hi')", temperature=0.8),
        ]
        valid = ssd.filter_samples(samples)
        assert len(valid) == 2
        assert {s.problem_id for s in valid} == {"ok", "ok2"}

    def test_strips_markdown_fences(self, ssd):
        samples = [
            SSDSample(
                problem_id="md",
                prompt="test",
                solution="```python\ndef bar():\n    return 1\n```",
                temperature=0.8,
            ),
        ]
        valid = ssd.filter_samples(samples)
        assert len(valid) == 1
        assert "```" not in valid[0].solution

    def test_empty_input(self, ssd):
        assert ssd.filter_samples([]) == []


# ── train_lora ───────────────────────────────────────────────────────


class TestTrainLora:
    async def test_creates_training_data_file(self, ssd):
        ssd.iteration = 1
        samples = [
            SSDSample(problem_id="p1", prompt="Write add", solution="def add(a,b): return a+b", temperature=0.8),
        ]
        adapter_dir = await ssd.train_lora(samples)
        data_path = Path(adapter_dir) / "train.jsonl"
        assert data_path.exists()

        with open(data_path) as fh:
            lines = fh.readlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["instruction"] == "Write add"
        assert record["output"] == "def add(a,b): return a+b"

    async def test_creates_config_file(self, ssd):
        ssd.iteration = 2
        samples = [
            SSDSample(problem_id="p1", prompt="test", solution="x=1", temperature=0.8),
        ]
        adapter_dir = await ssd.train_lora(samples, prev_lora="/some/prev")
        config_path = Path(adapter_dir) / "config.json"
        assert config_path.exists()

        with open(config_path) as fh:
            cfg = json.load(fh)
        assert cfg["prev_lora"] == "/some/prev"
        assert cfg["base_model"] == "test-model"


# ── Promotion logic ──────────────────────────────────────────────────


class TestPromotionLogic:
    async def test_promotes_when_above_threshold(self, ssd, db_conn):
        """Improvement > 2% should promote."""
        ssd.iteration = 1
        baseline = 0.50
        new_score = 0.55  # 10% improvement — above 2%

        improved = new_score > baseline * (1.0 + ssd.config.min_improvement)
        assert improved is True

    async def test_no_promote_when_below_threshold(self, ssd):
        """Improvement ≤ 2% should NOT promote."""
        baseline = 0.50
        new_score = 0.505  # 1% improvement — below 2%

        improved = new_score > baseline * (1.0 + ssd.config.min_improvement)
        assert improved is False

    async def test_no_promote_when_score_drops(self, ssd):
        """Score decrease should NOT promote."""
        baseline = 0.60
        new_score = 0.55

        improved = new_score > baseline * (1.0 + ssd.config.min_improvement)
        assert improved is False

    async def test_promote_writes_to_registry(self, ssd, db_conn):
        ssd.iteration = 5
        ssd.promote_lora("/fake/adapter/path")

        row = db_conn.execute(
            "SELECT * FROM model_registry WHERE model_name = ?",
            ("ssd-iter-5",),
        ).fetchone()
        assert row is not None
        assert row["model_type"] == "lora"
        assert row["source"] == "ssd"
        assert row["status"] == "active"


# ── Iteration counter ────────────────────────────────────────────────


class TestIterationCounter:
    async def test_iteration_increments_on_run(self, ssd, db_conn):
        """Iteration counter should increment even if skipped early."""
        ssd.config.enabled = True
        # Provide empty problems so it returns early after incrementing
        with patch.object(ssd, "get_coding_problems", return_value=[]):
            result = await ssd.run_iteration()

        assert ssd.iteration == 1
        assert result["status"] == "skipped"

    async def test_iteration_does_not_increment_when_should_not_run(self, ssd, db_conn):
        ssd.config.enabled = False
        result = await ssd.run_iteration()
        assert ssd.iteration == 0
        assert result["status"] == "skipped"


# ── Config loading ───────────────────────────────────────────────────


class TestConfig:
    def test_default_config(self):
        cfg = SSDConfig()
        assert cfg.enabled is True
        assert cfg.samples_per_problem == 25
        assert cfg.temperatures == [0.6, 0.8, 1.0]
        assert cfg.min_improvement == 0.02
        assert cfg.max_iterations == 100

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("SSD_ENABLED", "false")
        monkeypatch.setenv("SSD_SAMPLES_PER_PROBLEM", "10")
        monkeypatch.setenv("SSD_TEMPERATURES", "0.5,0.7")
        monkeypatch.setenv("SSD_MIN_IMPROVEMENT", "0.05")
        cfg = SSDConfig()
        assert cfg.enabled is False
        assert cfg.samples_per_problem == 10
        assert cfg.temperatures == [0.5, 0.7]
        assert cfg.min_improvement == 0.05

    def test_custom_dirs(self):
        cfg = SSDConfig(problems_dir="/custom/problems", lora_dir="/custom/loras")
        assert cfg.problems_dir == "/custom/problems"
        assert cfg.lora_dir == "/custom/loras"


# ── extract_code helper ─────────────────────────────────────────────


class TestExtractCode:
    def test_plain_code(self):
        assert SelfDistillation._extract_code("x = 1") == "x = 1"

    def test_markdown_fenced(self):
        text = "```python\ndef f():\n    pass\n```"
        result = SelfDistillation._extract_code(text)
        assert result == "def f():\n    pass"

    def test_bare_fence(self):
        text = "```\nx = 1\n```"
        result = SelfDistillation._extract_code(text)
        assert result == "x = 1"


# ── get_status / get_history ─────────────────────────────────────────


class TestStatus:
    def test_get_status_returns_config(self, ssd, db_conn):
        status = ssd.get_status()
        assert status["enabled"] is True
        assert "config" in status
        assert status["config"]["samples_per_problem"] == 4

    def test_get_history_empty(self, ssd, db_conn):
        assert ssd.get_history() == []

    def test_get_history_with_runs(self, ssd, db_conn):
        db_conn.execute(
            "INSERT INTO evolution_runs "
            "(run_type, status, results, created_at) "
            "VALUES ('self_distillation', 'completed', '{}', datetime('now'))"
        )
        db_conn.commit()
        history = ssd.get_history()
        assert len(history) == 1


# ── run_iteration full path ──────────────────────────────────────────


class TestRunIteration:
    async def test_full_iteration_no_improvement(self, ssd, db_conn, sample_problems):
        """End-to-end: generates, filters, trains, evals, no improvement."""
        valid_samples = [
            SSDSample(problem_id="t1", prompt="test", solution="def add(a,b): return a+b", temperature=0.8),
        ]

        with (
            patch.object(ssd, "get_coding_problems", return_value=sample_problems),
            patch.object(ssd, "get_test_problems", return_value=sample_problems),
            patch.object(ssd, "evaluate", new_callable=AsyncMock, return_value=0.5),
            patch.object(ssd, "generate_samples", new_callable=AsyncMock, return_value=valid_samples),
        ):
            result = await ssd.run_iteration()

        assert result["status"] == "no_improvement"
        assert result["iteration"] == 1

    async def test_full_iteration_promoted(self, ssd, db_conn, sample_problems):
        """End-to-end: improvement triggers promotion."""
        valid_samples = [
            SSDSample(problem_id="t1", prompt="test", solution="def add(a,b): return a+b", temperature=0.8),
        ]

        eval_scores = iter([0.40, 0.55])

        async def mock_eval(lora_path, test_problems):
            return next(eval_scores)

        with (
            patch.object(ssd, "get_coding_problems", return_value=sample_problems),
            patch.object(ssd, "get_test_problems", return_value=sample_problems),
            patch.object(ssd, "evaluate", side_effect=mock_eval),
            patch.object(ssd, "generate_samples", new_callable=AsyncMock, return_value=valid_samples),
        ):
            result = await ssd.run_iteration()

        assert result["status"] == "promoted"
        assert result["new_score"] > result["baseline_score"]

    async def test_logs_run_to_db(self, ssd, db_conn, sample_problems):
        """Every iteration should log to evolution_runs."""
        with patch.object(ssd, "get_coding_problems", return_value=[]):
            await ssd.run_iteration()

        row = db_conn.execute(
            "SELECT * FROM evolution_runs WHERE run_type = 'self_distillation'"
        ).fetchone()
        assert row is not None
