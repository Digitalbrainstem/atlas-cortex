"""Admin API — learning progress, sessions, and parent reports."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from cortex.admin.helpers import _db, _rows, _row, require_admin
from cortex.learning.education import ProgressTracker

router = APIRouter(prefix="/learning", tags=["learning"])
_tracker = ProgressTracker()


@router.get("/progress")
async def all_progress(_=Depends(require_admin)):
    """All users' learning progress."""
    db = _db()
    cur = db.execute(
        "SELECT user_id, subject, topic, proficiency, total_attempts, "
        "correct_attempts, streak, best_streak, last_practiced, next_review "
        "FROM learning_progress ORDER BY user_id, subject, topic"
    )
    return {"progress": _rows(cur)}


@router.get("/progress/{user_id}")
async def user_progress(user_id: str, _=Depends(require_admin)):
    """Specific user's learning progress."""
    db = _db()
    cur = db.execute(
        "SELECT subject, topic, proficiency, total_attempts, "
        "correct_attempts, streak, best_streak, last_practiced, next_review "
        "FROM learning_progress WHERE user_id = ? "
        "ORDER BY subject, topic",
        (user_id,),
    )
    rows = _rows(cur)

    # Aggregate per-subject summaries
    subjects: dict[str, dict] = {}
    for r in rows:
        subj = r["subject"]
        if subj not in subjects:
            subjects[subj] = {
                "topics": 0, "total_attempts": 0, "correct_attempts": 0,
                "avg_proficiency": 0.0, "best_streak": 0,
            }
        subjects[subj]["topics"] += 1
        subjects[subj]["total_attempts"] += r["total_attempts"]
        subjects[subj]["correct_attempts"] += r["correct_attempts"]
        subjects[subj]["avg_proficiency"] += r["proficiency"]
        subjects[subj]["best_streak"] = max(
            subjects[subj]["best_streak"], r["best_streak"],
        )
    for s in subjects.values():
        if s["topics"]:
            s["avg_proficiency"] = round(s["avg_proficiency"] / s["topics"], 4)

    return {"user_id": user_id, "progress": rows, "subjects": subjects}


@router.get("/sessions")
async def recent_sessions(days: int = 7, _=Depends(require_admin)):
    """Recent learning sessions (last N days)."""
    db = _db()
    cur = db.execute(
        "SELECT id, user_id, subject, topic, mode, difficulty_level, "
        "started_at, ended_at, score, questions_asked, correct_answers "
        "FROM learning_sessions "
        "WHERE started_at >= datetime('now', ? || ' days') "
        "ORDER BY started_at DESC",
        (str(-days),),
    )
    return {"sessions": _rows(cur)}


@router.get("/report/{user_id}")
async def parent_report(user_id: str, days: int = 7, _=Depends(require_admin)):
    """Parent-friendly progress report via ProgressTracker."""
    return _tracker.get_parent_report(user_id, days=days)


@router.get("/leaderboard")
async def leaderboard(_=Depends(require_admin)):
    """Top streaks and scores across all users."""
    db = _db()

    # Top streaks
    cur_streaks = db.execute(
        "SELECT user_id, subject, topic, best_streak, proficiency "
        "FROM learning_progress "
        "ORDER BY best_streak DESC LIMIT 20"
    )
    top_streaks = _rows(cur_streaks)

    # Top scores from game sessions
    cur_scores = db.execute(
        "SELECT user_id, subject, topic, score, correct_answers, "
        "questions_asked, started_at "
        "FROM learning_sessions "
        "WHERE mode = 'game' AND score > 0 "
        "ORDER BY score DESC LIMIT 20"
    )
    top_scores = _rows(cur_scores)

    return {"top_streaks": top_streaks, "top_scores": top_scores}
