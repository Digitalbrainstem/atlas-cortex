"""STEM Games plugin — gamified learning through themed quiz adventures.

Games wrap QuizGenerator and ProgressTracker in fun narratives so kids
learn without realising they're learning.  Natural triggers like
"let's play a game" or "I'm bored" start a session — the word "math"
is never required.
"""
from __future__ import annotations

import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any

from cortex.db import get_db
from cortex.learning.education import ProgressTracker, QuizGenerator, QuizQuestion
from cortex.plugins.base import CommandMatch, CommandResult, CortexPlugin
from cortex.profiles import get_or_create_user_profile
from cortex.safety import resolve_content_tier

logger = logging.getLogger(__name__)
MAX_GAME_QUESTIONS = 10
MAX_GAME_SECONDS = 600
MAX_GAME_SESSIONS = 64
SessionKey = str | tuple[str, str, str]

# ── Game Definitions ─────────────────────────────────────────────

GAMES: dict[str, dict[str, Any]] = {
    "number_quest": {
        "name": "Number Quest",
        "subject": "math",
        "description": "Explorer finding treasure by solving math puzzles",
        "intro": (
            "🗺️ Welcome to **Number Quest**! You're a brave explorer "
            "searching for hidden treasure. Solve math puzzles to find "
            "golden keys and unlock treasure chests! Let's begin!"
        ),
        "correct_templates": [
            "🗝️ You found a golden key! Excellent work, explorer!",
            "💎 A hidden gem! You're on fire!",
            "🏆 The treasure chest opens! Amazing!",
            "⭐ A star lights up the path ahead! Great job!",
            "🔓 Another lock opened! You're unstoppable!",
        ],
        "wrong_templates": [
            "🗺️ Hmm, the path looks a bit tricky. Let's try again!",
            "🧭 Almost! Your compass is pointing close. One more try!",
            "🔍 Good effort, explorer! Here's what happened: {explanation}",
        ],
        "question_prefix": "🗝️ To open the next treasure chest: ",
        "level_up": "🎉 **LEVEL UP!** You unlocked a new region of the map! Moving to Level {level}!",
    },
    "science_safari": {
        "name": "Science Safari",
        "subject": "science",
        "description": "Scientist exploring nature, discovering facts",
        "intro": (
            "🌿 Welcome to **Science Safari**! You're a young scientist "
            "exploring the wild world of nature. Discover amazing facts "
            "about animals, plants, and the universe! 🔬"
        ),
        "correct_templates": [
            "🦋 You spotted a rare butterfly! Brilliant discovery!",
            "🌺 A new species catalogued! You're a natural scientist!",
            "🔬 Lab results confirmed! Outstanding work!",
            "🌍 Earth shares another secret with you! Wonderful!",
            "🚀 Mission accomplished! You're stellar!",
        ],
        "wrong_templates": [
            "🔍 Interesting hypothesis! Let's look more closely...",
            "🧪 Not quite the right formula. Here's a clue!",
            "🌱 Almost! Nature can be surprising. {explanation}",
        ],
        "question_prefix": "🦋 Quick discovery quiz: ",
        "level_up": "🎉 **FIELD PROMOTION!** You're now a Level {level} Scientist!",
    },
    "word_wizard": {
        "name": "Word Wizard",
        "subject": "language",
        "description": "Wizard learning magical words",
        "intro": (
            "✨ Welcome to **Word Wizard**! You're an apprentice wizard "
            "learning the power of magical words. Each new word adds to "
            "your spell book! 📖🪄"
        ),
        "correct_templates": [
            "✨ Your spell book glows! A new word mastered!",
            "📖 The ancient text reveals its meaning! Brilliant!",
            "🪄 *Wingardium Vocabula!* Perfect spell!",
            "🌟 Your wizard powers grow stronger! Amazing!",
            "🎆 A burst of magic! You're a true word wizard!",
        ],
        "wrong_templates": [
            "📜 The scroll is tricky! Let's re-read it...",
            "🔮 Your crystal ball is a bit foggy. Try again!",
            "🧙 Almost! Even great wizards need practice. {explanation}",
        ],
        "question_prefix": "✨ Your spell book needs a new word! ",
        "level_up": "🎉 **WIZARD RANK UP!** You're now a Level {level} Word Wizard!",
    },
}

# ── Game Session State ───────────────────────────────────────────

@dataclass
class GameSession:
    """Active game session for a user."""

    game_id: str
    user_id: str
    difficulty: int = 1
    score: int = 0
    streak: int = 0
    questions_asked: int = 0
    correct_answers: int = 0
    current_question: QuizQuestion | None = None
    db_session_id: int | None = None
    started_at: float = field(default_factory=time.time)
    max_difficulty: int = 10


# ── Pattern matching ─────────────────────────────────────────────

_PLAY_GAME = re.compile(
    r"(?:let'?s?\s+play|i\s+want\s+to\s+play|can\s+we\s+play|play)\s+"
    r"(?:a\s+)?(?:game|number\s*quest|science\s*safari|word\s*wizard)",
    re.IGNORECASE,
)

_GENERIC_GAME = re.compile(
    r"^(?:play\s+a\s+game|game\s+time|i'?m\s+bored)$",
    re.IGNORECASE,
)

_SPECIFIC_GAME = re.compile(
    r"(?:number\s*quest|science\s*safari|word\s*wizard)",
    re.IGNORECASE,
)

_CONTINUE = re.compile(
    r"^(?:next\s+question|another\s+one|keep\s+going|next|more|continue)$",
    re.IGNORECASE,
)

_PROGRESS_CHECK = re.compile(
    r"(?:how\s+am\s+i\s+doing|what'?s?\s+my\s+score|my\s+progress|my\s+score|show\s+score)",
    re.IGNORECASE,
)

_STOP = re.compile(
    r"^(?:stop|quit|end\s+game|i'?m?\s+done|exit|no\s+more)[.!?,]*$",
    re.IGNORECASE,
)


def _detect_game_id(message: str) -> str | None:
    """Extract specific game from message, or None for menu."""
    lower = message.lower()
    if "number" in lower and "quest" in lower:
        return "number_quest"
    if "science" in lower and "safari" in lower:
        return "science_safari"
    if "word" in lower and "wizard" in lower:
        return "word_wizard"
    # Subject hints (optional refinements, not required)
    if any(w in lower for w in ("math", "number", "count", "calcul")):
        return "number_quest"
    if any(w in lower for w in ("science", "nature", "animal", "space", "physics")):
        return "science_safari"
    if any(w in lower for w in ("word", "spell", "vocab", "language", "english")):
        return "word_wizard"
    return None


# ── Plugin ───────────────────────────────────────────────────────

class STEMGamesPlugin(CortexPlugin):
    """Gamified STEM learning through themed quiz adventures."""

    plugin_id = "stem_games"
    display_name = "STEM Games"
    plugin_type = "action"
    supports_learning = True
    version = "1.0.0"
    author = "Atlas"

    def __init__(self) -> None:
        super().__init__()
        self._quiz: QuizGenerator | None = None
        self._tracker: ProgressTracker | None = None
        self._sessions: dict[SessionKey, GameSession] = {}

    @staticmethod
    def _session_key(context: dict[str, Any]) -> SessionKey:
        user = context.get("user_id", "unknown")
        device, room = context.get("satellite_id") or "", context.get("room") or ""
        return (user, device, room) if device or room else user

    # ── Lifecycle ─────────────────────────────────────────────

    async def setup(self, config: dict[str, Any]) -> bool:
        self._quiz = QuizGenerator()
        self._tracker = ProgressTracker()
        return True

    async def health(self) -> bool:
        return True

    # ── Match ─────────────────────────────────────────────────

    async def match(
        self, message: str, context: dict[str, Any],
    ) -> CommandMatch:
        msg = message.strip()
        user_id = self._session_key(context)
        if _PLAY_GAME.search(msg):
            return CommandMatch(True, "start_game", confidence=0.95,
                                metadata={"game_id": _detect_game_id(msg)})

        # Active-session patterns: answer, continue, stop, progress
        if user_id in self._sessions:
            if time.time() - self._sessions[user_id].started_at >= MAX_GAME_SECONDS:
                return CommandMatch(True, "expired_game")
            if _STOP.search(msg):
                return CommandMatch(
                    matched=True, intent="stop_game", confidence=0.95,
                )
            if _CONTINUE.search(msg):
                return CommandMatch(
                    matched=True, intent="continue_game", confidence=0.95,
                )
            if _PROGRESS_CHECK.search(msg):
                return CommandMatch(
                    matched=True, intent="check_progress", confidence=0.9,
                )
            # Any text while a question is active is treated as an answer
            session = self._sessions[user_id]
            if session.current_question is not None and not re.match(
                r"^(?:what|where|when|why|how|who|please|set|cancel|turn|remind|add|remove|tell)\b",
                msg, re.IGNORECASE,
            ):
                return CommandMatch(
                    matched=True, intent="answer", confidence=0.85,
                    metadata={"answer": msg},
                )

        # New game triggers
        if _PLAY_GAME.search(msg):
            game_id = _detect_game_id(msg)
            return CommandMatch(
                matched=True, intent="start_game", confidence=0.95,
                metadata={"game_id": game_id},
            )

        if _GENERIC_GAME.search(msg):
            return CommandMatch(
                matched=True, intent="suggest_game", confidence=0.9,
            )

        # Progress check outside a game
        if _PROGRESS_CHECK.search(msg):
            return CommandMatch(
                matched=True, intent="check_progress", confidence=0.85,
            )

        return CommandMatch(matched=False)

    # ── Handle ────────────────────────────────────────────────

    async def handle(
        self, message: str, match: CommandMatch, context: dict[str, Any],
    ) -> CommandResult:
        assert self._quiz is not None
        assert self._tracker is not None

        user_id = context.get("user_id", "unknown")
        key = self._session_key(context)
        intent = match.intent

        if intent == "suggest_game":
            return self._suggest_game(user_id)
        if intent == "start_game":
            return self._start_game(user_id, match.metadata.get("game_id"), key=key)
        if intent == "answer":
            return self._handle_answer(key, message.strip())
        if intent == "continue_game":
            return self._next_question(key)
        if intent in ("stop_game", "expired_game"):
            result = self._end_game(key)
            if intent == "expired_game":
                result.response = "That game session has expired. " + result.response
            return result
        if intent == "check_progress":
            return self._check_progress(user_id, key=key)

        return CommandResult(success=False, response="I'm not sure what you mean. Want to play a game? 🎮")

    # ── Game logic ────────────────────────────────────────────

    def _suggest_game(self, user_id: str) -> CommandResult:
        """Offer game choices, recommending based on weakest subject."""
        assert self._tracker is not None
        lines = [
            "🎮 **Choose your adventure!**\n",
            "1️⃣ **Number Quest** — Hunt for treasure with math puzzles 🗺️",
            "2️⃣ **Science Safari** — Explore nature and discover facts 🌿",
            "3️⃣ **Word Wizard** — Learn magical words and spells ✨",
            "\nJust say the name of the game you want to play!",
        ]

        # Recommend weakest subject
        weakest = self._weakest_subject(user_id)
        if weakest:
            game_name = GAMES[weakest]["name"]
            lines.append(f"\n💡 I'd recommend **{game_name}** — let's level up there!")

        return CommandResult(
            success=True,
            response="\n".join(lines),
            metadata={"action": "suggest_game"},
        )

    def _weakest_subject(self, user_id: str) -> str | None:
        """Find the game whose subject the user is weakest in."""
        assert self._tracker is not None
        lowest_prof = 2.0
        weakest = None
        for game_id, game in GAMES.items():
            p = self._tracker.get_proficiency(user_id, game["subject"])
            prof = p["proficiency"]
            if prof < lowest_prof:
                lowest_prof = prof
                weakest = game_id
        return weakest

    def _start_game(self, user_id: str, game_id: str | None, *, key: SessionKey | None = None) -> CommandResult:
        """Start a new game session."""
        assert self._quiz is not None
        assert self._tracker is not None

        if game_id is None:
            return self._suggest_game(user_id)

        if game_id not in GAMES:
            return self._suggest_game(user_id)
        key = user_id if key is None else key
        for expired_key, old in list(self._sessions.items()):
            if time.time() - old.started_at >= MAX_GAME_SECONDS:
                self._end_game(expired_key)
        if key in self._sessions:
            self._end_game(key)
        if len(self._sessions) >= MAX_GAME_SESSIONS:
            logger.warning("Voice game session capacity reached")
            return CommandResult(False, "There are too many active games. Please finish one before starting another.")

        game = GAMES[game_id]
        db = get_db()
        profile = get_or_create_user_profile(db, user_id)
        tier = resolve_content_tier(profile)
        max_difficulty = 1 if profile.get("age_group") == "toddler" else 3 if tier in ("child", "unknown") else 10
        difficulty = min(max_difficulty, self._quiz.get_adaptive_difficulty(user_id, game["subject"]))

        # Create DB learning session
        cur = db.execute(
            "INSERT INTO learning_sessions "
            "(user_id, subject, topic, mode, difficulty_level) "
            "VALUES (?, ?, ?, 'game', ?)",
            (user_id, game["subject"], game_id, difficulty),
        )
        db.commit()
        session_id = cur.lastrowid

        session = GameSession(
            game_id=game_id,
            user_id=user_id,
            difficulty=difficulty,
            db_session_id=session_id,
            max_difficulty=max_difficulty,
        )
        self._sessions[key] = session

        # Generate first question
        question = self._generate_question(session)
        session.current_question = question
        session.questions_asked = 1

        intro = game["intro"]
        q_text = game["question_prefix"] + question.question

        return CommandResult(
            success=True,
            response=f"{intro}\n\n{q_text}",
            metadata={
                "action": "start_game",
                "game": game["name"],
                "difficulty": difficulty,
            },
        )

    def _handle_answer(self, user_id: SessionKey, answer: str) -> CommandResult:
        """Check the user's answer and respond."""
        assert self._quiz is not None
        assert self._tracker is not None

        session = self._sessions.get(user_id)
        if not session or not session.current_question:
            return CommandResult(
                success=False,
                response="No active question! Say 'next question' or start a new game.",
            )

        game = GAMES[session.game_id]
        question = session.current_question
        is_correct, feedback = self._quiz.check_answer(question, answer)

        # Record progress
        self._tracker.record_attempt(
            session.user_id, game["subject"], question.topic, is_correct, session.difficulty,
        )

        if is_correct:
            session.correct_answers += 1
            session.streak += 1
            points = 10
            if session.streak >= 3:
                points += 5
            session.score += points

            template = random.choice(game["correct_templates"])
            parts = [template]
            parts.append(f"  **+{points} points!** (Total: {session.score})")
            if session.streak >= 3:
                parts.append(f"  🔥 Streak: {session.streak}! (+5 bonus)")

            # Check auto-advance
            level_up_msg = self._check_auto_advance(session, session.user_id)
            if level_up_msg:
                parts.append(f"\n{level_up_msg}")

        else:
            session.streak = 0
            template = random.choice(game["wrong_templates"])
            parts = [template.format(explanation=question.explanation)]
            parts.append(f"\nThe answer was: **{question.correct_answer}**")
            if question.hint:
                parts.append(f"💡 Hint for next time: {question.hint}")

        if session.questions_asked >= MAX_GAME_QUESTIONS:
            self._update_db_session(session)
            result = self._end_game(user_id)
            result.response = "\n".join(parts) + "\nLet's take a break. " + result.response
            return result

        # Generate next question
        next_q = self._generate_question(session)
        session.current_question = next_q
        session.questions_asked += 1

        parts.append(f"\n{game['question_prefix']}{next_q.question}")

        # Check if we should offer to stop after many questions
        if session.questions_asked > 10 and session.questions_asked % 5 == 0:
            parts.append("\n_(Say 'stop' whenever you want to finish!)_")

        # Update DB session
        self._update_db_session(session)

        return CommandResult(
            success=True,
            response="\n".join(parts),
            metadata={
                "action": "answer",
                "correct": is_correct,
                "score": session.score,
                "streak": session.streak,
                "questions_asked": session.questions_asked,
            },
        )

    def _next_question(self, user_id: SessionKey) -> CommandResult:
        """Generate next question for active session."""
        session = self._sessions.get(user_id)
        if not session:
            return CommandResult(
                success=False,
                response="No active game! Want to play? Say 'let's play a game' 🎮",
            )

        if session.questions_asked >= MAX_GAME_QUESTIONS:
            return self._end_game(user_id)
        game = GAMES[session.game_id]
        question = self._generate_question(session)
        session.current_question = question
        session.questions_asked += 1
        self._update_db_session(session)

        score_line = f"Score: {session.score} | Streak: {session.streak}"
        return CommandResult(
            success=True,
            response=f"{score_line}\n\n{game['question_prefix']}{question.question}",
            metadata={
                "action": "next_question",
                "score": session.score,
                "questions_asked": session.questions_asked,
            },
        )

    def _end_game(self, user_id: SessionKey) -> CommandResult:
        """End the current game and show summary."""
        session = self._sessions.pop(user_id, None)
        if not session:
            return CommandResult(
                success=True,
                response="No game in progress. Want to start one? 🎮",
            )

        game = GAMES[session.game_id]
        accuracy = (
            round(session.correct_answers / session.questions_asked * 100)
            if session.questions_asked > 0
            else 0
        )

        # Finalise DB session
        db = get_db()
        if session.db_session_id:
            db.execute(
                "UPDATE learning_sessions SET "
                "ended_at = CURRENT_TIMESTAMP, score = ?, "
                "questions_asked = ?, correct_answers = ? "
                "WHERE id = ?",
                (session.score, session.questions_asked, session.correct_answers,
                 session.db_session_id),
            )
            db.commit()

        lines = [
            f"🏁 **Game Over — {game['name']}!**\n",
            f"📊 **Final Score:** {session.score} points",
            f"✅ **Correct:** {session.correct_answers}/{session.questions_asked} ({accuracy}%)",
            f"🔥 **Best Streak:** {session.streak}",
            f"📈 **Difficulty Level:** {session.difficulty}",
        ]

        if accuracy >= 80:
            lines.append("\n🌟 Outstanding performance! You're a superstar!")
        elif accuracy >= 60:
            lines.append("\n👏 Great job! Keep practicing and you'll be even better!")
        else:
            lines.append("\n💪 Good effort! Practice makes perfect — play again anytime!")

        lines.append("\nSay 'let's play a game' to start another adventure! 🎮")

        return CommandResult(
            success=True,
            response="\n".join(lines),
            metadata={
                "action": "end_game",
                "score": session.score,
                "accuracy": accuracy,
                "questions_asked": session.questions_asked,
            },
        )

    def _check_progress(self, user_id: str, *, key: SessionKey | None = None) -> CommandResult:
        """Show the user's learning progress."""
        assert self._tracker is not None

        lines = ["📊 **Your Learning Progress**\n"]

        for game_id, game in GAMES.items():
            p = self._tracker.get_proficiency(user_id, game["subject"])
            pct = round(p["proficiency"] * 100)
            bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
            streak_str = f"🔥 {p['streak']}" if p["streak"] > 0 else ""
            lines.append(
                f"**{game['name']}** ({game['subject']}): "
                f"[{bar}] {pct}% {streak_str}"
            )
            if p["total_attempts"] > 0:
                lines.append(
                    f"  {p['correct_attempts']}/{p['total_attempts']} correct, "
                    f"best streak: {p['best_streak']}"
                )

        # Active session info
        session = self._sessions.get(user_id if key is None else key)
        if session:
            lines.append(f"\n🎮 **Active game:** {GAMES[session.game_id]['name']}")
            lines.append(f"  Score: {session.score} | Level: {session.difficulty}")

        # Due reviews
        due = self._tracker.get_due_reviews(user_id)
        if due:
            lines.append(f"\n📅 **Topics due for review:** {len(due)}")
            for d in due[:3]:
                lines.append(f"  • {d['subject']} — {d['topic']}")

        return CommandResult(
            success=True,
            response="\n".join(lines),
            metadata={"action": "check_progress"},
        )

    # ── Helpers ───────────────────────────────────────────────

    def _generate_question(self, session: GameSession) -> QuizQuestion:
        """Generate a question appropriate for the active game and difficulty."""
        assert self._quiz is not None
        game = GAMES[session.game_id]
        subject = game["subject"]

        if subject == "math":
            if session.difficulty == 1:
                number = random.randint(1, 4)
                return QuizQuestion(
                    f"What number comes after {number}?", str(number + 1), 1,
                    "math", "counting", explanation=f"After {number} comes {number + 1}.",
                )
            return self._quiz.generate_math_question(session.difficulty)
        if subject == "science":
            return self._quiz.generate_science_question(session.difficulty)

        word, opposite = random.choice([("hot", "cold"), ("up", "down"), ("big", "small"), ("day", "night")])
        return QuizQuestion(
            f"What is the opposite of {word}?", opposite, session.difficulty,
            "language", "opposites", explanation=f"The opposite of {word} is {opposite}.",
        )

    def _check_auto_advance(self, session: GameSession, user_id: str) -> str | None:
        """Auto-advance difficulty when proficiency > 0.8."""
        assert self._tracker is not None
        game = GAMES[session.game_id]
        p = self._tracker.get_proficiency(user_id, game["subject"])

        if p["proficiency"] > 0.8 and session.difficulty < session.max_difficulty:
            session.difficulty += 1
            return game["level_up"].format(level=session.difficulty)

        if p["proficiency"] < 0.3 and p["total_attempts"] >= 5 and session.difficulty > 1:
            session.difficulty -= 1
            return f"📚 Let's practice a bit more at Level {session.difficulty} — you've got this!"

        return None

    def _update_db_session(self, session: GameSession) -> None:
        """Persist current session stats to the DB."""
        if not session.db_session_id:
            return
        try:
            db = get_db()
            db.execute(
                "UPDATE learning_sessions SET "
                "score = ?, questions_asked = ?, correct_answers = ?, "
                "difficulty_level = ? "
                "WHERE id = ?",
                (session.score, session.questions_asked, session.correct_answers,
                 session.difficulty, session.db_session_id),
            )
            db.commit()
        except Exception:
            logger.debug("Failed to update game session in DB", exc_info=True)
