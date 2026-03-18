"""Unit conversion plugin — pure local math for Layer 2."""

# Module ownership: Fast-path unit conversions (no API needed)

from __future__ import annotations

import logging
import re
from typing import Any

from cortex.plugins.base import CommandMatch, CommandResult, CortexPlugin

logger = logging.getLogger(__name__)

# ── Conversion tables ────────────────────────────────────────────
# All factors are TO the SI base unit (meters, grams, liters, kelvin).

_LENGTH: dict[str, tuple[float, str]] = {
    "miles": (1609.344, "meters"),
    "mile": (1609.344, "meters"),
    "mi": (1609.344, "meters"),
    "kilometers": (1000.0, "meters"),
    "kilometer": (1000.0, "meters"),
    "km": (1000.0, "meters"),
    "feet": (0.3048, "meters"),
    "foot": (0.3048, "meters"),
    "ft": (0.3048, "meters"),
    "meters": (1.0, "meters"),
    "meter": (1.0, "meters"),
    "m": (1.0, "meters"),
    "inches": (0.0254, "meters"),
    "inch": (0.0254, "meters"),
    "in": (0.0254, "meters"),
    "centimeters": (0.01, "meters"),
    "centimeter": (0.01, "meters"),
    "cm": (0.01, "meters"),
    "yards": (0.9144, "meters"),
    "yard": (0.9144, "meters"),
    "yd": (0.9144, "meters"),
    "millimeters": (0.001, "meters"),
    "millimeter": (0.001, "meters"),
    "mm": (0.001, "meters"),
}

_WEIGHT: dict[str, tuple[float, str]] = {
    "pounds": (453.592, "grams"),
    "pound": (453.592, "grams"),
    "lbs": (453.592, "grams"),
    "lb": (453.592, "grams"),
    "kilograms": (1000.0, "grams"),
    "kilogram": (1000.0, "grams"),
    "kg": (1000.0, "grams"),
    "ounces": (28.3495, "grams"),
    "ounce": (28.3495, "grams"),
    "oz": (28.3495, "grams"),
    "grams": (1.0, "grams"),
    "gram": (1.0, "grams"),
    "g": (1.0, "grams"),
}

_VOLUME: dict[str, tuple[float, str]] = {
    "gallons": (3785.41, "ml"),
    "gallon": (3785.41, "ml"),
    "gal": (3785.41, "ml"),
    "liters": (1000.0, "ml"),
    "liter": (1000.0, "ml"),
    "l": (1000.0, "ml"),
    "cups": (236.588, "ml"),
    "cup": (236.588, "ml"),
    "milliliters": (1.0, "ml"),
    "milliliter": (1.0, "ml"),
    "ml": (1.0, "ml"),
    "tablespoons": (14.7868, "ml"),
    "tablespoon": (14.7868, "ml"),
    "tbsp": (14.7868, "ml"),
    "teaspoons": (4.92892, "ml"),
    "teaspoon": (4.92892, "ml"),
    "tsp": (4.92892, "ml"),
    "pints": (473.176, "ml"),
    "pint": (473.176, "ml"),
    "pt": (473.176, "ml"),
    "quarts": (946.353, "ml"),
    "quart": (946.353, "ml"),
    "qt": (946.353, "ml"),
    "fluid ounces": (29.5735, "ml"),
    "fluid ounce": (29.5735, "ml"),
    "fl oz": (29.5735, "ml"),
}

_ALL_UNITS: dict[str, tuple[float, str]] = {**_LENGTH, **_WEIGHT, **_VOLUME}

# Canonical display names (prefer plural form)
_DISPLAY: dict[str, str] = {
    "meters": "meters", "m": "meters",
    "kilometers": "kilometers", "km": "kilometers",
    "miles": "miles", "mi": "miles",
    "feet": "feet", "ft": "feet", "foot": "feet",
    "inches": "inches", "in": "inches", "inch": "inches",
    "centimeters": "centimeters", "cm": "centimeters",
    "yards": "yards", "yd": "yards", "yard": "yards",
    "millimeters": "millimeters", "mm": "millimeters",
    "grams": "grams", "g": "grams", "gram": "grams",
    "kilograms": "kilograms", "kg": "kilograms", "kilogram": "kilograms",
    "pounds": "pounds", "lb": "pounds", "lbs": "pounds", "pound": "pounds",
    "ounces": "ounces", "oz": "ounces", "ounce": "ounces",
    "milliliters": "milliliters", "ml": "milliliters", "milliliter": "milliliters",
    "liters": "liters", "l": "liters", "liter": "liters",
    "cups": "cups", "cup": "cups",
    "gallons": "gallons", "gal": "gallons", "gallon": "gallons",
    "tablespoons": "tablespoons", "tbsp": "tablespoons", "tablespoon": "tablespoons",
    "teaspoons": "teaspoons", "tsp": "teaspoons", "teaspoon": "teaspoons",
    "pints": "pints", "pt": "pints", "pint": "pints",
    "quarts": "quarts", "qt": "quarts", "quart": "quarts",
    "fluid ounces": "fluid ounces", "fl oz": "fluid ounces", "fluid ounce": "fluid ounces",
}

# Temperature aliases
_TEMP_ALIASES: dict[str, str] = {
    "fahrenheit": "F", "f": "F", "°f": "F",
    "celsius": "C", "c": "C", "°c": "C",
    "kelvin": "K", "k": "K",
}

# ── Intent detection patterns ────────────────────────────────────

_CONVERT_RE = re.compile(
    r"(?:convert\s+)?(-?[\d,.]+)\s+([a-z\s°]+?)\s+(?:to|in(?:to)?)\s+([a-z\s°]+)",
    re.IGNORECASE,
)

_HOW_MANY_RE = re.compile(
    r"how\s+many\s+([a-z\s°]+?)\s+(?:in|are\s+in)\s+(?:a\s+|an?\s+)?(-?[\d,.]+)?\s*([a-z\s°]+)",
    re.IGNORECASE,
)


def _parse_number(s: str) -> float | None:
    """Parse a number string, handling commas."""
    try:
        return float(s.replace(",", ""))
    except (ValueError, AttributeError):
        return None


def _normalize_unit(raw: str) -> str:
    """Normalize a unit string to lowercase, stripped."""
    return raw.strip().lower().rstrip("s") if len(raw.strip()) > 2 else raw.strip().lower()


def _convert_temperature(value: float, from_unit: str, to_unit: str) -> float | None:
    """Convert between F, C, and K."""
    f = _TEMP_ALIASES.get(from_unit.lower().strip(), "")
    t = _TEMP_ALIASES.get(to_unit.lower().strip(), "")
    if not f or not t:
        return None

    # Convert to Celsius first
    if f == "F":
        c = (value - 32) * 5 / 9
    elif f == "K":
        c = value - 273.15
    else:
        c = value

    # Convert from Celsius to target
    if t == "F":
        return c * 9 / 5 + 32
    elif t == "K":
        return c + 273.15
    else:
        return c


def _convert_units(value: float, from_unit: str, to_unit: str) -> tuple[float, str, str] | None:
    """Convert *value* from *from_unit* to *to_unit*.

    Returns ``(result, from_display, to_display)`` or None.
    """
    fl = from_unit.lower().strip()
    tl = to_unit.lower().strip()

    # Temperature special case
    if fl in _TEMP_ALIASES or tl in _TEMP_ALIASES:
        result = _convert_temperature(value, fl, tl)
        if result is not None:
            fd = _TEMP_ALIASES.get(fl, fl).upper()
            td = _TEMP_ALIASES.get(tl, tl).upper()
            return result, f"°{fd}", f"°{td}"
        return None

    from_entry = _ALL_UNITS.get(fl)
    to_entry = _ALL_UNITS.get(tl)
    if not from_entry or not to_entry:
        return None

    from_factor, from_base = from_entry
    to_factor, to_base = to_entry

    # Units must be in the same category
    if from_base != to_base:
        return None

    result = value * from_factor / to_factor
    return result, _DISPLAY.get(fl, fl), _DISPLAY.get(tl, tl)


def _format_number(n: float) -> str:
    """Format a number nicely — drop trailing zeros."""
    if n == int(n) and abs(n) < 1e12:
        return f"{int(n):,}"
    return f"{n:,.4f}".rstrip("0").rstrip(".")


# ── Plugin class ─────────────────────────────────────────────────

class ConversionPlugin(CortexPlugin):
    """Layer 2 plugin for unit conversions (pure local math)."""

    plugin_id = "conversions"
    display_name = "Unit Conversion"
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
        m = _CONVERT_RE.search(message)
        if m:
            return CommandMatch(
                matched=True,
                intent="convert",
                entities=[m.group(1), m.group(2).strip(), m.group(3).strip()],
                confidence=0.95,
            )
        m = _HOW_MANY_RE.search(message)
        if m:
            to_unit = m.group(1).strip()
            num = m.group(2) or "1"
            from_unit = m.group(3).strip()
            return CommandMatch(
                matched=True,
                intent="convert",
                entities=[num, from_unit, to_unit],
                confidence=0.9,
            )
        return CommandMatch(matched=False)

    async def handle(
        self,
        message: str,
        match: CommandMatch,
        context: dict[str, Any],
    ) -> CommandResult:
        if len(match.entities) < 3:
            return CommandResult(
                success=False,
                response="I need a value and two units to convert. Try: 'convert 5 miles to km'.",
            )

        raw_value, from_unit, to_unit = match.entities[0], match.entities[1], match.entities[2]
        value = _parse_number(raw_value)
        if value is None:
            return CommandResult(
                success=False,
                response=f"I couldn't parse \"{raw_value}\" as a number.",
            )

        result = _convert_units(value, from_unit, to_unit)
        if result is None:
            return CommandResult(
                success=False,
                response=f"I don't know how to convert {from_unit} to {to_unit}.",
            )

        converted, from_display, to_display = result
        text = f"{_format_number(value)} {from_display} = {_format_number(converted)} {to_display}"
        return CommandResult(success=True, response=text)
