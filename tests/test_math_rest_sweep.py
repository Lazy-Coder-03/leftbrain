"""Adversarial sweep of the `math` modes #52 did not cover: matrix, stats, diff, integrate,
limit, series, convert_form, plot_points."""

import pytest

from leftbrain.core.mathx import math as math_tool

# --- A/H. a wrong closed form is caught by arithmetic --------------------------------------


def test_a_definite_integral_is_cross_checked_numerically():
    """SymPy 1.14 returns 0 for the first and pi/20 for the second; both are wrong."""
    r = math_tool("integrate", expr="1/(x^8+1)", lower=0, upper=1)
    assert r["ok"] and r["result"]["decimal"].startswith("0.92465170577"), r
    assert any("cross-check" in w for w in r["warnings"]), r["warnings"]
    r = math_tool("integrate", expr="1/(x^10+1)", lower=0, upper=1)
    assert r["result"]["decimal"].startswith("0.93809428703"), r


def test_an_antiderivative_is_checked_by_differentiation():
    r = math_tool("integrate", expr="1/(x^10+1)")
    assert r["ok"] and r["result"]["verified"] is False and r["warnings"], r
    r = math_tool("integrate", expr="x^2")
    assert r["result"]["verified"] is True and not r["warnings"]


def test_a_divergent_or_non_real_integral_is_said():
    r = math_tool("integrate", expr="1/x", lower=-1, upper=1)
    assert r["ok"] is False and "diverge" in r["message"], r
    r = math_tool("integrate", expr="sqrt(x)", lower=-1, upper=1)
    assert r["ok"] and any("not real" in w for w in r["warnings"]), r


def test_integrating_a_step_function_numerically():
    r = math_tool("integrate", expr="floor(x)", lower=0, upper=3)
    assert r["ok"] and r["result"]["decimal"] == "3", r


# --- E. limits that do not exist ----------------------------------------------------------------


def test_limits_that_do_not_exist_say_so():
    r = math_tool("limit", expr="sin(1/x)", point=0)
    assert r["ok"] and r["result"]["exists"] is False and any("oscillat" in w for w in r["warnings"]), r
    r = math_tool("limit", expr="1/x", point=0)
    assert r["result"]["exists"] is False and r["result"]["left"]["value"] == "-oo" and r["result"]["right"]["value"] == "oo", r


# --- G/E. matrix ---------------------------------------------------------------------------------------


def test_matrix_shape_problems_are_named():
    r = math_tool("matrix", op="solve", A=[[1, 2], [3, 4]], b=[5, 6, 7])
    assert r["ok"] is False and "3" in r["message"] and "2" in r["message"], r
    r = math_tool("matrix", op="solve", A=[[1, 1], [1, 1]], b=[1, 2])
    assert r["ok"] and r["result"]["consistent"] is False and any("inconsistent" in w for w in r["warnings"]), r
    for op in ("inv", "trace", "eig"):
        r = math_tool("matrix", op=op, A=[[1, 2, 3], [4, 5, 6]])
        assert r["ok"] is False and "square" in r["message"] and "2×3" in r["message"], (op, r)
    r = math_tool("matrix", op="det", A=[[1, 2], [3]])
    assert r["ok"] is False and "row 2" in r["message"], r
    assert math_tool("matrix", op="det", A="[[1,2],[3,4]]")["result"]["decimal"] == "-2"


def test_matrix_power_growth_is_estimated():
    r = math_tool("matrix", op="pow", A=[[1, 1], [1, 0]], n=100000)
    assert r["ok"] is False and r["error"] == "too_large" and "digits" in r["message"], r
    assert math_tool("matrix", op="pow", A=[[1, 1], [1, 0]], n=10)["result"]["rows"][0][0] == "89"


# --- E. plot_points ---------------------------------------------------------------------------------


def test_plot_points_edges():
    r = math_tool("plot_points", expr="tan(x)", range=[0, "pi"], n=5)
    assert r["result"]["count"] == 4 and any("pole" in w for w in r["warnings"]), r
    r = math_tool("plot_points", expr="zeta(x)", range=[2, 10], n=5)
    assert r["result"]["count"] == 5, r
    r = math_tool("plot_points", expr="x*y", var="x", range=[0, 1], n=3)
    assert r["ok"] is False and "y" in r["message"], r


# --- D/E. convert_form, diff, stats -----------------------------------------------------------------------


def test_convert_form_edges():
    r = math_tool("convert_form", expr="0.333", form="fraction", tolerance=0.01)
    assert r["result"]["fraction"] == "1/3" and r["result"]["approximate"] is True, r
    r = math_tool("convert_form", expr="0", form="polar")
    assert r["ok"] and r["result"]["theta_rad"] is None and any("undefined" in a for a in r["assumptions"]), r
    r = math_tool("convert_form", expr="1/0", form="decimal")
    assert r["ok"] is False and "undefined" in r["message"]
    r = math_tool("convert_form", expr="1e-400", form="decimal")
    assert r["result"]["exact"] != "0", r
    assert math_tool("convert_form", expr="10^400", form="scientific")["result"]["value"] == "1.00000e+400"


def test_diff_edges():
    r = math_tool("diff", expr="abs(x)", at=0)
    assert r["ok"] is False and "not differentiable" in r["message"], r
    assert math_tool("diff", expr="x*y", var="x")["result"]["type"] == "expression"
    r = math_tool("diff", expr="x^2", order=-1)
    assert r["ok"] is False and "order" in r["message"] and "ValueError" not in r["message"]


def test_stats_edges():
    r = math_tool("stats", op="zscore", data=[5], value=5)
    assert r["ok"] is False and "2" in r["message"], r
    r = math_tool("stats", op="regress", data=[1, 2, 3], y=[1, 1, 1])
    assert r["ok"] and r["result"]["slope"]["decimal"] == "0" and r["result"]["r_squared"] is None, r


@pytest.mark.parametrize(
    ("call", "want"),
    [
        (lambda: math_tool("series", expr="zeta(x)", at=1, order=3), "pole"),
        (lambda: math_tool("stats", op="mean", data=["a", 3]), "data[0]"),
        (lambda: math_tool("stats", op="percentile", data=[1, 2], percentile="abc"), "percentile"),
        (lambda: math_tool("stats", op="weighted_mean", data=[1, 2], weights=[1, -1]), "weights"),
        (lambda: math_tool("stats", op="covariance", data=[1], y=[2]), "2"),
        (lambda: math_tool("stats", op="mean", data=[float("inf"), 1]), "infinite"),
        (lambda: math_tool("convert_form", expr="1234", form="scientific", significant=0), "significant"),
        (lambda: math_tool("convert_form", expr="0.5", form="fraction", tolerance="abc"), "tolerance"),
        (lambda: math_tool("convert_form", expr="pi", form="fraction", tolerance=0), "tolerance"),
        (lambda: math_tool("plot_points", expr="x", range=["a", 1]), "range"),
    ],
)
def test_bad_inputs_are_refused_in_words(call, want):
    r = call()
    assert r["ok"] is False, r
    assert want in r["message"], r["message"]
    for leak in ("TypeError", "ValueError", "ZeroDivisionError", "AttributeError", "PoleError", "NonSquareMatrixError", "Invalid literal", "invalid input:"):
        assert leak not in r["message"], r["message"]
