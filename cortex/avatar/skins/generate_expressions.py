"""Generate expression SVG groups derived from each skin's base face geometry.

Parses mouth-IDLE and eyes-base from each skin SVG to extract actual
dimensions (center, width, radii, stroke style), then generates expression
shapes using ratios from expressions.json.  The same expression definition
produces correctly proportioned results on skins of different sizes/styles.

Usage:
    python -m cortex.avatar.skins.generate_expressions
    python -m cortex.avatar.skins.generate_expressions --skin default
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

SKINS_DIR = Path(__file__).parent
EXPRESSIONS_FILE = SKINS_DIR / "expressions.json"

# Mouth shapes that use an ellipse element instead of a path
_ELLIPSE_MOUTH_SHAPES = {"open_o", "pucker", "small_o"}

# All recognised mouth shape names
_MOUTH_SHAPES = {
    "smile", "frown", "open_o", "smirk", "wavy", "big_grin",
    "open_frown", "side", "pucker", "small_o",
}

# All recognised eye shape names
_EYE_SHAPES = {
    "squint", "wide", "closed", "wink", "narrow", "droopy",
    "look_up", "asymmetric", "asymmetric_size", "nearly_closed",
    "slightly_wide", "confident", "dreamy",
}

# All recognised eyebrow shape names
_BROW_SHAPES = {
    "raised", "raised_high", "worry", "v_furrowed", "slight_furrow",
    "asymmetric", "asymmetric_brow", "one_raised", "soft",
    "relaxed_low", "wavy_brow",
}


def load_expressions() -> dict:
    """Load the expression library."""
    return json.loads(EXPRESSIONS_FILE.read_text())


# ── Base geometry extraction ──────────────────────────────────────

def _parse_path_mq(d: str) -> dict | None:
    """Parse a simple ``M x1 y1 Q cx cy x2 y2`` path string."""
    m = re.match(
        r"M\s*([\d.+-]+)\s+([\d.+-]+)\s+Q\s*([\d.+-]+)\s+([\d.+-]+)\s+([\d.+-]+)\s+([\d.+-]+)",
        d.strip(),
    )
    if not m:
        return None
    x1, y1, qx, qy, x2, y2 = (float(v) for v in m.groups())
    return {"x1": x1, "y1": y1, "qx": qx, "qy": qy, "x2": x2, "y2": y2}


def derive_mouth_geometry(root: ET.Element) -> dict:
    """Extract mouth centre, width, curve height and stroke style from mouth-IDLE."""
    ns = "http://www.w3.org/2000/svg"
    idle = root.find(f".//*[@id='mouth-IDLE']")
    if idle is None:
        raise ValueError("SVG missing mouth-IDLE group")

    path_el = idle.find(f"{{{ns}}}path")
    if path_el is None:
        path_el = idle.find("path")
    if path_el is None:
        raise ValueError("mouth-IDLE has no <path> child")

    parsed = _parse_path_mq(path_el.get("d", ""))
    if parsed is None:
        raise ValueError(f"Cannot parse mouth-IDLE path: {path_el.get('d')}")

    cx = (parsed["x1"] + parsed["x2"]) / 2
    cy = (parsed["y1"] + parsed["y2"]) / 2
    half_width = (parsed["x2"] - parsed["x1"]) / 2
    curve_height = parsed["qy"] - cy

    return {
        "cx": cx,
        "cy": cy,
        "half_width": half_width,
        "curve_height": curve_height,
        "stroke": path_el.get("stroke", "#333"),
        "stroke_width": float(path_el.get("stroke-width", "4")),
        "stroke_linecap": path_el.get("stroke-linecap", "round"),
    }


def derive_eye_geometry(root: ET.Element) -> dict:
    """Extract eye positions and radii from eyes-base."""
    ns = "http://www.w3.org/2000/svg"
    eyes_g = root.find(f".//*[@id='eyes-base']")
    if eyes_g is None:
        raise ValueError("SVG missing eyes-base group")

    ellipses = eyes_g.findall(f"{{{ns}}}ellipse")
    if not ellipses:
        ellipses = eyes_g.findall("ellipse")

    if len(ellipses) < 2:
        raise ValueError("eyes-base needs at least 2 ellipses (left + right eye whites)")

    # The two largest-rx ellipses are the eye whites
    by_rx = sorted(ellipses, key=lambda e: float(e.get("rx", "0")), reverse=True)
    pair = sorted(by_rx[:2], key=lambda e: float(e.get("cx", "0")))
    left_el, right_el = pair

    left_cx = float(left_el.get("cx", "0"))
    left_cy = float(left_el.get("cy", "0"))
    left_rx = float(left_el.get("rx", "0"))
    left_ry = float(left_el.get("ry", "0"))
    left_fill = left_el.get("fill", "#fff")
    left_stroke = left_el.get("stroke", "")

    right_cx = float(right_el.get("cx", "0"))
    right_cy = float(right_el.get("cy", "0"))
    right_rx = float(right_el.get("rx", "0"))
    right_ry = float(right_el.get("ry", "0"))

    rx = (left_rx + right_rx) / 2
    ry = (left_ry + right_ry) / 2
    spacing = right_cx - left_cx

    return {
        "left_cx": left_cx,
        "left_cy": left_cy,
        "right_cx": right_cx,
        "right_cy": right_cy,
        "rx": rx,
        "ry": ry,
        "spacing": spacing,
        "fill": left_fill,
        "stroke": left_stroke,
    }


# ── Shape generators ─────────────────────────────────────────────

def _fmt(v: float) -> str:
    """Format a float nicely for SVG: drop trailing .0, round to 1 decimal."""
    r = round(v, 1)
    return f"{r:g}"


def generate_mouth_element(
    base: dict,
    expr: dict,
    ns: str,
) -> ET.Element:
    """Generate an SVG element for an expression mouth."""
    shape = expr.get("shape", "smile")
    cx = base["cx"]
    cy = base["cy"]
    hw = base["half_width"] * expr.get("width_ratio", 1.0)
    ch = abs(base["curve_height"]) * expr.get("curve_ratio", 0.3)
    stroke = base["stroke"]
    sw = base["stroke_width"]
    linecap = base["stroke_linecap"]

    # Ellipse-based mouths
    if shape in _ELLIPSE_MOUTH_SHAPES:
        el = ET.Element(f"{{{ns}}}ellipse")
        rx = base["half_width"] * expr.get("rx_ratio", 0.2)
        ry = base["half_width"] * expr.get("ry_ratio", 0.25)
        el.set("cx", _fmt(cx))
        el.set("cy", _fmt(cy))
        el.set("rx", _fmt(rx))
        el.set("ry", _fmt(ry))
        fill = expr.get("fill", stroke)
        el.set("fill", fill)
        if shape == "small_o":
            el.set("opacity", "0.5")
        elif shape == "pucker":
            el.set("opacity", "0.8")
        else:
            el.set("opacity", "0.85")
        return el

    # Path-based mouths
    el = ET.Element(f"{{{ns}}}path")
    el.set("fill", expr.get("fill", "none"))
    el.set("stroke", stroke)
    el.set("stroke-width", _fmt(sw))
    el.set("stroke-linecap", linecap)

    if shape == "smile":
        d = f"M{_fmt(cx - hw)} {_fmt(cy)} Q{_fmt(cx)} {_fmt(cy + ch)} {_fmt(cx + hw)} {_fmt(cy)}"
    elif shape == "frown":
        d = f"M{_fmt(cx - hw)} {_fmt(cy)} Q{_fmt(cx)} {_fmt(cy - ch)} {_fmt(cx + hw)} {_fmt(cy)}"
    elif shape == "smirk":
        d = (
            f"M{_fmt(cx - hw)} {_fmt(cy + 2)} "
            f"Q{_fmt(cx)} {_fmt(cy)} {_fmt(cx + hw)} {_fmt(cy - ch * 0.5)}"
        )
    elif shape == "wavy":
        qw = hw * 0.4
        d = (
            f"M{_fmt(cx - hw)} {_fmt(cy)} "
            f"Q{_fmt(cx - qw)} {_fmt(cy - ch * 0.6)} {_fmt(cx)} {_fmt(cy)} "
            f"Q{_fmt(cx + qw)} {_fmt(cy + ch * 0.6)} {_fmt(cx + hw)} {_fmt(cy)}"
        )
    elif shape == "big_grin":
        el.set("stroke-width", _fmt(sw * 0.75))
        d = (
            f"M{_fmt(cx - hw)} {_fmt(cy - 2)} "
            f"Q{_fmt(cx)} {_fmt(cy + ch * 1.5)} {_fmt(cx + hw)} {_fmt(cy - 2)} Z"
        )
    elif shape == "open_frown":
        w = hw * 0.8
        d = (
            f"M{_fmt(cx - w)} {_fmt(cy + ch * 0.3)} "
            f"Q{_fmt(cx)} {_fmt(cy - ch)} {_fmt(cx + w)} {_fmt(cy + ch * 0.3)} "
            f"Q{_fmt(cx)} {_fmt(cy + ch * 0.8)} {_fmt(cx - w)} {_fmt(cy + ch * 0.3)} Z"
        )
        el.set("fill", stroke)
        el.set("opacity", "0.65")
    elif shape == "side":
        ox = base["half_width"] * expr.get("offset_x", 0.15)
        d = (
            f"M{_fmt(cx - hw * 0.5 + ox)} {_fmt(cy + 2)} "
            f"Q{_fmt(cx + ox - hw * 0.1)} {_fmt(cy - ch * 0.3)} "
            f"{_fmt(cx + hw * 0.6 + ox)} {_fmt(cy)}"
        )
    else:
        d = f"M{_fmt(cx - hw)} {_fmt(cy)} Q{_fmt(cx)} {_fmt(cy + ch)} {_fmt(cx + hw)} {_fmt(cy)}"

    el.set("d", d)
    return el


def generate_eye_elements(
    base_eyes: dict,
    expr: dict,
    ns: str,
) -> list[ET.Element]:
    """Generate SVG elements for expression eyes."""
    shape = expr.get("shape", "normal")
    lcx = base_eyes["left_cx"]
    lcy = base_eyes["left_cy"]
    rcx = base_eyes["right_cx"]
    rcy = base_eyes["right_cy"]
    rx = base_eyes["rx"]
    ry = base_eyes["ry"]
    fill = base_eyes["fill"]
    stroke_col = base_eyes["stroke"] or fill

    default_ry_ratio = expr.get("ry_ratio", 1.0)
    elements: list[ET.Element] = []

    def _ellipse(cx: float, cy: float, erx: float, ery: float, efill: str = fill) -> ET.Element:
        el = ET.Element(f"{{{ns}}}ellipse")
        el.set("cx", _fmt(cx))
        el.set("cy", _fmt(cy))
        el.set("rx", _fmt(erx))
        el.set("ry", _fmt(ery))
        el.set("fill", efill)
        return el

    def _closed_line(cx: float, cy: float, erx: float, stroke: str = stroke_col, sw: float = 4) -> ET.Element:
        el = ET.Element(f"{{{ns}}}path")
        el.set("d", f"M{_fmt(cx - erx)} {_fmt(cy)} Q{_fmt(cx)} {_fmt(cy - erx * 0.35)} {_fmt(cx + erx)} {_fmt(cy)}")
        el.set("fill", "none")
        el.set("stroke", stroke)
        el.set("stroke-width", _fmt(sw))
        el.set("stroke-linecap", "round")
        return el

    if shape == "squint":
        ery = ry * default_ry_ratio
        elements.append(_ellipse(lcx, lcy, rx, ery))
        elements.append(_ellipse(rcx, rcy, rx, ery))

    elif shape == "narrow":
        ery = ry * default_ry_ratio
        elements.append(_ellipse(lcx, lcy, rx * 0.95, ery))
        elements.append(_ellipse(rcx, rcy, rx * 0.95, ery))

    elif shape == "droopy":
        ery = ry * default_ry_ratio
        elements.append(_ellipse(lcx, lcy + 2, rx, ery))
        elements.append(_ellipse(rcx, rcy + 2, rx, ery))

    elif shape == "wide":
        ery = ry * default_ry_ratio
        elements.append(_ellipse(lcx, lcy, rx, ery))
        elements.append(_ellipse(rcx, rcy, rx, ery))

    elif shape == "slightly_wide":
        ery = ry * default_ry_ratio
        elements.append(_ellipse(lcx, lcy, rx, ery))
        elements.append(_ellipse(rcx, rcy, rx, ery))

    elif shape == "closed":
        elements.append(_closed_line(lcx, lcy, rx))
        elements.append(_closed_line(rcx, rcy, rx))

    elif shape == "wink":
        elements.append(_closed_line(lcx, lcy, rx))
        elements.append(_ellipse(rcx, rcy, rx, ry))

    elif shape == "nearly_closed":
        ery = ry * default_ry_ratio
        elements.append(_ellipse(lcx, lcy + 2, rx, ery))
        elements.append(_ellipse(rcx, rcy + 2, rx, ery))

    elif shape == "look_up":
        ery = ry * default_ry_ratio
        dy = expr.get("pupil_dy", -3)
        elements.append(_ellipse(lcx, lcy + dy, rx, ery))
        elements.append(_ellipse(rcx, rcy + dy, rx, ery))

    elif shape == "confident":
        ery = ry * default_ry_ratio
        elements.append(_ellipse(lcx, lcy, rx, ery))
        elements.append(_ellipse(rcx, rcy, rx, ery))

    elif shape == "dreamy":
        ery = ry * default_ry_ratio
        elements.append(_ellipse(lcx, lcy + 1, rx, ery))
        elements.append(_ellipse(rcx, rcy + 1, rx, ery))

    elif shape == "asymmetric":
        left_ry = ry * expr.get("left_ry_ratio", 0.9)
        right_ry = ry * expr.get("right_ry_ratio", 0.7)
        elements.append(_ellipse(lcx, lcy, rx, left_ry))
        elements.append(_ellipse(rcx, rcy, rx * 0.9, right_ry))

    elif shape == "asymmetric_size":
        left_ry = ry * expr.get("left_ry_ratio", 1.2)
        right_ry = ry * expr.get("right_ry_ratio", 0.65)
        elements.append(_ellipse(lcx, lcy, rx * 1.05, left_ry))
        elements.append(_ellipse(rcx, rcy, rx * 0.85, right_ry))

    else:
        elements.append(_ellipse(lcx, lcy, rx, ry))
        elements.append(_ellipse(rcx, rcy, rx, ry))

    return elements


def generate_eyebrow_elements(
    base_eyes: dict,
    base_mouth: dict,
    expr: dict,
    ns: str,
) -> list[ET.Element]:
    """Generate SVG elements for expression eyebrows."""
    shape = expr.get("shape", "raised")
    lcx = base_eyes["left_cx"]
    lcy = base_eyes["left_cy"]
    rcx = base_eyes["right_cx"]
    rcy = base_eyes["right_cy"]
    rx = base_eyes["rx"]
    ry = base_eyes["ry"]

    brow_y = lcy - ry - 6
    brow_span = rx * 0.9
    stroke = base_mouth["stroke"]
    sw = base_mouth["stroke_width"] * 0.65

    dy = expr.get("dy", 0)
    elements: list[ET.Element] = []

    def _brow_path(d: str, extra_sw: float = 0) -> ET.Element:
        el = ET.Element(f"{{{ns}}}path")
        el.set("d", d)
        el.set("fill", "none")
        el.set("stroke", stroke)
        el.set("stroke-width", _fmt(sw + extra_sw))
        el.set("stroke-linecap", "round")
        return el

    if shape == "raised":
        curve = expr.get("curve", 0.15)
        arc = brow_span * curve * 4
        y = brow_y + dy
        elements.append(_brow_path(
            f"M{_fmt(lcx - brow_span)} {_fmt(y)} Q{_fmt(lcx)} {_fmt(y - arc)} {_fmt(lcx + brow_span)} {_fmt(y)}"
        ))
        elements.append(_brow_path(
            f"M{_fmt(rcx - brow_span)} {_fmt(y)} Q{_fmt(rcx)} {_fmt(y - arc)} {_fmt(rcx + brow_span)} {_fmt(y)}"
        ))

    elif shape == "raised_high":
        arc = brow_span * 0.5
        y = brow_y + dy
        elements.append(_brow_path(
            f"M{_fmt(lcx - brow_span)} {_fmt(y)} Q{_fmt(lcx)} {_fmt(y - arc)} {_fmt(lcx + brow_span)} {_fmt(y)}"
        ))
        elements.append(_brow_path(
            f"M{_fmt(rcx - brow_span)} {_fmt(y)} Q{_fmt(rcx)} {_fmt(y - arc)} {_fmt(rcx + brow_span)} {_fmt(y)}"
        ))

    elif shape == "worry":
        idy = expr.get("inner_dy", -8)
        ody = expr.get("outer_dy", 3)
        y = brow_y + dy
        elements.append(_brow_path(
            f"M{_fmt(lcx - brow_span)} {_fmt(y + ody)} "
            f"Q{_fmt(lcx)} {_fmt(y + idy)} {_fmt(lcx + brow_span)} {_fmt(y)}"
        ))
        elements.append(_brow_path(
            f"M{_fmt(rcx - brow_span)} {_fmt(y)} "
            f"Q{_fmt(rcx)} {_fmt(y + idy)} {_fmt(rcx + brow_span)} {_fmt(y + ody)}"
        ))

    elif shape == "v_furrowed":
        y = brow_y + dy
        elements.append(_brow_path(
            f"M{_fmt(lcx - brow_span)} {_fmt(y - 4)} "
            f"Q{_fmt(lcx)} {_fmt(y - 10)} {_fmt(lcx + brow_span)} {_fmt(y + 2)}",
            extra_sw=0.5,
        ))
        elements.append(_brow_path(
            f"M{_fmt(rcx - brow_span)} {_fmt(y + 2)} "
            f"Q{_fmt(rcx)} {_fmt(y - 10)} {_fmt(rcx + brow_span)} {_fmt(y - 4)}",
            extra_sw=0.5,
        ))

    elif shape == "slight_furrow":
        y = brow_y + dy
        elements.append(_brow_path(
            f"M{_fmt(lcx - brow_span)} {_fmt(y)} "
            f"Q{_fmt(lcx)} {_fmt(y - 4)} {_fmt(lcx + brow_span)} {_fmt(y + 2)}"
        ))
        elements.append(_brow_path(
            f"M{_fmt(rcx - brow_span)} {_fmt(y + 2)} "
            f"Q{_fmt(rcx)} {_fmt(y - 4)} {_fmt(rcx + brow_span)} {_fmt(y)}"
        ))

    elif shape in ("asymmetric", "asymmetric_brow"):
        y = brow_y + dy
        arc = brow_span * 0.3
        elements.append(_brow_path(
            f"M{_fmt(lcx - brow_span)} {_fmt(y)} Q{_fmt(lcx)} {_fmt(y - arc)} {_fmt(lcx + brow_span)} {_fmt(y)}"
        ))
        elements.append(_brow_path(
            f"M{_fmt(rcx - brow_span)} {_fmt(y)} Q{_fmt(rcx)} {_fmt(y - arc * 1.8)} {_fmt(rcx + brow_span)} {_fmt(y - 2)}"
        ))

    elif shape == "one_raised":
        y = brow_y + dy
        arc_lo = brow_span * 0.2
        arc_hi = brow_span * 0.55
        elements.append(_brow_path(
            f"M{_fmt(lcx - brow_span)} {_fmt(y)} Q{_fmt(lcx)} {_fmt(y - arc_lo)} {_fmt(lcx + brow_span)} {_fmt(y)}"
        ))
        elements.append(_brow_path(
            f"M{_fmt(rcx - brow_span)} {_fmt(y)} Q{_fmt(rcx)} {_fmt(y - arc_hi)} {_fmt(rcx + brow_span)} {_fmt(y - 2)}"
        ))

    elif shape == "soft":
        y = brow_y + dy
        arc = brow_span * 0.25
        elements.append(_brow_path(
            f"M{_fmt(lcx - brow_span)} {_fmt(y)} Q{_fmt(lcx)} {_fmt(y - arc)} {_fmt(lcx + brow_span)} {_fmt(y)}"
        ))
        elements.append(_brow_path(
            f"M{_fmt(rcx - brow_span)} {_fmt(y)} Q{_fmt(rcx)} {_fmt(y - arc)} {_fmt(rcx + brow_span)} {_fmt(y)}"
        ))

    elif shape == "relaxed_low":
        y = brow_y + dy
        arc = brow_span * 0.12
        elements.append(_brow_path(
            f"M{_fmt(lcx - brow_span)} {_fmt(y)} Q{_fmt(lcx)} {_fmt(y - arc)} {_fmt(lcx + brow_span)} {_fmt(y)}"
        ))
        elements.append(_brow_path(
            f"M{_fmt(rcx - brow_span)} {_fmt(y)} Q{_fmt(rcx)} {_fmt(y - arc)} {_fmt(rcx + brow_span)} {_fmt(y)}"
        ))

    elif shape == "wavy_brow":
        y = brow_y + dy
        seg = brow_span * 0.5
        amp = brow_span * 0.25
        elements.append(_brow_path(
            f"M{_fmt(lcx - brow_span)} {_fmt(y)} "
            f"Q{_fmt(lcx - seg)} {_fmt(y - amp)} {_fmt(lcx)} {_fmt(y)} "
            f"Q{_fmt(lcx + seg)} {_fmt(y + amp * 0.5)} {_fmt(lcx + brow_span)} {_fmt(y - 2)}"
        ))
        elements.append(_brow_path(
            f"M{_fmt(rcx - brow_span)} {_fmt(y + 1)} "
            f"Q{_fmt(rcx - seg)} {_fmt(y - amp * 0.6)} {_fmt(rcx)} {_fmt(y)} "
            f"Q{_fmt(rcx + seg)} {_fmt(y + amp * 0.3)} {_fmt(rcx + brow_span)} {_fmt(y)}"
        ))

    else:
        # Fallback: gentle arch
        y = brow_y + dy
        arc = brow_span * 0.2
        elements.append(_brow_path(
            f"M{_fmt(lcx - brow_span)} {_fmt(y)} Q{_fmt(lcx)} {_fmt(y - arc)} {_fmt(lcx + brow_span)} {_fmt(y)}"
        ))
        elements.append(_brow_path(
            f"M{_fmt(rcx - brow_span)} {_fmt(y)} Q{_fmt(rcx)} {_fmt(y - arc)} {_fmt(rcx + brow_span)} {_fmt(y)}"
        ))

    return elements


# ── SVG injection ─────────────────────────────────────────────────

def inject_expressions(svg_path: Path) -> int:
    """Read a skin SVG, derive base geometry, inject expression groups.

    Returns the number of expressions injected.
    """
    ns = "http://www.w3.org/2000/svg"
    ET.register_namespace("", ns)
    ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")

    tree = ET.parse(svg_path)
    root = tree.getroot()
    lib = load_expressions()

    # Derive base face geometry from this skin
    base_mouth = derive_mouth_geometry(root)
    base_eyes = derive_eye_geometry(root)

    # Remove ALL existing expr-* groups
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
        # neutral and listening use the default face
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

        # Mouth (derived from base geometry)
        if expr.get("replace_mouth") and "mouth" in expr:
            g.append(generate_mouth_element(base_mouth, expr["mouth"], ns))

        # Eyes (derived from base geometry)
        if expr.get("replace_eyes") and "eyes" in expr:
            for eye_el in generate_eye_elements(base_eyes, expr["eyes"], ns):
                g.append(eye_el)

        # Eyebrows (derived from base geometry)
        if "eyebrows" in expr:
            for brow_el in generate_eyebrow_elements(base_eyes, base_mouth, expr["eyebrows"], ns):
                g.append(brow_el)

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
