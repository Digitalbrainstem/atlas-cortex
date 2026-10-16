"""Regression coverage for gaps found during MVP acceptance review."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from cortex.auth_user import UserAuthConfig, UserAuthManager
from cortex.db import get_db, init_db, set_db_path
from cortex.safety import GuardrailResult, Severity


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "repairs.db"
    set_db_path(path)
    init_db(path)
    conn = get_db()
    monkeypatch.setattr("cortex.auth_user._manager", None)
    monkeypatch.setattr("cortex.server._get_db", lambda: conn)
    yield conn
    monkeypatch.setattr("cortex.auth_user._manager", None)


def test_persisted_auth_is_loaded_before_http_identity_resolution(db):
    from cortex.server import app
    manager = UserAuthManager()
    manager._users["alice"] = UserAuthConfig("alice", "Alice")
    manager.set_pin("alice", "1234")
    manager.save_to_db(db, manager.get_user("alice"))
    response = TestClient(app).post("/v1/chat/completions", json={
        "user": "alice", "messages": [{"role": "user", "content": "2 + 2"}],
    })
    assert response.status_code == 401


async def test_setting_pin_preserves_child_tier_and_old_adult_default(db, monkeypatch):
    from cortex.admin.users import SetUserAuthRequest, set_user_auth
    from cortex.safety.middleware import PipelineSafetyMiddleware
    monkeypatch.setattr("cortex.admin.helpers._db", lambda: db)
    db.execute("INSERT INTO user_profiles(user_id,display_name,age_group,age_confidence) VALUES('kid','Kid','child',1)")
    db.commit()
    await set_user_auth("kid", SetUserAuthRequest(method="pin", value="1234"), {})
    assert PipelineSafetyMiddleware(db)._resolve_profile("kid")[1] == "child"
    db.execute("UPDATE user_auth SET content_tier='adult' WHERE user_id='kid'")
    db.commit()
    assert PipelineSafetyMiddleware(db)._resolve_profile("kid")[1] == "child"


def test_http_speech_rechecks_policy_after_synthesis(db, monkeypatch):
    from cortex.server import app
    calls = []
    class Safety:
        blocked = False
        def check_output(self, *args, **kwargs):
            return GuardrailResult(
                Severity.SOFT_BLOCK if self.blocked else Severity.PASS, "canary", "Synthetic",
            )
    safety = Safety()
    class TTS:
        sample_rate = 16000
        async def synthesize(self, **kwargs):
            calls.append(kwargs["text"])
            safety.blocked = True
            yield b"\0\0" * 20
    monkeypatch.setattr("cortex.server._safety_middleware", safety)
    monkeypatch.setattr("cortex.server.get_tts_provider", lambda *args: TTS())
    client = TestClient(app)
    assert client.post("/v1/audio/speech", json={"input": "Synthetic canary"}).status_code == 403
    assert calls == ["Synthetic canary"]
    assert client.post("/v1/audio/speech", json={"input": "Blocked before synthesis"}).status_code == 403
    assert calls == ["Synthetic canary"]
    assert client.post("/v1/audio/speech", json={"input": "x" * 16001}).status_code == 422


def test_auth_load_failure_does_not_publish_empty_manager(db, monkeypatch):
    from cortex.auth_user import get_user_auth
    def fail(self, conn):
        raise RuntimeError("Synthetic storage failure")
    monkeypatch.setattr(UserAuthManager, "load_from_db", fail)
    with pytest.raises(RuntimeError, match="storage failure"):
        get_user_auth()
    import cortex.auth_user
    assert cortex.auth_user._manager is None
