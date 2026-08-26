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


def test_the_retired_from_to_bound_names_are_not_accepted():
    """Bounds are `lower`/`upper`; a limit approaches `point`. Old names are simply not read."""
    r = math_tool("integrate", expr="x^2", var="x", **{"from": 0, "to": 3})
    assert r["ok"] and r["result"]["value"].endswith("+ C")  # no bounds seen -> indefinite
    r = math_tool("limit", expr="1/x", var="x", **{"to": 2})
    assert r["ok"] and r["result"]["decimal"] != "0.5"  # approached 0, the default, not 2
    r = math_tool("convert_form", expr="0.375", **{"to": "fraction"})
    assert r["ok"] and r["result"]["value"] != "3/8"  # fell back to the default decimal form


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
