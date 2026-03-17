"""Adaptive quiz generator — procedurally generates questions across subjects.

Math questions are generated programmatically (not hardcoded).
Difficulty 1–10 maps to a curriculum that introduces fractions, decimals, and
radians early per project requirements.
"""
from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass

from cortex.db import get_db

logger = logging.getLogger(__name__)


@dataclass
class QuizQuestion:
    """A single quiz question with metadata."""

    question: str
    correct_answer: str
    difficulty: int
    subject: str
    topic: str
    hint: str = ""
    explanation: str = ""


# ── Math topic pools by difficulty ────────────────────────────────────


def _gen_counting(difficulty: int) -> QuizQuestion:
    """Difficulty 1: counting objects."""
    n = random.randint(1, 10)
    emoji = random.choice(["🍎", "⭐", "🐱", "🌸"])
    return QuizQuestion(
        question=f"How many {emoji} are here? {emoji * n}",
        correct_answer=str(n),
        difficulty=difficulty,
        subject="math",
        topic="counting",
        hint="Try pointing at each one and counting out loud!",
        explanation=f"There are {n} {emoji}.",
    )


def _gen_addition_basic(difficulty: int) -> QuizQuestion:
    """Difficulty 1–2: single-digit addition."""
    a = random.randint(1, 9)
    b = random.randint(1, 9)
    return QuizQuestion(
        question=f"What is {a} + {b}?",
        correct_answer=str(a + b),
        difficulty=difficulty,
        subject="math",
        topic="addition",
        hint=f"Start at {a} and count up {b} more.",
        explanation=f"{a} + {b} = {a + b}",
    )


def _gen_subtraction_basic(difficulty: int) -> QuizQuestion:
    """Difficulty 1–2: single-digit subtraction (no negatives)."""
    a = random.randint(2, 10)
    b = random.randint(1, a)
    return QuizQuestion(
        question=f"What is {a} - {b}?",
        correct_answer=str(a - b),
        difficulty=difficulty,
        subject="math",
        topic="subtraction",
        hint=f"Start at {a} and count back {b}.",
        explanation=f"{a} - {b} = {a - b}",
    )


def _gen_multiplication(difficulty: int) -> QuizQuestion:
    """Difficulty 3–4: single-digit multiplication."""
    a = random.randint(2, 9)
    b = random.randint(2, 9)
    return QuizQuestion(
        question=f"What is {a} × {b}?",
        correct_answer=str(a * b),
        difficulty=difficulty,
        subject="math",
        topic="multiplication",
        hint=f"Think of {a} groups of {b}.",
        explanation=f"{a} × {b} = {a * b}",
    )


def _gen_division(difficulty: int) -> QuizQuestion:
    """Difficulty 3–4: exact division."""
    b = random.randint(2, 9)
    a = b * random.randint(2, 9)
    return QuizQuestion(
        question=f"What is {a} ÷ {b}?",
        correct_answer=str(a // b),
        difficulty=difficulty,
        subject="math",
        topic="division",
        hint=f"How many groups of {b} fit into {a}?",
        explanation=f"{a} ÷ {b} = {a // b}",
    )


def _gen_fraction_intro(difficulty: int) -> QuizQuestion:
    """Difficulty 3–4: intro fractions — what fraction of a group."""
    denom = random.choice([2, 3, 4])
    numer = random.randint(1, denom - 1)
    total = denom * random.randint(2, 4)
    part = total * numer // denom
    return QuizQuestion(
        question=f"If you have {total} cookies and eat {numer}/{denom} of them, how many did you eat?",
        correct_answer=str(part),
        difficulty=difficulty,
        subject="math",
        topic="fractions",
        hint=f"First figure out what 1/{denom} of {total} is, then multiply by {numer}.",
        explanation=f"{numer}/{denom} of {total} = {part}",
    )


def _gen_decimal_intro(difficulty: int) -> QuizQuestion:
    """Difficulty 3–4: intro decimals — relate to money."""
    dollars = random.randint(1, 9)
    cents = random.choice([25, 50, 75])
    total = dollars + cents / 100
    return QuizQuestion(
        question=f"You have ${dollars} and {cents} cents. How much is that as a decimal? (e.g. 3.50)",
        correct_answer=f"{total:.2f}",
        difficulty=difficulty,
        subject="math",
        topic="decimals",
        hint="Cents are parts of a dollar — 100 cents = $1.00.",
        explanation=f"${dollars} and {cents}¢ = ${total:.2f}",
    )


def _gen_two_digit_ops(difficulty: int) -> QuizQuestion:
    """Difficulty 5–6: two-digit addition/subtraction."""
    a = random.randint(10, 99)
    b = random.randint(10, 99)
    if random.choice([True, False]):
        return QuizQuestion(
            question=f"What is {a} + {b}?",
            correct_answer=str(a + b),
            difficulty=difficulty,
            subject="math",
            topic="two-digit operations",
            hint="Try breaking each number into tens and ones.",
            explanation=f"{a} + {b} = {a + b}",
        )
    big, small = max(a, b), min(a, b)
    return QuizQuestion(
        question=f"What is {big} - {small}?",
        correct_answer=str(big - small),
        difficulty=difficulty,
        subject="math",
        topic="two-digit operations",
        hint="Subtract ones first, then tens.",
        explanation=f"{big} - {small} = {big - small}",
    )


def _gen_geometry_basic(difficulty: int) -> QuizQuestion:
    """Difficulty 5–6: basic geometry."""
    shapes = [
        ("rectangle", random.randint(2, 10), random.randint(2, 10)),
        ("square", random.randint(2, 10), None),
    ]
    shape, a, b = random.choice(shapes)
    if shape == "square":
        area = a * a
        return QuizQuestion(
            question=f"What is the area of a square with side length {a}?",
            correct_answer=str(area),
            difficulty=difficulty,
            subject="math",
            topic="geometry",
            hint="Area of a square = side × side.",
            explanation=f"Area = {a} × {a} = {area}",
        )
    area = a * b
    return QuizQuestion(
        question=f"What is the area of a rectangle with length {a} and width {b}?",
        correct_answer=str(area),
        difficulty=difficulty,
        subject="math",
        topic="geometry",
        hint="Area of a rectangle = length × width.",
        explanation=f"Area = {a} × {b} = {area}",
    )


def _gen_negative_numbers(difficulty: int) -> QuizQuestion:
    """Difficulty 5–6: intro to negative numbers."""
    a = random.randint(-10, 10)
    b = random.randint(-10, 10)
    return QuizQuestion(
        question=f"What is {a} + ({b})?",
        correct_answer=str(a + b),
        difficulty=difficulty,
        subject="math",
        topic="negative numbers",
        hint="Think of a number line — adding a negative is like moving left.",
        explanation=f"{a} + ({b}) = {a + b}",
    )


def _gen_radian_concept(difficulty: int) -> QuizQuestion:
    """Difficulty 5–6: radians as parts of a circle."""
    fractions = [
        (1, 4, "quarter"),
        (1, 2, "half"),
        (3, 4, "three-quarters"),
        (1, 1, "full"),
    ]
    numer, denom, word = random.choice(fractions)
    degrees = 360 * numer // denom
    return QuizQuestion(
        question=(
            f"A full circle is 360 degrees. "
            f"How many degrees is a {word} of a circle?"
        ),
        correct_answer=str(degrees),
        difficulty=difficulty,
        subject="math",
        topic="radians",
        hint=f"Divide 360 by {denom}" + (f", then multiply by {numer}" if numer > 1 else "") + ".",
        explanation=f"A {word} circle = {degrees}°",
    )


def _gen_prealgebra(difficulty: int) -> QuizQuestion:
    """Difficulty 7–8: solve for x in simple equations."""
    a = random.randint(2, 12)
    x = random.randint(1, 15)
    b = a * x
    return QuizQuestion(
        question=f"Solve for x: {a}x = {b}",
        correct_answer=str(x),
        difficulty=difficulty,
        subject="math",
        topic="pre-algebra",
        hint=f"Divide both sides by {a}.",
        explanation=f"x = {b} ÷ {a} = {x}",
    )


def _gen_ratio_percentage(difficulty: int) -> QuizQuestion:
    """Difficulty 7–8: percentages and ratios."""
    pct = random.choice([10, 20, 25, 30, 40, 50, 75])
    total = random.choice([40, 60, 80, 100, 200])
    answer = total * pct // 100
    return QuizQuestion(
        question=f"What is {pct}% of {total}?",
        correct_answer=str(answer),
        difficulty=difficulty,
        subject="math",
        topic="percentages",
        hint=f"Convert {pct}% to a decimal ({pct/100}), then multiply.",
        explanation=f"{pct}% of {total} = {answer}",
    )


def _gen_unit_circle(difficulty: int) -> QuizQuestion:
    """Difficulty 7–8: unit circle / basic trig."""
    angles = [
        (30, "π/6", 0.5, "sin"),
        (45, "π/4", round(math.sqrt(2) / 2, 4), "sin"),
        (60, "π/3", round(math.sqrt(3) / 2, 4), "sin"),
        (90, "π/2", 1.0, "sin"),
        (0, "0", 1.0, "cos"),
        (60, "π/3", 0.5, "cos"),
    ]
    deg, rad, val, func = random.choice(angles)
    return QuizQuestion(
        question=f"What is {func}({deg}°)?  (Hint: {deg}° = {rad} radians. Answer as a decimal.)",
        correct_answer=str(val),
        difficulty=difficulty,
        subject="math",
        topic="trigonometry",
        hint="Remember the unit circle — or use SOH-CAH-TOA.",
        explanation=f"{func}({deg}°) = {val}",
    )


def _gen_quadratic(difficulty: int) -> QuizQuestion:
    """Difficulty 9–10: solve a quadratic equation (integer roots)."""
    r1 = random.randint(-6, 6)
    r2 = random.randint(-6, 6)
    # (x - r1)(x - r2) = x² - (r1+r2)x + r1*r2
    b = -(r1 + r2)
    c = r1 * r2
    b_str = f"+ {b}" if b > 0 else f"- {-b}" if b < 0 else ""
    c_str = f"+ {c}" if c > 0 else f"- {-c}" if c < 0 else ""
    roots_sorted = sorted({r1, r2})
    answer = ", ".join(str(r) for r in roots_sorted)
    return QuizQuestion(
        question=f"Solve: x² {b_str}x {c_str} = 0  (list roots separated by commas)",
        correct_answer=answer,
        difficulty=difficulty,
        subject="math",
        topic="quadratics",
        hint="Try factoring into (x - ?)(x - ?) = 0.",
        explanation=f"Roots: {answer}",
    )


def _gen_derivative_concept(difficulty: int) -> QuizQuestion:
    """Difficulty 9–10: basic derivative using power rule."""
    n = random.randint(2, 5)
    coeff = random.randint(1, 6)
    new_coeff = coeff * n
    new_exp = n - 1
    exp_str = f"x^{new_exp}" if new_exp > 1 else "x" if new_exp == 1 else ""
    return QuizQuestion(
        question=f"What is the derivative of {coeff}x^{n}?",
        correct_answer=f"{new_coeff}{exp_str}",
        difficulty=difficulty,
        subject="math",
        topic="calculus",
        hint="Power rule: d/dx [ax^n] = a·n·x^(n-1).",
        explanation=f"d/dx [{coeff}x^{n}] = {coeff}·{n}·x^{n - 1} = {new_coeff}{exp_str}",
    )


def _gen_limit_concept(difficulty: int) -> QuizQuestion:
    """Difficulty 9–10: basic limits."""
    a = random.randint(1, 5)
    b = random.randint(1, 5)
    # lim x→a of (b*x) = b*a
    answer = a * b
    return QuizQuestion(
        question=f"What is lim(x→{a}) of {b}x?",
        correct_answer=str(answer),
        difficulty=difficulty,
        subject="math",
        topic="limits",
        hint="For continuous functions, just substitute the value of x.",
        explanation=f"lim(x→{a}) {b}x = {b}·{a} = {answer}",
    )


# Difficulty → generator mapping
_MATH_GENERATORS: dict[int, list] = {
    1: [_gen_counting, _gen_addition_basic, _gen_subtraction_basic],
    2: [_gen_addition_basic, _gen_subtraction_basic],
    3: [_gen_multiplication, _gen_division, _gen_fraction_intro, _gen_decimal_intro],
    4: [_gen_multiplication, _gen_division, _gen_fraction_intro, _gen_decimal_intro],
    5: [_gen_two_digit_ops, _gen_geometry_basic, _gen_negative_numbers, _gen_radian_concept],
    6: [_gen_two_digit_ops, _gen_geometry_basic, _gen_negative_numbers, _gen_radian_concept],
    7: [_gen_prealgebra, _gen_ratio_percentage, _gen_unit_circle],
    8: [_gen_prealgebra, _gen_ratio_percentage, _gen_unit_circle],
    9: [_gen_quadratic, _gen_derivative_concept, _gen_limit_concept],
    10: [_gen_quadratic, _gen_derivative_concept, _gen_limit_concept],
}


# ── Science question pools ────────────────────────────────────────

_SCIENCE_QUESTIONS: dict[int, list[dict]] = {
    1: [
        {"q": "What do plants need to grow?", "a": "sunlight, water, soil", "t": "biology",
         "h": "Think about what you see in a garden.", "e": "Plants need sunlight, water, and soil (nutrients)."},
        {"q": "How many legs does a spider have?", "a": "8", "t": "biology",
         "h": "More than 6, fewer than 10.", "e": "Spiders are arachnids and have 8 legs."},
        {"q": "What is the closest star to Earth?", "a": "the sun", "t": "space",
         "h": "You can see it every day!", "e": "The Sun is our closest star."},
    ],
    2: [
        {"q": "What is H2O commonly known as?", "a": "water", "t": "chemistry",
         "h": "You drink it every day.", "e": "H2O is the chemical formula for water."},
        {"q": "What planet is known as the Red Planet?", "a": "mars", "t": "space",
         "h": "It's the fourth planet from the Sun.", "e": "Mars appears red due to iron oxide on its surface."},
        {"q": "What force keeps us on the ground?", "a": "gravity", "t": "physics",
         "h": "What makes things fall down?", "e": "Gravity pulls objects toward Earth's center."},
    ],
    3: [
        {"q": "What gas do humans breathe out?", "a": "carbon dioxide", "t": "biology",
         "h": "Plants use this gas for photosynthesis.", "e": "We exhale CO2 (carbon dioxide)."},
        {"q": "What are the three states of matter?", "a": "solid, liquid, gas", "t": "chemistry",
         "h": "Think about water as ice, liquid water, and steam.", "e": "Solid, liquid, and gas."},
    ],
    4: [
        {"q": "What is the chemical symbol for gold?", "a": "au", "t": "chemistry",
         "h": "It comes from the Latin word 'aurum'.", "e": "Gold's symbol is Au (from Latin aurum)."},
        {"q": "What type of rock is formed from cooled lava?", "a": "igneous", "t": "earth science",
         "h": "The word comes from Latin 'ignis' meaning fire.", "e": "Igneous rocks form from cooled magma/lava."},
    ],
    5: [
        {"q": "What is Newton's first law of motion about?", "a": "inertia", "t": "physics",
         "h": "An object at rest stays at rest unless...",
         "e": "Newton's first law: an object stays at rest or in motion unless acted on by a force (inertia)."},
        {"q": "What organelle is the powerhouse of the cell?", "a": "mitochondria", "t": "biology",
         "h": "It produces ATP (energy).", "e": "Mitochondria generate most of the cell's ATP."},
    ],
    6: [
        {"q": "What is the speed of light in a vacuum (approx. in km/s)?", "a": "300000", "t": "physics",
         "h": "About 3 × 10^5 km/s.", "e": "Light travels at ~300,000 km/s in a vacuum."},
        {"q": "What element has atomic number 6?", "a": "carbon", "t": "chemistry",
         "h": "This element is the basis of organic chemistry.", "e": "Carbon (C) has atomic number 6."},
    ],
    7: [
        {"q": "What is the formula for kinetic energy?", "a": "1/2 mv^2", "t": "physics",
         "h": "It involves mass and velocity.", "e": "KE = ½mv² (one-half mass times velocity squared)."},
        {"q": "What is the pH of pure water?", "a": "7", "t": "chemistry",
         "h": "It's right in the middle of the pH scale.", "e": "Pure water has a neutral pH of 7."},
    ],
    8: [
        {"q": "What is Avogadro's number (approx.)?", "a": "6.022e23", "t": "chemistry",
         "h": "About 6 × 10^23.", "e": "Avogadro's number ≈ 6.022 × 10²³ particles per mole."},
        {"q": "What is the unit of electrical resistance?", "a": "ohm", "t": "physics",
         "h": "Named after Georg Simon ___.", "e": "Resistance is measured in ohms (Ω)."},
    ],
    9: [
        {"q": "What is the universal gas constant R (in J/(mol·K))?", "a": "8.314", "t": "chemistry",
         "h": "Used in PV = nRT.", "e": "R ≈ 8.314 J/(mol·K)."},
        {"q": "What particle carries the strong nuclear force?", "a": "gluon", "t": "physics",
         "h": "It 'glues' quarks together.", "e": "Gluons mediate the strong nuclear force."},
    ],
    10: [
        {"q": "What is Planck's constant (in J·s)?", "a": "6.626e-34", "t": "physics",
         "h": "About 6.626 × 10^-34.", "e": "Planck's constant h ≈ 6.626 × 10⁻³⁴ J·s."},
        {"q": "What is the Schwarzschild radius formula?", "a": "2GM/c^2", "t": "physics",
         "h": "It involves G, mass, and the speed of light.", "e": "r_s = 2GM/c² — the event horizon radius."},
    ],
}


class QuizGenerator:
    """Generates quiz questions with adaptive difficulty."""

    def generate_math_question(
        self, difficulty: int, topic: str = "",
    ) -> QuizQuestion:
        """Generate a procedurally-created math question at the given difficulty."""
        difficulty = max(1, min(difficulty, 10))
        generators = _MATH_GENERATORS[difficulty]

        if topic:
            topic_lower = topic.lower()
            matching = [
                g for g in generators
                if topic_lower in (g.__doc__ or "").lower()
                or topic_lower in g.__name__.lower()
            ]
            if matching:
                generators = matching

        gen = random.choice(generators)
        return gen(difficulty)

    def generate_science_question(
        self, difficulty: int, topic: str = "",
    ) -> QuizQuestion:
        """Generate a science question from the question pool."""
        difficulty = max(1, min(difficulty, 10))
        pool = _SCIENCE_QUESTIONS.get(difficulty, _SCIENCE_QUESTIONS[1])

        if topic:
            topic_lower = topic.lower()
            matching = [q for q in pool if topic_lower in q["t"].lower()]
            if matching:
                pool = matching

        q = random.choice(pool)
        return QuizQuestion(
            question=q["q"],
            correct_answer=q["a"],
            difficulty=difficulty,
            subject="science",
            topic=q["t"],
            hint=q["h"],
            explanation=q["e"],
        )

    def check_answer(
        self, question: QuizQuestion, user_answer: str,
    ) -> tuple[bool, str]:
        """Check if the answer is correct with tolerance for numeric answers.

        Returns (is_correct, feedback_message).
        """
        user_answer = user_answer.strip()
        correct = question.correct_answer.strip()

        if not user_answer:
            return False, "You didn't provide an answer. Try again!"

        # Numeric comparison with tolerance
        user_num = _try_parse_number(user_answer)
        correct_num = _try_parse_number(correct)

        if user_num is not None and correct_num is not None:
            if correct_num == 0:
                is_correct = abs(user_num) < 1e-6
            else:
                is_correct = abs(user_num - correct_num) / max(abs(correct_num), 1e-9) < 0.01
            if is_correct:
                return True, f"Correct! {question.explanation}"
            return False, f"Not quite — the answer is {correct}. {question.explanation}"

        # Multi-value answers (e.g., quadratic roots "2, 3")
        if "," in correct:
            correct_parts = {s.strip().lower() for s in correct.split(",")}
            user_parts = {s.strip().lower() for s in user_answer.split(",")}
            if correct_parts == user_parts:
                return True, f"Correct! {question.explanation}"
            # Check if numeric parts match
            correct_nums = {_try_parse_number(s.strip()) for s in correct.split(",")}
            user_nums = {_try_parse_number(s.strip()) for s in user_answer.split(",")}
            if None not in correct_nums and None not in user_nums and correct_nums == user_nums:
                return True, f"Correct! {question.explanation}"

        # Fuzzy text matching
        if _normalize_text(user_answer) == _normalize_text(correct):
            return True, f"Correct! {question.explanation}"

        # Check for containment of key words
        correct_words = set(_normalize_text(correct).split())
        user_words = set(_normalize_text(user_answer).split())
        if correct_words and correct_words.issubset(user_words):
            return True, f"Correct! {question.explanation}"

        return False, f"Not quite — the answer is {correct}. {question.explanation}"

    def get_adaptive_difficulty(self, user_id: str, subject: str) -> int:
        """Determine the right difficulty based on user's learning_progress.

        Auto-advance: if proficiency > 0.8, bump difficulty.
        Auto-ease: if proficiency < 0.3 and streak == 0, lower difficulty.
        """
        db = get_db()

        row = db.execute(
            "SELECT AVG(proficiency) as avg_prof, AVG(streak) as avg_streak, "
            "       MAX(difficulty_level) as max_diff "
            "FROM learning_progress p "
            "LEFT JOIN learning_sessions s ON s.user_id = p.user_id AND s.subject = p.subject "
            "WHERE p.user_id = ? AND p.subject = ?",
            (user_id, subject),
        ).fetchone()

        if not row or row["avg_prof"] is None:
            return 1  # new user starts at difficulty 1

        avg_prof = row["avg_prof"]
        avg_streak = row["avg_streak"] or 0
        current_max = row["max_diff"] or 1

        if avg_prof > 0.8 and avg_streak >= 2:
            return min(current_max + 1, 10)
        if avg_prof < 0.3 and avg_streak < 1:
            return max(current_max - 1, 1)
        return max(current_max, 1)


def _try_parse_number(s: str) -> float | None:
    """Try to parse a string as a number."""
    s = s.strip().lower().replace(",", "").replace("$", "")
    try:
        return float(s)
    except ValueError:
        return None


def _normalize_text(s: str) -> str:
    """Normalize text for fuzzy matching."""
    return " ".join(s.lower().strip().split())
