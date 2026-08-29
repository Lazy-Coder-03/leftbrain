"""Adversarial sweep of `text`, in the failure classes #52 found in `math`."""

import time

import pytest

from leftbrain.core.text import text

# --- F. the ReDoS guard had four doors round the side ---------------------------


@pytest.mark.parametrize(
    ("pattern", "flags"),
    [
        (r"(a{1,60}){1,60}$", None),  # bounded nesting: 60^60 ways
        (r"(a|[a])+$", None),  # overlapping branches, one of them a class
        (r"(a?){30}$", None),  # a nullable body repeated 30 times: 2^30 ways
        ("(a+)#c\n+$", "x"),  # a comment hid the nested quantifier
        (r"(a{1,3})+$", None),  # unbounded outer, variable inner
    ],
)
def test_the_guard_refuses_every_shape_that_hung_the_engine(pattern, flags):
    started = time.monotonic()
    r = text("regex_match", text="a" * 30 + "b", pattern=pattern, flags=flags)
    assert time.monotonic() - started < 1.0, "must be refused, not run"
    assert r["ok"] is False and r["error"] == "unsupported", r


def test_polynomial_patterns_over_long_text_are_budgeted():
    started = time.monotonic()
    r = text("regex_match", text="a" * 100_000 + "!", pattern=r"a*a*a*b", limit=3)
    assert time.monotonic() - started < 1.0
    assert r["ok"] is False and r["error"] == "unsupported" and "characters" in r["message"], r
    assert text("regex_match", text="a" * 100 + "!", pattern=r"a*a*a*b")["ok"]  # short text: fine


@pytest.mark.parametrize("pattern", [r"(\d{1,3}\.){3}\d{1,3}", r"(\w{1,3}\s?){1,5}", r"(?:https?://)?[\w.-]+", r"^\s*(\d+)\s*$", r"(a|b)+c", r"\b[\w'-]+\b"])
def test_ordinary_patterns_are_untouched(pattern):
    assert text("regex_match", text="192.168.0.1 abc def", pattern=pattern)["ok"], pattern


# --- B. case-insensitive positions ------------------------------------------------


def test_case_insensitive_positions_are_in_the_callers_string():
    """`t.lower()` changes length for İ, so positions were reported against the wrong string."""
    r = text("count", text="İx", what="occurrences", substring="x", case_sensitive=False)
    assert r["result"]["positions"] == [1], r
    r = text("find", text="İ" * 10 + " needle here", substring="needle", context=3)
    assert r["result"]["hits"][0]["start"] == 11 and "needle" in r["result"]["hits"][0]["context"], r


# --- E/G. keys that resolve for nobody -----------------------------------------------


def test_dedupe_does_not_collapse_rows_that_lack_the_key():
    r = text("dedupe", items=[{"a": 1}, {"a": 2}, {"a": 3}], key="zzz")
    assert r["result"]["count"] == 3 and r["result"]["removed"] == 0 and r["warnings"], r
    r = text("dedupe", items=[{"a": {"b": 1}}, {"a": {"b": 2}}, {"a": {"b": 1}}], key="a.b")
    assert r["result"]["count"] == 2


def test_sort_warns_when_the_key_matches_nothing_and_reads_dotted_paths():
    r = text("sort", items=[{"a": {"b": 2}}, {"a": {"b": 1}}], key="a.b")
    assert [x["a"]["b"] for x in r["result"]["sorted"]] == [1, 2]
    r = text("sort", items=[{"a": 2}, {"a": 1}], key="zzz")
    assert r["ok"] and any("zzz" in w for w in r["warnings"]), r


def test_booleans_and_strings_sort_together():
    assert text("sort", items=[True, "a", 3])["ok"]


# --- C. raw exception text -------------------------------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        lambda: text("count", text="1 2 3", what="occurrences", substring=1),
        lambda: text("regex_match", text="a a a", pattern="a", limit="x"),
        lambda: text("regex_replace", text="abc", pattern="a", replacement=r"\g<nope>"),
        lambda: text("find", text="a1b", substring=1),
        lambda: text("extract", text="x", what=5),
        lambda: text("regex_replace", text="abc", pattern="a", replacement="x", count="two"),
    ],
)
def test_bad_inputs_are_refused_in_words(call):
    r = call()
    if r["ok"]:
        return  # an int substring read as its digits is an answer, not an error
    assert r["error"] == "invalid_input", r
    for leak in ("TypeError", "ValueError", "IndexError", "AttributeError", "invalid literal", "must be str"):
        assert leak not in r["message"], r["message"]


def test_string_flags_read_the_word_false():
    assert text("sort", items=["file10", "file9"], natural="false")["result"]["sorted"] == ["file10", "file9"]
    assert text("dedupe", items=["A", "a"], case_insensitive="false")["result"]["count"] == 2
    assert text("extract", text="a@x.com a@x.com", what="emails", unique="false")["result"]["emails"] == ["a@x.com", "a@x.com"]
    r = text("find", text="ABC", substring="abc", case_sensitive="maybe")
    assert r["ok"] is False and "case_sensitive" in r["message"]


# --- F. work bounded by total size, not one side ----------------------------------------


def test_similarity_over_many_long_candidates_is_refused_up_front():
    started = time.monotonic()
    r = text("similarity", text="a" * 5000, items=["b" * 5000] * 3)
    assert time.monotonic() - started < 1.0 and r["ok"] is False and r["error"] == "too_large", r


def test_char_diff_has_its_own_cap():
    started = time.monotonic()
    r = text("diff", a="ab" * 5000, b="ba" * 5000, granularity="char")
    assert time.monotonic() - started < 2.0 and r["ok"] is False and r["error"] == "too_large", r


# --- E. contradictory fields -------------------------------------------------------------


def test_any_is_about_the_total_not_the_truncated_list():
    r = text("regex_match", text="aaa", pattern="a", limit=1)
    assert r["result"]["any"] is True and r["result"]["count"] == 3
    r = text("regex_match", text="aaa", pattern="a", limit=0)
    assert r["ok"] is False and "limit" in r["message"]


def test_a_diff_that_finds_no_changes_says_what_differs():
    r = text("diff", a="x\r\ny", b="x\ny")
    assert r["result"]["identical"] is False and any("line ending" in w for w in r["warnings"]), r


def test_line_counts_agree_with_each_other():
    r = text("count", text="a\rb", what="lines")
    assert r["result"]["lines"] == 2


# --- H. extraction boundaries -------------------------------------------------------------


def test_extract_boundaries():
    assert text("extract", text="pi is 3.14.", what="numbers")["result"]["numbers"] == ["3.14"]
    assert text("extract", text="see www.z.net/q, and https://a.b/c.", what="urls")["result"]["urls"] == ["www.z.net/q", "https://a.b/c"]
    assert text("extract", text="at 10:30 we", what="times")["result"]["times"] == ["10:30"]


# --- #65: `count` took a needle it was given and answered a different question ---------
#
# "how many r in blueberry" reached the tool as `count(text=..., substring="r")`. `substring`
# is in the mode's own accepted-parameter list, was supplied, and was dropped without a word
# — the reply was the dozen summary counts (`words: 1`, `lines: 1`, …), and whichever the
# model then picked was the wrong answer it reported.


@pytest.mark.parametrize("key", ["substring", "needle"])
@pytest.mark.parametrize(("word", "letter", "expected"), [("blueberry", "r", 2), ("mississippi", "s", 4), ("strawberry", "r", 3)])
def test_a_needle_alone_counts_that_needle(key, word, letter, expected):
    r = text("count", text=word, **{key: letter})
    assert r["ok"] and r["result"]["count"] == expected, r["result"]


@pytest.mark.parametrize("key", ["substring", "needle"])
def test_counting_a_needle_says_which_question_it_answered(key):
    r = text("count", text="blueberry", **{key: "r"})
    said = " ".join(r["assumptions"])
    assert "occurrences" in said and "'r'" in said, r["assumptions"]
    assert "what=" in said, "the summary counts must still be reachable from the assumption"


def test_the_summary_counts_are_unchanged():
    r = text("count", text="blueberry", what="all")
    assert r["ok"] and r["result"]["chars"] == 9 and r["result"]["words"] == 1


def test_an_explicit_what_still_wins_over_a_needle():
    """A caller who named `what` gets `what`; the ignored needle is reported, not dropped."""
    r = text("count", text="blueberry", what="words", substring="r")
    assert r["ok"] and r["result"] == {"words": 1}
    assert any("substring" in a or "'r'" in a for a in r["assumptions"]), r["assumptions"]


def test_the_what_error_lists_every_value_that_works():
    """The old message omitted `substring` and `occurrences`' aliases — the working values."""
    r = text("count", text="abc", what="nonsense")
    assert not r["ok"]
    for value in ("substring", "occurrences", "chars", "graphemes"):
        assert value in r["message"], (value, r["message"])


def test_every_value_the_error_names_is_a_value_the_code_branches_on():
    """Read the accepted set out of the refusal and call every one of them (#65 acceptance 5)."""
    named = text("count", text="abc", what="nonsense")["message"].split("one of ", 1)[1].split(", ")
    assert len(named) > 15, named
    for what in named:
        assert text("count", text="abc", what=what, substring="a")["ok"], what


def test_counting_occurrences_without_a_needle_still_refuses():
    r = text("count", text="abc", what="occurrences")
    assert not r["ok"] and r["error"] == "invalid_input"
