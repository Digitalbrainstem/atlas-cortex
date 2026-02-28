"""Tests for the voice identity (speaker recognition) module."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import numpy as np
import pytest

from cortex.db import init_db, set_db_path
from cortex.voice.identity import IdentifyResult, SpeakerIdentifier


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


def _random_embedding(dim: int = 256, seed: int | None = None) -> list[float]:
    """Return a normalised random embedding."""
    rng = np.random.default_rng(seed)
    vec = rng.standard_normal(dim)
    vec = vec / np.linalg.norm(vec)
    return vec.tolist()


def _similar_embedding(base: list[float], noise: float = 0.05, seed: int | None = None) -> list[float]:
    """Return an embedding close to *base* (high cosine similarity)."""
    rng = np.random.default_rng(seed)
    vec = np.asarray(base) + rng.standard_normal(len(base)) * noise
    vec = vec / np.linalg.norm(vec)
    return vec.tolist()


# ---------------------------------------------------------------------------
# IdentifyResult dataclass
# ---------------------------------------------------------------------------


class TestIdentifyResult:
    def test_defaults(self):
        r = IdentifyResult(user_id=None, confidence=0.0, is_known=False)
        assert r.user_id is None
        assert r.confidence == 0.0
        assert r.is_known is False
        assert r.age_estimate is None

    def test_with_values(self):
        r = IdentifyResult(user_id="alice", confidence=0.92, is_known=True, age_estimate="adult")
        assert r.user_id == "alice"
        assert r.is_known is True
        assert r.age_estimate == "adult"


# ---------------------------------------------------------------------------
# Enrollment & identification
# ---------------------------------------------------------------------------


class TestEnrollAndIdentify:
    @pytest.mark.asyncio
    async def test_enroll_returns_true(self, db_conn):
        sid = SpeakerIdentifier(db_conn)
        emb = _random_embedding(seed=1)
        assert await sid.enroll("alice", emb, label="sample1") is True

    @pytest.mark.asyncio
    async def test_identify_enrolled_speaker(self, db_conn):
        sid = SpeakerIdentifier(db_conn)
        emb = _random_embedding(seed=2)
        await sid.enroll("bob", emb)

        # Query with a very similar embedding
        query = _similar_embedding(emb, noise=0.02, seed=10)
        result = await sid.identify(query)
        assert result.is_known is True
        assert result.user_id == "bob"
        assert result.confidence > 0.75

    @pytest.mark.asyncio
    async def test_enrollment_persisted_to_db(self, db_conn):
        sid = SpeakerIdentifier(db_conn)
        emb = _random_embedding(seed=3)
        await sid.enroll("carol", emb)

        row = db_conn.execute(
            "SELECT user_id FROM voice_enrollments WHERE user_id = ?", ("carol",)
        ).fetchone()
        assert row is not None
        assert row["user_id"] == "carol"


# ---------------------------------------------------------------------------
# Unknown speaker
# ---------------------------------------------------------------------------


class TestUnknownSpeaker:
    @pytest.mark.asyncio
    async def test_no_enrollments_returns_unknown(self, db_conn):
        sid = SpeakerIdentifier(db_conn)
        result = await sid.identify(_random_embedding(seed=42))
        assert result.is_known is False
        assert result.user_id is None

    @pytest.mark.asyncio
    async def test_dissimilar_embedding_returns_unknown(self, db_conn):
        sid = SpeakerIdentifier(db_conn)
        await sid.enroll("dave", _random_embedding(seed=100))

        # Completely different embedding
        foreign = _random_embedding(seed=999)
        result = await sid.identify(foreign)
        assert result.is_known is False
        assert result.user_id is None


# ---------------------------------------------------------------------------
# Multiple enrollment samples
# ---------------------------------------------------------------------------


class TestMultipleEnrollments:
    @pytest.mark.asyncio
    async def test_multiple_samples_improve_centroid(self, db_conn):
        sid = SpeakerIdentifier(db_conn)
        base = _random_embedding(seed=50)
        # Enroll several noisy variants
        for i in range(5):
            await sid.enroll("eve", _similar_embedding(base, noise=0.08, seed=50 + i))

        query = _similar_embedding(base, noise=0.03, seed=200)
        result = await sid.identify(query)
        assert result.is_known is True
        assert result.user_id == "eve"

    @pytest.mark.asyncio
    async def test_in_memory_cache_matches_db(self, db_conn):
        sid = SpeakerIdentifier(db_conn)
        for i in range(3):
            await sid.enroll("frank", _random_embedding(seed=300 + i))

        rows = db_conn.execute(
            "SELECT COUNT(*) AS cnt FROM voice_enrollments WHERE user_id = ?",
            ("frank",),
        ).fetchone()
        assert rows["cnt"] == 3
        assert len(sid._enrollments["frank"]) == 3


# ---------------------------------------------------------------------------
# Unenrollment
# ---------------------------------------------------------------------------


class TestUnenroll:
    @pytest.mark.asyncio
    async def test_unenroll_removes_from_db(self, db_conn):
        sid = SpeakerIdentifier(db_conn)
        await sid.enroll("gina", _random_embedding(seed=400))
        assert await sid.unenroll("gina") is True

        row = db_conn.execute(
            "SELECT COUNT(*) AS cnt FROM voice_enrollments WHERE user_id = ?",
            ("gina",),
        ).fetchone()
        assert row["cnt"] == 0

    @pytest.mark.asyncio
    async def test_unenroll_removes_from_cache(self, db_conn):
        sid = SpeakerIdentifier(db_conn)
        await sid.enroll("hank", _random_embedding(seed=500))
        await sid.unenroll("hank")
        assert "hank" not in sid._enrollments

    @pytest.mark.asyncio
    async def test_unenroll_nonexistent_user_is_safe(self, db_conn):
        sid = SpeakerIdentifier(db_conn)
        assert await sid.unenroll("nobody") is True

    @pytest.mark.asyncio
    async def test_identify_after_unenroll_returns_unknown(self, db_conn):
        sid = SpeakerIdentifier(db_conn)
        emb = _random_embedding(seed=600)
        await sid.enroll("iris", emb)
        await sid.unenroll("iris")

        result = await sid.identify(_similar_embedding(emb, noise=0.02, seed=601))
        assert result.is_known is False


# ---------------------------------------------------------------------------
# Age estimation placeholder
# ---------------------------------------------------------------------------


class TestAgeEstimate:
    def test_returns_valid_group(self, db_conn):
        sid = SpeakerIdentifier(db_conn)
        group, conf = sid.estimate_age_group(_random_embedding(seed=700))
        assert group in ("child", "adult")
        assert conf > 0.0

    def test_empty_embedding_returns_unknown(self, db_conn):
        sid = SpeakerIdentifier(db_conn)
        group, conf = sid.estimate_age_group([])
        assert group == "unknown"
        assert conf == 0.0

    def test_low_energy_embedding_is_child(self, db_conn):
        """An embedding with very low energy/variance should map to child."""
        sid = SpeakerIdentifier(db_conn)
        # Tiny values → low energy, low variance → child
        emb = [0.01] * 256
        group, conf = sid.estimate_age_group(emb)
        assert group == "child"

    def test_high_energy_embedding_is_adult(self, db_conn):
        """An embedding with high energy/variance should map to adult."""
        sid = SpeakerIdentifier(db_conn)
        rng = np.random.RandomState(42)
        emb = (rng.randn(256) * 2.0).tolist()  # high variance
        group, conf = sid.estimate_age_group(emb)
        assert group == "adult"

    def test_confidence_always_low(self, db_conn):
        """Voice-based estimation should always have low confidence (<=0.3)."""
        sid = SpeakerIdentifier(db_conn)
        for seed in [1, 50, 100, 200]:
            _, conf = sid.estimate_age_group(_random_embedding(seed=seed))
            assert conf <= 0.3

    @pytest.mark.asyncio
    async def test_identify_includes_age_estimate(self, db_conn):
        sid = SpeakerIdentifier(db_conn)
        result = await sid.identify(_random_embedding(seed=800))
        assert result.age_estimate in ("child", "adult")


# ---------------------------------------------------------------------------
# Hybrid age resolution (profiles + voice)
# ---------------------------------------------------------------------------


class TestHybridAgeResolution:
    """Test set_user_age() and resolve_age_group() in profiles module."""

    def test_set_user_age_adult(self, db_conn):
        from cortex.profiles import get_or_create_user_profile, set_user_age
        get_or_create_user_profile(db_conn, "alice")
        result = set_user_age(db_conn, "alice", birth_year=1993, birth_month=6)
        assert result["age_group"] == "adult"
        assert result["age_confidence"] == 0.95
        assert result["age"] >= 32

    def test_set_user_age_child(self, db_conn):
        from cortex.profiles import get_or_create_user_profile, set_user_age
        get_or_create_user_profile(db_conn, "timmy")
        result = set_user_age(db_conn, "timmy", birth_year=2018, birth_month=3)
        assert result["age_group"] in ("child", "toddler")
        assert result["age_confidence"] == 0.95

    def test_set_user_age_teen(self, db_conn):
        from cortex.profiles import get_or_create_user_profile, set_user_age
        get_or_create_user_profile(db_conn, "jake")
        result = set_user_age(db_conn, "jake", birth_year=2011, birth_month=1)
        assert result["age_group"] == "teen"

    def test_resolve_admin_age_wins_over_voice(self, db_conn):
        from cortex.profiles import resolve_age_group
        profile = {"age_group": "adult", "age_confidence": 0.95}
        voice = ("child", 0.3)
        group, conf = resolve_age_group(profile, voice_estimate=voice)
        assert group == "adult"
        assert conf == 0.95

    def test_resolve_voice_fallback_for_unknown(self, db_conn):
        from cortex.profiles import resolve_age_group
        profile = {"age_group": "unknown", "age_confidence": 0.0}
        voice = ("child", 0.3)
        group, conf = resolve_age_group(profile, voice_estimate=voice)
        assert group == "child"
        assert conf == 0.3

    def test_resolve_no_data_returns_unknown(self, db_conn):
        from cortex.profiles import resolve_age_group
        profile = {"age_group": "unknown", "age_confidence": 0.0}
        group, conf = resolve_age_group(profile, voice_estimate=None)
        assert group == "unknown"
        assert conf == 0.0

    def test_resolve_low_confidence_admin_uses_voice(self, db_conn):
        from cortex.profiles import resolve_age_group
        profile = {"age_group": "adult", "age_confidence": 0.2}
        voice = ("child", 0.3)
        group, conf = resolve_age_group(profile, voice_estimate=voice)
        # Admin confidence too low (<0.5) so voice fallback kicks in
        assert group == "child"
