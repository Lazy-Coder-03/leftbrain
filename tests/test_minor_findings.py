"""#28 §3.13: the small list. None of these is dangerous; all of them are wrong or silent.

The pattern they share is a parameter that was accepted and then not used, or a result that
was technically true and practically misleading.
"""


from leftbrain.core.collections_ import collections
from leftbrain.core.datetimex import datetime_tool
from leftbrain.core.encode import encode
from leftbrain.core.holidays_ import holidays
from leftbrain.core.numbers import numbers
from leftbrain.core.random_ import random_tool
from leftbrain.core.text import text
from leftbrain.core.validate import validate


def said(response) -> str:
    return " ".join(response.get("warnings", []) + response.get("assumptions", []) + [str(response.get("message", ""))])


# --- parameters that were accepted and ignored ------------------------------


def test_more_groups_than_items_says_so():
    r = random_tool("sample", items=[1, 2, 3], groups=10)
    assert r["ok"] and "empt" in said(r)


def test_a_float_is_rounded_to_the_decimals_asked_for():
    r = random_tool("float", decimals=3, seed=1)
    assert r["ok"] and len(str(r["result"]["value"]).split(".")[1]) <= 3


def test_an_impossible_decimals_is_refused_rather_than_ignored():
    r = random_tool("float", decimals=100)
    assert r["ok"] is False and r["error"] == "invalid_input"


def test_urlsafe_decoding_actually_uses_the_urlsafe_alphabet():
    """`++//` is standard base64; under `urlsafe` it is not valid input."""
    r = encode("base64", action="decode", text="++//", urlsafe=True)
    assert r["ok"] is False or said(r)
    assert encode("base64", action="decode", text="--__", urlsafe=True)["ok"]


# --- results that were true and misleading ----------------------------------


def test_credentials_in_a_url_are_flagged():
    r = validate("url", value="https://user:pass@example.com/x")
    assert r["result"]["valid"] is True
    assert "credential" in said(r)


def test_a_homograph_or_punycode_host_is_flagged():
    for value in ("https://xn--80ak6aa92e.com", "https://аpple.com"):
        r = validate("url", value=value)
        assert r["result"].get("idn") or "look-alike" in said(r) or "punycode" in said(r), value


def test_an_ordinary_url_is_not_flagged():
    r = validate("url", value="https://example.com/x?q=1")
    assert r["result"]["valid"] is True and not r["warnings"]


def test_a_scoped_ipv6_address_is_valid():
    """RFC 4007. `fe80::1%eth0` is what a link-local address looks like in practice."""
    r = validate("ip", value="fe80::1%eth0")
    assert r["result"]["valid"] is True and r["result"].get("zone") == "eth0"


def test_a_trailing_comment_is_not_counted_as_a_statement():
    r = validate("sql_parse", sql="SELECT 1; DROP TABLE users; --")
    assert r["result"]["statement_count"] == 2


def test_a_malformed_percent_escape_is_reported():
    r = encode("url", action="decode", text="a%zz%20b+c")
    assert r["ok"] and "%zz" in said(r)


def test_an_ordinary_decode_says_nothing_extra():
    assert not encode("url", action="decode", text="a%20b")["warnings"]


def test_the_unicode_minus_is_a_minus():
    """U+2212 is what copy-paste from a document produces."""
    r = numbers("parse", value="−5")
    assert r["ok"] and str(r["result"]["value"]) == "-5"


def test_case_insensitive_duplicates_also_ignore_surrounding_space():
    r = collections(
        "find_duplicates",
        items=[{"e": "a@x.com "}, {"e": "A@x.com"}],
        key="e",
        case_insensitive=True,
    )
    assert r["result"]["has_duplicates"] is True


def test_aggregate_can_take_a_median_like_math_stats_can():
    r = collections("aggregate", items=[{"v": 1}, {"v": 2}, {"v": 10}], field="v", agg=["median"])
    assert r["ok"] and r["result"]["median"] == "2"  # numbers come back as exact strings


def test_the_short_form_of_a_fortnightly_rule_is_understood():
    """`every other tuesday` is what people write."""
    r = datetime_tool("recurrence", rule="every other tuesday", start="2026-09-01", count=3)
    assert r["ok"] and len(r["result"]["occurrences"]) == 3


def test_a_year_with_no_calendar_data_says_so():
    r = holidays("list", region="IN", year=1800)
    assert r["result"]["count"] == 0
    assert said(r)


def test_natural_sort_says_it_does_not_collate_accents():
    r = text("sort", items=["Zebra", "éclair", "apple"], natural=True)
    assert r["ok"] and "collat" in said(r)


def test_a_grapheme_count_is_reported_for_combined_characters():
    r = text("count", text="\U0001f469‍\U0001f469‍\U0001f467")  # a ZWJ family emoji
    assert r["result"]["graphemes"] == 1 and r["result"]["chars"] == 5


def test_invisible_and_bidi_characters_are_flagged():
    r = text("count", text="admin‮gnp.exe")
    assert "bidi" in said(r) or "invisible" in said(r)


def test_ordinary_text_is_not_flagged():
    r = text("count", text="hello world")
    assert r["result"]["graphemes"] == 11 and not r["warnings"]
