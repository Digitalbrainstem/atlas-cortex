"""Layer 1: Instant Answers (~5 ms, no LLM).

Handles:
  - Date / time / day-of-week queries
  - Basic math (sandboxed eval)
  - Identity questions ("who are you", "what can you do")
  - Greetings (time-of-day + user-name aware)
  - Simple memory recall (from context)
"""

from __future__ import annotations

import ast
import logging
import math
import operator
import re
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────
# Safe math evaluator
# ──────────────────────────────────────────────────────────────────

_SAFE_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
    ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
}

_SAFE_NAMES = {
    "pi": math.pi,
    "e": math.e,
    "sqrt": math.sqrt,
    "abs": abs,
    "round": round,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log2": math.log2,
    "log10": math.log10,
    "floor": math.floor,
    "ceil": math.ceil,
}


def _safe_eval(expr: str) -> float | int:
    """Evaluate a mathematical expression safely (no exec, no imports)."""
    tree = ast.parse(expr, mode="eval")
    return _eval_node(tree.body)


def _eval_node(node: ast.expr) -> float | int:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError(f"Unsupported constant: {node.value!r}")
    if isinstance(node, ast.Name):
        if node.id in _SAFE_NAMES:
            return _SAFE_NAMES[node.id]  # type: ignore[return-value]
        raise ValueError(f"Unknown name: {node.id!r}")
    if isinstance(node, ast.Call):
        func = _eval_node(node.func)  # type: ignore[arg-type]
        args = [_eval_node(a) for a in node.args]
        return func(*args)  # type: ignore[operator]
    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _SAFE_OPERATORS:
            raise ValueError(f"Unsupported operator: {op_type}")
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        return _SAFE_OPERATORS[op_type](left, right)
    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _SAFE_OPERATORS:
            raise ValueError(f"Unsupported unary operator: {op_type}")
        operand = _eval_node(node.operand)
        return _SAFE_OPERATORS[op_type](operand)
    raise ValueError(f"Unsupported AST node: {type(node)}")


# ──────────────────────────────────────────────────────────────────
# Pattern matching helpers
# ──────────────────────────────────────────────────────────────────

_DATE_PATTERNS = re.compile(
    r"\b(what('?s| is) (the )?(date|today|day|time)|"
    r"(what|which) (day|month|year)|"
    r"(current|today'?s?) (date|day|time))\b",
    re.IGNORECASE,
)

_TIME_PATTERNS = re.compile(
    r"\b(what (time|hour) is it|current time|tell me the time|"
    r"what time is it (now|currently)?)\b",
    re.IGNORECASE,
)

_MATH_PATTERNS = re.compile(
    r"^\s*(what('?s| is)\s+)?(calculate|compute|eval)?\s*"
    r"(?P<expr>[\d\s\+\-\*\/\(\)\.\%\^]+[\d\)])\s*[=?]*\s*$",
    re.IGNORECASE,
)

_IDENTITY_PATTERNS = re.compile(
    r"\b(who are you|what are you|what('?s| is) your name|"
    r"what can you do|what do you do|tell me about yourself|"
    r"introduce yourself|are you an ai|are you a bot|are you atlas)\b",
    re.IGNORECASE,
)

_GREETING_PATTERNS = re.compile(
    r"^\s*(hello|hi|hey|good (morning|afternoon|evening|night|day)|"
    r"howdy|sup|what'?s up)[!.,]?\s*$",
    re.IGNORECASE,
)


def _greeting_response(context: dict[str, Any]) -> str:
    tod = context.get("time_of_day", "morning")
    user = context.get("display_name") or context.get("user_id", "")
    name_part = f", {user}" if user and user != "default" else ""

    tod_greetings = {
        "morning": f"Good morning{name_part}!",
        "afternoon": f"Hey{name_part}!",
        "evening": f"Good evening{name_part}.",
        "late_night": f"Still at it{name_part}?",
    }
    return tod_greetings.get(tod, f"Hey{name_part}!")


# ──────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────

async def try_instant_answer(
    message: str,
    context: dict[str, Any],
) -> tuple[str | None, float]:
    """Try to answer *message* instantly (no LLM).

    Returns ``(response_text, confidence)`` where *response_text* is ``None``
    if no instant answer is available.
    """
    lower = message.strip().lower()

    # ── Date / time ────────────────────────────────────────────
    if _DATE_PATTERNS.search(lower):
        now = datetime.now(tz=timezone.utc).astimezone()
        return now.strftime("Today is %A, %B %d, %Y.").replace(" 0", " "), 1.0

    if _TIME_PATTERNS.search(lower):
        now = datetime.now(tz=timezone.utc).astimezone()
        return now.strftime("It's %I:%M %p."), 1.0

    # ── Math ───────────────────────────────────────────────────
    math_match = _MATH_PATTERNS.match(message)
    if math_match:
        raw_expr = math_match.group("expr") or message
        # Replace ^ with ** for Python
        expr = raw_expr.replace("^", "**").strip()
        try:
            result = _safe_eval(expr)
            # Format nicely: no decimal for integers
            if isinstance(result, float) and result.is_integer():
                result = int(result)
            return f"{result}", 1.0
        except Exception as exc:
            logger.debug("Math eval failed for %r: %s", expr, exc)

    # ── Identity ────────────────────────────────────────────────
    if _IDENTITY_PATTERNS.search(lower):
        return (
            "I'm Atlas Cortex — a self-evolving AI assistant. "
            "I can answer questions, control smart home devices, remember things "
            "about you, and get smarter over time. What can I help you with?",
            1.0,
        )

    # ── Greetings ───────────────────────────────────────────────
    if _GREETING_PATTERNS.match(lower):
        return _greeting_response(context), 1.0

    return None, 0.0
