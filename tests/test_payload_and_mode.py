"""#74 (an exact value spelled at 1,100 digits) and #79 (a mode nobody asked for)."""

import asyncio
import json

import pytest

from leftbrain.core.numbers import numbers

pytest.importorskip("mcp", reason="the mode default lives at the MCP boundary")

from leftbrain.mcp_server import server  # noqa: E402  # isort: skip


# --- #74 -------------------------------------------------------------------------------
#
# `allocate` rendered `exact_unrounded` as a decimal, so a 7-way split of 1000 emitted about
# 1,100 digits of `142.857142857...` per share and ~8 KB overall. Every byte landed in the
# caller's context and none of it was usable - the caller needs `142.86`, which `share`
# already gives, or the exact value, which is `1000/7`. `truncated` reported false, because
# nothing had been cut.


def test_a_repeating_share_is_written_as_a_fraction():
    r = numbers("allocate", total=1000, parts=7)
    assert r["ok"]
    assert {i["exact_unrounded"] for i in r["result"]["items"]} == {"1000/7"}


def test_the_response_is_no_longer_kilobytes_of_one_repeating_digit_string():
    r = numbers("allocate", total=1000, parts=7)
    assert len(json.dumps(r, default=str)) < 2000
    assert max(len(i["exact_unrounded"]) for i in r["result"]["items"]) < 20


def test_a_fifty_way_split_stays_small():
    """The payload grew linearly: ~55 KB at 50 parts, pushing toward the truncation ceiling."""
    r = numbers("allocate", total=1000, parts=50)
    assert r["ok"] and len(json.dumps(r, default=str)) < 12_000


@pytest.mark.parametrize(("total", "parts", "expected"), [(100, 4, "25"), (100, 8, "12.5"), (10, 2, "5")])
def test_a_value_that_terminates_is_still_written_as_a_decimal(total, parts, expected):
    """`250` reads better than `250/1`, and `12.5` better than `25/2`."""
    r = numbers("allocate", total=total, parts=parts)
    assert {i["exact_unrounded"] for i in r["result"]["items"]} == {expected}


def test_the_shares_themselves_are_unchanged():
    r = numbers("allocate", total=1000, parts=7)
    assert [i["share"] for i in r["result"]["items"]][:2] == ["142.86", "142.86"]
    assert r["result"]["sum_of_shares"] == "1000"
    assert r["result"]["leftover_units_distributed"] == 5


def test_weights_are_exact_and_brief_too():
    r = numbers("allocate", total=100, weights=[1, 2, 1])
    assert [i["weight"] for i in r["result"]["items"]] == ["1", "2", "1"]
    assert [i["exact_unrounded"] for i in r["result"]["items"]] == ["25", "50", "25"]


# --- #79 -------------------------------------------------------------------------------
#
# A call with no `mode` gets the schema's default. That was invisible: no `meta.mode`, and the
# refusal spoke in the default's vocabulary - a `validate` call the caller believed said
# `mode: "email"` was told it needed `rules`, which belongs to `assert` and appears nowhere in
# the email path. It pointed away from the real problem for several turns.


def call(name, arguments):
    return asyncio.run(server.call_tool(name, arguments)).structured_content


@pytest.mark.parametrize(("tool", "default"), [("validate", "assert"), ("numbers", "compare"), ("math", "eval"), ("text", "count")])
def test_the_mode_that_ran_is_always_reported(tool, default):
    assert call(tool, {})["meta"]["mode"] == default


@pytest.mark.parametrize(("tool", "default"), [("validate", "assert"), ("numbers", "compare"), ("math", "eval")])
def test_a_refusal_from_a_defaulted_mode_says_so(tool, default):
    envelope = call(tool, {})
    assert envelope["ok"] is False
    assert f"no 'mode' was given, so '{default}' ran" in envelope["message"], envelope["message"]
    assert "mode=" in envelope["hint"]


def test_a_successful_defaulted_call_says_so_in_assumptions():
    envelope = call("math", {"expr": "1+1"})
    assert envelope["ok"] and "mode not given: eval" in envelope["assumptions"]


def test_a_mode_the_caller_named_is_reported_as_theirs_and_adds_nothing():
    envelope = call("math", {"mode": "exact", "expr": "1+1"})
    assert envelope["meta"]["mode"] == "exact"
    assert not any("mode not given" in a for a in envelope["assumptions"])


def test_a_refusal_that_never_ran_the_mode_is_not_relabelled():
    """A scope refusal happens before the tool; claiming the default 'ran' would be its own
    wrong answer, so only refusals about the mode's own parameters are prefixed."""
    from leftbrain.mcp_contract import _MODE_SHAPED

    assert "forbidden" not in _MODE_SHAPED and "busy" not in _MODE_SHAPED
    assert {"invalid_input", "ambiguous"} <= _MODE_SHAPED
