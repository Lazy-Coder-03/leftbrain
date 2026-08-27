"""#28 §5: the two ways leftbrain could be turned against the host it runs on.

`url_check` fetches a URL the caller supplies. On a cloud host that is a request the caller
gets to aim — at the instance metadata service, at the pod's own admin ports, at anything on
the private network the server can reach but the caller cannot. `collections.to_csv` writes
cells a caller supplies into a file another person opens in a spreadsheet.
"""

import pytest

from leftbrain.core.collections_ import collections
from leftbrain.external.tools import url_check

# --- SSRF -------------------------------------------------------------------

BLOCKED = [
    "http://169.254.169.254/latest/meta-data/",  # AWS/GCP/Azure instance metadata
    "http://metadata.google.internal/computeMetadata/v1/",
    "http://localhost:8000/healthz",
    "http://127.0.0.1/",
    "http://[::1]/",
    "http://10.0.0.1/",
    "http://192.168.1.1/",
    "http://172.16.0.1/",
    "http://0.0.0.0/",
    "http://[fd00::1]/",  # unique local address
    "http://[fe80::1]/",  # link-local
]


@pytest.mark.parametrize("url", BLOCKED)
def test_a_private_or_metadata_address_is_refused_before_any_request(url):
    r = url_check(url=url)
    assert r["ok"] is False and r["error"] == "invalid_input", url
    assert r["retryable"] is False
    assert "only fetches public addresses" in r["message"]


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x", "gopher://x/", "data:text/plain,hi"])
def test_only_http_and_https_are_allowed(url):
    """`file:///etc/passwd` used to be rewritten to `https://file:///etc/passwd` and attempted."""
    r = url_check(url=url)
    assert r["ok"] is False and r["error"] == "invalid_input"
    assert "scheme" in r["message"]
    assert "https://file" not in r["message"]


def test_a_bare_hostname_still_gets_https_prefixed():
    from leftbrain.external.tools import normalise_url

    assert normalise_url("example.com/x") == "https://example.com/x"


def test_a_public_hostname_passes_the_check():
    from leftbrain.external.tools import check_public

    check_public("https://example.com/")  # must not raise


def test_the_refusal_names_what_it_resolved_to():
    r = url_check(url="http://127.0.0.1/")
    assert r["details"]["url"] == "http://127.0.0.1/"
    assert r["details"]["address"]


# --- CSV formula injection --------------------------------------------------

DANGEROUS = ["=cmd|' /C calc'!A0", "+1-2", "-1+2", "@SUM(1)", "\tx", "\rx"]


@pytest.mark.parametrize("cell", DANGEROUS)
def test_a_formula_cell_is_neutralised(cell):
    r = collections("to_csv", items=[{"note": cell}])
    assert r["ok"]
    body = r["result"]["csv"].splitlines()[1]
    assert body.lstrip('"').startswith("'"), body
    assert any("formula" in a for a in r["assumptions"])


@pytest.mark.parametrize("delimiter", [",", ";", "\t", "|"])
def test_the_escape_does_not_depend_on_the_delimiter(delimiter):
    r = collections("to_csv", items=[{"note": "=1+1"}], delimiter=delimiter)
    assert "'=1+1" in r["result"]["csv"]


def test_a_header_that_looks_like_a_formula_is_escaped_too():
    r = collections("to_csv", items=[{"=danger": 1}])
    assert r["result"]["csv"].splitlines()[0].lstrip('"').startswith("'")


def test_ordinary_cells_are_untouched_and_nothing_is_claimed():
    r = collections("to_csv", items=[{"name": "Asha", "amount": 1200, "note": "-"}])
    assert "'" not in r["result"]["csv"]
    assert not any("formula" in a for a in r["assumptions"])


def test_a_negative_number_is_not_mistaken_for_a_formula():
    """`-12.5` is a number, not a lead-in to `-1+cmd()`; escaping it would corrupt the data."""
    r = collections("to_csv", items=[{"delta": -12.5}])
    assert "-12.5" in r["result"]["csv"] and "'-12.5" not in r["result"]["csv"]


def test_escaping_can_be_turned_off_deliberately():
    r = collections("to_csv", items=[{"formula": "=A1+B1"}], escape_formulas=False)
    assert "'=A1+B1" not in r["result"]["csv"] and "=A1+B1" in r["result"]["csv"]
    assert any("not escaped" in w for w in r["warnings"])
