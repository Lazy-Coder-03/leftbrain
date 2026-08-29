"""Adversarial sweep of `holidays`, in the failure classes #52 found in `math`."""

import pytest

from leftbrain.core.holidays_ import holidays


def test_country_names_and_three_letter_codes_resolve_to_the_iso_code():
    """The code said "allow country names" and no name ever matched; `IND` was echoed back as `IND`."""
    for region in ("India", "IND", "in", "india"):
        r = holidays("list", region=region, year=2026)
        assert r["ok"] and r["result"]["region"] == "IN", (region, r)
    assert holidays("list", region="Turkey", year=2026)["result"]["region"] == "TR"
    assert holidays("list", region="Türkiye", year=2026)["result"]["region"] == "TR"
    assert holidays("list", region="United Kingdom", year=2026)["result"]["region"] == "GB"
    r = holidays("list", region="Atlantis", year=2026)
    assert r["ok"] is False and "Atlantis" in r["message"]


@pytest.mark.parametrize("call", [lambda: holidays("list", region="IN", year=0), lambda: holidays("list", region="IN", year=True, month=1), lambda: holidays("next", region="IN", date="2026-08-29", n=-1), lambda: holidays("next", region="IN", date="2026-08-29", n=0)])
def test_falsy_and_negative_counts_are_refused(call):
    r = call()
    assert r["ok"] is False and r["error"] == "invalid_input" and "whole number" in r["message"] or "at least" in r["message"], r


def test_month_names_are_read():
    r = holidays("list", region="IN", year=2026, month="Oct")
    assert r["ok"] and all(h["date"].startswith("2026-10") for h in r["result"]["holidays"]), r
    r = holidays("list", region="IN", year=2026, month="Octember")
    assert r["ok"] is False and "month" in r["message"] and "invalid literal" not in r["message"]


def test_check_outside_the_calendar_data_says_so():
    r = holidays("check", region="IN", date="2200-08-15")
    assert r["ok"] and r["result"]["is_holiday"] is False and any("no data" in w for w in r["warnings"]), r
    assert holidays("check", region="IN", date="2026-08-15")["warnings"] == []


def test_parameters_that_a_mode_does_not_read_are_refused():
    r = holidays("check", region="IN", date="2026-08-15", n=3)
    assert r["ok"] is False and "n" in r["message"], r
    r = holidays("list", region="IN", year=2026, date="2026-01-01")
    assert r["ok"] is False and "date" in r["message"]
