"""Adversarial sweep of `encode`, in the failure classes #52 found in `math`."""

import base64

import pytest

from leftbrain.core.encode import encode

# --- E. a wrong digest verified -----------------------------------------------------------


def test_a_case_mangled_base64_digest_does_not_match():
    """`digest_matches` lower-cased every form, Base64 included, despite the comment saying otherwise."""
    assert encode("hash", text="abc", expected="ungwv48bz+pbqudexa4ii7adyaowf3qctbd/yfiafa0=")["result"]["matches"] is False
    assert encode("hash", text="abc", expected="ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=")["result"]["matches"] is True
    assert encode("hash", text="abc", expected="BA7816BF8F01CFEA414140DE5DAE2223B00361A396177A9CB410FF61F20015AD")["result"]["matches"] is True  # hex is case-free


def test_a_key_that_is_not_base64_is_refused_not_emptied():
    r = encode("hmac", key="!!!", text="msg", key_base64=True)
    assert r["ok"] is False and "base64" in r["message"], r


# --- C. raw exception text ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        lambda: encode("hash", text="x", encoding="klingon"),
        lambda: encode("hash", text="é", encoding="ascii"),
        lambda: encode("url", action="encode", text="a/b", safe=0),
        lambda: encode("json", action="parse", text="1" * 5000),
        lambda: encode("json", action="format", data={"a": 1}, indent="four"),
    ],
)
def test_bad_inputs_are_refused_in_words(call):
    r = call()
    if r["ok"]:
        return  # safe=0 read as the string "0" is an answer
    assert r["error"] in ("invalid_input", "too_large"), r
    for leak in ("LookupError", "UnicodeEncodeError", "TypeError", "ValueError", "invalid literal", "sys.set_int"):
        assert leak not in r["message"], r["message"]


def test_a_jwt_expiry_in_milliseconds_is_read_as_such():
    tok = "eyJhbGciOiJIUzI1NiJ9." + base64.urlsafe_b64encode(b'{"exp": 1700000000000}').decode().rstrip("=") + ".x"
    r = encode("jwt_decode", token=tok)
    assert r["ok"] and r["result"]["exp_iso"].startswith("2023-11-14") and any("milliseconds" in w for w in r["warnings"]), r
    tok = "eyJhbGciOiJIUzI1NiJ9." + base64.urlsafe_b64encode(b'{"exp": 100000000000000000000}').decode().rstrip("=") + ".x"
    r = encode("jwt_decode", token=tok)
    assert r["ok"] and "exp_iso" not in r["result"] and any("exp" in w for w in r["warnings"]), r


# --- E/B. lenient decoders ----------------------------------------------------------------------------


def test_json_parse_rejects_what_json_cannot_spell():
    r = encode("json", action="parse", text="[NaN, Infinity]")
    assert r["result"]["valid"] is False and "NaN" in r["result"]["error"], r
    r = encode("json", action="parse", text="1e400")
    assert r["result"]["valid"] is True and any("Infinity" in w for w in r["warnings"]), r


def test_base64_decode_is_strict_and_says_what_is_wrong():
    r = encode("base64", action="decode", text="aGVsbG8=ZZZZ")
    assert r["ok"] is False and "padding" in r["message"], r
    r = encode("base64", action="decode", text="aGV$bG8=")
    assert r["ok"] is False and "$" in r["message"], r
    assert encode("base64", action="decode", text="aGVsbG8")["result"]["text"] == "hello"  # missing padding is fine


def test_hex_decode_strips_only_a_prefix_and_refuses_unknown_actions():
    r = encode("hex", action="decode", text="680x")
    assert r["ok"] is False and "hex" in r["message"], r
    assert encode("hex", action="decode", text="0X6869")["result"]["text"] == "hi"
    r = encode("hex", action="flip", text="6869")
    assert r["ok"] is False and "action" in r["message"]


def test_string_flags_read_the_word_false():
    assert encode("base64", action="encode", text="a", strip_padding="false")["result"]["encoded"] == "YQ=="
    assert encode("html", action="escape", text="\"'", quote="false")["result"]["escaped"] == "\"'"
    assert encode("url", action="encode", text="a b", plus="false")["result"]["encoded"] == "a%20b"
    r = encode("json", action="format", data={"b": 1, "a": 2}, sort_keys="false")
    assert r["result"]["text"].index('"b"') < r["result"]["text"].index('"a"')
