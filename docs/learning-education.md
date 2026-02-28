# Atlas Cortex — Learning & Education (Part 6)

Age-appropriate educational interactions including homework help, interactive quizzes, science experiments, and language learning.

## Overview

Atlas acts as a patient, adaptive tutor that adjusts to each learner's age, knowledge level, and learning style. It never gives direct answers to homework — instead it guides discovery through Socratic questioning.

## Core Principles

1. **Guide, don't answer** — for homework, use Socratic method to help students discover answers
2. **Age-calibrated** — vocabulary, complexity, and examples match the learner's age group
3. **Encouraging but honest** — celebrate effort, correct mistakes gently, never patronize
4. **Safe content** — all educational content passes through safety guardrails (C12)
5. **Multi-modal** — voice, text, and (future) avatar demonstrations

## Educational Modes

### Homework Helper
```
Child (age 10): "What's 3/4 plus 1/2?"
Atlas: "Good question! To add fractions, they need the same bottom number — 
  that's called a common denominator. What number works for both 4 and 2?"
Child: "8?"
Atlas: "Close! 8 works, but there's an even simpler one. 
  What's the smallest number both 4 and 2 go into?"
Child: "4!"
Atlas: "That's it! So what does 1/2 become with 4 on the bottom?"
```

### Interactive Quizzes
```
User: "Quiz me on state capitals"
Atlas: "Let's go! What's the capital of California?"
User: "Los Angeles?"
Atlas: "Good guess — LA is the biggest city, but the capital is actually 
  Sacramento. Sacramento has been the capital since 1854. Ready for the next one?"
```

### Science Experiments
```
Child: "What happens if we mix baking soda and vinegar?"
Atlas: "Ooh, that's a fun one! It makes a fizzy volcano! The baking soda is a 
  base and vinegar is an acid. When they meet, they create carbon dioxide gas — 
  that's the bubbles! Want to try it? You'll need a cup, 2 tablespoons of 
  baking soda, and about half a cup of vinegar."
```

### Language Learning
```
User: "Teach me some Spanish"
Atlas: "¡Hola! Let's start with greetings. Repeat after me: 
  'Buenos días' — that means 'Good morning.' Can you say it?"
```

## Tutoring Engine

```python
class TutoringEngine:
    """Adaptive tutoring that adjusts to learner level."""

    async def help_with(self, topic: str, question: str, user_profile: dict) -> str:
        """Generate Socratic guidance based on topic and learner level."""

    async def generate_quiz(self, topic: str, difficulty: str, count: int = 5) -> Quiz:
        """Create age-appropriate quiz questions."""

    async def check_answer(self, question: str, answer: str, user_profile: dict) -> Feedback:
        """Evaluate answer and provide constructive feedback."""

    async def explain_concept(self, concept: str, age_group: str) -> str:
        """Explain concept at appropriate level with examples."""
```

## Database Schema

```sql
CREATE TABLE IF NOT EXISTS learning_sessions (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    subject       TEXT NOT NULL,
    topic         TEXT,
    mode          TEXT NOT NULL,            -- "homework", "quiz", "experiment", "language"
    questions_asked INTEGER DEFAULT 0,
    correct_answers INTEGER DEFAULT 0,
    started_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ended_at      TIMESTAMP
);

CREATE TABLE IF NOT EXISTS learning_progress (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       TEXT NOT NULL,
    subject       TEXT NOT NULL,
    topic         TEXT NOT NULL,
    proficiency   REAL DEFAULT 0.0,         -- 0.0 to 1.0
    last_practiced TIMESTAMP,
    practice_count INTEGER DEFAULT 0,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, subject, topic)
);
```

## Implementation Tasks

| Task | Description |
|------|-------------|
| P6.1 | Tutoring engine — Socratic method, age-adapted explanations |
| P6.2 | Quiz generator — topic-based, adaptive difficulty, scoring |
| P6.3 | Homework helper — guide without giving answers, show-your-work mode |
| P6.4 | Science experiments — safe, age-appropriate, step-by-step with timers |
| P6.5 | Language learning — vocabulary drills, pronunciation, conversation practice |
| P6.6 | Progress tracking — per-subject proficiency, spaced repetition scheduling |
| P6.7 | Parent reporting — summary of what child learned, areas needing help |
