"""Adversarial sweep of `random`, in the failure classes #52 found in `math`."""

import time

import pytest

from leftbrain.core.random_ import random_tool


def test_negative_weights_are_refused():
    r = random_tool("pick", items=["a", "b", "c"], weights=[-1, 2, 3], n=5, unique=False, seed=1)
    assert r["ok"] is False and "weights" in r["message"], r
    r = random_tool("pick", items=["a", "b"], weights=[0, 0])
    assert r["ok"] is False and "weights" in r["message"] and "ValueError" not in r["message"], r


def test_weighted_unique_picks_cannot_exceed_the_items():
    r = random_tool("pick", items=["a", "b", "c"], n=5, unique=True, weights=[1, 2, 3])
    assert r["ok"] is False and "unique" in r["message"], r


def test_float_bounds_must_be_finite_and_span_a_double():
    r = random_tool("float", min=-1e308, max=1e308, n=2)
    assert r["ok"] is False and "range" in r["message"], r
    r = random_tool("float", min=float("nan"), max=1)
    assert r["ok"] is False and "finite" in r["message"], r


def test_token_volume_is_capped_up_front():
    started = time.monotonic()
    r = random_tool("token", length=4096, n=10000)
    assert time.monotonic() - started < 1.0 and r["ok"] is False and r["error"] == "too_large", r


@pytest.mark.parametrize(
    ("call", "want"),
    [
        (lambda: random_tool("sample", items=["a", "b", "c"], groups=-1), "groups"),
        (lambda: random_tool("sample", items=["a", "b", "c"], k=-1), "k"),
        (lambda: random_tool("bool", p="abc"), "p"),
        (lambda: random_tool("int", min=1.5, max=2.7), "whole number"),
        (lambda: random_tool("uuid", format="braces"), "format"),
        (lambda: random_tool("pick", items=["a", "b"], weights=["x", 1]), "weights"),
    ],
)
def test_bad_inputs_are_refused_in_words(call, want):
    r = call()
    assert r["ok"] is False and r["error"] == "invalid_input", r
    assert want in r["message"], r["message"]
    for leak in ("ZeroDivisionError", "ValueError", "invalid literal", "could not convert"):
        assert leak not in r["message"], r["message"]
