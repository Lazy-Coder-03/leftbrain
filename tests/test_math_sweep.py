"""#52: eight findings from a sweep of every `math` mode against v0.3.1.

1 and 2 returned a wrong answer with no warning; 3 ran to the deadline; 4 rejected the
tool's own documented syntax; 5-8 were right answers with wrong or missing explanations.
"""

import time

import pytest

from leftbrain.core.mathx import math as math_tool

# --- 1. exact treated decimal literals as binary floats ---------------------


def test_exact_reads_a_decimal_literal_as_the_rational_it_prints_as():
    """`0.1 + 0.2 - 0.3` came back as 277555756156289/5·10^30: the IEEE-754 error, faithfully rationalised."""
    r = math_tool("exact", expr="0.1 + 0.2 - 0.3")
    assert r["ok"] and r["result"]["exact"] == "0", r


def test_eval_does_not_leak_float_error_either():
    r = math_tool("eval", expr="0.1 + 0.2 - 0.3")
    assert r["result"]["exact"] == "0" and r["result"]["decimal"] == "0"
    assert math_tool("eval", expr="0.1 * 3")["result"]["exact"] == "3/10"


@pytest.mark.parametrize(("expr", "want"), [("0.75 + 1/3", "13/12"), ("2.5e3", "2500"), ("1e-5 * 2", "1/50000"), ("2^0.5", "sqrt(2)")])
def test_decimal_and_scientific_literals_stay_exact(expr, want):
    assert math_tool("exact", expr=expr)["result"]["exact"] == want


# --- 2. Indian comma grouping was parsed as a tuple -------------------------


def test_digit_grouping_commas_are_thousands_separators_not_a_tuple():
    """`17.5% of 8,45,000 + 12% of 1,20,000` returned five numbers for a scalar question."""
    r = math_tool("eval", expr="17.5% of 8,45,000 + 12% of 1,20,000")
    assert r["ok"] and r["result"]["decimal"] == "162275", r
    assert any("grouping" in a for a in r["assumptions"])


@pytest.mark.parametrize(("expr", "want"), [("845,000 + 1", "845001"), ("1,000,000 / 4", "250000"), ("(1,20,000) * 2", "240000"), ("2,500.75 + 0.25", "2501")])
def test_western_and_indian_grouping_both_read(expr, want):
    assert math_tool("eval", expr=expr)["result"]["decimal"] == want, expr


def test_commas_inside_a_function_call_still_separate_arguments():
    assert math_tool("eval", expr="max(10,200)")["result"]["decimal"] == "200"
    assert math_tool("eval", expr="max(1,000, 45)")["result"]["decimal"] == "45"  # ambiguous, but inside a call the argument convention wins


@pytest.mark.parametrize("expr", ["3,14 * 2", "1,2345 + 1", "10,000,00 + 1", "123,45,678 + 1", "1,234,56 + 1"])
def test_a_comma_that_is_not_grouping_is_refused_not_evaluated_as_a_tuple(expr):
    """The same two shapes `numbers.parse` accepts, and nothing else: a run that groups the
    digits neither in threes nor in twos-then-a-three is not a number."""
    r = math_tool("eval", expr=expr)
    assert r["ok"] is False and r["error"] == "invalid_input" and "comma" in r["message"] and r["hint"], (expr, r)


@pytest.mark.parametrize("expr", ["1,23,45,678", "12,34,567", "1,234,567", "1,000.5"])
def test_math_and_numbers_agree_on_which_groupings_are_numbers(expr):
    from leftbrain.core.numbers import numbers

    assert math_tool("eval", expr=expr)["result"]["decimal"] == numbers("parse", value=expr)["result"]["value"], expr


# --- 3. a rational power blow-up ran to the deadline ------------------------


def test_a_huge_exact_rational_is_estimated_not_built():
    """(1+1/10^6)^10^6 is ~6 million digits over 6 million; the value is e-ish and fits in 30 digits."""
    started = time.monotonic()
    r = math_tool("eval", expr="(1+1/1000000)^1000000", precision=30)
    assert time.monotonic() - started < 2.0, "the exact rational must not be built"
    assert r["ok"], r
    assert r["result"]["decimal"].startswith("2.7182804693193768838197997084"), r["result"]
    assert "exact" not in r["result"] and "fraction" not in r["result"]
    assert any("digits" in w for w in r["warnings"]), r["warnings"]


def test_the_same_in_exact_mode_is_refused_with_a_way_out():
    started = time.monotonic()
    r = math_tool("exact", expr="(1+1/1000000)^1000000")
    assert time.monotonic() - started < 2.0
    assert r["ok"] is False and r["error"] == "too_large" and "eval" in r["hint"], r


def test_a_decimal_base_takes_the_same_numeric_path():
    r = math_tool("eval", expr="1.000001^1000000", precision=30)
    assert r["ok"] and r["result"]["decimal"].startswith("2.71828046931937688") and "exact" not in r["result"]


def test_small_rational_powers_are_still_exact():
    assert math_tool("eval", expr="(3/2)^10")["result"]["exact"] == "59049/1024"
    assert math_tool("exact", expr="0.5^20")["result"]["exact"] == "1/1048576"


# --- 4. ode rejected the y'' syntax its docstring documents -----------------


def test_ode_reads_primes_after_a_coefficient():
    """`4y'` failed the token guard: the prime rewrite required a word boundary before `y`."""
    r = math_tool("ode", equation="y'' + 4y' + 4y = 0", func="y(x)", ics={"y(0)": 1, "y'(0)": 0})
    assert r["ok"], r
    assert r["result"]["value"] == "Eq(y(x), (2*x + 1)*exp(-2*x))"


def test_ode_still_accepts_the_spaced_and_explicit_forms():
    assert math_tool("ode", equation="y'' + 4*y' + 4*y = 0", func="y(x)")["ok"]
    assert math_tool("ode", equation="Derivative(y(x), x, 2) + 4*Derivative(y(x), x) + 4*y(x) = 0", func="y(x)")["ok"]


# --- 5. "no real solutions" was reported as a numeric failure ---------------


def test_no_real_solutions_is_an_answer_not_an_error():
    """`x^2 + 1 = 0` over the reals is `ok` with an empty list, and says where the roots went."""
    r = math_tool("solve", equations=["x^2 + 1 = 0"], domain="real")
    assert r["ok"] and r["result"]["solutions"] == [] and r["result"]["count"] == 0, r
    assert any("no real solutions" in a and "2 complex" in a and "domain='complex'" in a for a in r["assumptions"]), r["assumptions"]


def test_numeric_fallback_respects_the_real_domain():
    """x^4 - 2x^2 + 3 has four complex roots and no real ones; they must not be reported as real."""
    r = math_tool("solve", equations=["x^4 - 2*x^2 + 3 = 0"], domain="real")
    assert r["ok"] and r["result"]["count"] == 0, r
    r = math_tool("solve", equations=["x^4 - 2*x^2 + 3 = 0"])
    assert r["ok"] and r["result"]["count"] == 4


def test_real_solutions_are_still_found():
    r = math_tool("solve", equations=["x^2 - 4 = 0"], domain="real")
    assert {s["x"]["value"] for s in r["result"]["solutions"]} == {"2", "-2"}


# --- 6. expand never expanded trigonometry, and said nothing ----------------


def test_expand_expands_trigonometry():
    assert math_tool("expand", expr="sin(2*x)")["result"]["value"] == "2*sin(x)*cos(x)"
    assert math_tool("expand", expr="sin(x + y)")["result"]["value"] == "sin(x)*cos(y) + sin(y)*cos(x)"


def test_expand_says_why_a_logarithm_stays_put():
    r = math_tool("expand", expr="log(x*y)")
    assert r["ok"] and r["result"]["value"] == "log(x*y)"
    assert any("positive" in a for a in r["assumptions"]), r["assumptions"]


def test_an_unchanged_result_says_so():
    assert any("already" in a for a in math_tool("expand", expr="x + 1")["assumptions"])
    r = math_tool("factor", expr="x^2 + 1")
    assert r["result"]["value"] == "x**2 + 1" and any("irreducible" in a for a in r["assumptions"])
    assert any("simplest" in a for a in math_tool("simplify", expr="x + 1")["assumptions"])


# --- 7. factor on an integer returned it unchanged --------------------------


def test_factor_factorises_an_integer():
    r = math_tool("factor", expr="12")
    assert r["ok"], r
    assert r["result"]["value"] == "2**2*3" and r["result"]["factors"] == {"2": 2, "3": 1}
    assert r["result"]["integer"] == 12 and r["result"]["type"] == "factorization"


def test_factor_of_a_negative_prime_and_a_unit():
    assert math_tool("factor", expr="-360")["result"]["value"] == "-2**3*3**2*5"
    assert math_tool("factor", expr="97")["result"]["factors"] == {"97": 1}
    r = math_tool("factor", expr="1")
    assert r["ok"] and r["result"]["value"] == "1" and any("no prime factors" in a for a in r["assumptions"])


# --- 8. factor rejected an equation with a raw CPython message --------------


def test_an_equation_where_an_expression_is_expected_is_explained():
    r = math_tool("factor", expr="x^2 - 5*x + 6 = 0")
    assert r["ok"] is False and r["error"] == "invalid_input"
    assert "equation" in r["message"] and "= 0" in r["message"] and "solve" in r["hint"], r
    assert "<string>" not in r["message"]


@pytest.mark.parametrize("expr", ["(x + 1", "x^^2", "2 +"])
def test_parse_failures_do_not_leak_the_interpreter(expr):
    r = math_tool("eval", expr=expr)
    assert r["ok"] is False and "<string>" not in r["message"] and "line 1" not in r["message"], r["message"]


# --- second-order: what the fixes above did not cover -------------------------


def test_a_huge_literal_subtree_is_refused_even_next_to_a_symbolic_one():
    """The estimators returned None as soon as any sibling was non-literal, but SymPy still
    builds every literal subtree while parsing - so `sin(30) * (1+1/10^6)^10^6` took 6 s and
    leaked `Exceeds the limit (4300 digits)`."""
    started = time.monotonic()
    r = math_tool("eval", expr="sin(30) * (1+1/1000000)^1000000", angle="deg", precision=20)
    assert time.monotonic() - started < 2.0
    assert r["ok"] and r["result"]["decimal"].startswith("1.359140234659688"), r
    started = time.monotonic()
    r = math_tool("eval", expr="sin(1) * 9^9^9^9", angle="rad")
    assert time.monotonic() - started < 1.0 and r["ok"] is False and r["error"] == "too_large", r
    r = math_tool("eval", expr="x * 2^100000")
    assert r["ok"] is False and r["error"] == "too_large" and "4300" not in r["message"].replace("4,300", ""), r


def test_a_literal_the_float_type_cannot_hold_is_measured_from_its_text():
    """`1e400` is 401 digits, not "more than 10^12": the AST float is inf."""
    r = math_tool("eval", expr="1e400")
    assert r["ok"] and r["result"]["exact"] == "1" + "0" * 400, r
    r = math_tool("eval", expr="1e400^1000")
    assert r["ok"] is False and r["error"] == "too_large"


def test_the_size_estimate_sees_through_vars():
    started = time.monotonic()
    r = math_tool("eval", expr="x^1000000", vars={"x": "1.000001"}, precision=20)
    assert time.monotonic() - started < 2.0, "the exact rational must not be built"
    assert r["ok"] and r["result"]["decimal"].startswith("2.7182804693193768838") and "exact" not in r["result"], r
    r = math_tool("eval", expr="x^y", vars={"x": 9, "y": "9^9^9"})
    assert r["ok"] is False and r["error"] == "too_large"


def test_solve_over_the_reals_drops_the_complex_roots_sympy_cannot_classify():
    """solve() with a real symbol returned 26 roots of x^40 - 2 = 0; 24 of them are complex."""
    r = math_tool("solve", equations=["x^40 - 2 = 0"], domain="real")
    assert r["ok"] and r["result"]["count"] == 2, r["result"]["count"]
    assert {s["x"]["value"] for s in r["result"]["solutions"]} == {"2**(1/40)", "-2**(1/40)"}
    r = math_tool("solve", equations=["x^40 - 2 = 0"], domain="positive")
    assert r["result"]["count"] == 1 and r["result"]["solutions"][0]["x"]["value"] == "2**(1/40)"


def test_an_identity_is_not_no_solutions():
    r = math_tool("solve", equations=["x = x"])
    assert r["ok"] and r["result"]["count"] is None and r["result"]["identity"] is True, r
    assert any("every value" in a for a in r["assumptions"])


def test_an_inconsistent_system_is_no_solutions_not_unsupported():
    r = math_tool("solve", equations=["x + y = 1", "x + y = 2"])
    assert r["ok"] and r["result"]["count"] == 0, r
    assert any("inconsistent" in a for a in r["assumptions"]), r["assumptions"]


def test_concatenated_variable_names_are_pointed_out():
    """`xy` is one symbol to the parser. With vars x and y given, that is never what was meant."""
    r = math_tool("eval", expr="2xy + 1", vars={"x": 3, "y": 4})
    assert r["ok"] is False and "xy" in r["message"] and "x*y" in r["hint"], r
    r = math_tool("ode", equation="y' = xy", func="y(x)")
    assert r["ok"] is False and "xy" in r["message"] and "x*y" in r["hint"], r
    assert math_tool("ode", equation="y' = x*y", func="y(x)")["ok"]
    assert math_tool("ode", equation="y' = k*y", func="y(x)")["ok"]  # a parameter is fine
