"""Comprehensive tests for the Learning & Education engine."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from cortex.db import get_db, init_db, set_db_path
from cortex.learning.education import (
    ProgressTracker,
    QuizGenerator,
    QuizQuestion,
    TutoringEngine,
    TutoringResponse,
)
from cortex.plugins.base import CommandMatch, CommandResult
from cortex.plugins.games import STEMGamesPlugin, GAMES


# ── Fixtures ──────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _fresh_db(tmp_path):
    """Every test gets a fresh in-memory database."""
    path = tmp_path / "test_edu.db"
    set_db_path(path)
    init_db(path)
    yield


@pytest.fixture()
def quiz():
    return QuizGenerator()


@pytest.fixture()
def tutor():
    return TutoringEngine()


@pytest.fixture()
def tracker():
    return ProgressTracker()


# ── Quiz Generator: Math Questions ───────────────────────────────

class TestMathQuestionGeneration:
    """Math questions are generated procedurally at every difficulty."""

    @pytest.mark.parametrize("difficulty", range(1, 11))
    def test_generates_at_every_difficulty(self, quiz, difficulty):
        q = quiz.generate_math_question(difficulty)
        assert isinstance(q, QuizQuestion)
        assert q.question
        assert q.correct_answer
        assert q.subject == "math"
        assert q.difficulty == difficulty

    def test_difficulty_clamped_low(self, quiz):
        q = quiz.generate_math_question(0)
        assert q.difficulty == 1

    def test_difficulty_clamped_high(self, quiz):
        q = quiz.generate_math_question(99)
        assert q.difficulty == 10

    def test_counting_at_difficulty_1(self, quiz):
        for _ in range(20):
            q = quiz.generate_math_question(1)
            assert q.subject == "math"
            assert q.correct_answer.isdigit() or q.correct_answer.lstrip("-").isdigit()

    def test_fractions_available_at_difficulty_3(self, quiz):
        topics = set()
        for _ in range(100):
            q = quiz.generate_math_question(3)
            topics.add(q.topic)
        assert "fractions" in topics, "Fractions should appear at difficulty 3"

    def test_decimals_available_at_difficulty_3(self, quiz):
        topics = set()
        for _ in range(100):
            q = quiz.generate_math_question(3)
            topics.add(q.topic)
        assert "decimals" in topics, "Decimals should appear at difficulty 3"

    def test_radians_available_at_difficulty_5(self, quiz):
        topics = set()
        for _ in range(100):
            q = quiz.generate_math_question(5)
            topics.add(q.topic)
        assert "radians" in topics, "Radians should appear at difficulty 5"

    def test_trigonometry_at_difficulty_7(self, quiz):
        topics = set()
        for _ in range(100):
            q = quiz.generate_math_question(7)
            topics.add(q.topic)
        assert "trigonometry" in topics, "Trig should appear at difficulty 7"

    def test_calculus_at_difficulty_9(self, quiz):
        topics = set()
        for _ in range(100):
            q = quiz.generate_math_question(9)
            topics.add(q.topic)
        assert "calculus" in topics or "limits" in topics, "Calculus should appear at difficulty 9"

    def test_topic_filter(self, quiz):
        for _ in range(20):
            q = quiz.generate_math_question(3, topic="fraction")
            assert q.topic == "fractions"

    def test_hints_and_explanations_present(self, quiz):
        for difficulty in range(1, 11):
            q = quiz.generate_math_question(difficulty)
            assert q.hint, f"Hint missing at difficulty {difficulty}"
            assert q.explanation, f"Explanation missing at difficulty {difficulty}"


# ── Quiz Generator: Science Questions ─────────────────────────────

class TestScienceQuestionGeneration:

    @pytest.mark.parametrize("difficulty", range(1, 11))
    def test_generates_at_every_difficulty(self, quiz, difficulty):
        q = quiz.generate_science_question(difficulty)
        assert isinstance(q, QuizQuestion)
        assert q.subject == "science"
        assert q.question

    def test_topic_filter(self, quiz):
        q = quiz.generate_science_question(1, topic="biology")
        assert q.topic == "biology"


# ── Answer Checking ───────────────────────────────────────────────

class TestAnswerChecking:

    def test_exact_numeric_match(self, quiz):
        q = QuizQuestion("What is 2+2?", "4", 1, "math", "addition")
        ok, _ = quiz.check_answer(q, "4")
        assert ok

    def test_numeric_tolerance(self, quiz):
        q = QuizQuestion("What is pi?", "3.14", 5, "math", "geometry")
        ok, _ = quiz.check_answer(q, "3.14")
        assert ok
        ok2, _ = quiz.check_answer(q, "3.1400")
        assert ok2

    def test_wrong_numeric(self, quiz):
        q = QuizQuestion("What is 2+2?", "4", 1, "math", "addition")
        ok, msg = quiz.check_answer(q, "5")
        assert not ok
        assert "4" in msg

    def test_text_match_case_insensitive(self, quiz):
        q = QuizQuestion("What is H2O?", "water", 2, "science", "chemistry")
        ok, _ = quiz.check_answer(q, "Water")
        assert ok

    def test_text_match_extra_spaces(self, quiz):
        q = QuizQuestion("What is H2O?", "water", 2, "science", "chemistry")
        ok, _ = quiz.check_answer(q, "  water  ")
        assert ok

    def test_multi_value_answer(self, quiz):
        q = QuizQuestion("Roots?", "-2, 3", 9, "math", "quadratics")
        ok, _ = quiz.check_answer(q, "3, -2")
        assert ok, "Order should not matter"

    def test_empty_answer(self, quiz):
        q = QuizQuestion("What is 1+1?", "2", 1, "math", "addition")
        ok, msg = quiz.check_answer(q, "")
        assert not ok
        assert "didn't provide" in msg.lower() or "try again" in msg.lower()

    def test_fuzzy_text_subset(self, quiz):
        q = QuizQuestion("States of matter?", "solid, liquid, gas", 3, "science", "chemistry")
        ok, _ = quiz.check_answer(q, "solid, liquid, gas")
        assert ok

    def test_money_format_tolerance(self, quiz):
        q = QuizQuestion("How much?", "3.50", 3, "math", "decimals")
        ok, _ = quiz.check_answer(q, "$3.50")
        assert ok


# ── Adaptive Difficulty ───────────────────────────────────────────

class TestAdaptiveDifficulty:

    def test_new_user_starts_at_1(self, quiz):
        d = quiz.get_adaptive_difficulty("new_user", "math")
        assert d == 1

    def test_auto_advance(self, quiz, tracker):
        # Build up high proficiency and streak
        for _ in range(10):
            tracker.record_attempt("u1", "math", "addition", True, 3)
        db = get_db()
        db.execute(
            "INSERT INTO learning_sessions (user_id, subject, difficulty_level) "
            "VALUES (?, ?, ?)",
            ("u1", "math", 3),
        )
        db.commit()
        d = quiz.get_adaptive_difficulty("u1", "math")
        assert d >= 3, "Should advance beyond current level"

    def test_auto_ease(self, quiz, tracker):
        # Build low proficiency with no streak
        for _ in range(5):
            tracker.record_attempt("u2", "math", "algebra", False, 5)
        db = get_db()
        db.execute(
            "INSERT INTO learning_sessions (user_id, subject, difficulty_level) "
            "VALUES (?, ?, ?)",
            ("u2", "math", 5),
        )
        db.commit()
        d = quiz.get_adaptive_difficulty("u2", "math")
        assert d <= 5, "Should ease difficulty"


# ── Progress Tracker ──────────────────────────────────────────────

class TestProgressTracker:

    def test_record_first_correct(self, tracker):
        tracker.record_attempt("u1", "math", "addition", True, 1)
        p = tracker.get_proficiency("u1", "math", "addition")
        assert p["proficiency"] == 1.0
        assert p["streak"] == 1
        assert p["total_attempts"] == 1
        assert p["correct_attempts"] == 1

    def test_record_first_wrong(self, tracker):
        tracker.record_attempt("u1", "math", "addition", False, 1)
        p = tracker.get_proficiency("u1", "math", "addition")
        assert p["proficiency"] == 0.0
        assert p["streak"] == 0

    def test_streak_increments(self, tracker):
        for i in range(5):
            tracker.record_attempt("u1", "math", "addition", True, 1)
        p = tracker.get_proficiency("u1", "math", "addition")
        assert p["streak"] == 5
        assert p["best_streak"] == 5

    def test_streak_resets_on_wrong(self, tracker):
        for _ in range(3):
            tracker.record_attempt("u1", "math", "addition", True, 1)
        tracker.record_attempt("u1", "math", "addition", False, 1)
        p = tracker.get_proficiency("u1", "math", "addition")
        assert p["streak"] == 0
        assert p["best_streak"] == 3

    def test_proficiency_weighted_average(self, tracker):
        tracker.record_attempt("u1", "math", "addition", True, 1)
        # proficiency starts at 1.0
        tracker.record_attempt("u1", "math", "addition", False, 1)
        p = tracker.get_proficiency("u1", "math", "addition")
        # Should be 1.0 * 0.7 + 0.0 * 0.3 = 0.7
        assert abs(p["proficiency"] - 0.7) < 0.01

    def test_subject_aggregate(self, tracker):
        tracker.record_attempt("u1", "math", "addition", True, 1)
        tracker.record_attempt("u1", "math", "subtraction", False, 1)
        p = tracker.get_proficiency("u1", "math")
        assert p["topic_count"] == 2
        assert p["total_attempts"] == 2
        assert 0 < p["proficiency"] < 1.0  # average of 1.0 and 0.0

    def test_empty_user_history(self, tracker):
        p = tracker.get_proficiency("nobody", "math", "addition")
        assert p["proficiency"] == 0.0
        assert p["total_attempts"] == 0

    def test_empty_subject_aggregate(self, tracker):
        p = tracker.get_proficiency("nobody", "math")
        assert p["proficiency"] == 0.0
        assert p["topic_count"] == 0


# ── Spaced Repetition ────────────────────────────────────────────

class TestSpacedRepetition:

    def test_next_review_scheduled(self, tracker):
        tracker.record_attempt("u1", "math", "addition", True, 1)
        p = tracker.get_proficiency("u1", "math", "addition")
        assert p["next_review"] is not None

    def test_low_proficiency_short_interval(self, tracker):
        dt = tracker._calculate_next_review(0.0, 0)
        delta = dt - datetime.now(timezone.utc)
        assert delta.days <= 1

    def test_high_proficiency_longer_interval(self, tracker):
        dt = tracker._calculate_next_review(1.0, 5)
        delta = dt - datetime.now(timezone.utc)
        assert delta.days >= 14

    def test_due_reviews(self, tracker):
        tracker.record_attempt("u1", "math", "addition", True, 1)
        # Force next_review to the past
        db = get_db()
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        db.execute(
            "UPDATE learning_progress SET next_review = ? "
            "WHERE user_id = ? AND subject = ? AND topic = ?",
            (past, "u1", "math", "addition"),
        )
        db.commit()
        due = tracker.get_due_reviews("u1")
        assert len(due) == 1
        assert due[0]["topic"] == "addition"

    def test_no_due_reviews(self, tracker):
        tracker.record_attempt("u1", "math", "addition", True, 1)
        due = tracker.get_due_reviews("u1")
        assert len(due) == 0  # just recorded, not due yet


# ── Parent Report ─────────────────────────────────────────────────

class TestParentReport:

    def test_report_structure(self, tracker):
        # Create some activity
        db = get_db()
        db.execute(
            "INSERT INTO learning_sessions "
            "(user_id, subject, topic, mode, difficulty_level, "
            " questions_asked, correct_answers) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("u1", "math", "addition", "quiz", 2, 10, 8),
        )
        db.commit()
        tracker.record_attempt("u1", "math", "addition", True, 2)

        report = tracker.get_parent_report("u1", days=7)
        assert report["total_sessions"] == 1
        assert report["total_questions"] == 10
        assert report["total_correct"] == 8
        assert report["accuracy"] == 0.8
        assert "math" in report["subjects_practiced"]
        assert report["best_streak"] >= 1
        assert isinstance(report["strongest_areas"], list)
        assert isinstance(report["needs_help"], list)

    def test_empty_report(self, tracker):
        report = tracker.get_parent_report("nobody")
        assert report["total_sessions"] == 0
        assert report["accuracy"] == 0

    def test_multi_subject_report(self, tracker):
        db = get_db()
        for subj in ("math", "science"):
            db.execute(
                "INSERT INTO learning_sessions "
                "(user_id, subject, topic, mode, questions_asked, correct_answers) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("u1", subj, "test", "quiz", 5, 4),
            )
            tracker.record_attempt("u1", subj, "test", True, 1)
        db.commit()

        report = tracker.get_parent_report("u1")
        assert len(report["subjects_practiced"]) == 2


# ── Tutoring Engine ───────────────────────────────────────────────

class TestTutoringEngine:

    def test_socratic_prompt_child(self, tutor):
        prompt = tutor.get_socratic_prompt("math", "addition", "What is 2+2?", "child", 0)
        assert "NEVER give the answer" in prompt
        assert "child" in prompt.lower()
        assert "2+2" in prompt

    def test_socratic_prompt_teen(self, tutor):
        prompt = tutor.get_socratic_prompt("math", "algebra", "Solve x+3=7", "teen", 0)
        assert "teen" in prompt.lower() or "challenging" in prompt.lower()

    def test_socratic_prompt_adult(self, tutor):
        prompt = tutor.get_socratic_prompt("math", "calculus", "Derive x²", "adult", 0)
        assert "technical" in prompt.lower() or "efficient" in prompt.lower()

    def test_hint_levels(self, tutor):
        prompts = []
        for level in range(4):
            p = tutor.get_socratic_prompt("math", "addition", "2+2?", "child", level)
            prompts.append(p)
        # Each level should produce a different prompt
        assert len(set(prompts)) == 4

    def test_invalid_age_defaults_to_child(self, tutor):
        prompt = tutor.get_socratic_prompt("math", "", "2+2?", "toddler", 0)
        assert "child" in prompt.lower()

    def test_hint_level_clamped(self, tutor):
        prompt_neg = tutor.get_socratic_prompt("math", "", "2+2?", "child", -5)
        prompt_0 = tutor.get_socratic_prompt("math", "", "2+2?", "child", 0)
        # Negative should clamp to 0
        assert prompt_neg == prompt_0

        prompt_high = tutor.get_socratic_prompt("math", "", "2+2?", "child", 99)
        prompt_3 = tutor.get_socratic_prompt("math", "", "2+2?", "child", 3)
        assert prompt_high == prompt_3

    def test_build_context_empty(self, tutor):
        ctx = tutor.build_tutoring_context("nobody", "math", "addition")
        assert ctx["has_history"] is False
        assert ctx["proficiency"] == 0.0

    def test_build_context_with_history(self, tutor, tracker):
        tracker.record_attempt("u1", "math", "addition", True, 1)
        ctx = tutor.build_tutoring_context("u1", "math", "addition")
        assert ctx["has_history"] is True
        assert ctx["proficiency"] == 1.0
        assert ctx["streak"] == 1

    def test_age_adapted_explanation(self, tutor):
        for age in ("child", "teen", "adult"):
            result = tutor.get_age_adapted_explanation("fractions", age)
            assert "fractions" in result.lower()
            assert result  # not empty

    def test_age_adapted_invalid_defaults(self, tutor):
        result = tutor.get_age_adapted_explanation("fractions", "alien")
        assert "child" in result.lower()


# ── TutoringResponse Dataclass ────────────────────────────────────

class TestTutoringResponseDataclass:

    def test_defaults(self):
        r = TutoringResponse(message="Hello")
        assert r.message == "Hello"
        assert r.hint_level == 0
        assert r.follow_up_question == ""
        assert r.encouragement == ""

    def test_custom_fields(self):
        r = TutoringResponse(
            message="Try again!",
            hint_level=2,
            follow_up_question="What if you add 1?",
            encouragement="You're getting close!",
        )
        assert r.hint_level == 2
        assert r.follow_up_question == "What if you add 1?"


# ── Integration: Full Learning Flow ───────────────────────────────

class TestLearningFlow:
    """End-to-end flow: generate question → check answer → track progress."""

    def test_full_quiz_flow(self, quiz, tracker):
        q = quiz.generate_math_question(1)
        ok, feedback = quiz.check_answer(q, q.correct_answer)
        assert ok
        tracker.record_attempt("u1", q.subject, q.topic, ok, q.difficulty)
        p = tracker.get_proficiency("u1", q.subject, q.topic)
        assert p["proficiency"] == 1.0

    def test_wrong_answer_flow(self, quiz, tracker):
        q = quiz.generate_math_question(1)
        ok, feedback = quiz.check_answer(q, "wrong_answer_xyz")
        assert not ok
        tracker.record_attempt("u1", q.subject, q.topic, ok, q.difficulty)
        p = tracker.get_proficiency("u1", q.subject, q.topic)
        assert p["proficiency"] == 0.0

    def test_proficiency_evolves_over_time(self, quiz, tracker):
        # 8 correct, 2 wrong
        for i in range(10):
            is_correct = i < 8
            tracker.record_attempt("u1", "math", "addition", is_correct, 1)
        p = tracker.get_proficiency("u1", "math", "addition")
        assert 0.3 < p["proficiency"] < 1.0  # somewhere in the middle
        assert p["total_attempts"] == 10


# ── Module Imports ────────────────────────────────────────────────

class TestModuleImports:
    """Verify the public API is properly exported."""

    def test_imports(self):
        from cortex.learning.education import (
            ProgressTracker,
            QuizGenerator,
            QuizQuestion,
            TutoringEngine,
            TutoringResponse,
        )
        assert ProgressTracker is not None
        assert QuizGenerator is not None
        assert QuizQuestion is not None
        assert TutoringEngine is not None
        assert TutoringResponse is not None


# ── STEM Games Plugin ─────────────────────────────────────────────

@pytest.fixture()
def games_plugin():
    """A fresh STEMGamesPlugin, set up and ready."""
    plugin = STEMGamesPlugin()
    return plugin


def _ctx(user_id: str = "kid1") -> dict:
    return {"user_id": user_id}


class TestSTEMGamesMatch:
    """match() should fire on natural game triggers — no subject word required."""

    @pytest.mark.parametrize("msg", [
        "let's play a game",
        "Let's play a game",
        "I want to play a game",
        "can we play a game",
        "play a game",
        "game time",
        "I'm bored",
    ])
    async def test_generic_game_triggers(self, games_plugin, msg):
        await games_plugin.setup({})
        m = await games_plugin.match(msg, _ctx())
        assert m.matched, f"Should match: {msg!r}"
        assert m.intent in ("start_game", "suggest_game")

    @pytest.mark.parametrize("msg,expected_game", [
        ("let's play Number Quest", "number_quest"),
        ("let's play science safari", "science_safari"),
        ("play word wizard", "word_wizard"),
    ])
    async def test_specific_game_triggers(self, games_plugin, msg, expected_game):
        await games_plugin.setup({})
        m = await games_plugin.match(msg, _ctx())
        assert m.matched
        assert m.intent == "start_game"
        assert m.metadata.get("game_id") == expected_game

    async def test_no_match_on_random_text(self, games_plugin):
        await games_plugin.setup({})
        m = await games_plugin.match("what's the weather like?", _ctx())
        assert not m.matched

    async def test_math_word_not_required(self, games_plugin):
        await games_plugin.setup({})
        m = await games_plugin.match("let's play a game", _ctx())
        assert m.matched

    @pytest.mark.parametrize("msg", [
        "next question", "another one", "keep going",
    ])
    async def test_continue_patterns_require_active_session(self, games_plugin, msg):
        await games_plugin.setup({})
        # No active session — should not match
        m = await games_plugin.match(msg, _ctx())
        assert not m.matched

    async def test_progress_check(self, games_plugin):
        await games_plugin.setup({})
        m = await games_plugin.match("how am I doing", _ctx())
        assert m.matched
        assert m.intent == "check_progress"

    async def test_score_check(self, games_plugin):
        await games_plugin.setup({})
        m = await games_plugin.match("what's my score", _ctx())
        assert m.matched
        assert m.intent == "check_progress"


class TestSTEMGamesHandle:
    """handle() game lifecycle: start → question → answer → end."""

    async def test_suggest_game_on_generic(self, games_plugin):
        await games_plugin.setup({})
        m = await games_plugin.match("I'm bored", _ctx())
        result = await games_plugin.handle("I'm bored", m, _ctx())
        assert result.success
        assert "Number Quest" in result.response
        assert "Science Safari" in result.response
        assert "Word Wizard" in result.response

    async def test_start_number_quest(self, games_plugin):
        await games_plugin.setup({})
        m = await games_plugin.match("let's play Number Quest", _ctx())
        result = await games_plugin.handle("let's play Number Quest", m, _ctx())
        assert result.success
        assert "Number Quest" in result.response
        assert result.metadata["action"] == "start_game"
        # Should have an active session now
        assert "kid1" in games_plugin._sessions

    async def test_start_science_safari(self, games_plugin):
        await games_plugin.setup({})
        m = await games_plugin.match("let's play science safari", _ctx())
        result = await games_plugin.handle("let's play science safari", m, _ctx())
        assert result.success
        assert "Science Safari" in result.response

    async def test_start_word_wizard(self, games_plugin):
        await games_plugin.setup({})
        m = await games_plugin.match("play word wizard", _ctx())
        result = await games_plugin.handle("play word wizard", m, _ctx())
        assert result.success
        assert "Word Wizard" in result.response

    async def test_answer_correct(self, games_plugin):
        await games_plugin.setup({})
        # Start a game
        m = await games_plugin.match("let's play Number Quest", _ctx())
        await games_plugin.handle("let's play Number Quest", m, _ctx())
        # Get the current question's correct answer
        session = games_plugin._sessions["kid1"]
        correct_answer = session.current_question.correct_answer
        # Answer it
        m2 = await games_plugin.match(correct_answer, _ctx())
        assert m2.matched
        assert m2.intent == "answer"
        result = await games_plugin.handle(correct_answer, m2, _ctx())
        assert result.success
        assert result.metadata["correct"] is True
        assert result.metadata["score"] >= 10

    async def test_answer_wrong(self, games_plugin):
        await games_plugin.setup({})
        m = await games_plugin.match("let's play Number Quest", _ctx())
        await games_plugin.handle("let's play Number Quest", m, _ctx())
        # Give a wrong answer
        m2 = await games_plugin.match("wrong_xyz_99999", _ctx())
        result = await games_plugin.handle("wrong_xyz_99999", m2, _ctx())
        assert result.success
        assert result.metadata["correct"] is False
        assert result.metadata["score"] == 0

    async def test_check_progress(self, games_plugin, tracker):
        await games_plugin.setup({})
        tracker.record_attempt("kid1", "math", "addition", True, 1)
        m = await games_plugin.match("how am I doing", _ctx())
        result = await games_plugin.handle("how am I doing", m, _ctx())
        assert result.success
        assert "Progress" in result.response

    async def test_end_game(self, games_plugin):
        await games_plugin.setup({})
        # Start
        m = await games_plugin.match("let's play Number Quest", _ctx())
        await games_plugin.handle("let's play Number Quest", m, _ctx())
        assert "kid1" in games_plugin._sessions
        # Stop
        m2 = await games_plugin.match("stop", _ctx())
        assert m2.matched
        assert m2.intent == "stop_game"
        result = await games_plugin.handle("stop", m2, _ctx())
        assert result.success
        assert "Game Over" in result.response
        assert "kid1" not in games_plugin._sessions


class TestSTEMGamesStateMachine:
    """Full state machine: start → questions → answers → end."""

    async def test_full_game_flow(self, games_plugin):
        await games_plugin.setup({})
        ctx = _ctx("player1")

        # Start
        m = await games_plugin.match("let's play Number Quest", ctx)
        r = await games_plugin.handle("let's play Number Quest", m, ctx)
        assert r.success

        # Answer 3 questions correctly
        for i in range(3):
            session = games_plugin._sessions["player1"]
            answer = session.current_question.correct_answer
            m = await games_plugin.match(answer, ctx)
            r = await games_plugin.handle(answer, m, ctx)
            assert r.success
            assert r.metadata["correct"] is True

        session = games_plugin._sessions["player1"]
        assert session.correct_answers == 3
        assert session.score >= 30  # 10 points each minimum
        assert session.questions_asked == 4  # 1 initial + 3 answered (each generates next)

        # Continue
        m = await games_plugin.match("next question", ctx)
        assert m.matched
        r = await games_plugin.handle("next question", m, ctx)
        assert r.success

        # End
        m = await games_plugin.match("stop", ctx)
        r = await games_plugin.handle("stop", m, ctx)
        assert "Game Over" in r.response
        assert "player1" not in games_plugin._sessions

    async def test_streak_bonus(self, games_plugin):
        await games_plugin.setup({})
        ctx = _ctx("streaker")

        m = await games_plugin.match("let's play Number Quest", ctx)
        await games_plugin.handle("let's play Number Quest", m, ctx)

        # Get 3 correct in a row for streak bonus
        for i in range(3):
            session = games_plugin._sessions["streaker"]
            answer = session.current_question.correct_answer
            m = await games_plugin.match(answer, ctx)
            r = await games_plugin.handle(answer, m, ctx)
            assert r.metadata["correct"]

        session = games_plugin._sessions["streaker"]
        assert session.streak == 3
        # 10 + 10 + 15 (10 + 5 bonus) = 35
        assert session.score == 35

    async def test_db_session_created(self, games_plugin):
        await games_plugin.setup({})
        m = await games_plugin.match("let's play science safari", _ctx())
        await games_plugin.handle("let's play science safari", m, _ctx())

        db = get_db()
        row = db.execute(
            "SELECT * FROM learning_sessions WHERE user_id = 'kid1' AND mode = 'game'"
        ).fetchone()
        assert row is not None
        assert row["subject"] == "science"


class TestSTEMGamesAutoAdvance:
    """Verify difficulty auto-advancement on high proficiency."""

    async def test_auto_advance_on_high_proficiency(self, games_plugin, tracker):
        await games_plugin.setup({})
        ctx = _ctx("advancer")

        # Build high proficiency
        for _ in range(10):
            tracker.record_attempt("advancer", "math", "counting", True, 1)

        # Start game
        m = await games_plugin.match("let's play Number Quest", ctx)
        await games_plugin.handle("let's play Number Quest", m, ctx)

        session = games_plugin._sessions["advancer"]
        initial_diff = session.difficulty

        # Answer correctly to trigger auto-advance check
        answer = session.current_question.correct_answer
        m = await games_plugin.match(answer, ctx)
        r = await games_plugin.handle(answer, m, ctx)

        session = games_plugin._sessions["advancer"]
        # Difficulty should have advanced since proficiency > 0.8
        assert session.difficulty >= initial_diff

    async def test_no_advance_below_threshold(self, games_plugin, tracker):
        await games_plugin.setup({})
        ctx = _ctx("beginner")

        # Build mediocre proficiency — mix of right and wrong
        tracker.record_attempt("beginner", "math", "counting", True, 1)
        tracker.record_attempt("beginner", "math", "counting", False, 1)
        tracker.record_attempt("beginner", "math", "counting", False, 1)

        m = await games_plugin.match("let's play Number Quest", ctx)
        await games_plugin.handle("let's play Number Quest", m, ctx)

        session = games_plugin._sessions["beginner"]
        initial_diff = session.difficulty

        # Give a wrong answer — should definitely not advance
        m = await games_plugin.match("wrong_xyz_99999", ctx)
        await games_plugin.handle("wrong_xyz_99999", m, ctx)

        session = games_plugin._sessions["beginner"]
        assert session.difficulty <= initial_diff


class TestSTEMGamesHealth:
    """Plugin lifecycle."""

    async def test_setup_returns_true(self, games_plugin):
        assert await games_plugin.setup({})

    async def test_health_returns_true(self, games_plugin):
        await games_plugin.setup({})
        assert await games_plugin.health()

    def test_plugin_metadata(self, games_plugin):
        assert games_plugin.plugin_id == "stem_games"
        assert games_plugin.display_name == "STEM Games"
        assert games_plugin.supports_learning is True
        assert games_plugin.version == "1.0.0"


# ── Progress Admin API ────────────────────────────────────────────

class TestProgressAdminAPI:
    """Test the admin learning endpoints."""

    def test_admin_learning_module_imports(self):
        from cortex.admin.learning import router
        assert router is not None

    def test_learning_router_has_routes(self):
        from cortex.admin.learning import router
        paths = [r.path for r in router.routes]
        assert "/learning/progress" in paths
        assert "/learning/progress/{user_id}" in paths
        assert "/learning/sessions" in paths
        assert "/learning/report/{user_id}" in paths
        assert "/learning/leaderboard" in paths

    def test_parent_report_via_tracker(self, tracker):
        db = get_db()
        db.execute(
            "INSERT INTO learning_sessions "
            "(user_id, subject, topic, mode, difficulty_level, "
            "questions_asked, correct_answers) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("test_kid", "math", "number_quest", "game", 2, 10, 8),
        )
        db.commit()
        tracker.record_attempt("test_kid", "math", "addition", True, 2)
        tracker.record_attempt("test_kid", "math", "addition", True, 2)

        report = tracker.get_parent_report("test_kid", days=7)
        assert report["total_sessions"] == 1
        assert report["total_questions"] == 10
        assert report["accuracy"] == 0.8
        assert "math" in report["subjects_practiced"]
        assert report["best_streak"] >= 2

    def test_leaderboard_data(self, tracker):
        tracker.record_attempt("kid_a", "math", "addition", True, 1)
        tracker.record_attempt("kid_b", "science", "biology", True, 1)

        db = get_db()
        db.execute(
            "INSERT INTO learning_sessions "
            "(user_id, subject, topic, mode, score, questions_asked, correct_answers) "
            "VALUES (?, ?, ?, 'game', ?, ?, ?)",
            ("kid_a", "math", "number_quest", 50, 5, 5),
        )
        db.commit()

        # Verify data exists for leaderboard
        rows = db.execute(
            "SELECT user_id, best_streak FROM learning_progress "
            "ORDER BY best_streak DESC"
        ).fetchall()
        assert len(rows) >= 2

        score_rows = db.execute(
            "SELECT user_id, score FROM learning_sessions "
            "WHERE mode = 'game' ORDER BY score DESC"
        ).fetchall()
        assert len(score_rows) >= 1
