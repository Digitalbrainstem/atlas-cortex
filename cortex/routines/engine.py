"""Core routine execution engine."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any

from cortex.db import get_db
from cortex.routines.actions import ActionResult, get_executor

logger = logging.getLogger(__name__)


class RoutineEngine:
    """Manages routine CRUD, step/trigger management, and execution."""

    # ── Routine CRUD ─────────────────────────────────────────────

    async def create_routine(
        self,
        name: str,
        description: str = "",
        user_id: str = "",
        template_id: str = "",
    ) -> int:
        """Create a new routine and return its ID."""
        conn = get_db()
        cur = conn.execute(
            "INSERT INTO routines (name, description, user_id, template_id) "
            "VALUES (?, ?, ?, ?)",
            (name, description, user_id, template_id),
        )
        conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    async def delete_routine(self, routine_id: int) -> bool:
        """Delete a routine and all its steps/triggers/runs (via CASCADE)."""
        conn = get_db()
        cur = conn.execute("DELETE FROM routines WHERE id = ?", (routine_id,))
        conn.commit()
        return cur.rowcount > 0

    async def enable_routine(self, routine_id: int) -> bool:
        """Enable a routine."""
        conn = get_db()
        cur = conn.execute(
            "UPDATE routines SET enabled = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (routine_id,),
        )
        conn.commit()
        return cur.rowcount > 0

    async def disable_routine(self, routine_id: int) -> bool:
        """Disable a routine."""
        conn = get_db()
        cur = conn.execute(
            "UPDATE routines SET enabled = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (routine_id,),
        )
        conn.commit()
        return cur.rowcount > 0

    async def list_routines(self, user_id: str = "") -> list[dict]:
        """List all routines, optionally filtered by user_id."""
        conn = get_db()
        if user_id:
            rows = conn.execute(
                "SELECT * FROM routines WHERE user_id = ? ORDER BY name",
                (user_id,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM routines ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    async def get_routine(self, routine_id: int) -> dict | None:
        """Get a routine with its steps and triggers."""
        conn = get_db()
        row = conn.execute("SELECT * FROM routines WHERE id = ?", (routine_id,)).fetchone()
        if row is None:
            return None

        routine = dict(row)

        steps = conn.execute(
            "SELECT * FROM routine_steps WHERE routine_id = ? ORDER BY step_order",
            (routine_id,),
        ).fetchall()
        routine["steps"] = [dict(s) for s in steps]

        triggers = conn.execute(
            "SELECT * FROM routine_triggers WHERE routine_id = ?",
            (routine_id,),
        ).fetchall()
        routine["triggers"] = [dict(t) for t in triggers]

        return routine

    # ── Step management ──────────────────────────────────────────

    async def add_step(
        self,
        routine_id: int,
        action_type: str,
        action_config: dict,
        step_order: int | None = None,
        condition: str = "",
        on_error: str = "continue",
    ) -> int:
        """Add a step to a routine. Returns step ID."""
        conn = get_db()

        if step_order is None:
            row = conn.execute(
                "SELECT COALESCE(MAX(step_order), 0) + 1 AS next_order "
                "FROM routine_steps WHERE routine_id = ?",
                (routine_id,),
            ).fetchone()
            step_order = row["next_order"]

        cur = conn.execute(
            "INSERT INTO routine_steps (routine_id, step_order, action_type, action_config, "
            "condition, on_error) VALUES (?, ?, ?, ?, ?, ?)",
            (routine_id, step_order, action_type, json.dumps(action_config), condition, on_error),
        )
        conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    async def remove_step(self, step_id: int) -> bool:
        """Remove a step by ID."""
        conn = get_db()
        cur = conn.execute("DELETE FROM routine_steps WHERE id = ?", (step_id,))
        conn.commit()
        return cur.rowcount > 0

    async def reorder_steps(self, routine_id: int, step_ids: list[int]) -> bool:
        """Reorder steps by providing the desired order of step IDs."""
        conn = get_db()
        for order, step_id in enumerate(step_ids, start=1):
            conn.execute(
                "UPDATE routine_steps SET step_order = ? WHERE id = ? AND routine_id = ?",
                (order, step_id, routine_id),
            )
        conn.commit()
        return True

    # ── Trigger management ───────────────────────────────────────

    async def add_trigger(
        self,
        routine_id: int,
        trigger_type: str,
        trigger_config: dict,
    ) -> int:
        """Add a trigger to a routine. Returns trigger ID."""
        conn = get_db()
        cur = conn.execute(
            "INSERT INTO routine_triggers (routine_id, trigger_type, trigger_config) "
            "VALUES (?, ?, ?)",
            (routine_id, trigger_type, json.dumps(trigger_config)),
        )
        conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    async def remove_trigger(self, trigger_id: int) -> bool:
        """Remove a trigger by ID."""
        conn = get_db()
        cur = conn.execute("DELETE FROM routine_triggers WHERE id = ?", (trigger_id,))
        conn.commit()
        return cur.rowcount > 0

    # ── Execution ────────────────────────────────────────────────

    async def run_routine(self, routine_id: int, context: dict | None = None) -> int:
        """Execute a routine. Returns the run_id."""
        conn = get_db()
        ctx = dict(context or {})
        ctx.setdefault("variables", {})

        routine = await self.get_routine(routine_id)
        if routine is None:
            raise ValueError(f"Routine {routine_id} not found")
        if not routine["enabled"]:
            raise ValueError(f"Routine {routine_id} is disabled")

        # Create run record
        cur = conn.execute(
            "INSERT INTO routine_runs (routine_id) VALUES (?)", (routine_id,)
        )
        conn.commit()
        run_id: int = cur.lastrowid  # type: ignore[assignment]

        steps = routine.get("steps", [])
        steps_completed = 0
        error_message = ""
        status = "completed"

        for step in steps:
            action_type = step["action_type"]
            try:
                action_config = json.loads(step["action_config"]) if isinstance(step["action_config"], str) else step["action_config"]
            except (json.JSONDecodeError, TypeError):
                action_config = {}

            # Variable substitution in message fields
            action_config = self._substitute_variables(action_config, ctx.get("variables", {}))

            executor = get_executor(action_type)
            if executor is None:
                logger.warning("Unknown action type: %s", action_type)
                if step.get("on_error", "continue") == "stop":
                    status = "failed"
                    error_message = f"Unknown action type: {action_type}"
                    break
                continue

            try:
                result: ActionResult = await executor.execute(action_config, ctx)
            except Exception as exc:
                logger.warning("Step %s failed: %s", action_type, exc)
                result = ActionResult(success=False, message=str(exc))

            # Merge variables from set_variable actions
            if result.variables:
                ctx.setdefault("variables", {}).update(result.variables)

            if result.success:
                steps_completed += 1
            else:
                on_error = step.get("on_error", "continue")
                if on_error == "stop":
                    status = "failed"
                    error_message = result.message
                    break
                elif on_error == "skip_rest":
                    break
                # else: continue to next step

                # Condition actions that return success=False skip remaining steps
                if action_type == "condition":
                    status = "completed"
                    break

        # Finalise run record
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE routine_runs SET finished_at = ?, status = ?, steps_completed = ?, "
            "error_message = ? WHERE id = ?",
            (now, status, steps_completed, error_message, run_id),
        )
        conn.execute(
            "UPDATE routines SET last_run = ?, run_count = run_count + 1, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (now, routine_id),
        )
        conn.commit()

        return run_id

    async def cancel_run(self, run_id: int) -> bool:
        """Mark a run as cancelled."""
        conn = get_db()
        now = datetime.now(timezone.utc).isoformat()
        cur = conn.execute(
            "UPDATE routine_runs SET status = 'cancelled', finished_at = ? "
            "WHERE id = ? AND status = 'running'",
            (now, run_id),
        )
        conn.commit()
        return cur.rowcount > 0

    # ── Trigger matching ─────────────────────────────────────────

    async def match_voice_trigger(self, phrase: str) -> int | None:
        """Match a voice phrase to a routine trigger. Returns routine_id or None."""
        conn = get_db()
        rows = conn.execute(
            "SELECT rt.routine_id, rt.trigger_config, r.enabled "
            "FROM routine_triggers rt "
            "JOIN routines r ON rt.routine_id = r.id "
            "WHERE rt.trigger_type = 'voice_phrase' AND rt.enabled = 1 AND r.enabled = 1",
        ).fetchall()

        phrase_lower = phrase.lower().strip()
        best_match: int | None = None
        best_score: float = 0.0

        for row in rows:
            try:
                config = json.loads(row["trigger_config"])
            except (json.JSONDecodeError, TypeError):
                continue

            trigger_phrase = config.get("phrase", "").lower().strip()
            if not trigger_phrase:
                continue

            # Exact match
            if phrase_lower == trigger_phrase:
                return row["routine_id"]

            # Fuzzy match — require at least 0.75 similarity
            score = SequenceMatcher(None, phrase_lower, trigger_phrase).ratio()
            if score > best_score and score >= 0.75:
                best_score = score
                best_match = row["routine_id"]

        return best_match

    async def match_ha_event(
        self, entity_id: str, new_state: str, old_state: str
    ) -> list[int]:
        """Match an HA state change to routine triggers. Returns list of routine_ids."""
        conn = get_db()
        rows = conn.execute(
            "SELECT rt.routine_id, rt.trigger_config "
            "FROM routine_triggers rt "
            "JOIN routines r ON rt.routine_id = r.id "
            "WHERE rt.trigger_type = 'ha_event' AND rt.enabled = 1 AND r.enabled = 1",
        ).fetchall()

        matched: list[int] = []
        for row in rows:
            try:
                config = json.loads(row["trigger_config"])
            except (json.JSONDecodeError, TypeError):
                continue

            if config.get("entity_id") != entity_id:
                continue

            to_state = config.get("to_state")
            from_state = config.get("from_state")

            if to_state and to_state != new_state:
                continue
            if from_state and from_state != old_state:
                continue

            matched.append(row["routine_id"])

        return matched

    # ── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _substitute_variables(config: dict, variables: dict[str, Any]) -> dict:
        """Replace {{var}} placeholders in string values with variable values."""
        result = {}
        for key, value in config.items():
            if isinstance(value, str):
                for var_name, var_value in variables.items():
                    value = value.replace("{{" + var_name + "}}", str(var_value))
            result[key] = value
        return result
