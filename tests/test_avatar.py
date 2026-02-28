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
