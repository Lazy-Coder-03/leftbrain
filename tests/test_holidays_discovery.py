"""#72, #75, #76, #83: what `holidays` knew and did not say.

#72 is the dangerous one. `categories` defaults to `public`, and `check` returned
`is_holiday: false` for 2026-10-18 in West Bengal - the middle of Durga Puja - with an empty
`assumptions` list. The agent asked a factual question, got `false`, and had no signal that a
filter had been applied. Omitting `subdiv` already produced an assumption; the category default
was the one narrowing that stayed hidden.
"""

import pytest

from leftbrain.core.holidays_ import holidays

WB = {"region": "IN", "subdiv": "WB"}


# --- #72: the default filter is stated, and the near miss reported ---------------------


@pytest.mark.parametrize("date", ["2026-10-18", "2026-01-23"])
def test_a_date_the_dataset_knows_is_not_reported_as_a_bare_false(date):
    r = holidays("check", **WB, date=date)
    assert r["ok"] and r["result"]["is_holiday"] is False
    assert r["result"]["observed_elsewhere"], r["result"]
    assert any("categories" in w for w in r["warnings"]), r["warnings"]


def test_the_default_filter_is_named_on_every_call_that_used_it():
    for mode, extra in (("check", {"date": "2026-10-20"}), ("next", {"date": "2026-10-15"}), ("list", {"year": 2026})):
        r = holidays(mode, **WB, **extra)
        assert any("public holidays only" in a for a in r["assumptions"]), (mode, r["assumptions"])


def test_naming_categories_explicitly_says_nothing_about_a_default():
    r = holidays("check", **WB, date="2026-10-18", categories=["public", "optional"])
    assert r["result"]["is_holiday"] is True and r["result"]["name"] == "Dussehra (Saptami)"
    assert not any("public holidays only" in a for a in r["assumptions"])


def test_a_date_that_is_no_kind_of_holiday_says_nothing_extra():
    r = holidays("check", **WB, date="2026-03-11")
    assert r["result"]["is_holiday"] is False
    assert "observed_elsewhere" not in r["result"] and r["warnings"] == []


def test_the_assumption_names_what_else_the_country_has():
    said = " ".join(holidays("check", **WB, date="2026-10-20")["assumptions"])
    assert "optional" in said and "pass categories" in said


# --- #75: the enum that cannot live in the schema --------------------------------------


def test_categories_can_be_discovered_the_way_subdivisions_can():
    r = holidays("categories", region="IN")
    assert r["ok"] and r["result"]["default"] == "public"
    assert set(r["result"]["categories"]) >= {"public", "optional"}


def test_the_valid_set_really_does_differ_by_country():
    """This is why it cannot be a static enum in the tool schema."""
    india = set(holidays("categories", region="IN")["result"]["categories"])
    usa = set(holidays("categories", region="US")["result"]["categories"])
    assert "optional" in india and "optional" not in usa
    assert "government" in usa


def test_a_wrong_category_is_refused_in_the_contract_shape():
    """It used to leak the upstream package's own text, with no `needs` to recover from."""
    r = holidays("list", region="US", subdiv="TX", year=2026, categories=["optional"])
    assert not r["ok"] and r["error"] == "ambiguous"
    assert r["needs"]["field"] == "categories"
    assert "optional" not in r["needs"]["options"] and "public" in r["needs"]["options"]
    assert "Category is not supported" not in r["message"]


def test_every_option_the_refusal_offers_actually_works():
    for option in holidays("list", region="US", year=2026, categories=["optional"])["needs"]["options"]:
        assert holidays("list", region="US", year=2026, categories=[option])["ok"], option


def test_a_mixed_valid_and_invalid_list_names_only_what_is_wrong():
    r = holidays("list", region="IN", year=2026, categories=["optional", "bank"])
    assert not r["ok"] and "bank" in r["message"] and "optional" not in r["message"].split(";")[0]


# --- #76: `locale` never localised anything --------------------------------------------


def test_locale_says_what_it_actually_does():
    r = holidays("check", **WB, date="2026-10-20", locale="IN")
    assert r["ok"]
    assert any("DD/MM" in a and "date_locale" in a for a in r["assumptions"]), r["assumptions"]


def test_date_locale_is_the_honest_name_and_says_nothing_extra():
    r = holidays("check", **WB, date="2026-10-20", date_locale="IN")
    assert r["ok"] and not any("date_locale" in a for a in r["assumptions"])


def test_the_two_names_read_a_date_the_same_way():
    a = holidays("check", region="IN", date="03/04/2026", locale="IN")["result"]["date"]
    b = holidays("check", region="IN", date="03/04/2026", date_locale="IN")["result"]["date"]
    assert a == b == "2026-04-03"


# --- #83: an alias resolved in silence --------------------------------------------------


def test_an_alias_says_which_country_it_resolved_to():
    r = holidays("list", region="BAH", year=2026, month=1)
    assert r["ok"] and r["result"]["region"] == "BH"
    assert any("read as BH (Bahrain)" in a for a in r["assumptions"]), r["assumptions"]


def test_a_code_two_conventions_disagree_about_says_so():
    said = " ".join(holidays("list", region="BAH", year=2026, month=1)["assumptions"])
    assert "IOC" in said and "Bahamas" in said and "BS" in said


def test_the_bahamas_is_reachable_by_its_own_codes():
    for code in ("BS", "BHS"):
        r = holidays("list", region=code, year=2026, month=1)
        assert r["ok"] and r["result"]["region"] == "BS"
        assert any("Majority Rule Day" in h["name"] for h in r["result"]["holidays"])


def test_a_code_that_needed_no_translation_says_nothing():
    r = holidays("list", region="IN", year=2026, month=1)
    assert not any("read as" in a for a in r["assumptions"]), r["assumptions"]


def test_countries_are_entries_rather_than_undifferentiated_strings():
    """~500 flat strings mixed ISO-2, ISO-3 and the dataset's own abbreviations, with no names
    and no way to tell which was which - so `BAH` looked like a supported Bahamas code."""
    rows = holidays("countries")["result"]["countries"]
    assert all({"code", "name", "aliases"} <= set(r) for r in rows)
    bahamas = next(r for r in rows if r["code"] == "BS")
    assert bahamas["name"] == "Bahamas" and "BHS" in bahamas["aliases"]
    bahrain = next(r for r in rows if r["code"] == "BH")
    assert "BAH" in bahrain["aliases"] and "IOC" in bahrain["note"]


def test_every_code_the_listing_publishes_resolves_to_itself():
    rows = holidays("countries")["result"]["countries"]
    for row in rows[:40]:
        assert holidays("subdivisions", region=row["code"])["result"]["region"] == row["code"]
