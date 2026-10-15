"""Durable acceptance, bounded work, recovery and failure evidence for P03."""

from __future__ import annotations

import asyncio
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from unittest.mock import patch

import pytest

from cortex.db import _create_schema
from cortex.orchestrator.turn_store import (
    TurnCapacityError, TurnConflictError, TurnStore, TurnStoreError,
    TurnStorePolicy, TurnStoreService,
)
from cortex.pipeline.turns import ActionOutcome, DispatchResult, RouteStatus, TurnIdentity


@pytest.fixture
def store(tmp_path):
    conn = sqlite3.connect(tmp_path / "turns.db", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    _create_schema(conn)
    conn.commit()
    yield TurnStore(conn, TurnStorePolicy())
    conn.close()


def accept(store, number=1, user="alice", conversation="chat", key=None):
    identity = TurnIdentity(str(number), conversation, user, idempotency_key=key)
    return store.accept(identity, f"Question {number}", input_validated=True)


def llm_pending(store, number):
    turn = accept(store, number)
    store.claim(str(number), "alice", "router")
    store.mark_llm_pending(str(number), "alice", "router", turn.identity.revision)
    return turn


def finish(store, number=1, outcome=ActionOutcome.NONE):
    return store.complete(
        str(number), "alice", "worker", 1,
        DispatchResult(RouteStatus.HANDLED, "An answer.", action_outcome=outcome),
        output_validated=True,
    )


def test_a03_one_hundred_turns_survive_reopen(store):
    for number in range(100):
        turn = accept(store, number, key=f"key-{number}")
        assert turn.sequence == number + 1
    expected_usage = store.status()["payload_bytes"]
    path = store.conn.execute("PRAGMA database_list").fetchone()["file"]
    store.conn.close()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    _create_schema(conn)
    conn.commit()
    reopened = TurnStore(conn)
    try:
        assert reopened.status()["counts"] == {"accepted": 100}
        assert reopened.status()["payload_bytes"] == expected_usage
        assert len(reopened.pending_ids()) == reopened.policy.prefetch
        for number in range(100):
            turn = reopened.get(str(number), "alice")
            assert turn.transcript == f"Question {number}"
            assert turn.sequence == number + 1
            assert turn.identity.idempotency_key == f"key-{number}"
        retry = reopened.accept(
            TurnIdentity("retry-id", "chat", "alice", idempotency_key="key-0"),
            "Question 0", input_validated=True,
        )
        assert retry.identity.turn_id == "0"
        assert reopened.status()["counts"] == {"accepted": 100}
    finally:
        conn.close()


def test_idempotency_checks_identity_and_payload(store):
    original = accept(store, key="request-1")
    assert accept(store, key="request-1") == original
    with pytest.raises(TurnConflictError):
        store.accept(
            TurnIdentity("other", "chat", "alice", idempotency_key="request-1"),
            "A different question", input_validated=True,
        )
    with pytest.raises(TurnConflictError):
        accept(store, user="bob")
    assert store.status()["counts"] == {"accepted": 1}


def test_idempotency_survives_storage_pressure(store):
    turn = accept(store, key="key")
    store.policy = replace(store.policy, max_store_bytes=1)
    assert accept(store, key="key") == turn
    with pytest.raises(TurnCapacityError):
        accept(store, 2)


def test_input_validation_and_resource_settings(store):
    with pytest.raises(TurnStoreError, match="validation"):
        store.accept(TurnIdentity("bad", "chat", "alice"), "bad", input_validated=False)
    with pytest.raises(TurnStoreError, match="JSON object"):
        store.accept(TurnIdentity("bad", "chat", "alice"), "bad", input_validated=True, context=[])
    with pytest.raises(TurnStoreError, match="JSON serializable"):
        store.accept(
            TurnIdentity("bad", "chat", "alice"), "bad", input_validated=True,
            context={"bad": object()},
        )
    with pytest.raises(TurnStoreError):
        TurnStorePolicy(prefetch=0)
    with pytest.raises(TurnStoreError):
        TurnStorePolicy(max_active=True)
    with patch.dict("os.environ", {"CORTEX_TURN_PREFETCH": "not-a-number"}):
        with pytest.raises(TurnStoreError, match="integer"):
            TurnStorePolicy.from_env()
    assert store.status()["counts"] == {}


def test_short_and_sensitive_operands_preserved_in_operational_store(store):
    text = "Call 555-123-4567"
    turn = store.accept(TurnIdentity("phone", "chat", "alice"), text, input_validated=True)
    assert turn.transcript == text
    with pytest.raises(TurnConflictError):
        store.get("phone", "bob")


def test_disk_pressure_never_evicts_existing_turns(store):
    accept(store)
    with patch("cortex.orchestrator.turn_store.shutil.disk_usage") as usage:
        usage.return_value.free = 0
        with pytest.raises(TurnCapacityError, match="free disk"):
            accept(store, 2)
        assert store.status()["admission_available"] is False
    assert store.get("1", "alice").state == "accepted"


def test_readonly_write_has_no_acceptance_or_sequence_increment(store):
    store.conn.execute("PRAGMA query_only=ON")
    with pytest.raises(TurnStoreError, match="storage failed"):
        accept(store)
    store.conn.execute("PRAGMA query_only=OFF")
    assert store.status()["counts"] == {}
    assert accept(store).sequence == 1


def test_foreign_transaction_is_not_committed_or_rolled_back(store):
    store.conn.execute("BEGIN")
    with pytest.raises(TurnConflictError, match="nest"):
        accept(store)
    assert store.conn.in_transaction
    store.conn.rollback()


def test_atomic_claim_between_independent_connections(store):
    accept(store)
    path = store.conn.execute("PRAGMA database_list").fetchone()["file"]
    barrier = Barrier(2)

    def claim(owner):
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        other = TurnStore(conn)
        try:
            barrier.wait(timeout=5)
            other.claim("1", "alice", owner)
            return True
        except TurnConflictError:
            return False
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, ("first", "second")))
    assert sorted(results) == [False, True]
    assert store.get("1", "alice").attempts == 1


def test_active_inference_limit_does_not_block_fast_path(store):
    llm_pending(store, 1)
    llm_pending(store, 2)
    store.claim("1", "alice", "worker", stage="llm")
    with pytest.raises(TurnCapacityError, match="Active inference"):
        store.claim("2", "alice", "other", stage="llm")
    accept(store, 3)
    assert store.claim("3", "alice", "fast-worker", stage="fastpath").state == "fastpath_running"
    assert store.get("2", "alice").state == "llm_pending"


def test_claim_heartbeat_and_wrong_owner(store):
    accept(store)
    store.claim("1", "alice", "worker")
    store.heartbeat("1", "alice", "worker", 1)
    with pytest.raises(TurnConflictError):
        store.heartbeat("1", "alice", "wrong", 1)
    with pytest.raises(TurnConflictError):
        store.claim("1", "alice", "worker")


def test_validated_result_and_delivery_are_separate(store):
    accept(store)
    store.claim("1", "alice", "worker")
    with pytest.raises(TurnStoreError, match="output-validated"):
        store.complete(
            "1", "alice", "worker", 1, DispatchResult(RouteStatus.HANDLED, "Unsafe"),
            output_validated=False,
        )
    turn = finish(store)
    assert turn.state == "completed"
    assert turn.delivery_state == "ready"
    assert turn.output_validated
    store.set_delivery("1", "alice", 1, "delivering")
    store.set_delivery("1", "alice", 1, "delivered", position=10)
    assert store.get("1", "alice").playback_position == 10


def test_result_capacity_failure_preserves_claim(store):
    accept(store)
    store.claim("1", "alice", "worker")
    store.policy = replace(store.policy, max_store_bytes=store.status()["payload_bytes"] + 1)
    with pytest.raises(TurnCapacityError):
        finish(store)
    assert store.get("1", "alice").state == "routing"
    assert store.get("1", "alice").result is None


def test_supersession_revokes_old_result_and_keeps_lineage(store):
    llm_pending(store, 1)
    store.claim("1", "alice", "worker", stage="llm")
    accept(store, 2)
    store.supersede("1", "alice", 1, "2")
    with pytest.raises(TurnConflictError):
        finish(store)
    assert store.get("1", "alice").state == "superseded"
    assert store.get("2", "alice").related_turn_id == "1"
    assert store.pending_ids() == ["2"]


def test_expired_claim_recovery_invalidates_revision(store):
    accept(store)
    store.claim("1", "alice", "worker")
    store.conn.execute("UPDATE pending_turns SET lease_until = 0")
    store.conn.commit()
    with pytest.raises(TurnConflictError):
        finish(store)
    assert store.recover() == 1
    turn = store.get("1", "alice")
    assert turn.state == "recovery_pending" and turn.identity.revision == 2
    assert store.pending_ids() == []
    assert store.recovery_ids("alice") == ["1"]
    assert store.recovery_ids("bob") == []
    store.confirm_recovery("1", "alice", 2)
    assert store.get("1", "alice").identity.revision == 3
    assert store.pending_ids() == ["1"]


def test_restart_does_not_replay_uncertain_action_or_stale_speech(store):
    accept(store)
    store.claim("1", "alice", "worker")
    store.begin_action("1", "alice", "worker", 1)
    accept(store, 2)
    store.claim("2", "alice", "worker")
    finish(store, 2)
    assert store.recover(startup=True) == 1
    assert store.get("1", "alice").state == "uncertain"
    assert store.recovery_ids("alice") == ["1", "2"]
    with pytest.raises(TurnConflictError, match="explicit retry"):
        store.confirm_recovery("1", "alice", 2)
    with pytest.raises(TurnConflictError, match="confirmation"):
        store.set_delivery("2", "alice", 1, "delivering")
    store.set_delivery("2", "alice", 1, "delivering", confirmed=True)
    store.confirm_recovery("1", "alice", 2, retry_uncertain=True)
    assert store.get("1", "alice").state == "accepted"


def test_cancelling_started_action_is_uncertain_not_undone(store):
    accept(store)
    store.claim("1", "alice", "worker")
    store.begin_action("1", "alice", "worker", 1)
    store.cancel("1", "alice", 1)
    turn = store.get("1", "alice")
    assert turn.state == "uncertain" and turn.action_outcome == "uncertain"
    with pytest.raises(TurnConflictError):
        finish(store, outcome=ActionOutcome.SUCCEEDED)


def test_failure_after_external_effect_is_uncertain(store):
    accept(store)
    store.claim("1", "alice", "worker")
    store.begin_action("1", "alice", "worker", 1)
    store.fail("1", "alice", "worker", 1, "backend_timeout")
    assert store.get("1", "alice").state == "uncertain"


def test_cleanup_never_deletes_pending_or_unheard_results(store):
    accept(store)
    accept(store, 2)
    store.claim("2", "alice", "worker")
    finish(store, 2)
    accept(store, 3)
    store.claim("3", "alice", "worker")
    store.fail("3", "alice", "worker", 1, "provider_unavailable")
    store.conn.execute("UPDATE pending_turns SET updated_at = 0")
    store.conn.commit()
    before = store.status()["payload_bytes"]
    assert store.cleanup() == 1
    assert store.get("1", "alice").state == "accepted"
    assert store.get("2", "alice").delivery_state == "ready"
    assert store.status()["payload_bytes"] < before
    assert accept(store, 4).sequence == 4


def test_user_deletion_updates_budget_and_preserves_other_users(store):
    accept(store)
    accept(store, 2, user="bob")
    assert store.delete_user("alice") == 1
    assert store.get("2", "bob").state == "accepted"
    assert store.status()["counts"] == {"accepted": 1}
    assert store.conn.execute(
        "SELECT COUNT(*) FROM pending_turn_sequences WHERE user_id = 'alice'",
    ).fetchone()[0] == 0


async def test_maintenance_lifecycle_is_owned_and_stoppable(store):
    service = TurnStoreService(store)
    await service.start()
    task = service._task
    await service.start()
    assert service._task is task
    await asyncio.sleep(0)
    await service.stop()
    assert task.done()
    assert service._task is None


async def test_prepared_context_snapshot_can_be_persisted(store, monkeypatch):
    from cortex.pipeline import prepare_turn
    from cortex.plugins import PluginRegistry

    monkeypatch.setattr("cortex.pipeline.layer2_plugins.get_registry", lambda: PluginRegistry())
    monkeypatch.setattr("cortex.pipeline.layer2_plugins._try_learned_patterns", lambda _: (None, 0))
    prepared = await prepare_turn("Explain clouds", user_id="alice")
    turn = store.accept(
        prepared.identity, prepared.message, input_validated=True,
        context=prepared.context_snapshot(),
    )
    assert turn.context["turn_identity"]["turn_id"] == turn.identity.turn_id


def test_context_order_is_not_an_idempotency_conflict(store):
    identity = TurnIdentity("1", "chat", "alice", idempotency_key="key")
    first = store.accept(identity, "Question", input_validated=True, context={"a": 1, "b": 2})
    retry = store.accept(identity, "Question", input_validated=True, context={"b": 2, "a": 1})
    assert first == retry


def test_committed_turns_survive_process_crash_and_claim_requires_confirmation(store):
    path = store.conn.execute("PRAGMA database_list").fetchone()["file"]
    script = """
import os, sqlite3, sys
from cortex.orchestrator.turn_store import TurnStore
from cortex.pipeline.turns import TurnIdentity
conn = sqlite3.connect(sys.argv[1])
conn.row_factory = sqlite3.Row
store = TurnStore(conn)
for index in range(100):
    store.accept(TurnIdentity(str(index), 'crash', 'alice'), 'Question', input_validated=True)
store.claim('0', 'alice', 'worker')
store.begin_action('0', 'alice', 'worker', 1)
os._exit(17)
"""
    result = subprocess.run([sys.executable, "-c", script, path], timeout=30, check=False)
    assert result.returncode == 17
    assert store.status()["counts"] == {"accepted": 99, "fastpath_running": 1}
    assert store.recover(startup=True) == 100
    assert store.status()["counts"] == {"recovery_pending": 99, "uncertain": 1}
    assert store.pending_ids() == []


def test_additive_migration_preserves_existing_profile(store):
    store.conn.execute(
        "INSERT INTO user_profiles (user_id, display_name) VALUES ('legacy-user', 'Legacy')",
    )
    for table in ("pending_turns", "pending_turn_sequences", "pending_turn_usage"):
        store.conn.execute(f"DROP TABLE {table}")
    store.conn.commit()
    _create_schema(store.conn)
    _create_schema(store.conn)
    store.conn.commit()
    assert store.conn.execute(
        "SELECT display_name FROM user_profiles WHERE user_id = 'legacy-user'",
    ).fetchone()[0] == "Legacy"
    assert accept(store).state == "accepted"


def test_corrupt_persisted_context_fails_explicitly(store):
    accept(store)
    store.conn.execute("UPDATE pending_turns SET context_json = 'not-json'")
    store.conn.commit()
    with pytest.raises(TurnStoreError, match="read failed"):
        store.get("1", "alice")


def test_p05_sequence_migration_preserves_existing_order(store):
    accept(store)
    store.conn.execute("ALTER TABLE pending_turn_sequences DROP COLUMN last_llm_dispatch")
    store.conn.commit()
    _create_schema(store.conn)
    _create_schema(store.conn)
    store.conn.commit()
    row = store.conn.execute("SELECT * FROM pending_turn_sequences").fetchone()
    assert row["sequence"] == 1
    assert row["last_llm_dispatch"] == 0
    assert accept(store, 2).sequence == 2


def test_p05_replacement_insert_failure_rolls_back_supersession(store):
    llm_pending(store, 1)
    store.conn.execute(
        "CREATE TRIGGER deny_replacement BEFORE INSERT ON pending_turns "
        "WHEN NEW.turn_id='2' BEGIN SELECT RAISE(ABORT, 'injected insert failure'); END",
    )
    store.conn.commit()
    with pytest.raises(TurnStoreError):
        store.accept(TurnIdentity("2", "chat", "alice"), "replacement",
                     input_validated=True, replaces=("1", 1))
    old = store.get("1", "alice")
    assert (old.state, old.identity.revision) == ("llm_pending", 1)
    assert store.status()["counts"] == {"llm_pending": 1}


def test_p05_output_limit_measures_utf8_bytes_at_exact_boundary(store):
    store.policy = replace(store.policy, max_output_bytes=4)
    for number, response in ((1, "\u00e9\u00e9"), (2, "\u00e9\u00e9a")):
        llm_pending(store, number)
        store.claim(str(number), "alice", "worker", stage="llm")
        result = DispatchResult(RouteStatus.HANDLED, response)
        if number == 1:
            assert store.complete(str(number), "alice", "worker", 1, result,
                                  output_validated=True).result == response
        else:
            with pytest.raises(TurnCapacityError, match="output byte limit"):
                store.complete(str(number), "alice", "worker", 1, result, output_validated=True)
            assert store.get(str(number), "alice").result is None


async def test_admin_user_deletion_includes_turn_ledger(store, monkeypatch):
    from cortex.admin.users import delete_user

    accept(store)
    accept(store, 2, user="bob")
    store.conn.execute(
        "INSERT INTO user_profiles (user_id, display_name) VALUES ('alice', 'Alice')",
    )
    store.conn.commit()
    monkeypatch.setattr("cortex.admin.helpers._db", lambda: store.conn)
    assert await delete_user("alice", {}) == {"ok": True}
    assert store.status()["counts"] == {"accepted": 1}
    assert store.get("2", "bob").state == "accepted"


async def test_admin_recovery_is_scoped_bounded_and_revision_checked(store, monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from cortex.server import app
    from cortex.admin.helpers import require_admin

    store.accept(TurnIdentity("1", "chat", "alice"), "Synthetic question", input_validated=True,
                 context={"generation_options": {"model_fast": "test", "model_thinking": "test"}})
    store.recover(startup=True)
    monkeypatch.setattr(app.state, "turn_store", store, raising=False)
    monkeypatch.setitem(app.dependency_overrides, require_admin, lambda: {"sub": "1", "username": "admin"})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/system/turns/recovery", params={"user_id": "alice"})
        assert response.status_code == 200
        assert response.json()["turns"][0]["revision"] == 2
        assert "transcript" not in response.text
        assert (await client.get(
            "/admin/system/turns/recovery", params={"user_id": "alice", "limit": 9},
        )).status_code == 400
        assert (await client.post("/admin/system/turns/1/recover", json={
            "user_id": "bob", "revision": 2,
        })).status_code == 409
        assert (await client.post("/admin/system/turns/1/recover", json={
            "user_id": "alice", "revision": True,
        })).status_code == 422
        assert (await client.post("/admin/system/turns/1/recover", json={
            "user_id": "alice", "revision": 2,
        })).status_code == 200
    assert store.get("1", "alice").state == "accepted"


async def test_admin_does_not_claim_an_unowned_action_will_be_retried(store, monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from cortex.server import app
    from cortex.admin.helpers import require_admin

    accept(store)
    store.recover(startup=True)
    monkeypatch.setattr(app.state, "turn_store", store, raising=False)
    monkeypatch.setitem(app.dependency_overrides, require_admin, lambda: {"sub": "1"})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/admin/system/turns/1/recover", json={
            "user_id": "alice", "revision": 2, "retry_uncertain": True,
        })
    assert response.status_code == 409
    assert "manual verification" in response.json()["detail"]
    assert store.get("1", "alice").state == "recovery_pending"

async def test_admin_status_and_cancel_are_scoped_bounded_and_revision_checked(store, monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from cortex.server import app
    from cortex.admin.helpers import require_admin

    accept(store)
    accept(store, 2, user="bob")
    monkeypatch.setattr(app.state, "turn_store", store, raising=False)
    monkeypatch.setattr(app.state, "conversation_coordinator", None, raising=False)
    monkeypatch.setitem(app.dependency_overrides, require_admin, lambda: {"sub": "1"})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/system/turns/user?user_id=alice")
        assert [turn["turn_id"] for turn in response.json()["turns"]] == ["1"]
        assert "Question" not in response.text
        assert "transcript" not in response.text
        assert (await client.get("/admin/system/turns/user?user_id=alice&limit=999")).status_code == 400
        assert (await client.post("/admin/system/turns/1/cancel", json={
            "user_id": "bob", "revision": 1,
        })).status_code == 409
        assert (await client.post("/admin/system/turns/1/cancel", json={
            "user_id": "alice", "revision": 2,
        })).status_code == 409
        assert (await client.post("/admin/system/turns/1/cancel", json={
            "user_id": "alice", "revision": 1,
        })).status_code == 200
    assert store.get("1", "alice").state == "cancelled"
    assert store.get("2", "bob").state == "accepted"


def test_browser_turn_status_cannot_cross_user_or_conversation(store, monkeypatch):
    from fastapi.testclient import TestClient
    from cortex.server import app

    store.accept(TurnIdentity("browser-id", "private", "alice", source="browser"),
                 "Synthetic private question", input_validated=True)
    monkeypatch.setattr(app.state, "turn_store", store, raising=False)
    with TestClient(app).websocket_connect("/ws/chat") as socket:
        for user, conversation, expected in [
            ("bob", "private", "not_available"),
            ("alice", "different", "not_available"),
            ("alice", "private", "accepted"),
        ]:
            socket.send_json({"type": "turn_status", "turn_id": "browser-id",
                              "user_id": user, "conversation_id": conversation})
            response = socket.receive_json()
            assert response["state"] == expected
            assert "question" not in str(response).lower()


@pytest.mark.parametrize("endpoint", ["/admin/system/turns", "/admin/system/turns/recovery?user_id=alice"])
async def test_admin_store_read_failure_is_503(store, monkeypatch, endpoint):
    from httpx import ASGITransport, AsyncClient
    from cortex.server import app
    from cortex.admin.helpers import require_admin

    monkeypatch.setattr(app.state, "turn_store", store, raising=False)
    monkeypatch.setitem(app.dependency_overrides, require_admin, lambda: {"sub": "1"})
    store.conn.close()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(endpoint)
    assert response.status_code == 503
    assert "failed" in response.json()["detail"]
