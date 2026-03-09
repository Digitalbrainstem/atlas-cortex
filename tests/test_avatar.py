"""Tests for the avatar system (Phase C7)."""

from __future__ import annotations

from cortex.avatar import (
    EXPRESSIONS,
    VISEME_CATEGORIES,
    VISEME_MAP,
    AvatarExpression,
    AvatarState,
    VisemeFrame,
)


# ──────────────────────────────────────────────────────────────────
# Viseme mapping
# ──────────────────────────────────────────────────────────────────

class TestVisemeMap:
    def test_all_visemes_are_valid_categories(self):
        for phoneme, viseme in VISEME_MAP.items():
            assert viseme in VISEME_CATEGORIES, f"{phoneme!r} → {viseme!r} not in VISEME_CATEGORIES"

    def test_silence_maps_to_idle(self):
        assert VISEME_MAP["sil"] == "IDLE"

    def test_bilabials_map_to_pp(self):
        for ph in ("p", "b", "m"):
            assert VISEME_MAP[ph] == "PP"

    def test_labiodentals_map_to_ff(self):
        for ph in ("f", "v"):
            assert VISEME_MAP[ph] == "FF"

    def test_vowels_present(self):
        vowel_visemes = {"AA", "EH", "IH", "OH", "OU"}
        mapped_vowels = {VISEME_MAP[ph] for ph in ("a", "e", "i", "o", "u")}
        assert mapped_vowels == vowel_visemes

    def test_minimum_category_count(self):
        assert len(VISEME_CATEGORIES) >= 10, "Need at least 10 viseme categories for adequate lip-sync"

    def test_every_category_has_at_least_one_phoneme(self):
        mapped_visemes = set(VISEME_MAP.values())
        assert mapped_visemes == VISEME_CATEGORIES


# ──────────────────────────────────────────────────────────────────
# Expression presets
# ──────────────────────────────────────────────────────────────────

class TestExpressions:
    def test_neutral_exists(self):
        assert "neutral" in EXPRESSIONS

    def test_required_expressions(self):
        required = {"neutral", "happy", "thinking", "surprised", "sad", "excited", "concerned", "listening"}
        assert required.issubset(set(EXPRESSIONS.keys()))

    def test_expression_types(self):
        for name, expr in EXPRESSIONS.items():
            assert isinstance(expr, AvatarExpression)
            assert expr.name == name

    def test_neutral_is_zeroed(self):
        n = EXPRESSIONS["neutral"]
        assert n.eyebrow_raise == 0.0
        assert n.eye_squint == 0.0
        assert n.mouth_smile == 0.0
        assert n.head_tilt == 0.0

    def test_happy_smiles(self):
        assert EXPRESSIONS["happy"].mouth_smile > 0

    def test_sad_frowns(self):
        assert EXPRESSIONS["sad"].mouth_smile < 0

    def test_blink_rates_positive(self):
        for expr in EXPRESSIONS.values():
            assert expr.blink_rate > 0, f"{expr.name} has non-positive blink rate"


# ──────────────────────────────────────────────────────────────────
# text_to_visemes
# ──────────────────────────────────────────────────────────────────

class TestTextToVisemes:
    def test_simple_word(self):
        state = AvatarState()
        frames = state.text_to_visemes("hi")
        assert len(frames) > 0
        assert all(isinstance(f, VisemeFrame) for f in frames)

    def test_frames_have_increasing_start_times(self):
        state = AvatarState()
        frames = state.text_to_visemes("hello world")
        for i in range(1, len(frames)):
            assert frames[i].start_ms > frames[i - 1].start_ms

    def test_visemes_are_valid_categories(self):
        state = AvatarState()
        frames = state.text_to_visemes("the quick brown fox")
        for f in frames:
            assert f.viseme in VISEME_CATEGORIES, f"Unknown viseme {f.viseme!r}"

    def test_intensity_range(self):
        state = AvatarState()
        frames = state.text_to_visemes("hello")
        for f in frames:
            assert 0.0 <= f.intensity <= 1.0

    def test_sets_is_speaking(self):
        state = AvatarState()
        assert state.is_speaking is False
        state.text_to_visemes("speak")
        assert state.is_speaking is True

    def test_empty_string_returns_empty(self):
        state = AvatarState()
        frames = state.text_to_visemes("")
        assert frames == []

    def test_stores_in_viseme_queue(self):
        state = AvatarState()
        frames = state.text_to_visemes("test")
        assert state.viseme_queue is frames

    def test_wpm_affects_duration(self):
        state = AvatarState()
        slow = state.text_to_visemes("hi", wpm=100)
        fast = state.text_to_visemes("hi", wpm=300)
        assert slow[0].duration_ms > fast[0].duration_ms

    def test_digraph_th(self):
        state = AvatarState()
        frames = state.text_to_visemes("the")
        visemes = [f.viseme for f in frames]
        assert "TH" in visemes

    def test_digraph_sh(self):
        state = AvatarState()
        frames = state.text_to_visemes("she")
        visemes = [f.viseme for f in frames]
        assert "SH" in visemes

    def test_space_produces_idle(self):
        state = AvatarState()
        frames = state.text_to_visemes("a b")
        visemes = [f.viseme for f in frames]
        assert "IDLE" in visemes

    def test_vowel_intensity_higher_than_consonant(self):
        state = AvatarState()
        frames = state.text_to_visemes("ab")
        vowel_frame = next(f for f in frames if f.viseme in {"AA", "EH", "IH", "OH", "OU"})
        consonant_frame = next(f for f in frames if f.viseme not in {"AA", "EH", "IH", "OH", "OU", "IDLE"})
        assert vowel_frame.intensity > consonant_frame.intensity


# ──────────────────────────────────────────────────────────────────
# Expression from sentiment
# ──────────────────────────────────────────────────────────────────

class TestExpressionFromSentiment:
    def test_greeting_maps_to_happy(self):
        state = AvatarState()
        expr = state.expression_from_sentiment("greeting", 1.0)
        assert expr.name == "happy"

    def test_frustrated_maps_to_concerned(self):
        state = AvatarState()
        expr = state.expression_from_sentiment("frustrated", 1.0)
        assert expr.name == "concerned"

    def test_question_maps_to_thinking(self):
        state = AvatarState()
        expr = state.expression_from_sentiment("question", 1.0)
        assert expr.name == "thinking"

    def test_unknown_sentiment_maps_to_neutral(self):
        state = AvatarState()
        expr = state.expression_from_sentiment("nonexistent_sentiment", 1.0)
        assert expr.name == "neutral"

    def test_confidence_scales_expression(self):
        state = AvatarState()
        full = state.expression_from_sentiment("greeting", 1.0)
        half = state.expression_from_sentiment("greeting", 0.5)
        assert abs(half.mouth_smile) < abs(full.mouth_smile)

    def test_zero_confidence_returns_neutral_values(self):
        state = AvatarState()
        expr = state.expression_from_sentiment("excited", 0.0)
        assert expr.eyebrow_raise == 0.0
        assert expr.mouth_smile == 0.0

    def test_sets_state_expression(self):
        state = AvatarState()
        expr = state.expression_from_sentiment("positive", 1.0)
        assert state.expression is expr


# ──────────────────────────────────────────────────────────────────
# AvatarState
# ──────────────────────────────────────────────────────────────────

class TestAvatarState:
    def test_default_state(self):
        state = AvatarState()
        assert state.expression.name == "neutral"
        assert state.viseme_queue == []
        assert state.is_speaking is False
        assert state.is_listening is False

    def test_set_expression(self):
        state = AvatarState()
        state.set_expression("happy")
        assert state.expression.name == "happy"

    def test_set_expression_unknown_falls_back(self):
        state = AvatarState()
        state.set_expression("nonexistent")
        assert state.expression.name == "neutral"


# ──────────────────────────────────────────────────────────────────
# JSON serialisation
# ──────────────────────────────────────────────────────────────────

class TestToJson:
    def test_json_has_required_keys(self):
        state = AvatarState()
        data = state.to_json()
        assert "expression" in data
        assert "viseme_queue" in data
        assert "is_speaking" in data
        assert "is_listening" in data

    def test_expression_fields_in_json(self):
        state = AvatarState()
        expr = state.to_json()["expression"]
        for key in ("name", "eyebrow_raise", "eye_squint", "mouth_smile", "head_tilt", "blink_rate"):
            assert key in expr, f"Missing expression key {key!r}"

    def test_viseme_queue_serialises(self):
        state = AvatarState()
        state.text_to_visemes("hi")
        data = state.to_json()
        assert len(data["viseme_queue"]) > 0
        frame = data["viseme_queue"][0]
        for key in ("viseme", "start_ms", "duration_ms", "intensity"):
            assert key in frame

    def test_json_values_are_serialisable(self):
        """Ensure the dict contains only JSON-safe types."""
        import json
        state = AvatarState()
        state.text_to_visemes("hello world")
        state.set_expression("happy")
        # Should not raise
        result = json.dumps(state.to_json())
        assert isinstance(result, str)

    def test_json_reflects_state_changes(self):
        state = AvatarState()
        state.set_expression("thinking")
        state.is_listening = True
        data = state.to_json()
        assert data["expression"]["name"] == "thinking"
        assert data["is_listening"] is True


# ──────────────────────────────────────────────────────────────────
# Database schema
# ──────────────────────────────────────────────────────────────────

class TestAvatarDB:
    def _setup_db(self):
        from cortex.db import set_db_path, init_db, get_db
        set_db_path(":memory:")
        init_db()
        return get_db()

    def test_avatar_skins_table_exists(self):
        conn = self._setup_db()
        cols = [r[1] for r in conn.execute("PRAGMA table_info(avatar_skins)").fetchall()]
        assert "id" in cols
        assert "name" in cols
        assert "metadata" in cols
        assert "is_default" in cols

    def test_avatar_assignments_table_exists(self):
        conn = self._setup_db()
        cols = [r[1] for r in conn.execute("PRAGMA table_info(avatar_assignments)").fetchall()]
        assert "user_id" in cols
        assert "skin_id" in cols

    def test_default_skin_seeded(self):
        conn = self._setup_db()
        row = conn.execute("SELECT id, name, is_default FROM avatar_skins WHERE id = 'default'").fetchone()
        assert row is not None
        assert row[0] == "default"
        assert row[1] == "Atlas Default"
        assert row[2] == 1

    def test_assignment_references_skin(self):
        conn = self._setup_db()
        conn.execute("INSERT INTO avatar_assignments (user_id, skin_id) VALUES ('u1', 'default')")
        conn.commit()
        row = conn.execute("SELECT user_id, skin_id FROM avatar_assignments WHERE user_id = 'u1'").fetchone()
        assert row[0] == "u1"
        assert row[1] == "default"

    def test_assignment_cascade_delete(self):
        conn = self._setup_db()
        conn.execute("INSERT INTO avatar_skins (id, name, type, path) VALUES ('test', 'Test', 'svg', 'test.svg')")
        conn.execute("INSERT INTO avatar_assignments (user_id, skin_id) VALUES ('u1', 'test')")
        conn.commit()
        conn.execute("DELETE FROM avatar_skins WHERE id = 'test'")
        conn.commit()
        row = conn.execute("SELECT * FROM avatar_assignments WHERE user_id = 'u1'").fetchone()
        assert row is None

    def test_metadata_column_stores_json(self):
        import json
        conn = self._setup_db()
        meta = json.dumps({"hair_color": "brown", "hat": True})
        conn.execute(
            "INSERT INTO avatar_skins (id, name, type, path, metadata) VALUES ('custom', 'Custom', 'svg', 'c.svg', ?)",
            (meta,),
        )
        conn.commit()
        row = conn.execute("SELECT metadata FROM avatar_skins WHERE id = 'custom'").fetchone()
        parsed = json.loads(row[0])
        assert parsed["hair_color"] == "brown"
        assert parsed["hat"] is True


# ──────────────────────────────────────────────────────────────────
# Skin resolution
# ──────────────────────────────────────────────────────────────────

class TestSkinResolution:
    def _setup_db(self):
        from cortex.db import set_db_path, init_db, get_db
        set_db_path(":memory:")
        init_db()
        return get_db()

    def test_default_skin_resolves(self):
        self._setup_db()
        from cortex.avatar.websocket import _resolve_skin_for_room
        skin = _resolve_skin_for_room("kitchen")
        assert skin["id"] == "default"

    def test_user_assignment_resolves(self):
        conn = self._setup_db()
        conn.execute("INSERT INTO avatar_skins (id, name, type, path) VALUES ('robot', 'Robot', 'svg', 'r.svg')")
        conn.execute("INSERT INTO avatar_assignments (user_id, skin_id) VALUES ('kid1', 'robot')")
        conn.commit()
        from cortex.avatar.websocket import _resolve_skin_for_room
        skin = _resolve_skin_for_room("kitchen", "kid1")
        assert skin["id"] == "robot"

    def test_unknown_user_falls_to_default(self):
        self._setup_db()
        from cortex.avatar.websocket import _resolve_skin_for_room
        skin = _resolve_skin_for_room("kitchen", "unknown_user")
        assert skin["id"] == "default"


# ──────────────────────────────────────────────────────────────────
# WebSocket message format
# ──────────────────────────────────────────────────────────────────

class TestAvatarWebSocketMessages:
    def test_broadcast_expression_format(self):
        """Verify the expression broadcast message structure."""
        import asyncio
        from cortex.avatar.websocket import broadcast_to_room

        async def run():
            await broadcast_to_room("test_room", {
                "type": "EXPRESSION",
                "expression": "happy",
                "intensity": 0.8,
            })

        asyncio.run(run())
        # Passes if no exception (no connected clients = no-op)

    def test_viseme_sequence_empty(self):
        """Empty sequence should not raise."""
        import asyncio
        from cortex.avatar.websocket import broadcast_viseme_sequence

        asyncio.run(broadcast_viseme_sequence("test_room", []))

    def test_connected_rooms_empty(self):
        from cortex.avatar.websocket import get_connected_rooms
        rooms = get_connected_rooms()
        assert isinstance(rooms, list)
