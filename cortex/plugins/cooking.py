"""Cooking helper plugin — reference data for Layer 2."""

# Module ownership: Fast-path cooking reference (no API needed)

from __future__ import annotations

import logging
import re
from typing import Any

from cortex.plugins.base import CommandMatch, CommandResult, CortexPlugin

logger = logging.getLogger(__name__)

# ── Reference data ───────────────────────────────────────────────

# Internal cooking temperatures (°F)
COOKING_TEMPS: dict[str, tuple[int, str]] = {
    "chicken": (165, "Chicken should reach an internal temperature of 165°F (74°C)."),
    "poultry": (165, "Poultry should reach an internal temperature of 165°F (74°C)."),
    "turkey": (165, "Turkey should reach an internal temperature of 165°F (74°C)."),
    "ground beef": (160, "Ground beef should reach an internal temperature of 160°F (71°C)."),
    "ground turkey": (165, "Ground turkey should reach an internal temperature of 165°F (74°C)."),
    "ground pork": (160, "Ground pork should reach an internal temperature of 160°F (71°C)."),
    "beef": (145, "Beef steaks and roasts should reach at least 145°F (63°C) with a 3-minute rest."),
    "steak": (145, "Steak should reach at least 145°F (63°C) for medium-rare with a 3-minute rest."),
    "pork": (145, "Pork should reach an internal temperature of 145°F (63°C) with a 3-minute rest."),
    "pork chops": (145, "Pork chops should reach 145°F (63°C) with a 3-minute rest."),
    "fish": (145, "Fish should reach an internal temperature of 145°F (63°C)."),
    "salmon": (145, "Salmon should reach an internal temperature of 145°F (63°C)."),
    "shrimp": (145, "Shrimp should be cooked until pink and opaque, reaching 145°F (63°C)."),
    "lamb": (145, "Lamb should reach at least 145°F (63°C) with a 3-minute rest."),
    "ham": (145, "Pre-cooked ham should be reheated to 140°F (60°C). Fresh ham to 145°F (63°C)."),
    "eggs": (160, "Egg dishes should reach 160°F (71°C). Cook until yolks are firm."),
    "casserole": (165, "Casseroles and leftovers should be reheated to 165°F (74°C)."),
    "leftovers": (165, "Leftovers should be reheated to 165°F (74°C)."),
}

# Common substitutions
SUBSTITUTIONS: dict[str, str] = {
    "butter": "Use ¾ cup oil per 1 cup butter, or equal amount of applesauce for baking.",
    "oil": "Use equal amount of melted butter, or applesauce for lower-fat baking.",
    "egg": "Use ¼ cup applesauce, 1 mashed banana, or 1 tbsp ground flaxseed + 3 tbsp water per egg.",
    "eggs": "Use ¼ cup applesauce, 1 mashed banana, or 1 tbsp ground flaxseed + 3 tbsp water per egg.",
    "milk": "Use equal amount of oat milk, almond milk, soy milk, or coconut milk.",
    "buttermilk": "Add 1 tbsp lemon juice or vinegar to 1 cup milk. Let sit 5 minutes.",
    "heavy cream": "Mix ¾ cup milk with ¼ cup melted butter, or use coconut cream.",
    "sour cream": "Use equal amount of plain Greek yogurt.",
    "cream cheese": "Use equal amount of Greek yogurt or Neufchâtel cheese.",
    "flour": "Use equal amount of almond flour (add ¼ tsp extra baking powder) or oat flour.",
    "all-purpose flour": "Use equal amount of almond flour (add ¼ tsp extra baking powder) or oat flour.",
    "bread crumbs": "Use crushed crackers, rolled oats, or crushed cornflakes.",
    "cornstarch": "Use 2 tbsp all-purpose flour per 1 tbsp cornstarch, or equal amount arrowroot.",
    "baking powder": "Use ¼ tsp baking soda + ½ tsp cream of tartar per 1 tsp baking powder.",
    "brown sugar": "Use 1 cup white sugar + 1 tbsp molasses per cup of brown sugar.",
    "honey": "Use equal amount of maple syrup or agave nectar.",
    "maple syrup": "Use equal amount of honey or agave nectar.",
    "lemon juice": "Use equal amount of lime juice or ½ amount of white vinegar.",
    "wine (cooking)": "Use equal amount of broth with a splash of vinegar.",
    "soy sauce": "Use coconut aminos or Worcestershire sauce (less sodium).",
    "vanilla extract": "Use equal amount of maple syrup or almond extract (use half).",
    "chocolate chips": "Chop a chocolate bar, or use cacao nibs for less sweetness.",
    "vegetable broth": "Use chicken broth, mushroom broth, or bouillon cube + water.",
    "chicken broth": "Use vegetable broth, mushroom broth, or bouillon cube + water.",
}

# Cooking measurement conversions
MEASUREMENTS: dict[str, str] = {
    "cups in a gallon": "There are 16 cups in a gallon.",
    "cups in a quart": "There are 4 cups in a quart.",
    "cups in a pint": "There are 2 cups in a pint.",
    "tablespoons in a cup": "There are 16 tablespoons in a cup.",
    "teaspoons in a tablespoon": "There are 3 teaspoons in a tablespoon.",
    "ounces in a cup": "There are 8 fluid ounces in a cup.",
    "cups in a liter": "There are about 4.23 cups in a liter.",
    "tablespoons in an ounce": "There are 2 tablespoons in a fluid ounce.",
    "teaspoons in a cup": "There are 48 teaspoons in a cup.",
    "pints in a quart": "There are 2 pints in a quart.",
    "quarts in a gallon": "There are 4 quarts in a gallon.",
    "ml in a cup": "There are about 237 ml in a cup.",
    "ml in a tablespoon": "There are about 15 ml in a tablespoon.",
    "ml in a teaspoon": "There are about 5 ml in a teaspoon.",
    "grams in an ounce": "There are about 28.35 grams in an ounce.",
    "grams in a pound": "There are about 453.6 grams in a pound.",
    "sticks of butter in a cup": "There are 2 sticks of butter in a cup (1 stick = ½ cup = 8 tbsp).",
}

# Approximate cooking times
COOK_TIMES: dict[str, str] = {
    "chicken breast": "Bake at 400°F for 20-25 min, or grill 6-8 min per side.",
    "chicken thighs": "Bake at 400°F for 35-45 min, or grill 6-8 min per side.",
    "whole chicken": "Roast at 350°F for about 20 min per pound (1-1.5 hours for 3-4 lbs).",
    "salmon": "Bake at 400°F for 12-15 min, or pan-sear 4 min per side.",
    "steak": "Pan-sear 3-4 min per side for medium-rare (1-inch thick), then rest 5 min.",
    "rice": "Bring to boil, reduce to low, cover and cook 18-20 min. Let rest 5 min.",
    "pasta": "Boil 8-12 min (check package). Al dente is usually 1-2 min less than package time.",
    "hard boiled eggs": "Place in cold water, bring to boil, cover, remove from heat, 10-12 min.",
    "soft boiled eggs": "Place in boiling water for 6-7 minutes, then ice bath.",
    "baked potato": "Bake at 400°F for 45-60 min, or microwave 5-8 min.",
    "bacon": "Bake at 400°F for 15-20 min on a rack, or pan-fry over medium 8-12 min.",
    "bread": "Bake at 350°F for 30-35 min until golden and hollow-sounding when tapped.",
}

# ── Intent detection patterns ────────────────────────────────────

_TEMP_RE = re.compile(
    r"(?:cooking|internal|safe)\s+temp(?:erature)?\s+(?:for|of)\s+(.+?)(?:\?|$)",
    re.IGNORECASE,
)
_TEMP_RE_ALT = re.compile(
    r"(?:what\s+temp(?:erature)?|how\s+hot)\s+(?:should\s+I\s+cook|to\s+cook|for)\s+(.+?)(?:\?|$)",
    re.IGNORECASE,
)

_SUB_RE = re.compile(
    r"substitute\s+(?:for\s+)?(.+?)(?:\?|$)",
    re.IGNORECASE,
)
_SUB_RE_ALT = re.compile(
    r"(?:what\s+can\s+I\s+use\s+instead\s+of|replacement\s+for|alternative\s+to)\s+(.+?)(?:\?|$)",
    re.IGNORECASE,
)

_MEASURE_RE = re.compile(
    r"how\s+many\s+(cups|tablespoons|teaspoons|ounces|ml|grams|pints|quarts|sticks\s+of\s+butter)\s+(?:in|are\s+in)\s+(?:a\s+|an?\s+)?(.+?)(?:\?|$)",
    re.IGNORECASE,
)

_COOK_TIME_RE = re.compile(
    r"how\s+long\s+(?:to|do\s+I|should\s+I)\s+(?:cook|bake|boil|roast|grill|fry)\s+(.+?)(?:\?|$)",
    re.IGNORECASE,
)
_COOK_TIME_RE_ALT = re.compile(
    r"(?:cooking|cook)\s+time\s+(?:for|of)\s+(.+?)(?:\?|$)",
    re.IGNORECASE,
)


def _detect_intent(message: str) -> tuple[str, str | None]:
    """Return (intent, subject) pair."""
    for pattern in (_TEMP_RE, _TEMP_RE_ALT):
        m = pattern.search(message)
        if m:
            return "cooking_temp", m.group(1).strip().lower().rstrip("?.,!")

    for pattern in (_SUB_RE, _SUB_RE_ALT):
        m = pattern.search(message)
        if m:
            return "substitute", m.group(1).strip().lower().rstrip("?.,!")

    m = _MEASURE_RE.search(message)
    if m:
        unit_a = m.group(1).strip().lower()
        unit_b = m.group(2).strip().lower().rstrip("?.,!")
        return "measurement", f"{unit_a} in a {unit_b}"

    for pattern in (_COOK_TIME_RE, _COOK_TIME_RE_ALT):
        m = pattern.search(message)
        if m:
            return "cook_time", m.group(1).strip().lower().rstrip("?.,!")

    return "", None


def _lookup_temp(subject: str) -> str | None:
    """Find a cooking temperature for the subject."""
    s = subject.lower().strip()
    if s in COOKING_TEMPS:
        return COOKING_TEMPS[s][1]
    for key, (_, text) in COOKING_TEMPS.items():
        if key in s or s in key:
            return text
    return None


def _lookup_substitution(ingredient: str) -> str | None:
    """Find a substitution for the ingredient."""
    s = ingredient.lower().strip()
    if s in SUBSTITUTIONS:
        return f"Substitute for {s}: {SUBSTITUTIONS[s]}"
    for key, text in SUBSTITUTIONS.items():
        if key in s or s in key:
            return f"Substitute for {key}: {text}"
    return None


def _lookup_measurement(query: str) -> str | None:
    """Find a measurement conversion."""
    q = query.lower().strip()
    if q in MEASUREMENTS:
        return MEASUREMENTS[q]
    for key, text in MEASUREMENTS.items():
        if key in q or q in key:
            return text
    return None


def _lookup_cook_time(subject: str) -> str | None:
    """Find a cooking time for the subject."""
    s = subject.lower().strip()
    if s in COOK_TIMES:
        return COOK_TIMES[s]
    for key, text in COOK_TIMES.items():
        if key in s or s in key:
            return text
    return None


# ── Plugin class ─────────────────────────────────────────────────

class CookingPlugin(CortexPlugin):
    """Layer 2 plugin for cooking reference data (pure local)."""

    plugin_id = "cooking"
    display_name = "Cooking Helper"
    plugin_type = "action"
    version = "1.0.0"
    author = "Atlas"

    async def setup(self, config: dict[str, Any]) -> bool:
        return True

    async def health(self) -> bool:
        return True

    async def match(
        self,
        message: str,
        context: dict[str, Any],
    ) -> CommandMatch:
        intent, subject = _detect_intent(message)
        if intent:
            entities = [subject] if subject else []
            return CommandMatch(
                matched=True,
                intent=intent,
                entities=entities,
                confidence=0.9,
            )
        return CommandMatch(matched=False)

    async def handle(
        self,
        message: str,
        match: CommandMatch,
        context: dict[str, Any],
    ) -> CommandResult:
        intent = match.intent
        subject = match.entities[0] if match.entities else ""

        if intent == "cooking_temp":
            answer = _lookup_temp(subject)
            if answer:
                return CommandResult(success=True, response=answer)
            return CommandResult(
                success=False,
                response=f"I don't have a cooking temperature for \"{subject}\". Try chicken, beef, pork, or fish.",
            )

        if intent == "substitute":
            answer = _lookup_substitution(subject)
            if answer:
                return CommandResult(success=True, response=answer)
            return CommandResult(
                success=False,
                response=f"I don't have a substitution for \"{subject}\". Try butter, egg, milk, or flour.",
            )

        if intent == "measurement":
            answer = _lookup_measurement(subject)
            if answer:
                return CommandResult(success=True, response=answer)
            return CommandResult(
                success=False,
                response=f"I don't have that measurement conversion. Try 'how many cups in a gallon'.",
            )

        if intent == "cook_time":
            answer = _lookup_cook_time(subject)
            if answer:
                return CommandResult(success=True, response=answer)
            return CommandResult(
                success=False,
                response=f"I don't have a cooking time for \"{subject}\". Try chicken breast, rice, or pasta.",
            )

        return CommandResult(success=False, response="I'm not sure how to help with that cooking question.")
