"""Progress tracker — records attempts, computes proficiency, spaced repetition.

Proficiency uses a weighted average that favours recent attempts.
Spaced repetition intervals grow with proficiency and streak length.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from cortex.db import get_db

logger = logging.getLogger(__name__)

# Spaced repetition base intervals in days
_BASE_INTERVALS = [1, 3, 7, 14, 30]

# Weight given to the most recent attempt vs. historical average
_RECENT_WEIGHT = 0.3


class ProgressTracker:
    """Tracks learning progress with spaced repetition scheduling."""

    def record_attempt(
        self,
        user_id: str,
        subject: str,
        topic: str,
        is_correct: bool,
        difficulty: int,
    ) -> None:
        """Record a quiz/game attempt and update proficiency.

        Proficiency = weighted average favouring recent attempts.
        Streak resets on wrong, increments on correct.
        Schedules next review using spaced repetition.
        """
        db = get_db()
        now = datetime.now(timezone.utc).isoformat()

        row = db.execute(
            "SELECT id, proficiency, total_attempts, correct_attempts, "
            "       streak, best_streak "
            "FROM learning_progress "
            "WHERE user_id = ? AND subject = ? AND topic = ?",
            (user_id, subject, topic),
        ).fetchone()

        if row is None:
            new_prof = 1.0 if is_correct else 0.0
            streak = 1 if is_correct else 0
            next_review = self._calculate_next_review(new_prof, streak)
            db.execute(
                "INSERT INTO learning_progress "
                "(user_id, subject, topic, proficiency, total_attempts, "
                " correct_attempts, streak, best_streak, last_practiced, next_review) "
                "VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?)",
                (
                    user_id, subject, topic, new_prof,
                    1 if is_correct else 0,
                    streak, streak, now,
                    next_review.isoformat(),
                ),
            )
        else:
            total = row["total_attempts"] + 1
            correct = row["correct_attempts"] + (1 if is_correct else 0)
            old_prof = row["proficiency"]

            # Weighted average: blend historical with latest result
            instant = 1.0 if is_correct else 0.0
            new_prof = round(
                old_prof * (1 - _RECENT_WEIGHT) + instant * _RECENT_WEIGHT, 4,
            )

            if is_correct:
                streak = row["streak"] + 1
            else:
                streak = 0
            best_streak = max(row["best_streak"], streak)

            next_review = self._calculate_next_review(new_prof, streak)

            db.execute(
                "UPDATE learning_progress "
                "SET proficiency = ?, total_attempts = ?, correct_attempts = ?, "
                "    streak = ?, best_streak = ?, last_practiced = ?, next_review = ? "
                "WHERE id = ?",
                (new_prof, total, correct, streak, best_streak,
                 now, next_review.isoformat(), row["id"]),
            )
        db.commit()

    def get_proficiency(
        self, user_id: str, subject: str, topic: str = "",
    ) -> dict:
        """Get proficiency data. If topic is empty, aggregate for subject."""
        db = get_db()

        if topic:
            row = db.execute(
                "SELECT proficiency, total_attempts, correct_attempts, "
                "       streak, best_streak, last_practiced, next_review "
                "FROM learning_progress "
                "WHERE user_id = ? AND subject = ? AND topic = ?",
                (user_id, subject, topic),
            ).fetchone()
            if not row:
                return {
                    "proficiency": 0.0,
                    "total_attempts": 0,
                    "correct_attempts": 0,
                    "streak": 0,
                    "best_streak": 0,
                    "last_practiced": None,
                    "next_review": None,
                }
            return dict(row)

        # Aggregate across all topics in the subject
        rows = db.execute(
            "SELECT proficiency, total_attempts, correct_attempts, "
            "       streak, best_streak "
            "FROM learning_progress "
            "WHERE user_id = ? AND subject = ?",
            (user_id, subject),
        ).fetchall()

        if not rows:
            return {
                "proficiency": 0.0,
                "total_attempts": 0,
                "correct_attempts": 0,
                "streak": 0,
                "best_streak": 0,
                "topic_count": 0,
            }

        total_attempts = sum(r["total_attempts"] for r in rows)
        correct_attempts = sum(r["correct_attempts"] for r in rows)
        avg_prof = sum(r["proficiency"] for r in rows) / len(rows)

        return {
            "proficiency": round(avg_prof, 4),
            "total_attempts": total_attempts,
            "correct_attempts": correct_attempts,
            "streak": max(r["streak"] for r in rows),
            "best_streak": max(r["best_streak"] for r in rows),
            "topic_count": len(rows),
        }

    def get_due_reviews(self, user_id: str) -> list[dict]:
        """Get topics due for spaced repetition review."""
        db = get_db()
        now = datetime.now(timezone.utc).isoformat()

        rows = db.execute(
            "SELECT subject, topic, proficiency, streak, last_practiced, next_review "
            "FROM learning_progress "
            "WHERE user_id = ? AND next_review <= ?",
            (user_id, now),
        ).fetchall()

        return [dict(r) for r in rows]

    def get_parent_report(self, user_id: str, days: int = 7) -> dict:
        """Generate a parent-friendly progress report."""
        db = get_db()
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        # Sessions in the period
        sessions = db.execute(
            "SELECT subject, mode, difficulty_level, score, "
            "       questions_asked, correct_answers, started_at, ended_at "
            "FROM learning_sessions "
            "WHERE user_id = ? AND started_at >= ? "
            "ORDER BY started_at DESC",
            (user_id, since),
        ).fetchall()

        # Current proficiency per subject
        progress = db.execute(
            "SELECT subject, topic, proficiency, streak, best_streak, "
            "       total_attempts, correct_attempts "
            "FROM learning_progress "
            "WHERE user_id = ?",
            (user_id,),
        ).fetchall()

        subjects_practiced: dict[str, dict] = {}
        total_questions = 0
        total_correct = 0
        total_sessions = len(sessions)

        for s in sessions:
            subj = s["subject"]
            if subj not in subjects_practiced:
                subjects_practiced[subj] = {
                    "sessions": 0, "questions": 0, "correct": 0,
                }
            subjects_practiced[subj]["sessions"] += 1
            subjects_practiced[subj]["questions"] += s["questions_asked"]
            subjects_practiced[subj]["correct"] += s["correct_answers"]
            total_questions += s["questions_asked"]
            total_correct += s["correct_answers"]

        # Strongest / weakest areas
        topic_scores: list[tuple[str, str, float]] = []
        for p in progress:
            topic_scores.append((p["subject"], p["topic"], p["proficiency"]))
        topic_scores.sort(key=lambda x: x[2], reverse=True)

        strongest = [
            {"subject": s, "topic": t, "proficiency": round(p, 2)}
            for s, t, p in topic_scores[:5] if p > 0
        ]
        weakest = [
            {"subject": s, "topic": t, "proficiency": round(p, 2)}
            for s, t, p in reversed(topic_scores) if p < 0.8
        ][:5]

        # Best streaks
        best_streak = max((p["best_streak"] for p in progress), default=0)
        current_streak = max((p["streak"] for p in progress), default=0)

        return {
            "period_days": days,
            "total_sessions": total_sessions,
            "total_questions": total_questions,
            "total_correct": total_correct,
            "accuracy": round(total_correct / total_questions, 2) if total_questions else 0,
            "subjects_practiced": subjects_practiced,
            "strongest_areas": strongest,
            "needs_help": weakest,
            "current_streak": current_streak,
            "best_streak": best_streak,
        }

    def _calculate_next_review(
        self, proficiency: float, streak: int,
    ) -> datetime:
        """Spaced repetition: higher proficiency + longer streak = longer interval.

        Base intervals: 1, 3, 7, 14, 30 days.
        Interval index = proficiency × (len(intervals) - 1), boosted by streak.
        """
        idx = proficiency * (len(_BASE_INTERVALS) - 1)
        # Streak bonus: each streak point moves up ~0.5 interval steps
        idx += min(streak, 5) * 0.5
        idx = int(min(idx, len(_BASE_INTERVALS) - 1))
        days = _BASE_INTERVALS[idx]
        return datetime.now(timezone.utc) + timedelta(days=days)
