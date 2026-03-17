"""Socratic tutoring engine — guides through discovery, never gives direct answers.

Generates system prompts for the LLM that instruct it to teach via the
Socratic method, adapted to the learner's age group and history.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from cortex.db import get_db

logger = logging.getLogger(__name__)

AGE_GROUPS = ("child", "teen", "adult")

_AGE_STYLE = {
    "child": {
        "label": "child (ages 4–8)",
        "vocab": "simple, everyday words",
        "examples": "toys, animals, food, playground, family",
        "tone": "warm, patient, enthusiastic — celebrate every small step",
        "depth": "concrete only; no abstract reasoning",
    },
    "teen": {
        "label": "teen (ages 9–15)",
        "vocab": "more technical language is OK, define new terms once",
        "examples": "sports, video games, social media, real-world scenarios",
        "tone": "encouraging but challenging — treat them as capable",
        "depth": "introduce abstract thinking, relate to real-world applications",
    },
    "adult": {
        "label": "adult (ages 16+)",
        "vocab": "full technical depth, use proper terminology",
        "examples": "professional, scientific, engineering contexts",
        "tone": "efficient, collegial — respect their time",
        "depth": "deep dives welcome; link to theory and proofs when relevant",
    },
}

_HINT_INSTRUCTIONS = {
    0: (
        "Ask a probing question that leads the student toward the answer. "
        "Do NOT reveal ANY part of the answer. Make them think."
    ),
    1: (
        "Give a gentle hint — point them toward the right concept or method "
        "without stating the answer. For example, 'What happens when you…?'"
    ),
    2: (
        "Give a stronger hint — narrow down the approach. You can name the "
        "technique or formula category, but let them apply it."
    ),
    3: (
        "Walk them through the solution step by step, but pause at each step "
        "and ask them to complete it. Guided discovery, not a lecture."
    ),
}


@dataclass
class TutoringResponse:
    """Structured response from the tutoring engine."""

    message: str
    hint_level: int = 0
    follow_up_question: str = ""
    encouragement: str = ""


class TutoringEngine:
    """Generates Socratic teaching prompts for the LLM.

    This engine does NOT call the LLM itself — it builds prompts that the
    calling code passes to an LLM provider.
    """

    def get_socratic_prompt(
        self,
        subject: str,
        topic: str,
        question: str,
        user_age_group: str = "child",
        hint_level: int = 0,
    ) -> str:
        """Generate a Socratic teaching system prompt for the LLM.

        Returns a system prompt that instructs the LLM to guide, not tell.
        Adapts language complexity to age group (child/teen/adult).
        """
        age_group = user_age_group if user_age_group in AGE_GROUPS else "child"
        style = _AGE_STYLE[age_group]
        hint_level = max(0, min(hint_level, 3))
        hint_instruction = _HINT_INSTRUCTIONS[hint_level]

        prompt = (
            f"You are a Socratic tutor helping a {style['label']} student learn "
            f"{subject}"
        )
        if topic:
            prompt += f" — specifically the topic of {topic}"
        prompt += ".\n\n"

        prompt += "RULES (follow strictly):\n"
        prompt += "1. NEVER give the answer directly.\n"
        prompt += f"2. {hint_instruction}\n"
        prompt += (
            "3. Use language appropriate for the student: "
            f"{style['vocab']}.\n"
        )
        prompt += (
            "4. Draw examples from: "
            f"{style['examples']}.\n"
        )
        prompt += f"5. Tone: {style['tone']}.\n"
        prompt += f"6. Depth: {style['depth']}.\n"
        prompt += (
            "7. End every response with a follow-up question to keep "
            "the student thinking.\n"
        )
        prompt += (
            "8. If the student is frustrated, acknowledge it warmly and "
            "simplify your approach.\n\n"
        )

        prompt += f"The student's question is:\n{question}"
        return prompt

    def build_tutoring_context(
        self, user_id: str, subject: str, topic: str,
    ) -> dict:
        """Build context from user's learning history for personalized tutoring."""
        db = get_db()
        context: dict = {
            "user_id": user_id,
            "subject": subject,
            "topic": topic,
            "has_history": False,
            "proficiency": 0.0,
            "streak": 0,
            "total_attempts": 0,
            "recent_sessions": [],
        }

        row = db.execute(
            "SELECT proficiency, total_attempts, correct_attempts, streak, "
            "       best_streak, last_practiced "
            "FROM learning_progress "
            "WHERE user_id = ? AND subject = ? AND topic = ?",
            (user_id, subject, topic),
        ).fetchone()

        if row:
            context["has_history"] = True
            context["proficiency"] = row["proficiency"]
            context["streak"] = row["streak"]
            context["total_attempts"] = row["total_attempts"]

        rows = db.execute(
            "SELECT mode, difficulty_level, score, questions_asked, "
            "       correct_answers, started_at "
            "FROM learning_sessions "
            "WHERE user_id = ? AND subject = ? "
            "ORDER BY started_at DESC LIMIT 5",
            (user_id, subject),
        ).fetchall()

        context["recent_sessions"] = [
            {
                "mode": r["mode"],
                "difficulty": r["difficulty_level"],
                "score": r["score"],
                "questions": r["questions_asked"],
                "correct": r["correct_answers"],
                "date": r["started_at"],
            }
            for r in rows
        ]

        return context

    def get_age_adapted_explanation(self, concept: str, age_group: str) -> str:
        """Return explanation style guidance based on age group.

        This returns instructions (not the explanation itself) that inform
        the LLM how to pitch its language for the given age group.
        """
        age_group = age_group if age_group in AGE_GROUPS else "child"
        style = _AGE_STYLE[age_group]

        return (
            f"Explain '{concept}' for a {style['label']} student.\n"
            f"Vocabulary: {style['vocab']}.\n"
            f"Use examples from: {style['examples']}.\n"
            f"Tone: {style['tone']}.\n"
            f"Depth: {style['depth']}."
        )
