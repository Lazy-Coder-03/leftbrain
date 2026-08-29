"""Adversarial sweep of `numbers`, in the failure classes #52 found in `math`.

Most of these share one root: Decimal's default 28-digit context. Every operation on a
value past 28 digits rounded it silently, and every quantize past 28 digits raised a bare
`InvalidOperation`. The rest are option types nobody checked, and a few readings that were
silently wrong.
"""

import pytest

from leftbrain.core.numbers import numbers

# --- A. exactness claimed, 28 digits delivered ------------------------------


def _fib(n: int) -> int:
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a


def test_fibonacci_terms_are_exact_past_28_digits():
    r = numbers("sequence", kind="fibonacci", n=140)
    assert r["ok"] and r["result"]["last"] == str(_fib(139)), r["result"]["last"]


def test_geometric_terms_are_exact_past_28_digits():
    r = numbers("sequence", kind="geometric", start=2, ratio=2, n=101)
    assert r["result"]["last"] == str(2**101)
    r = numbers("sequence", kind="arithmetic", start="1e30", step=1, n=3)
    assert r["result"]["terms"] == ["1000000000000000000000000000000", "1000000000000000000000000000001", "1000000000000000000000000000002"]


def test_a_fibonacci_run_that_outgrows_the_term_cap_is_refused_not_rounded():
    r = numbers("sequence", kind="fibonacci", n=10000)
    assert r["ok"] is False and r["error"] == "too_large" and "digits" in r["message"]


def test_to_words_spells_the_number_given_not_a_rounded_one():
    r = numbers("to_words", value="12345678901234567890123456789", system="indian")
    assert r["result"]["value"] == "12345678901234567890123456789"
    assert r["result"]["words"].endswith("eighty-nine"), r["result"]["words"]
    r = numbers("parse", value="(12345678901234567890123456789)")
    assert r["result"]["value"] == "-12345678901234567890123456789"
    r = numbers("parse", value="12345678901234567890123456789%")
    assert r["result"]["value"] == "123456789012345678901234567.89"


def test_a_number_too_long_to_spell_is_refused_not_rounded():
    r = numbers("to_words", value="9" * 5000, system="indian")
    assert r["ok"] is False and r["error"] == "too_large"


# --- C. raw Decimal / int() / AttributeError text ----------------------------


def test_compare_across_thirty_orders_of_magnitude():
    r = numbers("compare", a=1, b="1e30")
    assert r["ok"], r
    assert r["result"]["percent_change_a_to_b"] == "99999999999999999999999999999900"


def test_round_to_many_places_or_of_a_long_number():
    r = numbers("round", value=1, decimals=30)
    assert r["ok"] and r["result"]["value"] == "1", r
    r = numbers("round", value="123456789012345678901234567890.5", decimals=0)
    assert r["ok"] and r["result"]["value"] == "123456789012345678901234567891"


def test_format_of_a_long_number_or_many_decimals():
    assert numbers("format", value="1e30")["result"]["formatted"] == "1,000,000,000,000,000,000,000,000,000,000"
    r = numbers("format", value=1234.5, decimals=50)
    assert r["ok"] and r["result"]["formatted"].startswith("1,234.5000")


@pytest.mark.parametrize(
    "call",
    [
        lambda: numbers("round", value=2.5, rounding=5),
        lambda: numbers("to_words", value=1234, system=5),
        lambda: numbers("format", value=1234.5, locale=5),
    ],
)
def test_a_non_string_option_is_refused_in_words(call):
    r = call()
    assert r["ok"] is False and r["error"] == "invalid_input"
    assert "AttributeError" not in r["message"] and "'int' object" not in r["message"], r["message"]


@pytest.mark.parametrize(
    "call",
    [
        lambda: numbers("round", value=1234, decimals="two"),
        lambda: numbers("round", value=1234.5678, decimals=[2]),
        lambda: numbers("format", value=1234.5, decimals="two"),
        lambda: numbers("allocate", total=100, parts="three"),
        lambda: numbers("allocate", total=100, parts=3, decimals="two"),
        lambda: numbers("sequence", kind="arithmetic", n="five"),
        lambda: numbers("round", value=1234.5678, decimals=True),
        lambda: numbers("round", value=1234.5678, decimals=2.7),
        lambda: numbers("allocate", total=100, parts=3.7),
    ],
)
def test_a_count_that_is_not_a_whole_number_is_refused_in_words(call):
    r = call()
    assert r["ok"] is False and r["error"] == "invalid_input", r
    assert "whole number" in r["message"], r["message"]
    assert "invalid literal" not in r["message"] and "int()" not in r["message"]


@pytest.mark.parametrize(
    "call",
    [
        lambda: numbers("format", value=float("inf")),
        lambda: numbers("round", value=float("inf")),
        lambda: numbers("to_words", value=float("inf")),
        lambda: numbers("allocate", total=float("inf"), parts=3),
        lambda: numbers("allocate", total=100, weights=[1, float("inf")]),
    ],
)
def test_infinity_is_refused_in_words(call):
    r = call()
    assert r["ok"] is False and r["error"] == "invalid_input"
    assert "infinit" in r["message"].lower(), r["message"]
    assert "Error" not in r["message"] and "InvalidOperation" not in r["message"]


def test_to_words_beyond_quadrillion():
    assert numbers("to_words", value=10**18)["result"]["words"] == "one quintillion"
    r = numbers("to_words", value=10**36)
    assert r["ok"] is False and r["error"] == "unsupported" and "indian" in r["message"].lower()


def test_parse_of_an_absurd_exponent_is_a_sentence():
    r = numbers("parse", value="1e100000000")
    assert r["ok"] is False and r["error"] == "too_large" and "Overflow" not in r["message"]


# --- B. formats silently misread ----------------------------------------------


def test_a_european_number_with_both_separators():
    """`1.234,56` was read as 1.23456: the dot kept, the comma stripped."""
    r = numbers("parse", value="1.234,56")
    assert r["result"]["value"] == "1234.56", r
    assert any("decimal" in a and "comma" in a for a in r["assumptions"])
    assert numbers("parse", value="1,234.56")["result"]["value"] == "1234.56"
    assert numbers("parse", value="12.345.678,9")["result"]["value"] == "12345678.9"


@pytest.mark.parametrize("value", ["1,2345", "10,000,00", "1,,000"])
def test_commas_that_group_nothing_are_refused(value):
    r = numbers("parse", value=value)
    assert r["ok"] is False and "group" in r["message"], (value, r)


def test_non_breaking_spaces_are_separators():
    assert numbers("parse", value="1 000")["result"]["value"] == "1000"
    assert numbers("parse", value="1 234 567,5")["result"]["value"] == "1234567.5"


def test_a_sign_before_a_currency_symbol():
    assert numbers("parse", value="-₹500")["result"]["value"] == "-500"


def test_a_value_past_the_float_range_says_so():
    r = numbers("parse", value="1e400")
    assert r["result"]["value"].startswith("1000000") and r["result"]["number"] == float("inf")
    assert any("infinity" in a for a in r["assumptions"]), r["assumptions"]


# --- D. silent no-ops ---------------------------------------------------------


def test_an_empty_sequence_says_why():
    r = numbers("sequence", kind="arithmetic", start=10, end=1, step=1)
    assert r["ok"] and r["result"]["count"] == 0 and any("away from" in w for w in r["warnings"]), r
    r = numbers("sequence", kind="range", start=0, end=10, step=0)
    assert r["ok"] is False and "step" in r["message"]


def test_a_range_over_the_cap_is_refused_not_cut():
    r = numbers("sequence", kind="range", start=0, end=100000, step=1)
    assert r["ok"] is False and r["error"] == "too_large", r
    assert numbers("sequence", kind="range", start=0, end=9999, step=1)["result"]["count"] == 10000


def test_clashing_sequence_parameters_are_noted():
    r = numbers("sequence", kind="arithmetic", start=0, step=1, n=3, end=100)
    assert r["result"]["count"] == 3 and any("'n'" in a and "'end'" in a for a in r["assumptions"]), r
    r = numbers("sequence", kind="geometric", start=1, ratio=2, n=5, step=3)
    assert any("step" in a and "geometric" in a for a in r["assumptions"]), r["assumptions"]
    r = numbers("sequence", kind="arithmetic", start="1.5L", step=1, n=2)
    assert r["result"]["terms"][0] == "150000" and any("×10^5" in a for a in r["assumptions"])


def test_allocate_clashes_and_bad_inputs():
    r = numbers("allocate", total=100, weights=[1, 1], percentages=[50, 50])
    assert r["ok"] and [i["share"] for i in r["result"]["items"]] == ["50", "50"] and any("'weights'" in a for a in r["assumptions"]), r
    r = numbers("allocate", total=100, parts=0)
    assert r["ok"] is False and "at least 1" in r["message"]
    r = numbers("allocate", total=100, weights="1,2,3")
    assert r["ok"] is False and "list" in r["message"]
    r = numbers("allocate", total=100, weights=[1, 2, 3], n=5)
    assert r["ok"] and len(r["result"]["items"]) == 3 and any("'n'" in a for a in r["assumptions"])
    r = numbers("allocate", total=100, percentages=["50%", "50%"])
    assert r["ok"] and [i["share"] for i in r["result"]["items"]] == ["50", "50"]


def test_the_parts_cap_is_one_the_response_can_honour():
    r = numbers("allocate", total=100, parts=10000)
    assert r["ok"] is False and r["error"] == "too_large" and "bytes" not in r["message"], r
    assert numbers("allocate", total=100, parts=2000)["ok"]


def test_format_refuses_or_notes_what_it_does_not_apply():
    r = numbers("format", value=1234.5, style="scientific")
    assert r["ok"] is False and "style" in r["message"]
    r = numbers("format", value=1234.5, currency="INR")
    assert r["ok"] and any("currency" in a for a in r["assumptions"]), r
    r = numbers("format", value=-1234.5, accounting="false")
    assert r["ok"] and r["result"]["formatted"] == "-1,234.5"
    r = numbers("format", value=-1234.5, accounting="maybe")
    assert r["ok"] is False and "accounting" in r["message"]


def test_compact_rolls_over_to_the_next_unit():
    assert numbers("format", value=999999, style="compact")["result"]["formatted"] == "1M"
    assert numbers("format", value=99999, style="compact", locale="en_IN")["result"]["formatted"] == "1L"
    assert numbers("format", value=999999999, style="compact")["result"]["formatted"] == "1B"


# --- H. one convention across the package ------------------------------------


def test_percent_change_from_a_negative_base_matches_finance():
    """finance.percent divides by |a|; compare divided by a, so the same move had opposite signs."""
    r = numbers("compare", a=-100, b=-50)
    assert r["result"]["percent_change_a_to_b"] == "50"
    r = numbers("compare", a=0, b=5)
    assert "percent_change_a_to_b" not in r["result"] and any("zero" in a for a in r["assumptions"]), r
    assert numbers("compare", a=0.3, b="0.3")["result"]["percent_change_a_to_b"] == "0"


# --- G. wrong types accepted silently ----------------------------------------


def test_semver_refuses_a_float_because_1_10_cannot_survive_as_one():
    r = numbers("semver", a=1.9, b=1.10)
    assert r["ok"] is False and "string" in r["message"], r
    r = numbers("semver", a="01.0.0", b="1.0.0")
    assert r["ok"] is False and "leading zero" in r["message"]


def test_to_words_grammar():
    assert numbers("to_words", value=0.99, currency="USD")["result"]["words"] == "Zero dollars and ninety-nine cents only"
    assert numbers("to_words", value=-5, currency="INR")["result"]["words"] == "Minus five rupees only"
    assert numbers("to_words", value=5, currency="XYZ")["result"]["words"] == "Five XYZ only"


# --- #56: `start` was ignored without a word by the kinds that do not read it ----------
#
# `end`, `step` and `ratio` were already reported when the kind did not read them; `start`
# was left out of that list. `sequence kind=primes n=5 start=50` returned the primes from 2
# and said nothing, so "the primes from 50" came back as 2, 3, 5, 7, 11.


@pytest.mark.parametrize("kind", ["primes", "fibonacci", "squares"])
def test_a_kind_that_ignores_start_says_so(kind):
    r = numbers("sequence", kind=kind, n=5, start=50)
    assert r["ok"]
    assert f"'start' is not used by a {kind} sequence; ignored" in r["assumptions"], r["assumptions"]


@pytest.mark.parametrize("kind", ["arithmetic", "geometric", "range"])
def test_a_kind_that_reads_start_says_nothing_about_it(kind):
    r = numbers("sequence", kind=kind, start=10, n=4, **({"end": 20} if kind == "range" else {}))
    assert r["ok"]
    assert not any("'start'" in a for a in r["assumptions"]), r["assumptions"]
    assert r["result"]["terms"][0] == "10", r["result"]


def test_start_is_reported_alongside_the_others_it_was_missing_from():
    """`primes` with an `end` is a bounded range now (#57), so this asks without one."""
    said = " ".join(numbers("sequence", kind="primes", n=5, start=50, step=2)["assumptions"])
    for name in ("'start'", "'step'"):
        assert name in said, (name, said)
    # and `end` is still reported by a kind that does not read it at all
    assert "'end'" in " ".join(numbers("sequence", kind="geometric", start=2, ratio=2, n=4, end=99)["assumptions"])
