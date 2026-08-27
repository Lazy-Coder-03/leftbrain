"""color - convert, name, contrast, blend, harmonise, snap, simulate and grey a colour (colour is arithmetic, not opinion)."""

from __future__ import annotations

import base64
import math
import re
import struct
import zlib
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from typing import Any

from ..contract import Ambiguous, ToolError, ok, tool

MODES = ("convert", "describe", "swatch", "contrast", "mix", "harmony", "nearest", "simulate", "grayscale")

SPACES = ("hex", "rgb", "hsl", "hsv", "cmyk", "lab")
_SPACE_ALIASES = {"hsb": "hsv", "rgba": "rgb", "hsla": "hsl", "hsva": "hsv", "hsba": "hsv"}
HARMONIES: dict[str, tuple[int, ...]] = {
    "complementary": (180,),
    "analogous": (-30, 30),
    "triadic": (120, 240),
    "split_complementary": (150, 210),
}
#: Viénot, Brettel & Mollon (1999) - a dichromat's view as a linear map on linearised sRGB.
DEFICIENCIES: dict[str, tuple[tuple[float, float, float], ...]] = {
    "protanopia": ((0.170556992, 0.829443014, 0.0), (0.170556991, 0.829443008, 0.0), (-0.004517144, 0.004517144, 1.0)),
    "deuteranopia": ((0.33066007, 0.66933993, 0.0), (0.33066007, 0.66933993, 0.0), (-0.02785538, 0.02785538, 1.0)),
    "tritanopia": ((1.0, 0.1273989, -0.1273989), (0.0, 0.8739093, 0.1260907), (0.0, 0.8739093, 0.1260907)),
}
GRAY_METHODS = ("rec709", "rec601", "lab", "average", "hsl")
SWATCH_MIN, SWATCH_MAX, SWATCH_DEFAULT = 16, 256, 64
RAMP_DEFAULT, RAMP_MAX = 5, 64
WCAG = {"AA": {"normal_text": 4.5, "large_text": 3.0}, "AAA": {"normal_text": 7.0, "large_text": 4.5}}
DELTA_E_NOTE = "distance is CIE76 ΔE in CIELAB (D65 white, sRGB linearised)"

#: The 148 named colours of CSS Color Level 4.
CSS_NAMES: dict[str, str] = {
    "aliceblue": "#F0F8FF", "antiquewhite": "#FAEBD7", "aqua": "#00FFFF", "aquamarine": "#7FFFD4", "azure": "#F0FFFF",
    "beige": "#F5F5DC", "bisque": "#FFE4C4", "black": "#000000", "blanchedalmond": "#FFEBCD", "blue": "#0000FF",
    "blueviolet": "#8A2BE2", "brown": "#A52A2A", "burlywood": "#DEB887", "cadetblue": "#5F9EA0", "chartreuse": "#7FFF00",
    "chocolate": "#D2691E", "coral": "#FF7F50", "cornflowerblue": "#6495ED", "cornsilk": "#FFF8DC", "crimson": "#DC143C",
    "cyan": "#00FFFF", "darkblue": "#00008B", "darkcyan": "#008B8B", "darkgoldenrod": "#B8860B", "darkgray": "#A9A9A9",
    "darkgreen": "#006400", "darkgrey": "#A9A9A9", "darkkhaki": "#BDB76B", "darkmagenta": "#8B008B", "darkolivegreen": "#556B2F",
    "darkorange": "#FF8C00", "darkorchid": "#9932CC", "darkred": "#8B0000", "darksalmon": "#E9967A", "darkseagreen": "#8FBC8F",
    "darkslateblue": "#483D8B", "darkslategray": "#2F4F4F", "darkslategrey": "#2F4F4F", "darkturquoise": "#00CED1", "darkviolet": "#9400D3",
    "deeppink": "#FF1493", "deepskyblue": "#00BFFF", "dimgray": "#696969", "dimgrey": "#696969", "dodgerblue": "#1E90FF",
    "firebrick": "#B22222", "floralwhite": "#FFFAF0", "forestgreen": "#228B22", "fuchsia": "#FF00FF", "gainsboro": "#DCDCDC",
    "ghostwhite": "#F8F8FF", "gold": "#FFD700", "goldenrod": "#DAA520", "gray": "#808080", "green": "#008000",
    "greenyellow": "#ADFF2F", "grey": "#808080", "honeydew": "#F0FFF0", "hotpink": "#FF69B4", "indianred": "#CD5C5C",
    "indigo": "#4B0082", "ivory": "#FFFFF0", "khaki": "#F0E68C", "lavender": "#E6E6FA", "lavenderblush": "#FFF0F5",
    "lawngreen": "#7CFC00", "lemonchiffon": "#FFFACD", "lightblue": "#ADD8E6", "lightcoral": "#F08080", "lightcyan": "#E0FFFF",
    "lightgoldenrodyellow": "#FAFAD2", "lightgray": "#D3D3D3", "lightgreen": "#90EE90", "lightgrey": "#D3D3D3", "lightpink": "#FFB6C1",
    "lightsalmon": "#FFA07A", "lightseagreen": "#20B2AA", "lightskyblue": "#87CEFA", "lightslategray": "#778899", "lightslategrey": "#778899",
    "lightsteelblue": "#B0C4DE", "lightyellow": "#FFFFE0", "lime": "#00FF00", "limegreen": "#32CD32", "linen": "#FAF0E6",
    "magenta": "#FF00FF", "maroon": "#800000", "mediumaquamarine": "#66CDAA", "mediumblue": "#0000CD", "mediumorchid": "#BA55D3",
    "mediumpurple": "#9370DB", "mediumseagreen": "#3CB371", "mediumslateblue": "#7B68EE", "mediumspringgreen": "#00FA9A", "mediumturquoise": "#48D1CC",
    "mediumvioletred": "#C71585", "midnightblue": "#191970", "mintcream": "#F5FFFA", "mistyrose": "#FFE4E1", "moccasin": "#FFE4B5",
    "navajowhite": "#FFDEAD", "navy": "#000080", "oldlace": "#FDF5E6", "olive": "#808000", "olivedrab": "#6B8E23",
    "orange": "#FFA500", "orangered": "#FF4500", "orchid": "#DA70D6", "palegoldenrod": "#EEE8AA", "palegreen": "#98FB98",
    "paleturquoise": "#AFEEEE", "palevioletred": "#DB7093", "papayawhip": "#FFEFD5", "peachpuff": "#FFDAB9", "peru": "#CD853F",
    "pink": "#FFC0CB", "plum": "#DDA0DD", "powderblue": "#B0E0E6", "purple": "#800080", "rebeccapurple": "#663399",
    "red": "#FF0000", "rosybrown": "#BC8F8F", "royalblue": "#4169E1", "saddlebrown": "#8B4513", "salmon": "#FA8072",
    "sandybrown": "#F4A460", "seagreen": "#2E8B57", "seashell": "#FFF5EE", "sienna": "#A0522D", "silver": "#C0C0C0",
    "skyblue": "#87CEEB", "slateblue": "#6A5ACD", "slategray": "#708090", "slategrey": "#708090", "snow": "#FFFAFA",
    "springgreen": "#00FF7F", "steelblue": "#4682B4", "tan": "#D2B48C", "teal": "#008080", "thistle": "#D8BFD8",
    "tomato": "#FF6347", "turquoise": "#40E0D0", "violet": "#EE82EE", "wheat": "#F5DEB3", "white": "#FFFFFF",
    "whitesmoke": "#F5F5F5", "yellow": "#FFFF00", "yellowgreen": "#9ACD32",
}

#: Deterministic wording for `describe`: hue by degree band, then saturation and lightness by HSL percent.
_HUE_BANDS: tuple[tuple[float, str], ...] = (
    (8, "red"), (22, "red-orange"), (40, "orange"), (50, "amber"), (65, "yellow"), (85, "yellow-green"), (150, "green"),
    (170, "blue-green"), (195, "cyan"), (225, "azure"), (255, "blue"), (275, "violet"), (300, "purple"), (330, "magenta"),
    (352, "rose"), (360, "red"),
)
_SAT_BANDS: tuple[tuple[float, str], ...] = ((25, "greyish"), (50, "muted"), (75, "strong"), (101, "vivid"))
_LIGHT_BANDS: tuple[tuple[float, str], ...] = (
    (12, "very dark"), (30, "dark"), (45, "medium-dark"), (52, "medium"), (68, "medium-light"), (85, "light"), (101, "very light"),
)
_NEUTRAL_SAT = 8  # HSL saturation below which a colour is called grey

RGB = tuple[float, float, float]


# --------------------------------------------------------------------------- #
# Arithmetic
# --------------------------------------------------------------------------- #


def _rd(x: float, d: int = 0) -> int | float:
    """Half-up rounding through Decimal, so 127.5 is 128 and -0.0 never leaks."""
    q = Decimal(repr(float(x))).quantize(Decimal(1).scaleb(-d), rounding=ROUND_HALF_UP)
    return int(q) if d == 0 else float(q) + 0.0


def _chan(x: float) -> int:
    return min(255, max(0, int(_rd(x))))


def _hex(rgb: RGB, alpha: float = 1.0) -> str:
    r, g, b = (_chan(c) for c in rgb)
    s = f"#{r:02X}{g:02X}{b:02X}"
    return s + (f"{_chan(alpha * 255):02X}" if alpha < 1 else "")


def _hex_to_rgb(h: str) -> RGB:
    return (int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16))


def _rgb_to_hsl(rgb: RGB) -> tuple[float, float, float]:
    r, g, b = (c / 255 for c in rgb)
    hi, lo = max(r, g, b), min(r, g, b)
    d = hi - lo
    lum = (hi + lo) / 2
    if d == 0:
        return 0.0, 0.0, lum * 100
    sat = d / (1 - abs(2 * lum - 1))
    return _hue(r, g, b, hi, d), sat * 100, lum * 100


def _rgb_to_hsv(rgb: RGB) -> tuple[float, float, float]:
    r, g, b = (c / 255 for c in rgb)
    hi, lo = max(r, g, b), min(r, g, b)
    d = hi - lo
    if d == 0 or hi == 0:
        return 0.0, 0.0, hi * 100
    return _hue(r, g, b, hi, d), d / hi * 100, hi * 100


def _hue(r: float, g: float, b: float, hi: float, d: float) -> float:
    if hi == r:
        h = ((g - b) / d) % 6
    elif hi == g:
        h = (b - r) / d + 2
    else:
        h = (r - g) / d + 4
    return (h * 60) % 360


def _hue_chroma_to_rgb(h: float, c: float, m: float) -> RGB:
    hp = (h % 360) / 60
    x = c * (1 - abs(hp % 2 - 1))
    r, g, b = [(c, x, 0), (x, c, 0), (0, c, x), (0, x, c), (x, 0, c), (c, 0, x)][int(hp) % 6]
    return ((r + m) * 255, (g + m) * 255, (b + m) * 255)


def _hsl_to_rgb(h: float, s: float, lum: float) -> RGB:
    s, lum = s / 100, lum / 100
    c = (1 - abs(2 * lum - 1)) * s
    return _hue_chroma_to_rgb(h, c, lum - c / 2)


def _hsv_to_rgb(h: float, s: float, v: float) -> RGB:
    s, v = s / 100, v / 100
    c = v * s
    return _hue_chroma_to_rgb(h, c, v - c)


def _rgb_to_cmyk(rgb: RGB) -> tuple[float, float, float, float]:
    r, g, b = (c / 255 for c in rgb)
    k = 1 - max(r, g, b)
    if k >= 1:
        return 0.0, 0.0, 0.0, 100.0
    return tuple(100 * (1 - c - k) / (1 - k) for c in (r, g, b)) + (k * 100,)  # type: ignore[return-value]


def _cmyk_to_rgb(c: float, m: float, y: float, k: float) -> RGB:
    return tuple(255 * (1 - v / 100) * (1 - k / 100) for v in (c, m, y))  # type: ignore[return-value]


def _linear(c: float) -> float:
    """sRGB channel (0-255) to linear light (0-1)."""
    v = c / 255
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def _encode(v: float) -> float:
    """Linear light (0-1) to sRGB channel (0-255), clipped to the gamut."""
    v = min(1.0, max(0.0, v))
    return 255 * (v * 12.92 if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055)


_XN, _YN, _ZN = 0.95047, 1.0, 1.08883


def _rgb_to_lab(rgb: RGB) -> tuple[float, float, float]:
    r, g, b = (_linear(c) for c in rgb)
    x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / _XN
    y = (0.2126729 * r + 0.7151522 * g + 0.0721750 * b) / _YN
    z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / _ZN

    def f(t: float) -> float:
        return t ** (1 / 3) if t > (6 / 29) ** 3 else t / (3 * (6 / 29) ** 2) + 4 / 29

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def _lab_to_rgb(lab: tuple[float, float, float]) -> tuple[RGB, bool]:
    """Back to sRGB; the flag says whether any channel had to be clipped into the gamut."""
    lum, a, b = lab
    fy = (lum + 16) / 116
    fx, fz = a / 500 + fy, fy - b / 200

    def finv(t: float) -> float:
        return t**3 if t > 6 / 29 else 3 * (6 / 29) ** 2 * (t - 4 / 29)

    x, y, z = finv(fx) * _XN, finv(fy) * _YN, finv(fz) * _ZN
    lin = (
        3.2404542 * x - 1.5371385 * y - 0.4985314 * z,
        -0.9692660 * x + 1.8760108 * y + 0.0415560 * z,
        0.0556434 * x - 0.2040259 * y + 1.0572252 * z,
    )
    clipped = any(v < -1e-6 or v > 1 + 1e-6 for v in lin)
    return tuple(_encode(v) for v in lin), clipped  # type: ignore[return-value]


def _delta_e(a: RGB, b: RGB) -> float:
    return math.dist(_rgb_to_lab(a), _rgb_to_lab(b))


def _luminance(rgb: RGB) -> float:
    """WCAG 2.x relative luminance."""
    r, g, b = (_linear(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _ratio(fg: RGB, bg: RGB) -> float:
    lf, lb = _luminance(fg), _luminance(bg)
    hi, lo = max(lf, lb), min(lf, lb)
    return (hi + 0.05) / (lo + 0.05)


# --------------------------------------------------------------------------- #
# Parsing - every scheme, or a refusal
# --------------------------------------------------------------------------- #

_HEX = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_FUNC = re.compile(r"^([a-zA-Z]+)\s*\((.*)\)$")
_BARE = re.compile(r"^[\d.\s,/%+-]+$")
_NUM = re.compile(r"^([+-]?(?:\d+\.?\d*|\.\d+))(%|deg)?$")


def _components(body: str) -> list[str]:
    return [t for t in re.split(r"[\s,/]+", body.strip()) if t]


def _num(tok: str, scheme: str) -> tuple[float, str]:
    m = _NUM.match(tok)
    if not m:
        raise ToolError(f"'{tok}' in {scheme}(...) is not a number")
    return float(m.group(1)), m.group(2) or ""


def _channel(tok: str, scheme: str) -> float:
    v, unit = _num(tok, scheme)
    v = v * 2.55 if unit == "%" else v
    if not 0 <= v <= 255:
        raise ToolError(f"{scheme} channel '{tok}' is outside 0-255")
    return v


def _percent(tok: str, scheme: str, what: str) -> float:
    v, _unit = _num(tok, scheme)
    if not 0 <= v <= 100:
        raise ToolError(f"{scheme} {what} '{tok}' is outside 0-100%")
    return v


def _alpha(tok: str, scheme: str) -> float:
    v, unit = _num(tok, scheme)
    v = v / 100 if unit == "%" else v
    if not 0 <= v <= 1:
        raise ToolError(f"{scheme} alpha '{tok}' is outside 0-1")
    return v


def _arity(parts: list[str], scheme: str, n: int) -> None:
    if len(parts) not in (n, n + 1):
        raise ToolError(f"{scheme}(...) takes {n} components plus an optional alpha, got {len(parts)}")


def _parse(value: Any, what: str = "value") -> tuple[RGB, float, list[str]]:
    """A colour string to (rgb 0-255, alpha 0-1, assumptions)."""
    if value is None:
        raise ToolError(f"'{what}' is required")
    if not isinstance(value, str):
        raise ToolError(f"{what} must be a string such as '#F13A1A', 'rgb(241, 58, 26)' or 'tomato'")
    s = value.strip()
    if not s:
        raise ToolError(f"{what} is empty")
    if s.lower() in CSS_NAMES:
        return _hex_to_rgb(CSS_NAMES[s.lower()]), 1.0, []
    m = _HEX.match(s)
    if m:
        digits = m.group(1)
        notes = [] if s.startswith("#") else [f"'{s}' read as hex #{digits.upper()}"]
        if len(digits) in (3, 4):
            digits = "".join(c * 2 for c in digits)
        alpha = int(digits[6:8], 16) / 255 if len(digits) == 8 else 1.0
        return _hex_to_rgb("#" + digits), alpha, notes
    m = _FUNC.match(s)
    if m:
        return _from_scheme(m.group(1).lower(), _components(m.group(2)))
    if _BARE.match(s) and len(_components(s)) in (3, 4):
        raise Ambiguous(
            f"'{s}' has no colour scheme: as rgb, hsl and hsv it is three different colours. Resend it as rgb({s}), hsl({s}) or hsv({s}).",
            "scheme",
            ["rgb", "hsl", "hsv"],
        )
    raise ToolError(f"cannot read '{s}' as a colour: use #hex (3/4/6/8 digits), rgb()/hsl()/hsv()/hsb()/cmyk(), or one of the 148 CSS colour names")


def _from_scheme(scheme: str, parts: list[str]) -> tuple[RGB, float, list[str]]:
    base = scheme.rstrip("a") if scheme in ("rgba", "hsla", "hsva", "hsba") else scheme
    if base == "rgb":
        _arity(parts, scheme, 3)
        rgb = tuple(_channel(t, scheme) for t in parts[:3])
        return rgb, (_alpha(parts[3], scheme) if len(parts) == 4 else 1.0), []  # type: ignore[return-value]
    if base in ("hsl", "hsv", "hsb"):
        _arity(parts, scheme, 3)
        h = _num(parts[0], scheme)[0]
        s, x = _percent(parts[1], scheme, "saturation"), _percent(parts[2], scheme, "lightness" if base == "hsl" else "value")
        rgb = _hsl_to_rgb(h, s, x) if base == "hsl" else _hsv_to_rgb(h, s, x)
        return rgb, (_alpha(parts[3], scheme) if len(parts) == 4 else 1.0), []
    if base == "cmyk":
        if len(parts) != 4:
            raise ToolError(f"cmyk(...) takes 4 components, got {len(parts)}")
        nums = [_num(t, scheme) for t in parts]
        notes: list[str] = []
        if all(v <= 1 and unit != "%" for v, unit in nums) and any("." in t for t in parts):
            nums = [(v * 100, "%") for v, _u in nums]
            notes.append("cmyk fractions 0-1 read as percentages")
        for (v, _u), t in zip(nums, parts, strict=True):
            if not 0 <= v <= 100:
                raise ToolError(f"cmyk component '{t}' is outside 0-100%")
        return _cmyk_to_rgb(*(v for v, _u in nums)), 1.0, notes
    raise ToolError(f"unknown colour scheme '{scheme}': use rgb, hsl, hsv/hsb or cmyk")


def _decimals(p: dict[str, Any]) -> int:
    d = p.get("decimals", 0)
    if not isinstance(d, int) or isinstance(d, bool) or not 0 <= d <= 6:
        raise ToolError("decimals must be a whole number from 0 to 6")
    return d


def _spaces(p: dict[str, Any]) -> tuple[str, ...]:
    raw = p.get("spaces")
    if raw is None:
        return SPACES
    items = [raw] if isinstance(raw, str) else list(raw)
    out: list[str] = []
    for item in items:
        name = _SPACE_ALIASES.get(str(item).lower(), str(item).lower())
        if name not in SPACES:
            raise ToolError(f"spaces must be from {', '.join(SPACES)} (hsb is hsv); got '{item}'")
        if name not in out:
            out.append(name)
    return tuple(out)


def _block(rgb: RGB, alpha: float, d: int, spaces: tuple[str, ...] = SPACES) -> dict[str, Any]:
    """One colour in every requested space, numbers rounded to `d` decimals."""
    out: dict[str, Any] = {}
    r, g, b = (_chan(c) for c in rgb)
    a = _rd(alpha, 3)
    if "hex" in spaces:
        out["hex"] = _hex(rgb, alpha)
    if "rgb" in spaces:
        css = f"rgb({r}, {g}, {b})" if alpha >= 1 else f"rgba({r}, {g}, {b}, {a})"
        out["rgb"] = {"r": r, "g": g, "b": b, "css": css}
    if "hsl" in spaces:
        h, s, lum = (_rd(v, d) for v in _rgb_to_hsl(rgb))
        out["hsl"] = {"h": h, "s": s, "l": lum, "css": f"hsl({h}, {s}%, {lum}%)"}
    if "hsv" in spaces:
        h, s, v = (_rd(x, d) for x in _rgb_to_hsv(rgb))
        out["hsv"] = {"h": h, "s": s, "v": v, "css": f"hsv({h}, {s}%, {v}%)"}
    if "cmyk" in spaces:
        c, m, y, k = (_rd(x, d) for x in _rgb_to_cmyk(rgb))
        out["cmyk"] = {"c": c, "m": m, "y": y, "k": k, "css": f"cmyk({c}%, {m}%, {y}%, {k}%)"}
    if "lab" in spaces:
        lum, a_, b_ = (_rd(x, 2) for x in _rgb_to_lab(rgb))
        out["lab"] = {"l": lum, "a": a_, "b": b_}
    out["alpha"] = a
    return out


# --------------------------------------------------------------------------- #
# PNG - a solid strip, written with zlib and struct only
# --------------------------------------------------------------------------- #


def _size(p: dict[str, Any], notes: list[str]) -> int:
    size = p.get("size")
    if size is None:
        notes.append(f"size not given: {SWATCH_DEFAULT} px")
        return SWATCH_DEFAULT
    if not isinstance(size, int) or isinstance(size, bool) or not SWATCH_MIN <= size <= SWATCH_MAX:
        raise ToolError(f"size must be a whole number of pixels from {SWATCH_MIN} to {SWATCH_MAX}")
    return size


def _png_strip(colours: list[tuple[RGB, float]], size: int) -> dict[str, Any]:
    """Each colour as a `size`×`size` square, left to right, as RGBA PNG."""
    width, height = size * len(colours), size

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    row = b"\x00" + b"".join(bytes((_chan(r), _chan(g), _chan(b), _chan(a * 255))) * size for (r, g, b), a in colours)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(row * height, 9))
        + chunk(b"IEND", b"")
    )
    return {"width": width, "height": height, "mime": "image/png", "bytes": len(png), "png_base64": base64.b64encode(png).decode()}


# --------------------------------------------------------------------------- #
# Modes
# --------------------------------------------------------------------------- #


def _convert(p: dict[str, Any]) -> dict[str, Any]:
    d = _decimals(p)
    spaces = _spaces(p)
    rgb, alpha, notes = _parse(p.get("value"))
    if "cmyk" in spaces:
        notes.append("CMYK is the naive sRGB complement (K = 1 - max(R, G, B)), no ICC profile")
    return ok(_block(rgb, alpha, d, spaces), assumptions=notes)


@lru_cache(maxsize=1)
def _named() -> list[tuple[str, list[str], str, RGB]]:
    """The CSS names with duplicates folded: (name, aliases, hex, rgb)."""
    by_hex: dict[str, list[str]] = {}
    for name, h in CSS_NAMES.items():
        by_hex.setdefault(h, []).append(name)
    return [(names[0], names[1:], h, _hex_to_rgb(h)) for h, names in by_hex.items()]


def _band(value: float, bands: tuple[tuple[float, str], ...]) -> str:
    return next(word for limit, word in bands if value < limit)


def _tone(rgb: RGB) -> tuple[dict[str, Any], str]:
    h, s, lum = _rgb_to_hsl(rgb)
    if lum <= 3:
        return {"hue": None, "saturation": None, "lightness": "black"}, "black"
    if lum >= 97:
        return {"hue": None, "saturation": None, "lightness": "white"}, "white"
    light = _band(lum, _LIGHT_BANDS)
    if s < _NEUTRAL_SAT:
        return {"hue": None, "saturation": "neutral", "lightness": light}, f"{light} grey"
    hue, sat = _band(h, _HUE_BANDS), _band(s, _SAT_BANDS)
    return {"hue": hue, "saturation": sat, "lightness": light}, f"{sat} {hue}, {light}"


def _describe(p: dict[str, Any]) -> dict[str, Any]:
    rgb, alpha, notes = _parse(p.get("value"))
    ranked = sorted(((_delta_e(rgb, c), name, aliases, h) for name, aliases, h, c in _named()), key=lambda t: (t[0], t[1]))
    de, name, aliases, h = ranked[0]
    de2, name2, aliases2, h2 = ranked[1]
    tone, description = _tone(rgb)
    out = {
        "hex": _hex(rgb, alpha),
        "nearest": {"name": name, "hex": h, "delta_e": _rd(de, 2), **({"aliases": aliases} if aliases else {})},
        "exact": de < 1e-9,
        "runner_up": {"name": name2, "hex": h2, "delta_e": _rd(de2, 2), **({"aliases": aliases2} if aliases2 else {})},
        "description": description,
        "tone": tone,
    }
    return ok(out, assumptions=notes + [DELTA_E_NOTE, "wording comes from fixed HSL bands: hue by degree, saturation and lightness by percent"])


def _swatch(p: dict[str, Any]) -> dict[str, Any]:
    rgb, alpha, notes = _parse(p.get("value"))
    colours = [(rgb, alpha)]
    if p.get("other") is not None:
        rgb2, alpha2, notes2 = _parse(p["other"], "other")
        colours.append((rgb2, alpha2))
        notes += notes2
    size = _size(p, notes)
    return ok({**_png_strip(colours, size), "colours": [_hex(c, a) for c, a in colours]}, assumptions=notes)


def _contrast(p: dict[str, Any]) -> dict[str, Any]:
    fg, fa, notes = _parse(p.get("value"))
    bg, ba, notes2 = _parse(p.get("other"), "other")
    notes += notes2
    level = str(p.get("level") or "AA").upper()
    if level not in WCAG:
        raise ToolError("level must be AA or AAA")
    if p.get("level") is None:
        notes.append("level not given: AA")
    warnings = ["alpha ignored: contrast is computed on the opaque colours"] if fa < 1 or ba < 1 else []
    ratio = _ratio(fg, bg)
    target = WCAG[level]["normal_text"]
    passes = ratio >= target
    suggestion = None
    if not passes:
        h, s, lum = _rgb_to_hsl(fg)
        for step in range(1, 101):
            found = []
            for direction, word in ((-1, "darker"), (1, "lighter")):
                l2 = lum + direction * step
                if 0 <= l2 <= 100:
                    cand = _hsl_to_rgb(h, s, l2)
                    r2 = _ratio(cand, bg)
                    if r2 >= target:
                        found.append((r2, word, cand, l2))
            if found:
                r2, word, cand, l2 = max(found)
                suggestion = {"foreground": _hex(cand), "ratio": _rd(r2, 2), "direction": word, "lightness": {"from": _rd(lum), "to": _rd(l2)}}
                break
        if suggestion is None:
            warnings.append(f"no lightness change of the foreground reaches {target}:1 on this background; change the background too")
    out = {
        "foreground": _hex(fg),
        "background": _hex(bg),
        "ratio": _rd(ratio, 2),
        "luminance": {"foreground": _rd(_luminance(fg), 4), "background": _rd(_luminance(bg), 4)},
        "wcag": {lvl.lower(): {k: ratio >= v for k, v in th.items()} for lvl, th in WCAG.items()},
        "level": level,
        "target": target,
        "passes": passes,
        "suggestion": suggestion,
    }
    return ok(out, assumptions=notes + ["WCAG 2.x relative luminance; ratio (L1 + 0.05) / (L2 + 0.05); the suggestion steps HSL lightness 1% at a time"], warnings=warnings)


def _mix(p: dict[str, Any]) -> dict[str, Any]:
    d = _decimals(p)
    a, aa, notes = _parse(p.get("value"))
    b, ba, notes2 = _parse(p.get("other"), "other")
    notes += notes2
    t = p.get("ratio")
    if t is None:
        t = 0.5
        notes.append("ratio not given: 0.5, equal parts")
    if isinstance(t, bool) or not isinstance(t, (int, float)) or not 0 <= t <= 1:
        raise ToolError("ratio must be between 0 (all of value) and 1 (all of other)")
    space = str(p.get("space") or "srgb").lower()
    if space not in ("srgb", "lab"):
        raise ToolError("space must be srgb or lab")
    if p.get("space") is None:
        notes.append("space not given: mixed in sRGB (gamma-encoded channels); pass space=lab for a perceptual blend")
    notes.append("ratio is the share of `other`")
    warnings: list[str] = []
    if space == "srgb":
        mixed: RGB = tuple(x * (1 - t) + y * t for x, y in zip(a, b, strict=True))  # type: ignore[assignment]
    else:
        la, lb = _rgb_to_lab(a), _rgb_to_lab(b)
        mixed, clipped = _lab_to_rgb(tuple(x * (1 - t) + y * t for x, y in zip(la, lb, strict=True)))  # type: ignore[arg-type]
        if clipped:
            warnings.append("the Lab blend fell outside the sRGB gamut; channels clipped")
    alpha = aa * (1 - t) + ba * t
    return ok({"colours": [_hex(a, aa), _hex(b, ba)], "ratio": t, "space": space, "mix": _block(mixed, alpha, d)}, assumptions=notes, warnings=warnings)


def _harmony(p: dict[str, Any]) -> dict[str, Any]:
    rgb, alpha, notes = _parse(p.get("value"))
    h, s, lum = _rgb_to_hsl(rgb)

    def scheme(offsets: tuple[int, ...]) -> dict[str, Any]:
        hues = [_rd((h + o) % 360) for o in offsets]
        return {"hues": hues, "colours": [_hex(_hsl_to_rgb(h + o, s, lum)) for o in offsets]}

    kind = p.get("kind")
    if kind is None:
        notes.append("kind not given: every scheme returned")
        return ok({"base": _hex(rgb), "hue": _rd(h), "schemes": {k: scheme(v) for k, v in HARMONIES.items()}}, assumptions=notes + ["rotation in HSL hue; saturation and lightness kept"])
    key = str(kind).lower().replace("-", "_")
    if key not in HARMONIES:
        raise ToolError(f"kind must be one of {', '.join(HARMONIES)}")
    return ok({"base": _hex(rgb), "hue": _rd(h), "kind": key, **scheme(HARMONIES[key])}, assumptions=notes + ["rotation in HSL hue; saturation and lightness kept"])


def _nearest(p: dict[str, Any]) -> dict[str, Any]:
    rgb, alpha, notes = _parse(p.get("value"))
    palette = p.get("palette")
    if not isinstance(palette, list) or not palette:
        raise ToolError("palette must be a non-empty list of colours, e.g. ['#0B5FFF', '#F13A1A']")
    ranked = []
    for i, entry in enumerate(palette):
        try:
            c, _a, _n = _parse(entry, f"palette[{i}]")
        except ToolError as e:
            raise ToolError(f"palette[{i}]: {e.message}") from None
        ranked.append((_delta_e(rgb, c), i, {"colour": entry, "hex": _hex(c)}))
    ranked.sort(key=lambda t: (t[0], t[1]))
    de, _i, best = ranked[0]
    out = {"input": _hex(rgb, alpha), "nearest": {**best, "delta_e": _rd(de, 2)}, "runner_up": None, "candidates": len(palette)}
    if len(ranked) > 1:
        de2, _i2, second = ranked[1]
        out["runner_up"] = {**second, "delta_e": _rd(de2, 2)}
    return ok(out, assumptions=notes + [DELTA_E_NOTE])


def _simulate_one(rgb: RGB, matrix: tuple[tuple[float, float, float], ...]) -> RGB:
    lin = [_linear(c) for c in rgb]
    return tuple(_encode(sum(m * v for m, v in zip(row, lin, strict=True))) for row in matrix)  # type: ignore[return-value]


def _simulate(p: dict[str, Any]) -> dict[str, Any]:
    rgb, alpha, notes = _parse(p.get("value"))
    kind = p.get("kind")
    if kind is None:
        kinds = list(DEFICIENCIES)
        notes.append("kind not given: all three dichromacies returned")
    else:
        key = str(kind).lower()
        if key == "all":
            kinds = list(DEFICIENCIES)
        elif key in DEFICIENCIES:
            kinds = [key]
        else:
            raise ToolError(f"kind must be one of {', '.join(DEFICIENCIES)} or all")
    seen = {k: _simulate_one(rgb, DEFICIENCIES[k]) for k in kinds}

    def entry(c: RGB) -> dict[str, Any]:
        return {"hex": _hex(c), "rgb": {"r": _chan(c[0]), "g": _chan(c[1]), "b": _chan(c[2])}, "delta_e": _rd(_delta_e(rgb, c), 2)}

    out: dict[str, Any] = {"input": _hex(rgb)}
    if len(kinds) == 1:
        e = entry(seen[kinds[0]])
        out.update({"kind": kinds[0], "simulated": {"hex": e["hex"], "rgb": e["rgb"]}, "delta_e": e["delta_e"]})
    else:
        out["simulated"] = {k: entry(c) for k, c in seen.items()}
    if p.get("image"):
        out["image"] = _png_strip([(rgb, 1.0)] + [(seen[k], 1.0) for k in kinds], _size(p, notes))
    return ok(out, assumptions=notes + ["Viénot, Brettel & Mollon (1999) dichromat projection applied to linearised sRGB; " + DELTA_E_NOTE])


def _grey_level(rgb: RGB, method: str) -> float:
    r, g, b = rgb
    if method == "rec709":
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    if method == "rec601":
        return 0.299 * r + 0.587 * g + 0.114 * b
    if method == "average":
        return (r + g + b) / 3
    if method == "hsl":
        return (max(rgb) + min(rgb)) / 2
    lum = _rgb_to_lab(rgb)[0]
    return _lab_to_rgb((lum, 0.0, 0.0))[0][0]


def _grayscale(p: dict[str, Any]) -> dict[str, Any]:
    rgb, alpha, notes = _parse(p.get("value"))
    method = p.get("method")
    if method is None:
        method = "rec709"
        notes.append("method not given: rec709 luma (Y' = 0.2126 R' + 0.7152 G' + 0.0722 B' on gamma-encoded channels, what most image tools do)")
    method = str(method).lower()
    if method != "all" and method not in GRAY_METHODS:
        raise ToolError(f"method must be one of {', '.join(GRAY_METHODS)} or all")
    ramp = p.get("ramp")
    if ramp is not None:
        if method == "all":
            raise ToolError("ramp needs a single method, not all")
        if isinstance(ramp, bool) or not isinstance(ramp, int) or not 2 <= ramp <= RAMP_MAX:
            raise ToolError(f"ramp must be a whole number of steps from 2 to {RAMP_MAX}")

    def grey(m: str) -> tuple[dict[str, Any], RGB]:
        v = _grey_level(rgb, m)
        c = (v, v, v)
        return {"hex": _hex(c), "value": _chan(v), "percent": _rd(v / 255 * 100, 1)}, c

    out: dict[str, Any] = {"input": _hex(rgb)}
    strip: list[tuple[RGB, float]]
    if method == "all":
        greys = {m: grey(m) for m in GRAY_METHODS}
        out["greys"] = {m: g for m, (g, _c) in greys.items()}
        strip = [(rgb, 1.0)] + [(c, 1.0) for _g, c in greys.values()]
    else:
        g, c = grey(method)
        out.update({"method": method, "grey": g})
        strip = [(rgb, 1.0), (c, 1.0)]
        if ramp is not None:
            steps = [tuple(x + (y - x) * i / (ramp - 1) for x, y in zip(rgb, c, strict=True)) for i in range(ramp)]
            out["ramp"] = [_hex(s) for s in steps]  # type: ignore[arg-type]
            strip = [(s, 1.0) for s in steps]  # type: ignore[misc]
    if p.get("image"):
        out["image"] = _png_strip(strip, _size(p, notes))
    return ok(out, assumptions=notes)


@tool
def color(mode: str = "convert", **params: Any) -> dict[str, Any]:
    """Colour arithmetic. Modes: convert, describe, swatch, contrast, mix, harmony, nearest, simulate, grayscale."""
    if mode not in MODES:
        raise ToolError(f"mode must be one of {', '.join(MODES)}")
    p = {k: v for k, v in params.items() if v is not None}
    return {
        "convert": _convert,
        "describe": _describe,
        "swatch": _swatch,
        "contrast": _contrast,
        "mix": _mix,
        "harmony": _harmony,
        "nearest": _nearest,
        "simulate": _simulate,
        "grayscale": _grayscale,
    }[mode](p)


#: Worked examples for the reference page, one list per mode. Every one of them is
#: executed when /docs/tools/color is built and sorted by the result into "Examples"
#: and "Fails when", so a fixture never states an expectation of its own. The swatch
#: responses embed a real PNG: a solid square compresses to a few hundred Base64 characters.
EXAMPLES: dict[str, list[dict[str, Any]]] = {
    "convert": [
        {
            "caption": "A hex colour in every space: RGB, HSL, HSV, naive CMYK and Lab.",
            "args": {"mode": "convert", "value": "#F13A1A"},
        },
        {
            "caption": "Short hex expands; only the requested spaces come back.",
            "args": {"mode": "convert", "value": "#F3A", "spaces": ["hex", "rgb"]},
        },
        {
            "caption": "From HSL, with one decimal on the derived numbers.",
            "args": {"mode": "convert", "value": "hsl(9, 88%, 52%)", "spaces": ["hex", "hsv", "cmyk"], "decimals": 1},
        },
        {
            "caption": "Alpha is preserved: an 8-digit hex, or rgba(), stays translucent.",
            "args": {"mode": "convert", "value": "rgba(241, 58, 26, 0.5)", "spaces": ["hex", "rgb"]},
        },
        {
            "caption": "A CSS colour name.",
            "args": {"mode": "convert", "value": "tomato", "spaces": ["hex", "rgb", "hsl"]},
        },
        {
            "caption": "Three bare numbers have no scheme, so nothing is guessed.",
            "args": {"mode": "convert", "value": "58, 26, 241"},
        },
        {
            "caption": "A channel outside its range.",
            "args": {"mode": "convert", "value": "rgb(300, 0, 0)"},
        },
    ],
    "describe": [
        {
            "caption": "The nearest CSS name by Lab ΔE, the runner-up, and a fixed-wording description.",
            "args": {"mode": "describe", "value": "#F13A1A"},
        },
        {
            "caption": "An exact hit: ΔE 0, `exact: true`.",
            "args": {"mode": "describe", "value": "#663399"},
        },
        {
            "caption": "A neutral: named with its alias, described as a grey.",
            "args": {"mode": "describe", "value": "#808080"},
        },
        {
            "caption": "Not a colour.",
            "args": {"mode": "describe", "value": "not-a-colour"},
        },
    ],
    "swatch": [
        {
            "caption": "A solid 64 px PNG of the colour, as Base64, for a multimodal agent to look at.",
            "args": {"mode": "swatch", "value": "#F13A1A"},
        },
        {
            "caption": "Two colours side by side, 32 px each.",
            "args": {"mode": "swatch", "value": "#F13A1A", "other": "#1A6FF1", "size": 32},
        },
        {
            "caption": "The size is bounded.",
            "args": {"mode": "swatch", "value": "#F13A1A", "size": 8},
        },
    ],
    "contrast": [
        {
            "caption": "Mid grey on white fails AA for normal text; the suggestion is the smallest lightness change that passes.",
            "args": {"mode": "contrast", "value": "#777777", "other": "#FFFFFF"},
        },
        {
            "caption": "Black on white: 21:1, everything passes, nothing to suggest.",
            "args": {"mode": "contrast", "value": "#000000", "other": "#FFFFFF"},
        },
        {
            "caption": "Aiming for AAA.",
            "args": {"mode": "contrast", "value": "#1A6FF1", "other": "#FFFFFF", "level": "AAA"},
        },
        {
            "caption": "Only AA and AAA exist.",
            "args": {"mode": "contrast", "value": "#000000", "other": "#FFFFFF", "level": "AAAA"},
        },
    ],
    "mix": [
        {
            "caption": "Equal parts in sRGB, the default, and it says so.",
            "args": {"mode": "mix", "value": "#F13A1A", "other": "#1A6FF1"},
        },
        {
            "caption": "A quarter of the second colour, blended in Lab.",
            "args": {"mode": "mix", "value": "#F13A1A", "other": "#1A6FF1", "ratio": 0.25, "space": "lab"},
        },
        {
            "caption": "The ratio is a share, 0 to 1.",
            "args": {"mode": "mix", "value": "#F13A1A", "other": "#1A6FF1", "ratio": 2},
        },
    ],
    "harmony": [
        {
            "caption": "A triad by hue rotation.",
            "args": {"mode": "harmony", "value": "#F13A1A", "kind": "triadic"},
        },
        {
            "caption": "Every scheme at once.",
            "args": {"mode": "harmony", "value": "#F13A1A"},
        },
        {
            "caption": "An unknown scheme.",
            "args": {"mode": "harmony", "value": "#F13A1A", "kind": "monochrome"},
        },
    ],
    "nearest": [
        {
            "caption": "Snapping a colour to a brand palette: the winner, its distance, and the runner-up.",
            "args": {"mode": "nearest", "value": "#E8452C", "palette": ["#0B5FFF", "#F13A1A", "#1DB954", "#FFB000", "#111111"]},
        },
        {
            "caption": "Palette entries may use any scheme or name.",
            "args": {"mode": "nearest", "value": "rgb(250, 5, 5)", "palette": ["tomato", "hsl(120, 100%, 50%)", "#0000FF"]},
        },
        {
            "caption": "A palette is required.",
            "args": {"mode": "nearest", "value": "#E8452C"},
        },
    ],
    "simulate": [
        {
            "caption": "The colour as seen with each dichromacy, with the distance from the original.",
            "args": {"mode": "simulate", "value": "#F13A1A"},
        },
        {
            "caption": "One deficiency, with a strip of original and simulated.",
            "args": {"mode": "simulate", "value": "#F13A1A", "kind": "deuteranopia", "image": True, "size": 32},
        },
        {
            "caption": "Only the three dichromacies are modelled.",
            "args": {"mode": "simulate", "value": "#F13A1A", "kind": "achromatopsia"},
        },
    ],
    "grayscale": [
        {
            "caption": "The default is rec709 luma, and the response says so.",
            "args": {"mode": "grayscale", "value": "#F13A1A"},
        },
        {
            "caption": "Every method side by side.",
            "args": {"mode": "grayscale", "value": "#F13A1A", "method": "all"},
        },
        {
            "caption": "Perceptual grey with a five-step ramp and a strip to look at.",
            "args": {"mode": "grayscale", "value": "#F13A1A", "method": "lab", "ramp": 5, "image": True, "size": 24},
        },
        {
            "caption": "An unknown method is not guessed.",
            "args": {"mode": "grayscale", "value": "#F13A1A", "method": "luma"},
        },
    ],
}
