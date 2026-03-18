"""Tests for evolution admin API endpoints."""

from __future__ import annotations

import json
import sqlite3

import pytest

from cortex.auth import authenticate, create_token, seed_admin
from cortex.db import init_db, set_db_path


@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "test.db"
    set_db_path(path)
    init_db(path)
    return path


@pytest.fixture()
def db(db_path):
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@pytest.fixture()
def client(db_path):
    from unittest.mock import patch
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from cortex.admin_api import router
    from cortex.auth import seed_admin as _seed

    test_app = FastAPI()
    test_app.include_router(router)

    def get_test_db():
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        _seed(conn)
        return conn

    with patch("cortex.admin.helpers._db", get_test_db):
        yield TestClient(test_app)


@pytest.fixture()
def auth_header(db):
    seed_admin(db)
    user = authenticate(db, "admin", "atlas-admin")
    token = create_token(user["id"], user["username"])
    return {"Authorization": f"Bearer {token}"}


# ── Helpers ───────────────────────────────────────────────────────

def _insert_run(db_path, run_type="analysis", status="completed"):
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute(
        "INSERT INTO evolution_runs (run_type, status, config, results, "
        "started_at, completed_at, created_at) "
        "VALUES (?, ?, '{}', '{}', datetime('now'), datetime('now'), datetime('now'))",
        (run_type, status),
    )
    conn.commit()
    run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return run_id


def _insert_model(db_path, name="test-model", model_type="lora", status="candidate"):
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute(
        "INSERT INTO model_registry (model_name, model_type, source, status, "
        "eval_score, safety_score, personality_score, created_at) "
        "VALUES (?, ?, 'local', ?, 0.85, 0.92, 0.78, datetime('now'))",
        (name, model_type, status),
    )
    conn.commit()
    model_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return model_id


def _insert_metric(db_path, run_id, name="accuracy", value=0.95, domain="general"):
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute(
        "INSERT INTO evolution_metrics (run_id, metric_name, metric_value, domain, created_at) "
        "VALUES (?, ?, ?, ?, datetime('now'))",
        (run_id, name, value, domain),
    )
    conn.commit()
    conn.close()


# ── Tests ─────────────────────────────────────────────────────────

class TestEvolutionRuns:
    def test_list_runs_empty(self, client, auth_header):
        resp = client.get("/admin/evolution/runs", headers=auth_header)
        assert resp.status_code == 200
        data = resp.json()
        assert data["runs"] == []
        assert data["total"] == 0

    def test_list_runs(self, client, auth_header, db_path):
        _insert_run(db_path, "analysis", "completed")
        _insert_run(db_path, "training", "running")
        resp = client.get("/admin/evolution/runs", headers=auth_header)
        assert resp.status_code == 200
        assert resp.json()["total"] == 2

    def test_list_runs_filter_type(self, client, auth_header, db_path):
        _insert_run(db_path, "analysis", "completed")
        _insert_run(db_path, "training", "running")
        resp = client.get("/admin/evolution/runs?run_type=analysis", headers=auth_header)
        assert resp.status_code == 200
        assert resp.json()["total"] == 1
        assert resp.json()["runs"][0]["run_type"] == "analysis"

    def test_get_run_detail(self, client, auth_header, db_path):
        run_id = _insert_run(db_path)
        _insert_metric(db_path, run_id, "accuracy", 0.95)
        resp = client.get(f"/admin/evolution/runs/{run_id}", headers=auth_header)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == run_id
        assert len(data["metrics"]) == 1

    def test_get_run_not_found(self, client, auth_header):
        resp = client.get("/admin/evolution/runs/9999", headers=auth_header)
        assert resp.status_code == 404


class TestTriggerAnalysis:
    def test_trigger_analysis(self, client, auth_header):
        resp = client.post(
            "/admin/evolution/analyze",
            json={"days": 7, "min_interactions": 5},
            headers=auth_header,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "run_id" in data

    def test_trigger_creates_pending_run(self, client, auth_header):
        client.post(
            "/admin/evolution/analyze",
            json={"days": 3, "min_interactions": 2},
            headers=auth_header,
        )
        resp = client.get("/admin/evolution/runs", headers=auth_header)
        runs = resp.json()["runs"]
        assert len(runs) == 1
        assert runs[0]["run_type"] == "analysis"
        assert runs[0]["status"] == "pending"


class TestModelRegistry:
    def test_list_models_empty(self, client, auth_header):
        resp = client.get("/admin/evolution/models", headers=auth_header)
        assert resp.status_code == 200
        assert resp.json()["models"] == []

    def test_list_models(self, client, auth_header, db_path):
        _insert_model(db_path, "model-a", "lora", "candidate")
        _insert_model(db_path, "model-b", "base", "active")
        resp = client.get("/admin/evolution/models", headers=auth_header)
        assert resp.status_code == 200
        assert len(resp.json()["models"]) == 2

    def test_list_models_filter_status(self, client, auth_header, db_path):
        _insert_model(db_path, "model-a", "lora", "candidate")
        _insert_model(db_path, "model-b", "base", "active")
        resp = client.get("/admin/evolution/models?status=active", headers=auth_header)
        assert resp.status_code == 200
        models = resp.json()["models"]
        assert len(models) == 1
        assert models[0]["status"] == "active"

    def test_promote_model(self, client, auth_header, db_path):
        mid = _insert_model(db_path, "model-a", "lora", "candidate")
        resp = client.post(f"/admin/evolution/models/{mid}/promote", headers=auth_header)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        # Verify status changed
        resp2 = client.get("/admin/evolution/models?status=active", headers=auth_header)
        names = [m["model_name"] for m in resp2.json()["models"]]
        assert "model-a" in names

    def test_promote_retired_fails(self, client, auth_header, db_path):
        mid = _insert_model(db_path, "model-r", "lora", "retired")
        resp = client.post(f"/admin/evolution/models/{mid}/promote", headers=auth_header)
        assert resp.status_code == 400

    def test_promote_not_found(self, client, auth_header):
        resp = client.post("/admin/evolution/models/9999/promote", headers=auth_header)
        assert resp.status_code == 404

    def test_retire_model(self, client, auth_header, db_path):
        mid = _insert_model(db_path, "model-a", "lora", "active")
        resp = client.post(f"/admin/evolution/models/{mid}/retire", headers=auth_header)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        resp2 = client.get("/admin/evolution/models?status=retired", headers=auth_header)
        names = [m["model_name"] for m in resp2.json()["models"]]
        assert "model-a" in names

    def test_retire_not_found(self, client, auth_header):
        resp = client.post("/admin/evolution/models/9999/retire", headers=auth_header)
        assert resp.status_code == 404


class TestDriftEndpoint:
    def test_drift_empty(self, client, auth_header):
        resp = client.get("/admin/evolution/drift", headers=auth_header)
        assert resp.status_code == 200
        data = resp.json()
        assert data["drift_score"] == 0.0
        assert data["drift_level"] == "low"
        assert data["sample_count"] == 0

    def test_drift_with_metrics(self, client, auth_header, db_path):
        run_id = _insert_run(db_path)
        for v in [0.5, 0.52, 0.48, 0.51, 0.49]:
            _insert_metric(db_path, run_id, "tone_consistency", v, "personality")
        resp = client.get("/admin/evolution/drift", headers=auth_header)
        assert resp.status_code == 200
        data = resp.json()
        assert data["drift_level"] in ("low", "medium", "high")
        assert data["sample_count"] == 5


class TestQualityMetrics:
    def test_metrics_empty(self, client, auth_header):
        resp = client.get("/admin/evolution/metrics", headers=auth_header)
        assert resp.status_code == 200
        assert resp.json()["metrics"] == []

    def test_metrics_with_data(self, client, auth_header, db_path):
        run_id = _insert_run(db_path)
        _insert_metric(db_path, run_id, "accuracy", 0.95, "general")
        _insert_metric(db_path, run_id, "safety_score", 0.99, "safety")
        resp = client.get("/admin/evolution/metrics", headers=auth_header)
        assert resp.status_code == 200
        assert len(resp.json()["metrics"]) == 2

    def test_metrics_filter_domain(self, client, auth_header, db_path):
        run_id = _insert_run(db_path)
        _insert_metric(db_path, run_id, "accuracy", 0.95, "general")
        _insert_metric(db_path, run_id, "safety_score", 0.99, "safety")
        resp = client.get("/admin/evolution/metrics?domain=safety", headers=auth_header)
        assert resp.status_code == 200
        metrics = resp.json()["metrics"]
        assert len(metrics) == 1
        assert metrics[0]["domain"] == "safety"
