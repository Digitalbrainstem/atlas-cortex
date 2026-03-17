"""Learning & Education engine — user-facing tutoring, quizzes, and progress.

This is SEPARATE from cortex.learning (system self-learning).
This module provides Socratic tutoring, adaptive quiz generation,
and spaced-repetition progress tracking for human learners.
"""
from __future__ import annotations

from cortex.learning.education.tutoring import TutoringEngine, TutoringResponse
from cortex.learning.education.quiz import QuizGenerator, QuizQuestion
from cortex.learning.education.progress import ProgressTracker

__all__ = [
    "TutoringEngine",
    "TutoringResponse",
    "QuizGenerator",
    "QuizQuestion",
    "ProgressTracker",
]
