"""Adversarial sweep of `scale`, in the failure classes #52 found in `math`."""

from leftbrain.core.scale import scale


def test_an_explicit_factor_in_inverse_mode_is_inverted():
    """`inverse factor=2` doubled every entity while the note said doubling halves them."""
    r = scale(mode="inverse", factor=2, entities=[{"name": "days", "qty": 5}])
    assert r["ok"] and r["result"]["entities"][0]["scaled"]["value"] == 2.5, r
    assert r["result"]["factor"]["value"] == 0.5
    assert any("divides" in a for a in r["assumptions"]), r["assumptions"]


def test_scaling_to_zero_is_an_answer_in_direct_proportion():
    r = scale(mode="linear", from_qty=4, to_qty=0, entities=[{"name": "flour", "qty": 2}])
    assert r["ok"] and r["result"]["entities"][0]["scaled"]["value"] == 0.0 and r["result"]["percent_change"] == -100, r
    r = scale(mode="inverse", from_qty=4, to_qty=0, entities=[{"name": "days", "qty": 2}])
    assert r["ok"] is False and "inverse" in r["message"]


def test_precision_must_be_a_whole_number():
    r = scale(mode="linear", from_qty=4, to_qty=7, precision="six", entities=[{"name": "flour", "qty": 2}])
    assert r["ok"] is False and "whole number" in r["message"] and "invalid literal" not in r["message"]
