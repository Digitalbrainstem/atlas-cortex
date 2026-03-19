"""Generate expression SVG groups and inject them into skin files.

Reads expressions.json + skin SVGs, generates <g id="expr-*"> groups
with correct mouth/eyes/eyebrows positioned at the skin's anchor points,
and writes the updated SVGs back.

Usage:
    python -m cortex.avatar.skins.generate_expressions
    python -m cortex.avatar.skins.generate_expressions --skin default
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

SKINS_DIR = Path(__file__).parent
EXPRESSIONS_FILE = SKINS_DIR / "expressions.json"

# Attributes copied from JSON to SVG elements
_STYLE_ATTRS = ("fill", "stroke", "stroke-width", "stroke-linecap", "opacity")


def load_expressions() -> dict:
    """Load the expression library."""
    return json.loads(EXPRESSIONS_FILE.read_text())


def _get_anchor(
    root: ET.Element,
    anchor_id: str,
    default_cx: float,
    default_cy: float,
) -> tuple[float, float]:
    """Get anchor point coordinates from the SVG."""
    el = root.find(f".//*[@id='{anchor_id}']")
    if el is not None:
        cx = float(el.get("data-cx", str(default_cx)))
        cy = float(el.get("data-cy", str(default_cy)))
        return cx, cy
    return default_cx, default_cy


def _apply_style(el: ET.Element, definition: dict) -> None:
    """Copy style attributes from a JSON definition to an SVG element."""
    for attr in _STYLE_ATTRS:
        json_key = attr.replace("-", "_")
        if json_key in definition:
            el.set(attr, str(definition[json_key]))


def _create_mouth(mouth: dict, cx: float, cy: float, ns: str) -> ET.Element:
    """Create an SVG element for a mouth shape, translated to (cx, cy)."""
    if mouth["type"] == "path":
        el = ET.Element(f"{{{ns}}}path")
        el.set("d", mouth["d"])
        el.set("transform", f"translate({cx},{cy})")
    elif mouth["type"] == "ellipse":
        el = ET.Element(f"{{{ns}}}ellipse")
        el.set("cx", str(cx + mouth.get("cx_offset", 0)))
        el.set("cy", str(cy + mouth.get("cy_offset", 0)))
        el.set("rx", str(mouth.get("rx", 10)))
        el.set("ry", str(mouth.get("ry", 10)))
    else:
        raise ValueError(f"Unknown mouth type: {mouth['type']}")

    _apply_style(el, mouth)
    return el


def _create_eye(eye: dict, anchor_cx: float, anchor_cy: float, ns: str) -> ET.Element:
    """Create an SVG element for an eye, offset from anchor."""
    cx = anchor_cx + eye.get("cx_offset", 0)
    cy = anchor_cy + eye.get("cy_offset", 0)

    if eye["type"] == "path":
        el = ET.Element(f"{{{ns}}}path")
        el.set("d", eye["d"])
        el.set("transform", f"translate({cx},{cy})")
    elif eye["type"] == "ellipse":
        el = ET.Element(f"{{{ns}}}ellipse")
        el.set("cx", str(cx))
        el.set("cy", str(cy))
        el.set("rx", str(eye.get("rx", 10)))
        el.set("ry", str(eye.get("ry", 10)))
    else:
        raise ValueError(f"Unknown eye type: {eye['type']}")

    _apply_style(el, eye)
    return el


def _create_eyebrow(brow: dict, anchor_cx: float, anchor_cy: float, ns: str) -> ET.Element:
    """Create an SVG element for an eyebrow."""
    el = ET.Element(f"{{{ns}}}path")
    el.set("d", brow["d"])
    el.set("transform", f"translate({anchor_cx},{anchor_cy})")

    # Default eyebrow style
    defaults = {
        "stroke": "#333",
        "stroke-width": "3.5",
        "fill": "none",
        "stroke-linecap": "round",
    }
    for attr, default in defaults.items():
        json_key = attr.replace("-", "_")
        el.set(attr, str(brow.get(json_key, default)))

    if "opacity" in brow:
        el.set("opacity", str(brow["opacity"]))

    return el


def inject_expressions(svg_path: Path) -> int:
    """Read a skin SVG, remove old expressions, inject new ones from library.

    Returns the number of expressions injected.
    """
    ns = "http://www.w3.org/2000/svg"
    ET.register_namespace("", ns)
    ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")

    tree = ET.parse(svg_path)
    root = tree.getroot()
    lib = load_expressions()

    # Anchor positions
    mouth_cx, mouth_cy = _get_anchor(root, "mouth-anchor", 200, 280)
    eyes_cx, eyes_cy = _get_anchor(root, "eyes-anchor", 200, 170)
    brows_cx, brows_cy = _get_anchor(root, "eyebrows-anchor", 200, 148)

    # Remove ALL existing expr-* groups (except neutral/listening which are no-ops)
    parent_map = {c: p for p in root.iter() for c in p}
    for el in list(root.iter()):
        eid = el.get("id", "")
        if eid.startswith("expr-"):
            parent = parent_map.get(el)
            if parent is not None:
                parent.remove(el)

    # Find insertion point — before #blink if it exists
    blink = root.find(f".//*[@id='blink']")
    blink_parent = None
    blink_index = None
    if blink is not None:
        blink_parent = parent_map.get(blink)
        if blink_parent is not None:
            blink_index = list(blink_parent).index(blink)

    injected = 0
    for name, expr in lib["expressions"].items():
        # neutral and listening use the default face — add empty groups
        if name in ("neutral", "listening"):
            g = ET.SubElement(root, f"{{{ns}}}g")
            g.set("id", f"expr-{name}")
            g.set("style", "display:none")
            injected += 1
            continue

        if not expr.get("replace_mouth") and not expr.get("replace_eyes"):
            continue

        g = ET.Element(f"{{{ns}}}g")
        g.set("id", f"expr-{name}")
        g.set("style", "display:none")

        if expr.get("replace_mouth"):
            g.set("data-replace-mouth", "true")
        if expr.get("replace_eyes"):
            g.set("data-replace-eyes", "true")

        # Mouth
        if expr.get("replace_mouth") and "mouth" in expr:
            g.append(_create_mouth(expr["mouth"], mouth_cx, mouth_cy, ns))

        # Eyes
        if expr.get("replace_eyes") and "eyes" in expr:
            eyes = expr["eyes"]
            for side in ("left", "right"):
                if side in eyes:
                    g.append(_create_eye(eyes[side], eyes_cx, eyes_cy, ns))

        # Eyebrows
        if "eyebrows" in expr:
            brows = expr["eyebrows"]
            for side in ("left", "right"):
                if side in brows:
                    g.append(_create_eyebrow(brows[side], brows_cx, brows_cy, ns))

        # Insert before #blink if possible, else append to root
        if blink_parent is not None and blink_index is not None:
            blink_parent.insert(blink_index, g)
            blink_index += 1
        else:
            root.append(g)

        injected += 1

    tree.write(svg_path, xml_declaration=True, encoding="unicode")
    print(f"  ✓ {svg_path.name}: {injected} expressions injected")
    return injected


def main() -> None:
    """Regenerate expressions in all skin SVGs."""
    import sys

    skin_filter = None
    if "--skin" in sys.argv:
        idx = sys.argv.index("--skin")
        if idx + 1 < len(sys.argv):
            skin_filter = sys.argv[idx + 1]

    print("Generating avatar expressions...")
    for svg_file in sorted(SKINS_DIR.glob("*.svg")):
        if skin_filter and skin_filter not in svg_file.stem:
            continue
        inject_expressions(svg_file)
    print("Done ✓")


if __name__ == "__main__":
    main()
