#!/usr/bin/env python3
"""Lower modern CSS & desktop compat banner in DBX frontend dist for WebKitGTK 2.38.6.

Targeted fixes for Kylin V10 WebKitGTK 4.0 (2.38.x ≈ Safari 16.0):
1. Injects per-theme `--dbx-compat-<token>-rgb: R, G, B;` variables alongside
   semantic theme tokens (`:root`, `.dark`, `html.theme-*`, etc.).
2. Replaces unguarded `oklch(...)` literals with exact sRGB `#rrggbb` / `rgba(...)`
   (preserving originals under `@supports (color: oklch(0% 0 0))` for normal rules).
3. Replaces unguarded `color-mix(...)` calls with theme-aware `rgba(var(--dbx-compat-...-rgb), ...)`
   or static sRGB fallbacks, moving originals into `@supports (color: color-mix(in srgb, red, blue))`
   so neither visual styles nor custom properties break/poison on WebKitGTK 2.38.6.
4. Rewrites any remaining CSS Media Queries Level 4 range syntax (`width >= ...`)
   to classic `(min-width: ...)` / `(max-width: ...)`.
5. Silences only the advisory `degraded` "upgrade your browser" banner in
   `index.html`, while preserving `window.__DBX_WEBVIEW_COMPAT__`,
   `html.dbx-legacy-webview`, and `fatal` (`@layer` missing) detection.

Usage:
  patch-web-assets.py --self-test
  patch-web-assets.py /path/to/dist
"""

from __future__ import annotations

import math
import re
import sys
from pathlib import Path

CORE_THEME_TOKENS = frozenset(
    {
        "--background",
        "--foreground",
        "--card",
        "--card-foreground",
        "--popover",
        "--popover-foreground",
        "--primary",
        "--primary-foreground",
        "--secondary",
        "--secondary-foreground",
        "--muted",
        "--muted-foreground",
        "--accent",
        "--accent-foreground",
        "--destructive",
        "--border",
        "--input",
        "--ring",
        "--success",
        "--warning",
        "--info",
        "--sidebar",
        "--sidebar-foreground",
        "--sidebar-primary",
        "--sidebar-accent",
        "--sidebar-border",
        "--sidebar-ring",
    }
)

VAR_TO_COMPAT_RGB: dict[str, str] = {
    **{tok: f"--dbx-compat-{tok[2:]}-rgb" for tok in CORE_THEME_TOKENS},
    "--color-background": "--dbx-compat-background-rgb",
    "--monitor-panel-bg": "--dbx-compat-background-rgb",
    "--color-card": "--dbx-compat-card-rgb",
    "--color-foreground": "--dbx-compat-foreground-rgb",
    "--color-text": "--dbx-compat-foreground-rgb",
    "--monitor-text": "--dbx-compat-foreground-rgb",
    "--color-primary": "--dbx-compat-primary-rgb",
    "--color-secondary": "--dbx-compat-secondary-rgb",
    "--color-muted": "--dbx-compat-muted-rgb",
    "--color-background-secondary": "--dbx-compat-muted-rgb",
    "--monitor-surface": "--dbx-compat-muted-rgb",
    "--color-muted-foreground": "--dbx-compat-muted-foreground-rgb",
    "--color-text-secondary": "--dbx-compat-muted-foreground-rgb",
    "--monitor-muted": "--dbx-compat-muted-foreground-rgb",
    "--color-accent": "--dbx-compat-accent-rgb",
    "--color-hover": "--dbx-compat-accent-rgb",
    "--color-border": "--dbx-compat-border-rgb",
    "--monitor-border": "--dbx-compat-border-rgb",
    "--color-destructive": "--dbx-compat-destructive-rgb",
    "--color-error": "--dbx-compat-destructive-rgb",
    "--monitor-danger": "--dbx-compat-destructive-rgb",
    "--color-ring": "--dbx-compat-ring-rgb",
    "--color-success": "--dbx-compat-success-rgb",
    "--monitor-success": "--dbx-compat-success-rgb",
    "--color-warning": "--dbx-compat-warning-rgb",
    "--monitor-warning": "--dbx-compat-warning-rgb",
    "--color-info": "--dbx-compat-info-rgb",
    "--monitor-accent": "--dbx-compat-info-rgb",
    "--dbx-primary-rgb": "--dbx-primary-rgb",
    "--dbx-foreground-rgb": "--dbx-foreground-rgb",
    "--dbx-warning-rgb": "--dbx-warning-rgb",
    "--destructive-rgb": "--destructive-rgb",
}

DEFAULT_RGB_FALLBACKS: dict[str, str] = {
    "--dbx-compat-foreground-rgb": "100, 116, 139",
    "--dbx-compat-primary-rgb": "59, 130, 246",
    "--dbx-compat-background-rgb": "255, 255, 255",
    "--dbx-compat-muted-rgb": "244, 244, 245",
    "--dbx-compat-border-rgb": "229, 229, 229",
}

CONTAINER_AT_RULES = frozenset(
    {"media", "supports", "layer", "container", "document", "scope", "starting-style"}
)
KEYFRAME_AT_RULES = frozenset({"keyframes", "-webkit-keyframes", "-moz-keyframes", "-o-keyframes"})
LEAF_BLOCK_AT_RULES = frozenset({"property", "font-face", "page", "counter-style", "font-palette-values"})

SURFACE_TOKENS = frozenset(
    {
        "--background",
        "--color-background",
        "--card",
        "--color-card",
        "--popover",
        "--muted",
        "--color-muted",
        "--color-background-secondary",
        "--monitor-surface",
        "--monitor-panel-bg",
        "--border",
        "--color-border",
        "--monitor-border",
        "--input",
    }
)

IMPORTANT_RE = re.compile(r"\s*!\s*important\s*$", re.IGNORECASE)
FN_OKLCH_RE = re.compile(r"(?<![a-zA-Z0-9_-])oklch\(", re.IGNORECASE)
FN_COLOR_MIX_RE = re.compile(r"(?<![a-zA-Z0-9_-])color-mix\(", re.IGNORECASE)


def _strip_comments(text: str) -> str:
    """Remove /* ... */ CSS comments while preserving string literals."""
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            if end == -1:
                break
            out.append(" ")
            i = end + 2
            continue
        if ch in ("'", '"'):
            quote = ch
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == quote:
                    j += 1
                    break
                j += 1
            out.append(text[i:j])
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _find_matching_paren(text: str, open_pos: int) -> int:
    """Return index of closing ')' for '(' at open_pos, respecting strings and comments."""
    n = len(text)
    depth = 0
    i = open_pos
    while i < n:
        ch = text[i]
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        if ch in ("'", '"'):
            quote = ch
            i += 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _find_matching_brace(text: str, open_pos: int, limit: int | None = None) -> int:
    """Return index of closing '}' for '{' at open_pos, respecting strings and comments."""
    n = len(text) if limit is None else min(limit, len(text))
    depth = 0
    i = open_pos
    while i < n:
        ch = text[i]
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            if end == -1 or end + 2 > n:
                return -1
            i = end + 2
            continue
        if ch in ("'", '"'):
            quote = ch
            i += 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _split_top_level(text: str, sep: str) -> list[str]:
    """Split text by single-char sep only outside (), quotes, and comments."""
    parts: list[str] = []
    start = 0
    depth = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        if ch in ("'", '"'):
            quote = ch
            i += 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")" and depth > 0:
            depth -= 1
        elif ch == sep and depth == 0:
            parts.append(text[start:i])
            start = i + 1
        i += 1
    parts.append(text[start:])
    return parts


def _fmt_alpha(alpha: float) -> str:
    clamped = max(0.0, min(1.0, alpha))
    s = f"{clamped:.4f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _format_rgba_css(r: float, g: float, b: float, alpha: float) -> str:
    ri = max(0, min(255, int(round(r))))
    gi = max(0, min(255, int(round(g))))
    bi = max(0, min(255, int(round(b))))
    a = max(0.0, min(1.0, alpha))
    if a >= 0.9999:
        return f"#{ri:02x}{gi:02x}{bi:02x}"
    return f"rgba({ri}, {gi}, {bi}, {_fmt_alpha(a)})"


def _linear_to_srgb_255(c: float) -> float:
    c = max(0.0, min(1.0, c))
    if c <= 0.0031308:
        s = 12.92 * c
    else:
        s = 1.055 * (c ** (1.0 / 2.4)) - 0.055
    return max(0.0, min(255.0, s * 255.0))


def _oklch_components_to_rgba(inner: str) -> tuple[float, float, float, float] | None:
    """Convert `L C H [/ A]` inside oklch(...) to (r, g, b, a) with 0..255 RGB."""
    raw = _strip_comments(inner).strip()
    if not raw or raw.lower().startswith("from "):
        return None
    if "/" in raw:
        color_part, alpha_part = raw.split("/", 1)
    else:
        color_part, alpha_part = raw, None

    tokens = color_part.strip().split()
    if len(tokens) != 3:
        return None

    def parse_l(tok: str) -> float | None:
        t = tok.strip().lower()
        if t == "none":
            return 0.0
        if t.endswith("%"):
            return float(t[:-1]) / 100.0
        return float(t)

    def parse_c(tok: str) -> float | None:
        t = tok.strip().lower()
        if t == "none":
            return 0.0
        if t.endswith("%"):
            return float(t[:-1]) * 0.4 / 100.0
        return float(t)

    def parse_h(tok: str) -> float | None:
        t = tok.strip().lower()
        if t == "none":
            return 0.0
        if t.endswith("deg"):
            return float(t[:-3])
        if t.endswith("rad"):
            return math.degrees(float(t[:-3]))
        if t.endswith("grad"):
            return float(t[:-4]) * 0.9
        if t.endswith("turn"):
            return float(t[:-4]) * 360.0
        return float(t)

    try:
        l_val = parse_l(tokens[0])
        c_val = parse_c(tokens[1])
        h_deg = parse_h(tokens[2])
        if alpha_part is None:
            alpha = 1.0
        else:
            at = alpha_part.strip().lower()
            if at == "none":
                alpha = 0.0
            elif at.endswith("%"):
                alpha = float(at[:-1]) / 100.0
            else:
                alpha = float(at)
    except ValueError:
        return None

    if l_val is None or c_val is None or h_deg is None:
        return None

    h_rad = math.radians(h_deg)
    a_lab = c_val * math.cos(h_rad)
    b_lab = c_val * math.sin(h_rad)

    l_ = l_val + 0.3963377774 * a_lab + 0.2158037573 * b_lab
    m_ = l_val - 0.1055613458 * a_lab - 0.0638541728 * b_lab
    s_ = l_val - 0.0894841775 * a_lab - 1.2914855480 * b_lab

    l_cube = l_ * l_ * l_
    m_cube = m_ * m_ * m_
    s_cube = s_ * s_ * s_

    r_lin = +4.0767416621 * l_cube - 3.3077115913 * m_cube + 0.2309699292 * s_cube
    g_lin = -1.2684380046 * l_cube + 2.6097574011 * m_cube - 0.3413193965 * s_cube
    b_lin = -0.0041960863 * l_cube - 0.7034186147 * m_cube + 1.7076147010 * s_cube

    return (
        _linear_to_srgb_255(r_lin),
        _linear_to_srgb_255(g_lin),
        _linear_to_srgb_255(b_lin),
        max(0.0, min(1.0, alpha)),
    )


def oklch_to_srgb_css(expr: str) -> str:
    """Convert `oklch(...)` string to `#rrggbb` or `rgba(...)`."""
    stripped = expr.strip()
    if not stripped.lower().startswith("oklch(") or not stripped.endswith(")"):
        raise ValueError(f"unsupported oklch expression: {expr}")
    rgba = _oklch_components_to_rgba(stripped[6:-1])
    if rgba is None:
        raise ValueError(f"failed to parse oklch expression: {expr}")
    return _format_rgba_css(*rgba)


def parse_static_rgba(color_str: str) -> tuple[int, int, int, float] | None:
    """Parse hex, rgb/rgba, black/white/transparent, or oklch into (R, G, B, A)."""
    s = _strip_comments(color_str).strip()
    low = s.lower()
    if low == "black":
        return (0, 0, 0, 1.0)
    if low == "white":
        return (255, 255, 255, 1.0)
    if low == "transparent":
        return (0, 0, 0, 0.0)
    if low.startswith("#"):
        hx = low[1:]
        if len(hx) in (3, 4) and all(c in "0123456789abcdef" for c in hx):
            r = int(hx[0] * 2, 16)
            g = int(hx[1] * 2, 16)
            b = int(hx[2] * 2, 16)
            a = int(hx[3] * 2, 16) / 255.0 if len(hx) == 4 else 1.0
            return (r, g, b, a)
        if len(hx) in (6, 8) and all(c in "0123456789abcdef" for c in hx):
            r = int(hx[0:2], 16)
            g = int(hx[2:4], 16)
            b = int(hx[4:6], 16)
            a = int(hx[6:8], 16) / 255.0 if len(hx) == 8 else 1.0
            return (r, g, b, a)
        return None
    if low.startswith(("rgb(", "rgba(")) and low.endswith(")"):
        inner = s[s.find("(") + 1 : -1].strip()
        alpha = 1.0
        if "/" in inner:
            rgb_part, alpha_part = inner.split("/", 1)
            at = alpha_part.strip().lower()
            try:
                alpha = float(at[:-1]) / 100.0 if at.endswith("%") else float(at)
            except ValueError:
                return None
        else:
            rgb_part = inner
        if "," in rgb_part:
            nums = [p.strip() for p in rgb_part.split(",")]
        else:
            nums = rgb_part.split()
        if len(nums) not in (3, 4):
            return None
        try:
            vals: list[int] = []
            for tok in nums[:3]:
                if tok.endswith("%"):
                    vals.append(max(0, min(255, int(round(float(tok[:-1]) * 2.55)))))
                else:
                    vals.append(max(0, min(255, int(round(float(tok))))))
            if len(nums) == 4:
                a_tok = nums[3].lower()
                alpha = float(a_tok[:-1]) / 100.0 if a_tok.endswith("%") else float(a_tok)
            return (vals[0], vals[1], vals[2], max(0.0, min(1.0, alpha)))
        except ValueError:
            return None
    if low.startswith("oklch(") and low.endswith(")"):
        res = _oklch_components_to_rgba(s[6:-1])
        if res is None:
            return None
        return (
            max(0, min(255, int(round(res[0])))),
            max(0, min(255, int(round(res[1])))),
            max(0, min(255, int(round(res[2])))),
            res[3],
        )
    return None


def parse_static_rgb(color_str: str) -> tuple[int, int, int] | None:
    """Parse hex, rgb/rgba, black/white, or oklch into (R, G, B)."""
    res = parse_static_rgba(color_str)
    if res is None:
        return None
    return (res[0], res[1], res[2])


def _split_mix_side(side: str) -> tuple[str, float | None]:
    """Extract `<color>` and optional percentage weight from one color-mix argument."""
    s = side.strip()
    m_trail = re.match(r"^(.*?)\s+(\d+(?:\.\d+)?)%$", s, re.DOTALL)
    if m_trail:
        return m_trail.group(1).strip(), float(m_trail.group(2))
    m_lead = re.match(r"^(\d+(?:\.\d+)?)%\s+(.+)$", s, re.DOTALL)
    if m_lead:
        return m_lead.group(2).strip(), float(m_lead.group(1))
    return s, None


def _resolve_var_to_compat_rgb(color_expr: str) -> str | None:
    """Resolve `var(--foo, ...)` recursively to `--dbx-compat-*-rgb` if known."""
    s = color_expr.strip()
    if not (s.lower().startswith("var(") and s.endswith(")")):
        return None
    inner = s[4:-1].strip()
    parts = _split_top_level(inner, ",")
    var_name = parts[0].strip()
    if var_name in VAR_TO_COMPAT_RGB:
        return VAR_TO_COMPAT_RGB[var_name]
    if len(parts) > 1:
        fb = ",".join(parts[1:]).strip()
        if fb.lower().startswith("var(") and fb.endswith(")"):
            return _resolve_var_to_compat_rgb(fb)
    return None


def _resolve_static_rgba_through_var(color_expr: str) -> tuple[int, int, int, float] | None:
    s = color_expr.strip()
    direct = parse_static_rgba(s)
    if direct is not None:
        return direct
    if s.lower().startswith("var(") and s.endswith(")"):
        parts = _split_top_level(s[4:-1].strip(), ",")
        if len(parts) > 1:
            return _resolve_static_rgba_through_var(",".join(parts[1:]).strip())
    return None


def _rgba_var_expr(rgb_var: str, alpha: float) -> str:
    a_str = _fmt_alpha(alpha)
    default_rgb = DEFAULT_RGB_FALLBACKS.get(rgb_var)
    if default_rgb:
        return f"rgba(var({rgb_var}, {default_rgb}), {a_str})"
    return f"rgba(var({rgb_var}), {a_str})"


def _lower_single_color_mix(args_text: str, prop_name: str) -> str:
    """Lower a single innermost color-mix(...) argument string cleanly."""
    parts = [p.strip() for p in _split_top_level(args_text, ",")]
    if len(parts) != 3:
        raise ValueError(f"expected 3 args in color-mix(), got {len(parts)}: {args_text}")
    side1_raw, side2_raw = parts[1], parts[2]
    c1, w1 = _split_mix_side(side1_raw)
    c2, w2 = _split_mix_side(side2_raw)

    if w1 is None and w2 is None:
        w1, w2 = 50.0, 50.0
    elif w1 is not None and w2 is None:
        w2 = max(0.0, 100.0 - w1)
    elif w1 is None and w2 is not None:
        w1 = max(0.0, 100.0 - w2)
    assert w1 is not None and w2 is not None

    total = w1 + w2
    if total <= 0:
        w1, w2 = 50.0, 50.0
    else:
        w1 = (w1 / total) * 100.0
        w2 = (w2 / total) * 100.0

    c1_low = c1.lower()
    c2_low = c2.lower()

    # Case 1: mixing against `transparent`
    if c1_low == "transparent" and c2_low == "transparent":
        return "transparent"
    if c2_low == "transparent" or c1_low == "transparent":
        if c1_low == "transparent":
            c1, w1 = c2, w2
            c1_low = c1.lower()
        alpha = max(0.0, min(1.0, w1 / 100.0))

        rgb_var = _resolve_var_to_compat_rgb(c1)
        if rgb_var is not None:
            return _rgba_var_expr(rgb_var, alpha)

        static_rgba = _resolve_static_rgba_through_var(c1)
        if static_rgba is not None:
            return _format_rgba_css(static_rgba[0], static_rgba[1], static_rgba[2], static_rgba[3] * alpha)

        if c1_low == "currentcolor":
            prop_low = prop_name.lower()
            if (prop_low.startswith(("background", "box-shadow")) or prop_low == "--box-shadow") and w1 < 80.0:
                return _rgba_var_expr("--dbx-compat-foreground-rgb", alpha)
            return "currentColor"

        if c1_low.startswith("var("):
            if w1 <= 35.0:
                return _rgba_var_expr("--dbx-compat-primary-rgb", alpha)
            return c1

        return c1

    # Case 2: mixing two non-transparent colors
    rgba1 = _resolve_static_rgba_through_var(c1)
    rgba2 = _resolve_static_rgba_through_var(c2)
    if rgba1 is not None and rgba2 is not None:
        f1 = w1 / 100.0
        f2 = w2 / 100.0
        a_out = rgba1[3] * f1 + rgba2[3] * f2
        if a_out <= 0.0001:
            return "transparent"
        r_out = (rgba1[0] * rgba1[3] * f1 + rgba2[0] * rgba2[3] * f2) / a_out
        g_out = (rgba1[1] * rgba1[3] * f1 + rgba2[1] * rgba2[3] * f2) / a_out
        b_out = (rgba1[2] * rgba1[3] * f1 + rgba2[2] * rgba2[3] * f2) / a_out
        return _format_rgba_css(r_out, g_out, b_out, a_out)

    # Order dominant vs subordinate weight
    if w1 >= w2:
        dom_c, dom_w, sub_c, sub_w = c1, w1, c2, w2
    else:
        dom_c, dom_w, sub_c, sub_w = c2, w2, c1, w1

    def var_base(expr: str) -> str:
        s = expr.strip()
        if s.lower().startswith("var(") and s.endswith(")"):
            return _split_top_level(s[4:-1], ",")[0].strip().lower()
        return ""

    dom_base = var_base(dom_c)
    sub_base = var_base(sub_c)

    # Keep opaque panels opaque when mixing mostly background/card with minor muted/surface tint.
    if dom_base in SURFACE_TOKENS and sub_base in SURFACE_TOKENS and (dom_w >= 85.0 or prop_name.startswith("--")):
        return dom_c

    if sub_base in SURFACE_TOKENS and dom_w >= 92.0:
        return dom_c

    sub_rgb_var = _resolve_var_to_compat_rgb(sub_c)
    sub_static = _resolve_static_rgba_through_var(sub_c)
    if dom_base in SURFACE_TOKENS and sub_w <= 65.0:
        alpha = max(0.0, min(1.0, sub_w / 100.0))
        if sub_rgb_var is not None:
            return _rgba_var_expr(sub_rgb_var, alpha)
        if sub_static is not None:
            return _format_rgba_css(sub_static[0], sub_static[1], sub_static[2], sub_static[3] * alpha)

    return dom_c


def lower_css_value(value: str, prop_name: str, lower_oklch: bool = True, lower_cm: bool = True) -> str:
    """Lower all oklch() and color-mix() calls inside a CSS declaration value."""
    out = value

    if lower_oklch:
        while True:
            m = FN_OKLCH_RE.search(out)
            if not m:
                break
            open_paren = m.end() - 1
            close_paren = _find_matching_paren(out, open_paren)
            if close_paren == -1:
                raise ValueError(f"unbalanced oklch( in {prop_name}: {out}")
            replacement = oklch_to_srgb_css(out[m.start() : close_paren + 1])
            out = out[: m.start()] + replacement + out[close_paren + 1 :]

    if lower_cm:
        # Lower innermost color-mix(...) calls repeatedly so nested expressions collapse cleanly.
        while True:
            matches = list(FN_COLOR_MIX_RE.finditer(out))
            if not matches:
                break
            lowered_any = False
            for m in reversed(matches):
                open_paren = m.end() - 1
                close_paren = _find_matching_paren(out, open_paren)
                if close_paren == -1:
                    raise ValueError(f"unbalanced color-mix( in {prop_name}: {out}")
                inner = out[open_paren + 1 : close_paren]
                if FN_COLOR_MIX_RE.search(inner):
                    continue
                replacement = _lower_single_color_mix(inner, prop_name)
                out = out[: m.start()] + replacement + out[close_paren + 1 :]
                lowered_any = True
                break
            if not lowered_any:
                raise ValueError(f"could not make progress lowering color-mix( in {prop_name}: {out}")

    return out


def _convert_single_media_range(inner: str) -> str | None:
    """Convert `(width >= 40rem)` or `(20px <= height < 90px)` to classic min-/max-."""
    s = _strip_comments(inner).strip()
    m3 = re.match(
        r"^(.+?)\s*(<=|<|>=|>)\s*(width|height)\s*(<=|<|>=|>)\s*(.+)$",
        s,
        re.IGNORECASE,
    )
    if m3:
        v1, op1, dim, op2, v2 = (m3.group(1).strip(), m3.group(2), m3.group(3).lower(), m3.group(4), m3.group(5).strip())
        c1 = _single_bound_to_classic(dim, _invert_op(op1), v1)
        c2 = _single_bound_to_classic(dim, op2, v2)
        if c1 and c2:
            return f"{c1} and {c2}"
        return None

    m2a = re.match(r"^(width|height)\s*(<=|<|>=|>)\s*(.+)$", s, re.IGNORECASE)
    if m2a:
        return _single_bound_to_classic(m2a.group(1).lower(), m2a.group(2), m2a.group(3).strip())

    m2b = re.match(r"^(.+?)\s*(<=|<|>=|>)\s*(width|height)$", s, re.IGNORECASE)
    if m2b:
        return _single_bound_to_classic(m2b.group(3).lower(), _invert_op(m2b.group(2)), m2b.group(1).strip())

    return None


def _invert_op(op: str) -> str:
    return {"<": ">", "<=": ">=", ">": "<", ">=": "<="}[op]


def _single_bound_to_classic(dim: str, op: str, val: str) -> str | None:
    if op == ">=":
        return f"(min-{dim}: {val})"
    if op == "<=":
        return f"(max-{dim}: {val})"
    if op == ">":
        return f"(min-{dim}: calc({val} + 0.02px))"
    if op == "<":
        return f"(max-{dim}: calc({val} - 0.02px))"
    return None


def rewrite_media_prelude(prelude: str) -> str:
    """Rewrite Media Queries Level 4 range syntax inside an `@media` prelude."""
    out: list[str] = []
    i = 0
    n = len(prelude)
    while i < n:
        ch = prelude[i]
        if ch == "/" and i + 1 < n and prelude[i + 1] == "*":
            end = prelude.find("*/", i + 2)
            j = n if end == -1 else end + 2
            out.append(prelude[i:j])
            i = j
            continue
        if ch in ("'", '"'):
            q = ch
            j = i + 1
            while j < n:
                if prelude[j] == "\\":
                    j += 2
                    continue
                if prelude[j] == q:
                    j += 1
                    break
                j += 1
            out.append(prelude[i:j])
            i = j
            continue
        if ch == "(":
            close = _find_matching_paren(prelude, i)
            if close != -1:
                inner = prelude[i + 1 : close]
                if any(op in _strip_comments(inner) for op in ("<", ">")):
                    converted = _convert_single_media_range(inner)
                    if converted is not None:
                        out.append(converted)
                        i = close + 1
                        continue
                # Recurse inside grouping parens like `(all and (width >= 40rem))`
                rewritten_inner = rewrite_media_prelude(inner)
                out.append("(" + rewritten_inner + ")")
                i = close + 1
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _supports_guard_caps(prelude: str) -> tuple[bool, bool]:
    """Return (guards_color_mix, guards_oklch) for an `@supports` prelude."""
    clean = _strip_comments(prelude).strip().lower()
    is_not = clean == "not" or (
        clean.startswith("not") and (len(clean) == 3 or not (clean[3].isalnum() or clean[3] in ("-", "_")))
    )
    if is_not:
        return (False, False)
    has_cm = bool(FN_COLOR_MIX_RE.search(clean))
    has_ok = bool(FN_OKLCH_RE.search(clean))
    if has_cm:
        return (True, True)
    if has_ok:
        return (False, True)
    return (False, False)


def _split_decl_prop_val(decl: str) -> tuple[str, str, bool] | None:
    """Split a CSS declaration into (prop_name, value_without_important, is_important)."""
    i = 0
    n = len(decl)
    depth = 0
    colon_pos = -1
    while i < n:
        ch = decl[i]
        if ch == "/" and i + 1 < n and decl[i + 1] == "*":
            end = decl.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        if ch in ("'", '"'):
            q = ch
            i += 1
            while i < n:
                if decl[i] == "\\":
                    i += 2
                    continue
                if decl[i] == q:
                    i += 1
                    break
                i += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")" and depth > 0:
            depth -= 1
        elif ch == ":" and depth == 0:
            colon_pos = i
            break
        i += 1
    if colon_pos == -1:
        return None
    prop = _strip_comments(decl[:colon_pos]).strip()
    raw_val = decl[colon_pos + 1 :].strip()
    is_imp = False
    m_imp = IMPORTANT_RE.search(raw_val)
    if m_imp:
        is_imp = True
        raw_val = raw_val[: m_imp.start()].rstrip()
    return prop, raw_val, is_imp


def _has_top_level_brace(text: str, start: int, end: int) -> bool:
    i = start
    while i < end:
        ch = text[i]
        if ch == "/" and i + 1 < end and text[i + 1] == "*":
            e = text.find("*/", i + 2, end)
            i = end if e == -1 else e + 2
            continue
        if ch in ("'", '"'):
            q = ch
            i += 1
            while i < end:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == q:
                    i += 1
                    break
                i += 1
            continue
        if ch == "{":
            return True
        i += 1
    return False


def _transform_declarations(
    selector_prefix: str,
    body_text: str,
    guards_cm: bool,
    guards_ok: bool,
    emit_supports: bool,
) -> tuple[str, str]:
    """Transform rule declarations and return (base_body, extra_supports_blocks)."""
    raw_decls = _split_top_level(body_text, ";")
    base_decls: list[str] = []
    seen_norm: set[str] = set()
    existing_props: set[str] = set()
    injected_rgb: list[str] = []

    cm_supports_decls: list[str] = []
    ok_supports_decls: list[str] = []

    # First collect declared properties to avoid duplicating existing --dbx-compat-*-rgb
    parsed_items: list[tuple[str, tuple[str, str, bool] | None]] = []
    for raw in raw_decls:
        stripped = raw.strip()
        if not stripped:
            continue
        parsed = _split_decl_prop_val(raw)
        if parsed is not None:
            existing_props.add(parsed[0].lower())
        parsed_items.append((raw, parsed))

    for raw, parsed in parsed_items:
        stripped = raw.strip()
        if parsed is None:
            base_decls.append(stripped)
            continue
        prop, val, is_imp = parsed
        prop_low = prop.lower()
        imp_suffix = "!important" if is_imp else ""

        # Check theme RGB extraction (use lowered value if oklch was used)
        if prop_low in CORE_THEME_TOKENS:
            compat_rgb_name = f"--dbx-compat-{prop_low[2:]}-rgb"
            if compat_rgb_name not in existing_props:
                rgb = parse_static_rgb(val)
                if rgb is not None:
                    existing_props.add(compat_rgb_name)
                    injected_rgb.append(f"{compat_rgb_name}:{rgb[0]}, {rgb[1]}, {rgb[2]}")

        has_cm = not guards_cm and bool(FN_COLOR_MIX_RE.search(val))
        has_ok = not guards_ok and bool(FN_OKLCH_RE.search(val))

        if not has_cm and not has_ok:
            norm = f"{prop_low}:{val}{imp_suffix}".replace(" ", "")
            base_decls.append(stripped)
            seen_norm.add(norm)
            continue

        lowered_val = lower_css_value(val, prop, lower_oklch=has_ok, lower_cm=has_cm)
        lowered_decl = f"{prop}:{lowered_val}{imp_suffix}"
        norm_lowered = re.sub(r"\s+", "", lowered_decl).lower()
        if norm_lowered not in seen_norm:
            base_decls.append(lowered_decl)
            seen_norm.add(norm_lowered)

        if emit_supports:
            orig_decl = f"{prop}:{val}{imp_suffix}"
            if has_cm:
                cm_supports_decls.append(orig_decl)
            elif has_ok:
                ok_supports_decls.append(orig_decl)

    all_base = base_decls + injected_rgb
    base_body = ";".join(all_base)

    extra: list[str] = []
    if emit_supports and selector_prefix:
        if cm_supports_decls:
            extra.append(
                f"@supports (color: color-mix(in srgb, red, blue)){{{selector_prefix}{{{';'.join(cm_supports_decls)}}}}}"
            )
        if ok_supports_decls:
            extra.append(
                f"@supports (color: oklch(0% 0 0)){{{selector_prefix}{{{';'.join(ok_supports_decls)}}}}}"
            )
    return base_body, "".join(extra)


def _scan_to_statement_delimiter(text: str, start: int, end: int) -> tuple[str, int]:
    """Scan up to top-level '{' or ';' (respecting parens, strings, comments)."""
    i = start
    paren_depth = 0
    while i < end:
        ch = text[i]
        if ch == "/" and i + 1 < end and text[i + 1] == "*":
            c_end = text.find("*/", i + 2, end)
            i = end if c_end == -1 else c_end + 2
            continue
        if ch in ("'", '"'):
            q = ch
            i += 1
            while i < end:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == q:
                    i += 1
                    break
                i += 1
            continue
        if ch == "(":
            paren_depth += 1
        elif ch == ")" and paren_depth > 0:
            paren_depth -= 1
        elif paren_depth == 0 and ch in ("{", ";"):
            return ch, i
        i += 1
    return "", end


def _transform_block(
    text: str,
    start: int,
    end: int,
    guards_cm: bool,
    guards_ok: bool,
    emit_supports: bool,
) -> str:
    """Recursively transform CSS block [start, end) while preserving comments and brace structure."""
    out: list[str] = []
    pos = start

    while pos < end:
        # Preserve contiguous whitespace and /* ... */ comments verbatim before next item.
        if text[pos].isspace():
            ws_start = pos
            while pos < end and text[pos].isspace():
                pos += 1
            out.append(text[ws_start:pos])
            continue
        if text[pos] == "/" and pos + 1 < end and text[pos + 1] == "*":
            c_end = text.find("*/", pos + 2, end)
            j = end if c_end == -1 else c_end + 2
            out.append(text[pos:j])
            pos = j
            continue

        if text[pos] == "@":
            name_start = pos + 1
            j = name_start
            while j < end and (text[j].isalnum() or text[j] in ("-", "_")):
                j += 1
            at_name = text[name_start:j].lower()
            delim, delim_pos = _scan_to_statement_delimiter(text, j, end)
            if delim == ";" or delim == "":
                next_pos = delim_pos + 1 if delim == ";" else end
                out.append(text[pos:next_pos])
                pos = next_pos
                continue

            # Block at-rule `{ ... }`
            prelude = text[j:delim_pos]
            close_brace = _find_matching_brace(text, delim_pos, end)
            if close_brace == -1:
                raise ValueError(f"unbalanced brace in @{at_name}")

            if at_name in CONTAINER_AT_RULES:
                new_prelude = rewrite_media_prelude(prelude) if at_name == "media" else prelude
                child_cm, child_ok = guards_cm, guards_ok
                if at_name == "supports":
                    sup_cm, sup_ok = _supports_guard_caps(prelude)
                    child_cm = guards_cm or sup_cm
                    child_ok = guards_ok or sup_ok
                inner = _transform_block(
                    text,
                    delim_pos + 1,
                    close_brace,
                    guards_cm=child_cm,
                    guards_ok=child_ok,
                    emit_supports=emit_supports,
                )
                out.append(f"@{text[name_start:j]}{new_prelude}{{{inner}}}")
                pos = close_brace + 1
                continue

            if at_name in KEYFRAME_AT_RULES:
                inner = _transform_block(
                    text,
                    delim_pos + 1,
                    close_brace,
                    guards_cm=guards_cm,
                    guards_ok=guards_ok,
                    emit_supports=False,
                )
                out.append(f"@{text[name_start:j]}{prelude}{{{inner}}}")
                pos = close_brace + 1
                continue

            # Leaf block at-rules (@property, @font-face, @page, etc.)
            base_body, _ = _transform_declarations(
                "",
                text[delim_pos + 1 : close_brace],
                guards_cm=guards_cm,
                guards_ok=guards_ok,
                emit_supports=False,
            )
            out.append(f"@{text[name_start:j]}{prelude}{{{base_body}}}")
            pos = close_brace + 1
            continue

        # Normal style rule (or nested block rule inside @keyframes)
        delim, delim_pos = _scan_to_statement_delimiter(text, pos, end)
        if delim == ";":
            out.append(text[pos : delim_pos + 1])
            pos = delim_pos + 1
            continue
        if delim == "":
            out.append(text[pos:end])
            pos = end
            continue

        selector = text[pos:delim_pos].strip()
        close_brace = _find_matching_brace(text, delim_pos, end)
        if close_brace == -1:
            raise ValueError(f"unbalanced brace in selector {selector[:60]!r}")

        if _has_top_level_brace(text, delim_pos + 1, close_brace):
            inner = _transform_block(
                text,
                delim_pos + 1,
                close_brace,
                guards_cm=guards_cm,
                guards_ok=guards_ok,
                emit_supports=False,
            )
            out.append(f"{selector}{{{inner}}}")
        else:
            base_body, extra_supports = _transform_declarations(
                selector,
                text[delim_pos + 1 : close_brace],
                guards_cm=guards_cm,
                guards_ok=guards_ok,
                emit_supports=emit_supports,
            )
            out.append(f"{selector}{{{base_body}}}{extra_supports}")
        pos = close_brace + 1

    return "".join(out)


def _count_braces(text: str) -> tuple[int, int]:
    """Count top-level '{' and '}' outside CSS strings and comments."""
    opens = 0
    closes = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            if end == -1:
                break
            i = end + 2
            continue
        if ch in ("'", '"'):
            q = ch
            i += 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == q:
                    i += 1
                    break
                i += 1
            continue
        if ch == "{":
            opens += 1
        elif ch == "}":
            closes += 1
        i += 1
    return opens, closes


def transform_css_text(text: str, filename: str = "<memory>") -> str:
    """Transform an entire CSS stylesheet for WebKitGTK 2.38.6 compatibility."""
    orig_open, orig_close = _count_braces(text)
    if orig_open != orig_close:
        raise ValueError(f"{filename}: input CSS has unbalanced braces ({orig_open} open, {orig_close} close)")

    transformed = _transform_block(
        text,
        0,
        len(text),
        guards_cm=False,
        guards_ok=False,
        emit_supports=True,
    )

    new_open, new_close = _count_braces(transformed)
    if new_open != new_close:
        raise ValueError(f"{filename}: output CSS has unbalanced braces ({new_open} open, {new_close} close)")
    return transformed


def patch_index_html(index_path: Path) -> bool:
    """Disable advisory degraded browser-upgrade banner while keeping legacy class probe active."""
    text = index_path.read_text(encoding="utf-8")
    old = 'const dismissedDegraded = report.tier === "degraded" && storage("localStorage", BANNER_KEY) === "1";'
    new = 'const dismissedDegraded = report.tier === "degraded";'
    if new in text:
        return False
    if old not in text:
        raise SystemExit(f"{index_path}: expected compat banner check snippet not found")
    index_path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return True


def patch_dist_dir(dist_dir: Path) -> tuple[int, bool]:
    """Patch all CSS files and index.html in dist_dir."""
    if not dist_dir.is_dir():
        raise SystemExit(f"dist directory not found: {dist_dir}")
    index_html = dist_dir / "index.html"
    if not index_html.is_file():
        raise SystemExit(f"index.html not found under {dist_dir}")

    css_changed = 0
    for css_path in sorted(dist_dir.rglob("*.css")):
        orig = css_path.read_text(encoding="utf-8", errors="replace")
        patched = transform_css_text(orig, str(css_path.relative_to(dist_dir)))
        if patched != orig:
            css_path.write_text(patched, encoding="utf-8")
            css_changed += 1

    html_changed = patch_index_html(index_html)
    return css_changed, html_changed


def run_self_test() -> None:
    # 1. Exact OKLCH -> sRGB math
    red50 = oklch_to_srgb_css("oklch(97.1% .013 17.38)")
    assert red50.startswith("#") and len(red50) == 7, f"unexpected oklch red50: {red50}"
    semi_blue = oklch_to_srgb_css("oklch(0.6 0.2 250 / 50%)")
    assert semi_blue.startswith("rgba(") and semi_blue.endswith(", 0.5)"), f"unexpected semi_blue: {semi_blue}"
    assert oklch_to_srgb_css("oklch(100% 0 0)") == "#ffffff"
    assert oklch_to_srgb_css("oklch(0% 0 0/.12)") == "rgba(0, 0, 0, 0.12)"

    # 2. Custom property poisoning prevention + theme RGB variable injection
    poison_css = (
        ":root{--background:#fff;--warning:#d97706;--warning-bg:var(--warning);"
        "--warning-bg:color-mix(in srgb, var(--warning) 14%, transparent)}"
    )
    out_poison = transform_css_text(poison_css)
    assert "--dbx-compat-warning-rgb:217, 119, 6" in out_poison
    assert "--warning-bg:rgba(var(--dbx-compat-warning-rgb), 0.14)" in out_poison
    assert "@supports (color: color-mix(in srgb, red, blue)){:root{--warning-bg:color-mix(in srgb, var(--warning) 14%, transparent)}}" in out_poison

    # 3. Component color-mix with !important and nested var(..., color-mix(...))
    comp_css = (
        ".a{background:color-mix(in oklab, var(--primary) 14%, var(--background))!important;"
        "color:var(--app-tab-color, color-mix(in srgb, var(--foreground) 70%, transparent))}"
    )
    out_comp = transform_css_text(comp_css)
    assert "background:rgba(var(--dbx-compat-primary-rgb, 59, 130, 246), 0.14)!important" in out_comp
    assert "color:var(--app-tab-color, rgba(var(--dbx-compat-foreground-rgb, 100, 116, 139), 0.7))" in out_comp
    assert "@supports (color: color-mix(in srgb, red, blue)){.a{" in out_comp

    # 4. @keyframes in-place lowering (no @supports emitted inside @keyframes)
    kf_css = (
        "@keyframes pulse{0%,to{background-color:color-mix(in srgb, var(--primary) 4%, transparent)}"
        "50%{box-shadow:inset 0 0 0 3px color-mix(in srgb, var(--primary) 54%, transparent)}}"
    )
    out_kf = transform_css_text(kf_css)
    assert "@supports" not in out_kf
    assert "color-mix(" not in out_kf
    assert "rgba(var(--dbx-compat-primary-rgb, 59, 130, 246), 0.04)" in out_kf
    assert "rgba(var(--dbx-compat-primary-rgb, 59, 130, 246), 0.54)" in out_kf

    # 5. @media range syntax conversion + @supports oklch guard still lowers color-mix
    mq_css = (
        "@media (width >= 40rem) and (height < 900px){"
        "@supports (color: oklch(0% 0 0)){.b{color:oklch(70% .15 250);background:color-mix(in srgb, #10b981 15%, var(--background))}}}"
    )
    out_mq = transform_css_text(mq_css)
    assert "@media (min-width: 40rem) and (max-height: calc(900px - 0.02px))" in out_mq
    assert "color:oklch(70% .15 250)" in out_mq  # already inside oklch @supports
    assert "background:rgba(16, 185, 129, 0.15)" in out_mq

    # 6. Cross-check against verify-web-compat.py scanner (must report 0 unguarded)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import importlib

    vwc = importlib.import_module("verify-web-compat")
    combined = "\n".join([out_poison, out_comp, out_kf, out_mq])
    stats = vwc.scan_css(combined, "self-test.css")
    assert stats.unguarded_total == 0, f"expected 0 unguarded constructs, got {stats}"

    print("patch-web-assets self-test passed (all assertions green)")


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: patch-web-assets.py [--self-test | /path/to/dist]")
    arg = sys.argv[1]
    if arg == "--self-test":
        run_self_test()
        return 0
    css_changed, html_changed = patch_dist_dir(Path(arg))
    print(f"patched {css_changed} CSS file(s) and index.html={'updated' if html_changed else 'unchanged'} under {arg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
