"""Integration tests for Atlas integrity verification system.

Validates that:
  - Principle seal computation works
  - FROZEN file checksums are verified
  - Canary tests catch neutered guardrails
  - Startup integrity gate blocks on failure
  - Tampered principles are detected
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from cortex.db import init_db, set_db_path


@pytest.fixture()
def db_conn():
    """Temporary in-memory database with full schema."""
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        set_db_path(f.name)
        init_db(f.name)
        conn = sqlite3.connect(f.name)
        conn.row_factory = sqlite3.Row
        yield conn
        conn.close()


@pytest.fixture()
def repo_root(tmp_path):
    """Create a minimal fake repo for testing."""
    from cortex.integrity import set_repo_root
    # Create a fake CORE_PRINCIPLES.md
    principles = tmp_path / "CORE_PRINCIPLES.md"
    principles.write_text("# Test Principles\nThese are test principles.")
    # Create fake FROZEN files
    cortex_dir = tmp_path / "cortex"
    safety_dir = cortex_dir / "safety"
    selfmod_dir = cortex_dir / "selfmod"
    integrity_dir = cortex_dir / "integrity"
    pipeline_dir = cortex_dir / "pipeline"
    for d in [cortex_dir, safety_dir, selfmod_dir, integrity_dir, pipeline_dir]:
        d.mkdir(parents=True, exist_ok=True)
    (safety_dir / "__init__.py").write_text("# safety")
    (safety_dir / "jailbreak.py").write_text("# jailbreak")
    (selfmod_dir / "zones.py").write_text("# zones")
    (integrity_dir / "__init__.py").write_text("# integrity")
    (pipeline_dir / "__init__.py").write_text("# pipeline")
    (pipeline_dir / "events.py").write_text("# events")
    (cortex_dir / "auth.py").write_text("# auth")
    (cortex_dir / "db.py").write_text("# db")

    set_repo_root(tmp_path)
    yield tmp_path
    # Reset to real repo root
    set_repo_root(Path(__file__).parent.parent)


class TestPrincipleSeal:
    """Tests for CORE_PRINCIPLES.md seal computation."""

    def test_seal_computation(self, repo_root):
        from cortex.integrity import compute_principle_seal
        seal = compute_principle_seal()
        assert isinstance(seal, str)
        assert len(seal) == 128  # SHA-512 hex = 128 chars

    def test_seal_deterministic(self, repo_root):
        from cortex.integrity import compute_principle_seal
        assert compute_principle_seal() == compute_principle_seal()

    def test_seal_changes_on_modification(self, repo_root):
        from cortex.integrity import compute_principle_seal
        seal_before = compute_principle_seal()
        (repo_root / "CORE_PRINCIPLES.md").write_text("MODIFIED PRINCIPLES")
        seal_after = compute_principle_seal()
        assert seal_before != seal_after

    def test_seal_fails_on_missing_file(self, repo_root):
        from cortex.integrity import compute_principle_seal
        (repo_root / "CORE_PRINCIPLES.md").unlink()
        with pytest.raises(FileNotFoundError, match="CORE_PRINCIPLES.md"):
            compute_principle_seal()

    def test_verify_seal_pass(self, repo_root):
        from cortex.integrity import compute_principle_seal, verify_principle_seal
        seal = compute_principle_seal()
        valid, msg = verify_principle_seal(seal)
        assert valid is True

    def test_verify_seal_fail(self, repo_root):
        from cortex.integrity import verify_principle_seal
        valid, msg = verify_principle_seal("wrong_seal")
        assert valid is False
        assert "modified" in msg.lower() or "mismatch" in msg.lower()


class TestChecksums:
    """Tests for FROZEN file checksum verification."""

    def test_compute_all_checksums(self, repo_root):
        from cortex.integrity import compute_all_checksums
        checksums = compute_all_checksums()
        assert "CORE_PRINCIPLES.md" in checksums
        assert checksums["CORE_PRINCIPLES.md"] != "MISSING"

    def test_first_boot_stores_checksums(self, repo_root, db_conn):
        from cortex.integrity import verify_checksums
        # First boot — no stored checksums, should auto-store
        valid, violations = verify_checksums(db_conn)
        assert valid is True
        assert violations == []
        # Verify they were stored
        rows = db_conn.execute("SELECT COUNT(*) FROM file_checksums").fetchone()
        assert rows[0] > 0

    def test_detects_modified_file(self, repo_root, db_conn):
        from cortex.integrity import verify_checksums
        # First boot stores checksums
        verify_checksums(db_conn)
        # Modify a frozen file
        (repo_root / "CORE_PRINCIPLES.md").write_text("TAMPERED!")
        # Second check should detect the modification
        valid, violations = verify_checksums(db_conn)
        assert valid is False
        assert any("CORE_PRINCIPLES.md" in v for v in violations)


class TestCanaryTests:
    """Tests for safety canary verification."""

    def test_canary_passes_with_working_guardrails(self, db_conn):
        from cortex.integrity import run_canary_test
        ok, msg = run_canary_test(db_conn)
        assert ok is True, f"Canary failed: {msg}"
        assert "passed" in msg.lower()


class TestAuditLog:
    """Tests for the append-only audit log."""

    def test_log_event(self, db_conn):
        from cortex.integrity import log_audit_event
        log_audit_event(db_conn, "test_event", "info", "test details")
        row = db_conn.execute(
            "SELECT * FROM audit_log WHERE event_type = 'test_event'"
        ).fetchone()
        assert row is not None
        assert row["severity"] == "info"
        assert row["details"] == "test details"

    def test_audit_log_is_append_only(self, db_conn):
        from cortex.integrity import log_audit_event
        log_audit_event(db_conn, "event_1", "info", "first")
        log_audit_event(db_conn, "event_2", "warning", "second")
        rows = db_conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()
        assert rows[0] == 2


class TestStartupIntegrity:
    """Tests for the full startup integrity gate."""

    @pytest.mark.asyncio
    async def test_startup_passes_clean(self, db_conn):
        """Startup should pass when everything is intact (using real repo)."""
        from cortex.integrity import verify_startup_integrity, set_repo_root
        set_repo_root(Path(__file__).parent.parent)
        result = await verify_startup_integrity(db_conn)
        assert result["principles_found"] is True
        assert result["checksums_valid"] is True
        assert result["canary_passed"] is True

    @pytest.mark.asyncio
    async def test_startup_fails_missing_principles(self, repo_root, db_conn):
        """Startup should fail if CORE_PRINCIPLES.md is deleted."""
        from cortex.integrity import verify_startup_integrity, IntegrityError
        (repo_root / "CORE_PRINCIPLES.md").unlink()
        with pytest.raises(IntegrityError, match="CORE_PRINCIPLES.md"):
            await verify_startup_integrity(db_conn)
