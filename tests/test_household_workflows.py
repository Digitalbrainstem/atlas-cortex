"""Household routing and bounded, screen-independent voice activities."""

from __future__ import annotations

import pytest

from cortex.db import get_db, init_db, set_db_path
from cortex.notifications.satellite import SatelliteChannel
from cortex.plugins.games import MAX_GAME_QUESTIONS, MAX_GAME_SECONDS, STEMGamesPlugin


@pytest.fixture
def household_db(tmp_path):
    path = tmp_path / "household.db"
    set_db_path(path)
    init_db(path)
    db = get_db()
    for device, room in [("kitchen-pi", "kitchen"), ("bedroom-pi", "bedroom")]:
        db.execute("INSERT INTO satellites(id,display_name,room,status) VALUES(?,?,?,'online')",
                   (device, device, room))
    db.commit()
    return db


async def test_notification_scope_never_falls_back_to_other_household_members(household_db):
    channel = SatelliteChannel()
    for user, device in [("alice", "kitchen-pi"), ("bob", "bedroom-pi")]:
        interaction = household_db.execute(
            "INSERT INTO interactions(user_id,message,matched_layer) VALUES(?,'synthetic','plugin')",
            (user,),
        ).lastrowid
        household_db.execute(
            "INSERT INTO room_context_log(interaction_id,satellite_id) VALUES(?,?)",
            (interaction, device),
        )
    household_db.commit()
    assert await channel._resolve_targets("", "alice") == ["kitchen-pi"]
    assert await channel._resolve_targets("unavailable", "alice") == []
    assert await channel._resolve_targets("", "unknown") == []
    household_db.execute("UPDATE satellites SET status='offline' WHERE id='kitchen-pi'")
    household_db.commit()
    assert await channel._resolve_targets("", "alice") == []
    assert await channel._resolve_targets("", "") == ["bedroom-pi"]


@pytest.fixture
async def games(household_db):
    plugin = STEMGamesPlugin()
    await plugin.setup({})
    return plugin


async def command(plugin, text, context):
    match = await plugin.match(text, context)
    assert match.matched
    return await plugin.handle(text, match, context)


async def test_games_are_scoped_stoppable_and_persist_actual_user(games, household_db):
    kitchen = {"user_id": "alice", "satellite_id": "kitchen-pi", "room": "kitchen"}
    bedroom = {**kitchen, "satellite_id": "bedroom-pi", "room": "bedroom"}
    await command(games, "play Number Quest", kitchen)
    assert not (await games.match("4", bedroom)).matched
    await command(games, "play Word Wizard", bedroom)
    await command(games, "stop.", kitchen)
    assert games._session_key(kitchen) not in games._sessions
    assert games._session_key(bedroom) in games._sessions
    rows = household_db.execute("SELECT user_id,ended_at FROM learning_sessions ORDER BY rowid").fetchall()
    assert [row["user_id"] for row in rows] == ["alice", "alice"]
    assert rows[0]["ended_at"] is not None
    assert rows[1]["ended_at"] is None


@pytest.mark.parametrize("game", ["Number Quest", "Science Safari", "Word Wizard"])
async def test_games_end_at_question_limit(games, household_db, game):
    context = {"user_id": "alice"}
    await command(games, f"play {game}", context)
    for _ in range(MAX_GAME_QUESTIONS):
        session = games._sessions["alice"]
        result = await command(games, session.current_question.correct_answer, context)
    assert "alice" not in games._sessions
    assert result.metadata["questions_asked"] == MAX_GAME_QUESTIONS
    assert household_db.execute("SELECT ended_at FROM learning_sessions").fetchone()[0]


async def test_game_expiry_and_new_game_finalize_previous_session(games, household_db):
    context = {"user_id": "alice"}
    await command(games, "play Number Quest", context)
    await command(games, "play Word Wizard", context)
    assert household_db.execute("SELECT COUNT(*) FROM learning_sessions WHERE ended_at IS NOT NULL").fetchone()[0] == 1
    games._sessions["alice"].started_at -= MAX_GAME_SECONDS + 1
    assert "expired" in (await command(games, "next", context)).response
    assert not games._sessions


async def test_voice_games_use_spoken_counting_and_real_words(games, household_db):
    context = {"user_id": "alice"}
    await command(games, "play Number Quest", context)
    question = games._sessions["alice"].current_question
    assert question.question.isascii()
    assert question.topic == "counting"
    assert not (await games.match("what time is it", context)).matched
    await command(games, "play Word Wizard", context)
    assert games._sessions["alice"].current_question.subject == "language"


async def test_toddler_difficulty_cannot_escalate(games, household_db):
    household_db.execute(
        "INSERT INTO user_profiles(user_id,display_name,age_group,age_confidence) VALUES('kid','Kid','toddler',1)"
    )
    household_db.commit()
    await command(games, "play Number Quest", {"user_id": "kid"})
    session = games._sessions["kid"]
    assert session.difficulty == session.max_difficulty == 1
