"""color: conversions, names, contrast, blends, harmonies, palettes, dichromacy and greys - arithmetic, never opinion."""

import base64
import struct
import zlib

import pytest

import leftbrain as lb
from leftbrain.core import color as color_mod


def call(mode, **kw):
    return lb.color_tool(mode, **kw)


def png(res):
    """Decode a swatch result: the PNG signature, the IHDR size, and a pixel reader over the raw rows."""
    data = base64.b64decode(res["png_base64"])
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(data) == res["bytes"] and res["mime"] == "image/png"
    w, h = struct.unpack(">II", data[16:24])
    assert (w, h) == (res["width"], res["height"])
    pos, idat = 8, b""
    while pos < len(data):
        ln, tag = struct.unpack(">I4s", data[pos : pos + 8])
        if tag == b"IDAT":
            idat += data[pos + 8 : pos + 8 + ln]
        pos += 12 + ln
    raw = zlib.decompress(idat)
    stride = 1 + w * 4

    def px(x, y):
        o = y * stride + 1 + x * 4
        return tuple(raw[o : o + 4])

    return w, h, px


def close(rgb, expected, tol):
    return all(abs(rgb[k] - v) <= tol for k, v in zip("rgb", expected, strict=True))


# --- the named colours ---------------------------------------------------------


def test_the_css_named_colours_are_all_148():
    assert len(color_mod.CSS_NAMES) == 148
    assert all(len(h) == 7 and h.startswith("#") and int(h[1:], 16) >= 0 for h in color_mod.CSS_NAMES.values())
    assert color_mod.CSS_NAMES["rebeccapurple"] == "#663399"
    assert color_mod.CSS_NAMES["gray"] == color_mod.CSS_NAMES["grey"] == "#808080"
    assert color_mod.CSS_NAMES["aqua"] == color_mod.CSS_NAMES["cyan"]


# --- convert ---------------------------------------------------------------------


def test_convert_hex_to_every_space():
    r = call("convert", value="#F13A1A")
    assert r["ok"]
    res = r["result"]
    assert res["hex"] == "#F13A1A"
    assert res["rgb"] == {"r": 241, "g": 58, "b": 26, "css": "rgb(241, 58, 26)"}
    assert res["hsl"] == {"h": 9, "s": 88, "l": 52, "css": "hsl(9, 88%, 52%)"}
    assert res["hsv"] == {"h": 9, "s": 89, "v": 95, "css": "hsv(9, 89%, 95%)"}
    assert res["cmyk"] == {"c": 0, "m": 76, "y": 89, "k": 5, "css": "cmyk(0%, 76%, 89%, 5%)"}
    assert res["alpha"] == 1
    assert any("CMYK" in a for a in r["assumptions"])


def test_convert_lab_is_d65():
    lab = call("convert", value="#FF0000", spaces="lab")["result"]["lab"]
    assert 53.2 <= lab["l"] <= 53.3 and 80.0 <= lab["a"] <= 80.2 and 67.1 <= lab["b"] <= 67.3


def test_convert_short_hex_and_alpha_are_kept():
    assert call("convert", value="#F3A")["result"]["hex"] == "#FF33AA"
    half = call("convert", value="#F13A1A80")["result"]
    assert half["hex"] == "#F13A1A80" and half["alpha"] == 0.502
    assert half["rgb"]["css"] == "rgba(241, 58, 26, 0.502)"
    opaque = call("convert", value="#F13A1AFF")["result"]
    assert opaque["hex"] == "#F13A1A" and opaque["alpha"] == 1
    assert call("convert", value="rgb(241 58 26 / 50%)")["result"]["alpha"] == 0.5


def test_convert_from_every_scheme_and_from_names():
    assert call("convert", value="rgb(241, 58, 26)")["result"]["hex"] == "#F13A1A"
    assert close(call("convert", value="hsl(9, 88%, 52%)")["result"]["rgb"], (241, 58, 26), 2)
    assert close(call("convert", value="hsv(9, 89%, 95%)")["result"]["rgb"], (241, 58, 26), 2)
    assert close(call("convert", value="hsb(9, 89%, 95%)")["result"]["rgb"], (241, 58, 26), 2)
    assert close(call("convert", value="cmyk(0, 76, 89, 5)")["result"]["rgb"], (241, 58, 26), 3)
    assert call("convert", value="RebeccaPurple")["result"]["hex"] == "#663399"
    assert call("convert", value="rgb(100%, 0%, 0%)")["result"]["hex"] == "#FF0000"
    bare = call("convert", value="F13A1A")
    assert bare["result"]["hex"] == "#F13A1A" and any("hex" in a for a in bare["assumptions"])


def test_convert_spaces_filters_the_output():
    only = call("convert", value="#F13A1A", spaces="hsl")["result"]
    assert set(only) == {"hsl", "alpha"}
    both = call("convert", value="#F13A1A", spaces=["rgb", "hsb"])["result"]
    assert set(both) == {"rgb", "hsv", "alpha"}
    bad = call("convert", value="#F13A1A", spaces="xyz")
    assert not bad["ok"] and bad["error"] == "invalid_input"


def test_convert_decimals():
    hsl = call("convert", value="#F13A1A", spaces="hsl", decimals=1)["result"]["hsl"]
    assert (hsl["h"], hsl["s"], hsl["l"]) == (8.9, 88.5, 52.4)


def test_a_bare_triple_is_ambiguous_not_guessed():
    for value in ("58, 26, 241", "58 26 241"):
        r = call("convert", value=value)
        assert not r["ok"] and r["error"] == "ambiguous"
        assert r["needs"]["field"] == "scheme"
        assert "rgb" in r["needs"]["options"] and "hsl" in r["needs"]["options"]


@pytest.mark.parametrize(
    ("kwargs", "fragment"),
    [
        ({"value": "rgb(300, 0, 0)"}, "0-255"),
        ({"value": "notacolour"}, "cannot read"),
        ({"value": "hsl(9, 88%)"}, "hsl"),
        ({}, "'value'"),
        ({"value": "#F13A1A", "decimals": 9}, "decimals"),
    ],
)
def test_convert_rejects_bad_input(kwargs, fragment):
    r = call("convert", **kwargs)
    assert not r["ok"] and r["error"] == "invalid_input" and fragment in r["message"]


def test_unknown_mode():
    r = call("paint", value="#F13A1A")
    assert not r["ok"] and "mode must be one of" in r["message"]


# --- describe ---------------------------------------------------------------------


def test_describe_names_the_nearest_css_colour():
    red = call("describe", value="#FF0000")
    assert red["result"]["nearest"] == {"name": "red", "hex": "#FF0000", "delta_e": 0.0}
    assert red["result"]["exact"] is True
    assert call("describe", value="#663399")["result"]["nearest"]["name"] == "rebeccapurple"
    r = call("describe", value="#F13A1A")
    assert r["ok"] and r["result"]["exact"] is False
    assert r["result"]["nearest"]["delta_e"] > 0
    assert r["result"]["runner_up"]["name"] != r["result"]["nearest"]["name"]
    assert r["result"]["runner_up"]["delta_e"] >= r["result"]["nearest"]["delta_e"]
    assert any("CIE76" in a for a in r["assumptions"])


def test_describe_wording_is_a_fixed_table():
    r = call("describe", value="#F13A1A")["result"]
    assert r["description"] == "vivid red-orange, medium-light"
    assert r["tone"] == {"hue": "red-orange", "saturation": "vivid", "lightness": "medium-light"}
    assert call("describe", value="#000000")["result"]["description"] == "black"
    assert call("describe", value="#FFFFFF")["result"]["description"] == "white"
    grey = call("describe", value="#808080")["result"]
    assert grey["description"] == "medium grey"
    assert grey["nearest"]["name"] == "gray" and grey["nearest"]["aliases"] == ["grey"]
    assert call("describe", value="#1A6FF1")["result"]["tone"]["hue"] == "azure"


def test_describe_rejects_bad_input():
    r = call("describe", value="not-a-colour")
    assert not r["ok"] and r["error"] == "invalid_input"


# --- swatch -------------------------------------------------------------------------


def test_swatch_is_a_solid_png():
    r = call("swatch", value="#F13A1A")
    assert r["ok"] and any("64" in a for a in r["assumptions"])
    w, h, px = png(r["result"])
    assert (w, h) == (64, 64) and r["result"]["colours"] == ["#F13A1A"]
    assert px(0, 0) == px(63, 63) == (241, 58, 26, 255)


def test_swatch_pair_side_by_side():
    r = call("swatch", value="#F13A1A", other="#0000FF", size=32)
    w, h, px = png(r["result"])
    assert (w, h) == (64, 32) and r["result"]["colours"] == ["#F13A1A", "#0000FF"]
    assert px(0, 0) == (241, 58, 26, 255) and px(40, 10) == (0, 0, 255, 255)
    assert r["assumptions"] == []


def test_swatch_keeps_alpha():
    _w, _h, px = png(call("swatch", value="#F13A1A80", size=16)["result"])
    assert px(3, 3) == (241, 58, 26, 128)


@pytest.mark.parametrize("size", [8, 300, 0])
def test_swatch_size_must_be_16_to_256(size):
    r = call("swatch", value="#F13A1A", size=size)
    assert not r["ok"] and r["error"] == "invalid_input" and "16" in r["message"]


# --- contrast ----------------------------------------------------------------------


def test_contrast_black_on_white_is_21():
    r = call("contrast", value="#000000", other="#FFFFFF")
    res = r["result"]
    assert res["ratio"] == 21.0
    assert res["wcag"] == {"aa": {"normal_text": True, "large_text": True}, "aaa": {"normal_text": True, "large_text": True}}
    assert res["passes"] is True and res["suggestion"] is None
    assert res["luminance"] == {"foreground": 0.0, "background": 1.0}


def test_contrast_suggests_the_smallest_lightness_change_that_passes():
    r = call("contrast", value="#777777", other="#FFFFFF")
    res = r["result"]
    assert res["ratio"] == 4.48 and res["level"] == "AA" and res["target"] == 4.5
    assert res["wcag"]["aa"] == {"normal_text": False, "large_text": True}
    assert res["passes"] is False
    s = res["suggestion"]
    assert s["direction"] == "darker" and s["ratio"] >= 4.5
    assert s["lightness"]["to"] < s["lightness"]["from"]
    assert call("contrast", value=s["foreground"], other="#FFFFFF")["result"]["passes"] is True
    aaa = call("contrast", value="#777777", other="#FFFFFF", level="aaa")["result"]
    assert aaa["target"] == 7 and aaa["suggestion"]["ratio"] >= 7
    same = call("contrast", value="#FFFFFF", other="#FFFFFF")["result"]
    assert same["ratio"] == 1.0 and same["suggestion"]["direction"] == "darker"


def test_contrast_says_when_no_lightness_change_can_pass():
    r = call("contrast", value="#FFFFFF", other="#808080", level="AAA")
    assert r["ok"] and r["result"]["suggestion"] is None and r["warnings"]


def test_contrast_ignores_alpha_and_says_so():
    r = call("contrast", value="#00000080", other="#FFFFFF")
    assert r["result"]["ratio"] == 21.0 and any("alpha" in w for w in r["warnings"])


def test_contrast_rejects_bad_input():
    assert call("contrast", value="#000000", other="#FFFFFF", level="AAAA")["error"] == "invalid_input"
    r = call("contrast", value="#000000")
    assert not r["ok"] and "'other'" in r["message"]


# --- mix ------------------------------------------------------------------------------


def test_mix_in_srgb():
    r = call("mix", value="#000000", other="#FFFFFF")
    assert r["result"]["mix"]["hex"] == "#808080" and r["result"]["ratio"] == 0.5 and r["result"]["space"] == "srgb"
    assert any("0.5" in a for a in r["assumptions"]) and any("sRGB" in a for a in r["assumptions"])
    assert call("mix", value="#000000", other="#FFFFFF", ratio=0)["result"]["mix"]["hex"] == "#000000"
    assert call("mix", value="#000000", other="#FFFFFF", ratio=1)["result"]["mix"]["hex"] == "#FFFFFF"
    assert call("mix", value="#FF0000", other="#0000FF", ratio=0.5)["result"]["mix"]["hex"] == "#800080"


def test_mix_in_lab_differs_from_srgb():
    lab = call("mix", value="#FF0000", other="#0000FF", space="lab")
    assert lab["ok"] and lab["result"]["space"] == "lab"
    assert lab["result"]["mix"]["hex"] != "#800080"


@pytest.mark.parametrize("kwargs", [{"ratio": 1.5}, {"ratio": -0.1}, {"space": "xyz"}])
def test_mix_rejects_bad_input(kwargs):
    r = call("mix", value="#FF0000", other="#0000FF", **kwargs)
    assert not r["ok"] and r["error"] == "invalid_input"


def test_mix_needs_other():
    r = call("mix", value="#FF0000")
    assert not r["ok"] and "'other'" in r["message"]


# --- harmony ------------------------------------------------------------------------


def test_harmony_by_hue_rotation():
    assert call("harmony", value="#FF0000", kind="complementary")["result"]["colours"] == ["#00FFFF"]
    assert call("harmony", value="#FF0000", kind="triadic")["result"]["colours"] == ["#00FF00", "#0000FF"]
    assert call("harmony", value="#FF0000", kind="analogous")["result"]["colours"] == ["#FF0080", "#FF8000"]
    split = call("harmony", value="#FF0000", kind="split_complementary")["result"]
    assert split["colours"] == ["#00FF80", "#0080FF"] and split["hues"] == [150, 210]
    assert call("harmony", value="#FF0000", kind="split-complementary")["result"]["colours"] == ["#00FF80", "#0080FF"]


def test_harmony_defaults_to_every_scheme():
    r = call("harmony", value="#FF0000")
    assert set(r["result"]["schemes"]) == {"complementary", "analogous", "triadic", "split_complementary"}
    assert r["result"]["base"] == "#FF0000" and r["assumptions"]
    bad = call("harmony", value="#FF0000", kind="monochrome")
    assert not bad["ok"] and bad["error"] == "invalid_input"


# --- nearest ----------------------------------------------------------------------------


def test_nearest_snaps_to_a_palette():
    r = call("nearest", value="#FA0505", palette=["#FF0000", "#00FF00", "#0000FF"])
    assert r["ok"] and r["result"]["nearest"]["colour"] == "#FF0000" and r["result"]["nearest"]["hex"] == "#FF0000"
    assert r["result"]["runner_up"]["delta_e"] > r["result"]["nearest"]["delta_e"]
    assert any("CIE76" in a for a in r["assumptions"])
    single = call("nearest", value="#FA0505", palette=["tomato"])["result"]
    assert single["nearest"]["colour"] == "tomato" and single["runner_up"] is None


@pytest.mark.parametrize("palette", [[], ["#GGGGGG"], None])
def test_nearest_rejects_bad_palettes(palette):
    r = call("nearest", value="#FA0505", palette=palette)
    assert not r["ok"] and r["error"] == "invalid_input" and "palette" in r["message"]


# --- simulate ----------------------------------------------------------------------------


def test_simulate_one_deficiency():
    r = call("simulate", value="#FF0000", kind="protanopia")
    res = r["result"]
    assert res["kind"] == "protanopia" and res["input"] == "#FF0000"
    assert res["simulated"]["hex"].startswith("#") and set(res["simulated"]["rgb"]) == {"r", "g", "b"}
    assert res["delta_e"] > 0


def test_simulate_defaults_to_all_three_and_leaves_grey_alone():
    r = call("simulate", value="#808080")
    assert set(r["result"]["simulated"]) == {"deuteranopia", "protanopia", "tritanopia"}
    assert all(v["hex"] == "#808080" for v in r["result"]["simulated"].values())
    assert any("Viénot" in a for a in r["assumptions"]) and any("all three" in a for a in r["assumptions"])


def test_simulate_strip():
    r = call("simulate", value="#F13A1A", image=True)
    w, h, px = png(r["result"]["image"])
    assert (w, h) == (256, 64) and px(0, 0) == (241, 58, 26, 255)
    one = call("simulate", value="#F13A1A", kind="deuteranopia", image=True, size=16)["result"]["image"]
    assert (one["width"], one["height"]) == (32, 16)
    bad = call("simulate", value="#F13A1A", kind="achromatopsia")
    assert not bad["ok"] and bad["error"] == "invalid_input"


# --- grayscale ------------------------------------------------------------------------------


def test_grayscale_methods():
    r = call("grayscale", value="#FF0000")
    assert r["result"]["method"] == "rec709" and r["result"]["grey"] == {"hex": "#363636", "value": 54, "percent": 21.3}
    assert any("rec709" in a for a in r["assumptions"])
    assert call("grayscale", value="#FF0000", method="rec601")["result"]["grey"]["value"] == 76
    assert call("grayscale", value="#FF0000", method="average")["result"]["grey"]["value"] == 85
    assert call("grayscale", value="#FF0000", method="hsl")["result"]["grey"]["value"] == 128
    assert 126 <= call("grayscale", value="#FF0000", method="lab")["result"]["grey"]["value"] <= 128
    white = call("grayscale", value="#FFFFFF", method="rec709")["result"]["grey"]
    assert white == {"hex": "#FFFFFF", "value": 255, "percent": 100.0}
    every = call("grayscale", value="#FF0000", method="all")["result"]
    assert set(every["greys"]) == {"rec709", "rec601", "lab", "average", "hsl"}


def test_grayscale_ramp_and_strip():
    r = call("grayscale", value="#FF0000", ramp=5)["result"]
    assert len(r["ramp"]) == 5 and r["ramp"][0] == "#FF0000" and r["ramp"][-1] == "#363636"
    img = call("grayscale", value="#FF0000", ramp=3, image=True)["result"]["image"]
    assert (img["width"], img["height"]) == (192, 64)
    strip = call("grayscale", value="#FF0000", method="all", image=True, size=16)["result"]["image"]
    assert (strip["width"], strip["height"]) == (96, 16)


@pytest.mark.parametrize("kwargs", [{"method": "luma"}, {"ramp": 1}, {"method": "all", "ramp": 4}])
def test_grayscale_rejects_bad_input(kwargs):
    r = call("grayscale", value="#FF0000", **kwargs)
    assert not r["ok"] and r["error"] == "invalid_input"


# --- the package and the wrapper ---------------------------------------------------------


def test_examples_cover_every_mode():
    assert set(color_mod.EXAMPLES) == set(color_mod.MODES)
    assert all(len(v) >= 2 for v in color_mod.EXAMPLES.values())


def test_color_is_exported():
    assert lb.TOOLS["color"] is lb.color_tool
    assert lb.color_tool("convert", value="tomato")["result"]["hex"] == "#FF6347"


def test_mcp_wrapper():
    pytest.importorskip("mcp", reason="MCP wrappers need the optional 'mcp' package")
    from leftbrain import mcp_server as mcp

    assert mcp.color(mode="convert", value="#F13A1A")["result"]["rgb"]["r"] == 241
    assert mcp.color(mode="swatch", value="red", size=16)["result"]["width"] == 16
    assert mcp.color(mode="nearest", value="#FA0505", palette=["red", "blue"])["result"]["nearest"]["colour"] == "red"
