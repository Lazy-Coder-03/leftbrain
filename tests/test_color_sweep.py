"""Adversarial sweep of `color`, in the failure classes #52 found in `math`."""

from leftbrain.core.color import color


def test_cmyk_with_a_bare_1_is_ambiguous():
    """cmyk(0, 1, 1, 0) is red as fractions and near-white as percentages."""
    r = color("convert", value="cmyk(0, 1, 1, 0)", spaces=["hex"])
    assert r["ok"] is False and r["error"] == "ambiguous", r
    assert color("convert", value="cmyk(0, 1.0, 1.0, 0)", spaces=["hex"])["result"]["hex"] == "#FF0000"
    assert color("convert", value="cmyk(0%, 100%, 100%, 0%)", spaces=["hex"])["result"]["hex"] == "#FF0000"
    assert color("convert", value="cmyk(0, 0, 0, 0)", spaces=["hex"])["result"]["hex"] == "#FFFFFF"


def test_the_displayed_contrast_ratio_never_contradicts_the_verdict():
    r = color("contrast", value="#0099FF", other="#ffffff", level="AAA")
    assert r["result"]["ratio"] == 2.99 and r["result"]["ratio_exact"] < 3.0, r["result"]
    assert r["result"]["wcag"]["aa"]["large_text"] is False
    assert color("contrast", value="#000000", other="#ffffff")["result"]["ratio"] == 21.0
