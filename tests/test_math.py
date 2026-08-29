import pytest

from leftbrain import math_tool


def test_percent_of():
    r = math_tool("eval", expr="15% of 200 + sqrt(2)^2")
    assert r["ok"] and r["result"]["decimal"] == "32"


def test_trig_requires_angle():
    r = math_tool("eval", expr="sin(30)")
    assert not r["ok"] and r["error"] == "ambiguous" and r["needs"]["field"] == "angle"


def test_trig_degrees_exact():
    r = math_tool("eval", expr="sin(30)", angle="deg")
    assert r["result"]["exact"] == "1/2"


def test_degree_symbol_in_expression():
    r = math_tool("eval", expr="cos(60°)")
    assert r["ok"] and r["result"]["exact"] == "1/2"


def test_complex_arithmetic():
    r = math_tool("eval", expr="(3+4i)*(1-2i)")
    assert r["result"]["type"] == "complex" and r["result"]["re"] == "11" and r["result"]["im"] == "-2"


def test_exact_fraction():
    r = math_tool("exact", expr="0.75 + 1/3")
    assert r["result"]["exact"] == "13/12"


def test_solve_complex_roots():
    r = math_tool("solve", equations=["x^2 + 1 = 0"])
    vals = {s["x"]["value"] for s in r["result"]["solutions"]}
    assert vals == {"I", "-I"}


def test_solve_system():
    r = math_tool("solve", equations=["x + y = 10", "x - y = 2"])
    s = r["result"]["solutions"][0]
    assert s["x"]["value"] == "6" and s["y"]["value"] == "4"


def test_solve_needs_vars_when_underdetermined():
    r = math_tool("solve", equations=["x + y = 1"])
    assert not r["ok"] and r["error"] == "ambiguous"


def test_calculus():
    assert math_tool("integrate", expr="x^2", lower=0, upper=3)["result"]["decimal"] == "9"
    assert math_tool("limit", expr="sin(x)/x", point=0)["result"]["decimal"] == "1"
    d = math_tool("diff", expr="x^3", var="x")["result"]["value"]
    assert d == "3*x**2"
    s = math_tool("series", expr="exp(x)", order=3)["result"]["polynomial"]
    assert s == "x**2/2 + x + 1"


def test_the_retired_from_to_bound_names_are_refused():
    """Bounds are `lower`/`upper`; a limit approaches `point`.

    These used to be *ignored*: `integrate from=0 to=3` quietly returned the indefinite
    integral, and `limit to=2` approached 0. An answer computed from defaults after the
    caller's arguments were dropped is the failure #28 §2a is about, so they are refused
    now and the message names what replaced them.
    """
    r = math_tool("integrate", expr="x^2", var="x", **{"from": 0, "to": 3})
    assert not r["ok"] and r["error"] == "invalid_input" and "'lower'" in r["message"]
    r = math_tool("limit", expr="1/x", var="x", **{"to": 2})
    assert not r["ok"] and "'point'" in r["message"]
    r = math_tool("convert_form", expr="0.375", **{"to": "fraction"})
    assert not r["ok"] and "'form'" in r["message"]


def test_ode():
    r = math_tool("ode", equation="y'' + y = 0", func="y(x)")
    assert r["ok"] and "sin(x)" in r["result"]["value"]


def test_matrix():
    assert math_tool("matrix", op="det", A=[[1, 2], [3, 4]])["result"]["decimal"] == "-2"
    inv = math_tool("matrix", op="inv", A=[[2, 0], [0, 4]])["result"]["rows"]
    assert inv == [["1/2", "0"], ["0", "1/4"]]
    r = math_tool("matrix", op="solve", A=[[2, 1], [1, 3]], b=[3, 5])
    assert r["result"]["rows"] == [["4/5"], ["7/5"]]


def test_stats_exact():
    r = math_tool("stats", op="describe", data=[1, 2, 3, 4, 10])
    assert r["result"]["mean"]["decimal"] == "4" and r["result"]["median"]["decimal"] == "3"
    p = math_tool("stats", op="percentile", data=[1, 2, 3, 4], percentile=50)
    assert p["result"]["decimal"] == "2.5"
    reg = math_tool("stats", op="regress", data=[1, 2, 3], y=[2, 4, 6])["result"]
    assert reg["slope"]["decimal"] == "2" and reg["r_squared"]["decimal"] == "1"


def test_polar_form():
    r = math_tool("convert_form", expr="3+4i", form="polar")
    assert r["result"]["r"]["decimal"] == "5"


def test_sandbox_rejects_python():
    for bad in ("__import__('os')", "open('x')", "().__class__", "lambda: 1", "a.b", "import os"):
        r = math_tool("eval", expr=bad)
        assert not r["ok"], bad


def test_unknown_function_rejected():
    r = math_tool("eval", expr="foo(3)")
    assert not r["ok"] and "unknown function" in r["message"]


def test_plot_points_skips_undefined():
    r = math_tool("plot_points", expr="1/x", range=[-1, 1], n=3)
    assert r["result"]["count"] == 2 and r["warnings"]


def test_round_defers_until_vars_are_substituted():
    r = math_tool("eval", expr="round(rate * 12 / 365 * days, 2)", vars={"rate": 52000, "days": 3})
    assert r["ok"], r
    assert r["result"]["decimal"] == "5128.77" and r["result"]["exact"] == "512877/100"
    # one argument rounds to an integer; half-up, the invoice rule, not the float default
    assert math_tool("eval", expr="round(x)", vars={"x": "2.5"})["result"]["exact"] == "3"
    r = math_tool("eval", expr="round(2.675, 2)")["result"]
    assert r["exact"] == "67/25" and r["decimal"] == "2.68"  # the literal's decimal value, not its binary 2.67499…
    # a symbol that is never substituted stays symbolic instead of crashing the parse
    r = math_tool("eval", expr="round(y, 1) + 1")
    assert r["ok"] and r["result"]["value"] == "round(y, 1) + 1"
    # floor and ceil already deferred; pinned so they stay that way
    assert math_tool("eval", expr="floor(a / 2) + ceil(a / 2)", vars={"a": 7})["result"]["exact"] == "7"


# --- #66 / #69: parse-time folding erased the trig call before `angle` was applied ------
#
# `parse_expr(evaluate=True)` simplifies `sin(pi)` to `0` while parsing, because `pi` is a
# known constant. Degree conversion and the mandatory-`angle` guard both ran *after* that,
# inspecting a tree with no `sin` left in it: the conversion found nothing to convert and
# the guard found no trigonometry to insist on. A plain numeric argument survives parsing,
# which is why `sin(30)` was right the whole time.

#: (expression, value in degree mode) for the arguments SymPy folds eagerly.
FOLDED_TRIG = [
    ("sin(pi)", 0.0548036651488),
    ("cos(pi)", 0.998497149864),
    ("sin(pi/2)", 0.0274121335920),
    ("tan(pi/4)", 0.0137086425344),
    ("cos(2*pi)", 0.993993116572),
]


@pytest.mark.parametrize(("expr", "expected"), FOLDED_TRIG)
def test_degrees_are_applied_even_when_the_argument_folds(expr, expected):
    """`sin(pi)` in degree mode is sin(π°) ≈ 0.0548, not the radian answer 0 (#66)."""
    r = math_tool("eval", expr=expr, angle="deg")
    assert r["ok"], r
    assert float(r["result"]["decimal"]) == pytest.approx(expected, rel=1e-9), r["result"]


@pytest.mark.parametrize(("expr", "expected"), FOLDED_TRIG)
def test_degree_and_radian_modes_disagree_where_they_should(expr, expected):
    """The regression alarm: if a future eager simplification re-collapses these, they match again."""
    deg = math_tool("eval", expr=expr, angle="deg")
    rad = math_tool("eval", expr=expr, angle="rad")
    assert deg["ok"] and rad["ok"]
    assert float(deg["result"]["decimal"]) != float(rad["result"]["decimal"]), expr


@pytest.mark.parametrize(("expr", "_expected"), FOLDED_TRIG)
def test_a_folded_trig_call_still_demands_an_angle(expr, _expected):
    """The mandatory-`angle` refusal was skipped for exactly these inputs (#69)."""
    r = math_tool("eval", expr=expr)
    assert not r["ok"] and r["error"] == "ambiguous", r
    assert r["needs"]["field"] == "angle" and r["needs"]["options"] == ["rad", "deg"]


def test_degrees_say_so_when_the_argument_folds():
    """#66 acceptance 5: `angle` is never applied in silence."""
    r = math_tool("eval", expr="sin(pi)", angle="deg")
    assert any("degree" in a for a in r["assumptions"]), r["assumptions"]


@pytest.mark.parametrize(("expr", "exact"), [("sin(30)", "1/2"), ("sin(180)", "0"), ("cos(60)", "1/2"), ("tan(45)", "1")])
def test_a_numeric_argument_in_degrees_is_unchanged(expr, exact):
    r = math_tool("eval", expr=expr, angle="deg")
    assert r["ok"] and r["result"]["exact"] == exact, r["result"]


@pytest.mark.parametrize(("expr", "exact"), [("sin(pi)", "0"), ("cos(pi)", "-1"), ("sin(pi/2)", "1"), ("tan(pi/4)", "1"), ("cos(2*pi)", "1")])
def test_radian_mode_is_unchanged(expr, exact):
    r = math_tool("eval", expr=expr, angle="rad")
    assert r["ok"] and r["result"]["exact"] == exact, r["result"]


def test_inverse_trig_in_degrees_still_returns_degrees():
    r = math_tool("eval", expr="asin(1)", angle="deg")
    assert r["ok"] and float(r["result"]["decimal"]) == pytest.approx(90.0), r["result"]


def test_expressions_without_trigonometry_never_ask_for_an_angle():
    assert math_tool("eval", expr="2+2")["ok"]
    assert math_tool("eval", expr="log(e)")["ok"]


def test_the_degree_symbol_still_answers_the_angle_question():
    """`°` is its own answer; it must not start demanding `angle` too (#69 acceptance 5)."""
    assert math_tool("eval", expr="sin(180°)")["ok"]


# --- #67: convert_form refused `value`, which the tool's signature advertises -----------


def test_convert_form_accepts_value_as_well_as_expr():
    for key in ("expr", "value"):
        r = math_tool("convert_form", **{key: "0.125"}, form="fraction")
        assert r["ok"] and r["result"]["fraction"] == "1/8", (key, r)


def test_convert_form_refuses_two_different_inputs():
    r = math_tool("convert_form", expr="0.125", value="0.5", form="fraction")
    assert not r["ok"] and r["error"] == "invalid_input", r


def test_stats_still_reads_value_as_its_own_parameter():
    r = math_tool("stats", op="zscore", data=[1, 2, 3, 4], value=4)
    assert r["ok"], r
