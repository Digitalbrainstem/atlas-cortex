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
    "open_frown", "side", "pucker", "small_o", "tongue_out",
}

# All recognised eye shape names
_EYE_SHAPES = {
    "squint", "wide", "closed", "wink", "narrow", "droopy",
    "look_up", "asymmetric", "asymmetric_size", "nearly_closed",
    "slightly_wide", "confident", "dreamy", "hearts",
    "small_intense", "joy", "droopy_small", "crying",
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
    """Extract eye positions, radii, and pupils from eyes-base."""
    ns = "http://www.w3.org/2000/svg"
    eyes_g = root.find(f".//*[@id='eyes-base']")
    if eyes_g is None:
        raise ValueError("SVG missing eyes-base group")

    children = list(eyes_g)
    if len(children) < 4:
        raise ValueError("eyes-base needs at least 4 children (white+pupil × 2)")

    # Parse pairs: [white, pupil] × 2 (glints removed)
    def _parse_eye(pair: list[ET.Element]) -> dict:
        white_el, pupil_el = pair
        result = {
            "white_cx": float(white_el.get("cx", "0")),
            "white_cy": float(white_el.get("cy", "0")),
            "white_rx": float(white_el.get("rx", white_el.get("r", "0"))),
            "white_ry": float(white_el.get("ry", white_el.get("r", "0"))),
            "white_fill": white_el.get("fill", "#fff"),
            "pupil_cx": float(pupil_el.get("cx", "0")),
            "pupil_cy": float(pupil_el.get("cy", "0")),
            "pupil_fill": pupil_el.get("fill", "#2D2D2D"),
        }
        if pupil_el.get("rx") is not None:
            result["pupil_r"] = float(pupil_el.get("rx"))
            result["pupil_ry"] = float(pupil_el.get("ry", pupil_el.get("rx")))
        else:
            result["pupil_r"] = float(pupil_el.get("r", "0"))
            result["pupil_ry"] = result["pupil_r"]
        if pupil_el.get("opacity"):
            result["pupil_opacity"] = pupil_el.get("opacity")
        return result

    # Split children into two eyes (first half = left, second half = right)
    half = len(children) // 2
    left_data = _parse_eye(children[0:2])
    right_data = _parse_eye(children[half:half+2])

    # Backward-compatible top-level keys
    rx = (left_data["white_rx"] + right_data["white_rx"]) / 2
    ry = (left_data["white_ry"] + right_data["white_ry"]) / 2
    spacing = right_data["white_cx"] - left_data["white_cx"]

    return {
        "left_cx": left_data["white_cx"],
        "left_cy": left_data["white_cy"],
        "right_cx": right_data["white_cx"],
        "right_cy": right_data["white_cy"],
        "rx": rx,
        "ry": ry,
        "spacing": spacing,
        "fill": left_data["white_fill"],
        "stroke": children[0].get("stroke", ""),
        "left": left_data,
        "right": right_data,
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
    elif shape == "tongue_out":
        d = f"M{_fmt(cx - hw)} {_fmt(cy)} Q{_fmt(cx)} {_fmt(cy + ch)} {_fmt(cx + hw)} {_fmt(cy)}"
    else:
        d = f"M{_fmt(cx - hw)} {_fmt(cy)} Q{_fmt(cx)} {_fmt(cy + ch)} {_fmt(cx + hw)} {_fmt(cy)}"

    el.set("d", d)
    return el


def generate_eye_elements(
    base_eyes: dict,
    expr: dict,
    ns: str,
) -> list[ET.Element]:
    """Generate SVG elements for expression eyes (whites + pupils + glints)."""
    shape = expr.get("shape", "normal")

    # Fall back to legacy behavior if per-eye data is missing
    if "left" not in base_eyes:
        return _generate_eye_elements_legacy(base_eyes, expr, ns)

    elements: list[ET.Element] = []

    def _closed_line(cx: float, cy: float, erx: float, stroke: str, sw: float = 4) -> ET.Element:
        el = ET.Element(f"{{{ns}}}path")
        el.set("d", f"M{_fmt(cx - erx)} {_fmt(cy)} Q{_fmt(cx)} {_fmt(cy - erx * 0.35)} {_fmt(cx + erx)} {_fmt(cy)}")
        el.set("fill", "none")
        el.set("stroke", stroke)
        el.set("stroke-width", _fmt(sw))
        el.set("stroke-linecap", "round")
        return el

    default_ry_ratio = expr.get("ry_ratio", 1.0)
    closed_stroke = base_eyes.get("stroke") or base_eyes.get("left", {}).get("pupil_fill", "#333")
    if not closed_stroke or closed_stroke == "white":
        closed_stroke = "#333"  # Never draw closed eyes in white!

    for side_name, side_data in [("left", base_eyes["left"]), ("right", base_eyes["right"])]:
        wcx = side_data["white_cx"]
        wcy = side_data["white_cy"]
        wrx = side_data["white_rx"]
        wry = side_data["white_ry"]
        wfill = side_data["white_fill"]
        pcx = side_data["pupil_cx"]
        pcy = side_data["pupil_cy"]
        pr = side_data["pupil_r"]
        pry = side_data.get("pupil_ry", pr)
        pfill = side_data["pupil_fill"]
        p_opacity = side_data.get("pupil_opacity")

        # Determine ry_ratio for this side
        ry_ratio = default_ry_ratio
        if shape in ("asymmetric", "asymmetric_size"):
            if side_name == "left":
                ry_ratio = expr.get("left_ry_ratio", ry_ratio)
            else:
                ry_ratio = expr.get("right_ry_ratio", ry_ratio)

        # Determine rx scale — from shape-specific defaults or expression config
        rx_scale = expr.get("rx_ratio", 1.0)
        if shape == "narrow":
            rx_scale = min(rx_scale, 0.95)
        elif shape == "asymmetric_size":
            rx_scale = 1.05 if side_name == "left" else 0.85
        elif shape == "asymmetric":
            rx_scale = 1.0 if side_name == "left" else 0.9

        # Determine cy offset
        cy_shift = 0
        if shape in ("droopy", "droopy_small"):
            cy_shift = 2
        elif shape == "dreamy":
            cy_shift = 1
        elif shape == "look_up":
            cy_shift = expr.get("pupil_dy", -3)

        # Nearly closed uses curved lines (not flat ovals)
        is_closed = (shape == "closed") or (shape == "nearly_closed") or (shape == "wink" and side_name == "left")
        is_hearts = (shape == "hearts")

        if is_hearts:
            # Heart-shaped pupils for love expression
            # Draw eye white + heart instead of pupil + no glint
            new_ry = wry * ry_ratio
            white_el = ET.Element(f"{{{ns}}}ellipse")
            white_el.set("cx", _fmt(wcx))
            white_el.set("cy", _fmt(wcy))
            white_el.set("rx", _fmt(wrx * rx_scale))
            white_el.set("ry", _fmt(new_ry))
            white_el.set("fill", wfill)
            elements.append(white_el)
            # Heart path centered on pupil position
            hs = pr * 0.8  # heart size based on pupil
            hx, hy = pcx, pcy
            heart = ET.Element(f"{{{ns}}}path")
            heart.set("d",
                f"M{_fmt(hx)} {_fmt(hy + hs * 0.4)} "
                f"C{_fmt(hx - hs)} {_fmt(hy - hs * 0.2)} {_fmt(hx - hs * 0.5)} {_fmt(hy - hs * 0.9)} {_fmt(hx)} {_fmt(hy - hs * 0.4)} "
                f"C{_fmt(hx + hs * 0.5)} {_fmt(hy - hs * 0.9)} {_fmt(hx + hs)} {_fmt(hy - hs * 0.2)} {_fmt(hx)} {_fmt(hy + hs * 0.4)} Z"
            )
            heart.set("fill", "#E74C3C")
            heart.set("opacity", "0.9")
            elements.append(heart)
        elif is_closed:
            elements.append(_closed_line(wcx, wcy + cy_shift, wrx * rx_scale, closed_stroke))
        else:
            new_ry = wry * ry_ratio
            actual_cy = wcy + cy_shift

            # White
            white_el = ET.Element(f"{{{ns}}}ellipse")
            white_el.set("cx", _fmt(wcx))
            white_el.set("cy", _fmt(actual_cy))
            white_el.set("rx", _fmt(wrx * rx_scale))
            white_el.set("ry", _fmt(new_ry))
            white_el.set("fill", wfill)
            elements.append(white_el)

            # Pupil — scale proportionally with eye size
            pupil_offset_y = (pcy - wcy) * ry_ratio
            pupil_cy = actual_cy + pupil_offset_y
            # Scale pupil radius so it fits inside the eye white
            pupil_scale = min(ry_ratio, rx_scale)  # shrink pupil with eye
            scaled_pr = pr * max(pupil_scale, 0.3)  # minimum 30% of original
            scaled_pry = pry * max(pupil_scale, 0.3)
            # Ensure pupil fits inside eye white
            max_pupil = min(new_ry * 0.85, wrx * rx_scale * 0.7)
            scaled_pr = min(scaled_pr, max_pupil)
            scaled_pry = min(scaled_pry, max_pupil)
            if pry != pr:
                # Ellipse pupil (nick style)
                pupil_el = ET.Element(f"{{{ns}}}ellipse")
                pupil_el.set("cx", _fmt(pcx))
                pupil_el.set("cy", _fmt(pupil_cy))
                pupil_el.set("rx", _fmt(scaled_pr))
                pupil_el.set("ry", _fmt(scaled_pry))
            else:
                # Circle pupil (default style)
                pupil_el = ET.Element(f"{{{ns}}}circle")
                pupil_el.set("cx", _fmt(pcx))
                pupil_el.set("cy", _fmt(pupil_cy))
                pupil_el.set("r", _fmt(scaled_pr))
            pupil_el.set("fill", pfill)
            if p_opacity:
                pupil_el.set("opacity", p_opacity)
            elements.append(pupil_el)

    return elements


def _generate_eye_elements_legacy(
    base_eyes: dict,
    expr: dict,
    ns: str,
) -> list[ET.Element]:
    """Legacy eye generation (whites only, no pupils/glints)."""
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
    default_rx_ratio = expr.get("rx_ratio", 1.0)
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

    # For shapes that scale both rx and ry (smaller eyes, not just squished)
    def _scaled_eyes(rx_r: float, ry_r: float):
        erx = rx * rx_r
        ery = ry * ry_r
        elements.append(_ellipse(lcx, lcy, erx, ery))
        elements.append(_ellipse(rcx, rcy, erx, ery))

    if shape in ("small_intense", "joy", "droopy_small", "dreamy", "crying", "confident"):
        _scaled_eyes(default_rx_ratio, default_ry_ratio)

    elif shape == "squint":
        ery = ry * default_ry_ratio
        min_ry = rx * 0.25  # prevent pointy look — minimum height is 25% of width
        ery = max(ery, min_ry)
        elements.append(_ellipse(lcx, lcy, rx * 0.92, ery))
        elements.append(_ellipse(rcx, rcy, rx * 0.92, ery))

    elif shape == "narrow":
        ery = max(ry * default_ry_ratio, rx * 0.25)
        elements.append(_ellipse(lcx, lcy, rx * 0.95, ery))
        elements.append(_ellipse(rcx, rcy, rx * 0.95, ery))

    elif shape == "droopy":
        ery = max(ry * default_ry_ratio, rx * 0.25)
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
    # Use mouth stroke color for eyebrows (matches the face's color scheme)
    brow_color = base_mouth.get("stroke", "")
    if not brow_color or brow_color.lower() in ("white", "#fff", "#ffffff", "none"):
        # Fallback to pupil color
        pupil_fill = base_eyes.get("left", {}).get("pupil_fill", "")
        brow_color = pupil_fill if pupil_fill and pupil_fill.lower() not in ("white", "#fff") else "#333"
    stroke = brow_color
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

def generate_decoration_elements(
    base_eyes: dict,
    base_mouth: dict,
    decorations: list[dict],
    ns: str,
) -> list[ET.Element]:
    """Generate decorative SVG elements (tears, hearts, sparkles, etc.)."""
    elements: list[ET.Element] = []

    eyes_cy = (base_eyes["left_cy"] + base_eyes["right_cy"]) / 2
    mouth_cx = base_mouth["cx"]
    mouth_cy = base_mouth["cy"]

    for deco in decorations:
        dtype = deco["type"]

        if dtype == "teardrop":
            side = deco.get("side", "left")
            if side == "left":
                ref_cx = base_eyes["left_cx"]
                ref_cy = base_eyes["left_cy"]
                # Outer tangent: left edge of left eye
                outer_x = ref_cx - base_eyes["rx"]
            else:
                ref_cx = base_eyes["right_cx"]
                ref_cy = base_eyes["right_cy"]
                # Outer tangent: right edge of right eye
                outer_x = ref_cx + base_eyes["rx"]
            ox = deco.get("offset_x", 0)
            tear_ry = deco.get("ry", 7)
            cx = outer_x + ox
            # Top of tear starts at bottom of eye (tangent)
            eye_bottom = ref_cy + base_eyes["ry"]
            cy = eye_bottom + tear_ry  # center of tear = eye_bottom + half tear height
            el = ET.Element(f"{{{ns}}}ellipse")
            el.set("cx", _fmt(cx))
            el.set("cy", _fmt(cy))
            el.set("rx", _fmt(deco.get("rx", 4)))
            el.set("ry", _fmt(deco.get("ry", 7)))
            el.set("fill", deco.get("fill", "#5DADE2"))
            if "opacity" in deco:
                el.set("opacity", str(deco["opacity"]))
            if deco.get("animate"):
                delay = "0s" if side == "left" else "0.3s"
                el.set("style", f"animation: tear-stream 1.5s ease-in-out {delay} infinite")
            elements.append(el)

        elif dtype == "heart":
            ox = deco.get("offset_x", 0)
            oy = deco.get("offset_y", -20)
            s = deco.get("size", 10)
            cx = base_eyes["left_cx"] + (base_eyes["right_cx"] - base_eyes["left_cx"]) / 2 + ox
            cy = eyes_cy + oy
            d = (f"M{_fmt(cx)} {_fmt(cy + s * 0.3)} "
                 f"C{_fmt(cx)} {_fmt(cy - s * 0.3)} {_fmt(cx - s)} {_fmt(cy - s * 0.3)} {_fmt(cx - s)} {_fmt(cy + s * 0.1)} "
                 f"C{_fmt(cx - s)} {_fmt(cy + s * 0.6)} {_fmt(cx)} {_fmt(cy + s)} {_fmt(cx)} {_fmt(cy + s)} "
                 f"C{_fmt(cx)} {_fmt(cy + s)} {_fmt(cx + s)} {_fmt(cy + s * 0.6)} {_fmt(cx + s)} {_fmt(cy + s * 0.1)} "
                 f"C{_fmt(cx + s)} {_fmt(cy - s * 0.3)} {_fmt(cx)} {_fmt(cy - s * 0.3)} {_fmt(cx)} {_fmt(cy + s * 0.3)} Z")
            el = ET.Element(f"{{{ns}}}path")
            el.set("d", d)
            el.set("fill", deco.get("fill", "#E74C3C"))
            if "opacity" in deco:
                el.set("opacity", str(deco["opacity"]))
            elements.append(el)

        elif dtype == "sparkle":
            ox = deco.get("offset_x", 0)
            oy = deco.get("offset_y", -25)
            s = deco.get("size", 8)
            cx = base_eyes["left_cx"] + (base_eyes["right_cx"] - base_eyes["left_cx"]) / 2 + ox
            cy = eyes_cy + oy
            d = (f"M{_fmt(cx)} {_fmt(cy - s)} "
                 f"L{_fmt(cx + s * 0.3)} {_fmt(cy - s * 0.3)} "
                 f"L{_fmt(cx + s)} {_fmt(cy)} "
                 f"L{_fmt(cx + s * 0.3)} {_fmt(cy + s * 0.3)} "
                 f"L{_fmt(cx)} {_fmt(cy + s)} "
                 f"L{_fmt(cx - s * 0.3)} {_fmt(cy + s * 0.3)} "
                 f"L{_fmt(cx - s)} {_fmt(cy)} "
                 f"L{_fmt(cx - s * 0.3)} {_fmt(cy - s * 0.3)} Z")
            el = ET.Element(f"{{{ns}}}path")
            el.set("d", d)
            el.set("fill", deco.get("fill", "#FFD600"))
            if "opacity" in deco:
                el.set("opacity", str(deco["opacity"]))
            elements.append(el)

        elif dtype == "zzz":
            ox = deco.get("offset_x", 60)
            oy = deco.get("offset_y", -40)
            s = deco.get("size", 28)
            fill = deco.get("fill", "#999")
            opacity = str(deco.get("opacity", 0.7))
            cx = base_eyes["right_cx"] + ox
            cy = eyes_cy + oy
            for i, scale in enumerate([1.0, 0.7, 0.45]):
                zx = cx + i * s * 0.6
                zy = cy - i * s * 0.9
                sz = s * scale
                el = ET.Element(f"{{{ns}}}text")
                el.set("x", _fmt(zx))
                el.set("y", _fmt(zy))
                el.set("font-size", _fmt(sz))
                el.set("font-family", "sans-serif")
                el.set("font-weight", "bold")
                el.set("fill", fill)
                el.set("opacity", opacity)
                el.set("style", f"animation: float-up {1.5 + i * 0.3}s ease-in-out infinite")
                el.text = "z"
                elements.append(el)

        elif dtype == "tongue":
            oy = deco.get("offset_y", 15)
            w = deco.get("width", 20)
            h = deco.get("height", 15)
            fill = deco.get("fill", "#E74C6F")
            cx = mouth_cx
            cy = mouth_cy + oy
            el = ET.Element(f"{{{ns}}}ellipse")
            el.set("cx", _fmt(cx))
            el.set("cy", _fmt(cy))
            el.set("rx", _fmt(w / 2))
            el.set("ry", _fmt(h / 2))
            el.set("fill", fill)
            elements.append(el)

        elif dtype == "question_mark":
            ox = deco.get("offset_x", 50)
            oy = deco.get("offset_y", -35)
            s = deco.get("size", 32)
            fill = deco.get("fill", "#666")
            opacity = str(deco.get("opacity", 0.7))
            cx = base_eyes["right_cx"] + ox
            cy = eyes_cy + oy
            el = ET.Element(f"{{{ns}}}text")
            el.set("x", _fmt(cx))
            el.set("y", _fmt(cy))
            el.set("font-size", _fmt(s))
            el.set("font-family", "sans-serif")
            el.set("font-weight", "bold")
            el.set("fill", fill)
            el.set("opacity", opacity)
            el.set("style", "animation: wobble 1.2s ease-in-out infinite")
            el.text = "?"
            elements.append(el)

    return elements

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

    # Ensure eyebrows-base exists (for neutral expression)
    # Remove and regenerate eyebrows-base (ensure color matches mouth)
    existing_brows = root.find(f".//*[@id='eyebrows-base']")
    if existing_brows is not None:
        eb_parent = {c: p for p in root.iter() for c in p}.get(existing_brows)
        if eb_parent is not None:
            eb_parent.remove(existing_brows)
    # Always create fresh eyebrows-base
    if True:
        # Create default eyebrows above the eyes
        brow_g = ET.SubElement(root, f"{{{ns}}}g")
        brow_g.set("id", "eyebrows-base")
        brow_color = base_mouth.get("stroke", "")
        if not brow_color or brow_color.lower() in ("white", "#fff", "#ffffff", "none"):
            brow_color = base_eyes.get("left", {}).get("pupil_fill", "#333")
        if not brow_color or brow_color == "white":
            brow_color = "#333"
        lcx = base_eyes["left_cx"]
        rcx = base_eyes["right_cx"]
        ey = base_eyes["left_cy"]
        rx = base_eyes["rx"]
        brow_y = ey - base_eyes["ry"] - 8  # above the eyes
        brow_span = rx * 0.9
        sw = base_mouth["stroke_width"] * 0.65
        # Left brow
        lb = ET.SubElement(brow_g, f"{{{ns}}}path")
        lb.set("d", f"M{_fmt(lcx - brow_span)} {_fmt(brow_y)} Q{_fmt(lcx)} {_fmt(brow_y - brow_span * 0.25)} {_fmt(lcx + brow_span)} {_fmt(brow_y)}")
        lb.set("fill", "none")
        lb.set("stroke", brow_color)
        lb.set("stroke-width", _fmt(sw))
        lb.set("stroke-linecap", "round")
        # Right brow
        rb = ET.SubElement(brow_g, f"{{{ns}}}path")
        rb.set("d", f"M{_fmt(rcx - brow_span)} {_fmt(brow_y)} Q{_fmt(rcx)} {_fmt(brow_y - brow_span * 0.25)} {_fmt(rcx + brow_span)} {_fmt(brow_y)}")
        rb.set("fill", "none")
        rb.set("stroke", brow_color)
        rb.set("stroke-width", _fmt(sw))
        rb.set("stroke-linecap", "round")

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

        # Decorations (tears, hearts, sparkles, tongue, etc.)
        if "decorations" in expr:
            for deco_el in generate_decoration_elements(base_eyes, base_mouth, expr["decorations"], ns):
                g.append(deco_el)

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
