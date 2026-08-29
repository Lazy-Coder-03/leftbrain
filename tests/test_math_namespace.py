"""#57 predicates and bounded ranges, #55 physical constants, #63 an allowlist you can see,
and the `%` that meant two things.

The `%` one is the wrong-answer bug in this group: `17 % 5` was rewritten to `(17/100) 5` and
answered 0.85 where the convention says 2 — out by a factor of 20, with `% read as /100` as
the only tell. Both readings are legitimate spellings, so the tool asks instead of guessing,
the way it already refuses trigonometry without an `angle`.
"""

import pytest

from leftbrain.core.mathx import PHYSICAL_CONSTANTS, constant_table, function_names
from leftbrain.core.mathx import math as math_tool
from leftbrain.core.numbers import numbers


def value(expr, **kw):
    r = math_tool("eval", expr=expr, **kw)
    assert r["ok"], r
    return r["result"].get("decimal") or r["result"]["value"]


# --- #57: named predicates -------------------------------------------------------------


@pytest.mark.parametrize(("expr", "expected"), [
    ("is_even(8)", "True"), ("is_even(7)", "False"),
    ("is_odd(7)", "True"), ("is_odd(8)", "False"),
    ("is_negative(-3)", "True"), ("is_positive(-3)", "False"),
    ("is_integer(4)", "True"), ("is_integer(4.5)", "False"),
    ("is_square(144)", "True"), ("is_square(145)", "False"),
    ("is_perfect(28)", "True"), ("is_perfect(27)", "False"),
    ("is_prime(97)", "True"), ("is_prime(91)", "False"),
    ("is_coprime(9, 28)", "True"), ("is_coprime(9, 27)", "False"),
])
def test_a_predicate_answers_true_or_false(expr, expected):
    assert value(expr) == expected


def test_a_predicate_on_an_unknown_names_it_rather_than_answering_none():
    """SymPy answers `Symbol('n').is_even` with `None` - a third value, useless in an envelope
    that promises true or false. Naming the symbol says the same thing and is actionable."""
    r = math_tool("eval", expr="is_even(n)")
    assert not r["ok"] and r["error"] == "invalid_input"
    assert "is_even needs a number" in r["message"] and "n" in r["message"]


def test_predicates_add_up_which_is_the_only_batching_this_tool_has():
    """Documented deliberately rather than left to be rediscovered (#63)."""
    assert value("is_prime(11) + is_prime(12) + is_prime(13)") == "2"


def test_a_predicate_composes_with_the_rest_of_the_namespace():
    assert value("is_even(gcd(12, 18))") == "True"


# --- #57: a bounded range --------------------------------------------------------------


def terms(**kw):
    r = numbers("sequence", **kw)
    assert r["ok"], r
    return r["result"]["terms"]


def test_the_primes_between_two_bounds():
    assert terms(kind="primes", start=50, end=80) == ["53", "59", "61", "67", "71", "73", "79"]


@pytest.mark.parametrize(("kind", "expected"), [
    ("squares", ["16", "25", "36", "49", "64", "81", "100"]),
    ("fibonacci", ["13", "21", "34", "55", "89"]),
])
def test_the_other_bounded_kinds(kind, expected):
    assert terms(kind=kind, start=10, end=100) == expected


def test_a_bounded_range_says_it_read_the_bounds():
    said = " ".join(numbers("sequence", kind="primes", start=50, end=80)["assumptions"])
    assert "50" in said and "80" in said


def test_start_without_end_is_still_the_first_n_and_still_says_start_was_unused():
    """The #56 rule survives: `end` is what asks for a range, so `start` alone is still ignored."""
    r = numbers("sequence", kind="primes", n=5, start=50)
    assert r["result"]["terms"] == ["2", "3", "5", "7", "11"]
    assert any("'start' is not used" in a for a in r["assumptions"]), r["assumptions"]


def test_a_range_that_runs_backwards_is_refused():
    r = numbers("sequence", kind="primes", start=80, end=50)
    assert not r["ok"] and "runs upwards" in r["message"]


def test_an_unsievable_range_is_refused_before_it_is_sieved():
    r = numbers("sequence", kind="primes", start=1, end=10**9)
    assert not r["ok"] and r["error"] == "too_large"
    assert "primes" in r["message"]


# --- #55: physical constants -----------------------------------------------------------


#: CODATA figures, to be compared with what pint hands us.
CODATA = {
    "gravitational_constant": 6.6743e-11,
    "speed_of_light": 299792458,
    "planck_constant": 6.62607015e-34,
    "boltzmann_constant": 1.380649e-23,
    "avogadro_constant": 6.02214076e23,
    "molar_gas_constant": 8.314462618,
    "standard_gravity": 9.80665,
    "elementary_charge": 1.602176634e-19,
}


@pytest.mark.parametrize(("name", "expected"), sorted(CODATA.items()))
def test_each_constant_matches_the_published_figure(name, expected):
    got = float(constant_table()[name][0])
    assert got == pytest.approx(expected, rel=1e-9), (name, got, expected)


def test_every_constant_carries_a_unit():
    for name, (_value, unit, shown) in constant_table().items():
        assert unit and shown, name
        assert name in PHYSICAL_CONSTANTS


def test_a_long_name_computes_and_says_what_it_was():
    r = math_tool("eval", expr="gravitational_constant * 5.97e24 * 70 / 6371000^2")
    assert r["ok"] and float(r["result"]["decimal"]) == pytest.approx(687.17, rel=1e-3)
    assert any("gravitational_constant" in a and "6.6743e-11" in a for a in r["assumptions"])


def test_the_short_name_gives_the_same_answer_and_names_the_long_one():
    short = math_tool("eval", expr="G * 5.97e24 * 70 / 6371000^2")
    assert short["result"]["decimal"] == math_tool("eval", expr="gravitational_constant * 5.97e24 * 70 / 6371000^2")["result"]["decimal"]
    assert any("G read as gravitational_constant" in a for a in short["assumptions"])


def test_a_dimensionless_constant_carries_no_unit_suffix():
    said = " ".join(math_tool("eval", expr="alpha")["assumptions"])
    assert "fine_structure_constant" in said and "dimensionless" not in said


def test_the_callers_own_variable_beats_a_constant():
    r = math_tool("eval", expr="c*2", vars={"c": 3})
    assert r["result"]["decimal"] == "6"


def test_a_short_name_stays_an_unknown_where_the_answer_is_not_a_number():
    """`c`, `G`, `h` and `R` are the most common single-letter variables in algebra; binding
    them in the modes whose whole point is free symbols would be a regression, not a feature."""
    solved = math_tool("solve", equations=["a + b = c"], vars=["a"])
    assert solved["ok"] and "c" in solved["result"]["solutions"][0]["a"]["free_symbols"]
    assert math_tool("diff", expr="c*x^2", var="x")["result"]["value"] == "2*c*x"


# --- #63: the allowlist is visible -----------------------------------------------------


def test_a_rejected_function_is_told_what_would_have_worked():
    r = math_tool("eval", expr="primepi(10)")
    assert not r["ok"] and "primepi" in r["message"]
    assert r["details"]["did_you_mean"], r["details"]
    assert "isprime" in r["details"]["accepted"] and "is_prime" in r["details"]["accepted"]


def test_the_accepted_set_is_the_one_the_parser_actually_uses():
    names = function_names()
    assert len(names) > 50
    for name in ("isprime", "factorint", "gcd", "is_even", "sqrt"):
        assert name in names
    for name in ("primepi", "nextprime", "eval", "__import__"):
        assert name not in names


def test_factorint_returns_factors_rather_than_a_stringified_dict():
    r = math_tool("eval", expr="factorint(360)")
    assert r["result"]["factors"] == [
        {"prime": 2, "exponent": 3}, {"prime": 3, "exponent": 2}, {"prime": 5, "exponent": 1},
    ]
    assert r["result"]["type"] == "mapping"


# --- `%` meant two things --------------------------------------------------------------


def test_a_binary_percent_asks_which_was_meant():
    r = math_tool("eval", expr="17 % 5")
    assert not r["ok"] and r["error"] == "ambiguous"
    assert r["needs"] == {"field": "percent", "options": ["percent", "modulus"]}
    assert "17 mod 5" in r["message"] and "17% * 5" in r["message"]


@pytest.mark.parametrize(("expr", "expected"), [("17 % 5", "2"), ("2^10 % 7", "2"), ("(23) % (5)", "3"), ("100 % 7", "2")])
def test_percent_modulus_gives_the_remainder(expr, expected):
    assert value(expr, percent="modulus") == expected


def test_percent_percent_gives_the_old_reading():
    assert value("17 % 5", percent="percent") == "0.85"


@pytest.mark.parametrize(("expr", "expected"), [("17%", "0.17"), ("15% of 200", "30"), ("50% + 10", "10.5")])
def test_a_postfix_percent_is_unchanged_and_never_asks(expr, expected):
    assert value(expr) == expected


@pytest.mark.parametrize(("expr", "expected"), [("17 mod 5", "2"), ("2^10 mod 7", "2"), ("2*3 mod 4", "2"), ("17 MOD 5", "2")])
def test_mod_is_the_spelling_with_one_meaning(expr, expected):
    """It also had a precedence bug: `2^10 mod 7` matched `10` as the left operand and
    answered 2^(10 mod 7) = 8 instead of 1024 mod 7 = 2."""
    assert value(expr) == expected


def test_percent_supplied_where_it_changes_nothing_is_reported_not_dropped():
    r = math_tool("eval", expr="2+2", percent="modulus")
    assert r["ok"] and any("not needed" in a for a in r["assumptions"]), r["assumptions"]


def test_an_expression_using_both_readings_answers_and_says_so():
    r = math_tool("eval", expr="100 % 7 + 15% of 40", percent="modulus")
    assert r["ok"] and r["result"]["decimal"] == "8"
    said = " ".join(r["assumptions"])
    assert "remainder" in said and "/100" in said
