"""Local enrollment, conservative matching and deletion races."""
from __future__ import annotations

import asyncio
import base64
from unittest.mock import AsyncMock

import numpy as np
import pytest
from fastapi import HTTPException

from cortex.db import get_db, init_db, set_db_path
from cortex.speech.recognition import identify_audio


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "speakers.db"
    set_db_path(path)
    init_db(path)
    conn = get_db()
    conn.execute("INSERT INTO user_profiles(user_id,display_name) VALUES('alice','Alice'),('bob','Bob')")
    conn.commit()
    monkeypatch.setattr("cortex.admin.helpers._db", lambda: conn)
    return conn


def embedding(index=0):
    vector = np.zeros(256, dtype="<f4")
    vector[index] = 1
    return vector.tobytes()


async def test_enroll_identify_and_delete_share_the_admin_profile_store(db, monkeypatch):
    from cortex.admin.devices import VoiceEnrollmentRequest, enroll_speaker, delete_speaker
    monkeypatch.setattr("cortex.speech.recognition.audio_embedding", AsyncMock(return_value=embedding()))
    result = await enroll_speaker(VoiceEnrollmentRequest(
        user_id="alice", audio_base64=base64.b64encode(b"\0\0" * 16000).decode(),
    ), {})
    assert await identify_audio(b"\0\0" * 16000, db) == ("alice", result["speaker_id"])
    await delete_speaker(result["speaker_id"], {})
    assert await identify_audio(b"\0\0" * 16000, db) == ("anonymous", None)


async def test_ambiguous_voices_and_backend_failure_remain_anonymous(db, monkeypatch):
    for user in ("alice", "bob"):
        db.execute("INSERT INTO speaker_profiles(id,user_id,display_name,embedding) VALUES(?,?,?,?)",
                   (user, user, user, embedding()))
    db.commit()
    monkeypatch.setattr("cortex.speech.recognition.audio_embedding", AsyncMock(return_value=embedding()))
    assert await identify_audio(b"\0\0" * 16000, db) == ("anonymous", None)
    monkeypatch.setattr("cortex.speech.recognition.audio_embedding", AsyncMock(side_effect=RuntimeError("offline")))
    assert await identify_audio(b"\0\0" * 16000, db) == ("anonymous", None)


async def test_profile_deletion_during_embedding_does_not_restore_identity(db, monkeypatch):
    db.execute("INSERT INTO speaker_profiles(id,user_id,display_name,embedding) VALUES('s','alice','Alice',?)", (embedding(),))
    db.commit()
    entered, release = asyncio.Event(), asyncio.Event()
    async def encode(audio):
        entered.set()
        await release.wait()
        return embedding()
    monkeypatch.setattr("cortex.speech.recognition.audio_embedding", encode)
    task = asyncio.create_task(identify_audio(b"\0\0" * 16000, db))
    await entered.wait()
    db.execute("DELETE FROM user_profiles WHERE user_id='alice'")
    db.commit()
    release.set()
    assert await task == ("anonymous", None)


async def test_enrollment_rejects_unknown_profiles_and_invalid_audio(db):
    from cortex.admin.devices import VoiceEnrollmentRequest, enroll_speaker
    with pytest.raises(HTTPException) as missing:
        await enroll_speaker(VoiceEnrollmentRequest(user_id="missing", audio_base64="bad"), {})
    assert missing.value.status_code == 404
    with pytest.raises(HTTPException) as invalid:
        await enroll_speaker(VoiceEnrollmentRequest(user_id="alice", audio_base64="bad"), {})
    assert invalid.value.status_code == 400
