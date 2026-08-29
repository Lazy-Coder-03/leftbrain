"""#90 provenance, #89 classification, #95 three signals, #87/#88 festivals, #94 comparison.

The thread through all of these: `holidays` could only answer "is this date a public holiday
in the selected categories", and every other question had to be squeezed through that. A
festival that is not a public holiday was invisible; a year the tables do not reach came back
as an empty list, which reads exactly like "nothing happens then".
"""

import pytest

from leftbrain.core.holidays_ import CLOSES_OFFICES, classify, coverage, holidays

WB = {"region": "IN", "subdiv": "WB"}


# --- #90: where a date came from, and how far the source reaches -----------------------


@pytest.mark.parametrize(("mode", "extra"), [("check", {"date": "2026-10-20"}), ("next", {"date": "2026-10-01"}), ("list", {"year": 2026})])
def test_every_answer_says_where_it_came_from(mode, extra):
    prov = holidays(mode, **WB, **extra)["result"]["provenance"]
    assert prov["source"].startswith("python-holidays ")
    assert prov["calendar"] == "gregorian"
    assert prov["covers"] == {"from": 1948, "to": 2100}
    assert prov["categories"] == ["public"]


@pytest.mark.parametrize(("mode", "extra"), [("list", {"year": 2200}), ("list", {"year": 1800}), ("check", {"date": "2200-08-15"})])
def test_a_year_the_source_does_not_reach_is_refused(mode, extra):
    """An empty list because the tables stop in 2100 reads exactly like an empty list because
    nothing fell that year, and only one of those is a fact about holidays."""
    r = holidays(mode, region="IN", **extra)
    assert not r["ok"] and r["error"] == "unsupported"
    assert "1948" in r["message"] and "2100" in r["message"]


def test_the_covered_range_is_reported_where_it_can_be_acted_on():
    r = holidays("list", region="IN", year=2200)
    assert r["details"]["covers"] == {"from": 1948, "to": 2100}
    assert "1948" in r["hint"]


def test_a_year_inside_the_window_still_answers():
    assert holidays("list", region="IN", year=2100)["ok"]
    assert holidays("list", region="IN", year=1948)["ok"]


# --- #76, properly: holiday names really can be translated -----------------------------


def test_holiday_names_can_be_asked_for_in_a_language():
    """`locale='hi'` was refused with "use a country code" - but the dataset carries Hindi,
    Bengali, Tamil and eight more for India. That is plainly what a caller passing `hi` wanted."""
    bengali = holidays("list", **WB, year=2026, month=10, categories=["public", "optional"], language="bn")
    assert bengali["ok"]
    names = " ".join(h["name"] for h in bengali["result"]["holidays"])
    assert any("অ" <= ch <= "৿" for ch in names), names


def test_the_languages_on_offer_are_discoverable():
    found = holidays("categories", region="IN")["result"]
    assert "hi" in found["languages"] and "bn" in found["languages"]
    assert found["default_language"]


def test_a_language_that_is_not_carried_is_refused_with_the_ones_that_are():
    r = holidays("check", region="IN", date="2026-10-20", language="zz")
    assert not r["ok"] and r["error"] == "ambiguous"
    assert r["needs"]["field"] == "language" and "hi" in r["needs"]["options"]


# --- #89 / #95: three signals, and one vocabulary for the kind of day -------------------


def test_a_festival_that_is_not_a_public_holiday_is_visible():
    """2026-10-18 in West Bengal: `is_holiday: false, is_weekend: true` was true, useless, and
    silent about the date being Durga Puja Saptami."""
    r = holidays("check", **WB, date="2026-10-18")["result"]
    assert r["is_holiday"] is False
    assert r["is_observed"] is True
    assert r["observances"][0]["name"] == "Dussehra (Saptami)"
    assert r["observances"][0]["classification"] == "optional_holiday"


def test_day_off_answers_the_question_most_callers_mean():
    weekend_holiday = holidays("check", **WB, date="2026-10-18")["result"]
    weekday_holiday = holidays("check", **WB, date="2026-10-20")["result"]
    plain_weekend = holidays("check", **WB, date="2026-10-17")["result"]
    ordinary = holidays("check", **WB, date="2026-03-11")["result"]
    assert weekend_holiday["day_off"] and weekday_holiday["day_off"] and plain_weekend["day_off"]
    assert not ordinary["day_off"] and not ordinary["is_observed"]


def test_a_holiday_on_a_weekend_is_distinguishable_from_one_that_is_not():
    on_sunday = holidays("check", **WB, date="2026-10-18")["result"]
    on_tuesday = holidays("check", **WB, date="2026-10-20")["result"]
    assert on_sunday["is_weekend"] and not on_tuesday["is_weekend"]
    assert on_sunday["day_off"] and on_tuesday["day_off"]


def test_the_classification_means_the_same_thing_in_every_country():
    """The upstream sets differ per country; `optional` exists for India and not the US."""
    india = holidays("check", **WB, date="2026-10-20")["result"]["observances"]
    usa = holidays("check", region="US", date="2026-07-04")["result"]["observances"]
    assert india[0]["classification"] == "public_holiday"
    assert usa[0]["classification"] == "government_holiday"
    assert classify("bank") == "bank_holiday"
    assert classify("something_new").startswith("unclassified:")


def test_an_optional_holiday_does_not_claim_to_shut_the_office():
    assert "optional_holiday" not in CLOSES_OFFICES
    assert "public_holiday" in CLOSES_OFFICES


def test_is_holiday_keeps_its_old_meaning():
    """Existing callers read this field; it still answers for the selected categories only."""
    assert holidays("check", **WB, date="2026-10-18")["result"]["is_holiday"] is False
    assert holidays("check", **WB, date="2026-10-18", categories=["public", "optional"])["result"]["is_holiday"] is True


# --- #87 / #88: a festival by name, with its days ---------------------------------------


def test_a_multi_day_festival_comes_back_as_one_thing_with_its_days_named():
    r = holidays("festival", **WB, name="Durga Puja", year=2026)
    assert r["ok"], r
    days = r["result"]["days"]
    assert [d["day"] for d in days] == ["Saptami", "Mahanavami", "Mahashtami", None]
    assert r["result"]["span"] == {"start": "2026-10-18", "end": "2026-10-20"}


def test_the_dataset_s_own_name_for_a_festival_is_stated_not_assumed():
    r = holidays("festival", **WB, name="Durga Puja", year=2026)
    assert any("looked up as 'dussehra'" in a for a in r["assumptions"]), r["assumptions"]


def test_two_named_days_sharing_a_date_are_flagged_rather_than_looking_like_duplicates():
    r = holidays("festival", **WB, name="Durga Puja", year=2026)
    assert any("more than one named day" in w for w in r["warnings"]), r["warnings"]


@pytest.mark.parametrize(("asked", "found"), [
    ("Kali Puja", "Naraka Chaturdashi"),
    ("Saraswati Puja", "Basant Panchami / Shri Panchami"),
    ("Deepavali", "Diwali"),
    ("Pohela Boishakh", "Pohela Boishakh"),
])
def test_the_names_a_caller_reaches_for_resolve(asked, found):
    r = holidays("festival", **WB, name=asked, year=2026)
    assert r["ok"], (asked, r)
    assert r["result"]["festival"].startswith(found.split(" /")[0])


def test_a_festival_this_dataset_lacks_is_refused_with_near_misses():
    """"Not in this dataset" and "no such festival" are different claims, and only the first
    is ours to make. An empty list would have made the second one."""
    r = holidays("festival", **WB, name="Jagadhatri Puja", year=2026)
    assert not r["ok"] and r["error"] == "ambiguous"
    assert r["needs"]["field"] == "name" and r["needs"]["options"]


def test_a_festival_search_covers_every_category_by_default():
    """Durga Puja's Saptami is `optional`, so a public-only search would find nothing."""
    r = holidays("festival", **WB, name="Durga Puja", year=2026)
    assert any("every category searched" in a for a in r["assumptions"])


# --- #87: what is coming up -------------------------------------------------------------


def test_upcoming_reports_a_window_across_every_category():
    r = holidays("upcoming", **WB, start="2026-10-01", end="2026-10-31")
    assert r["ok"]
    dates = [f["date"] for f in r["result"]["festivals"]]
    assert "2026-10-18" in dates and "2026-10-20" in dates


def test_upcoming_defaults_to_the_following_twelve_months_and_says_so():
    r = holidays("upcoming", **WB, start="2026-06-01", n=5)
    assert r["ok"] and any("twelve months" in a for a in r["assumptions"])
    assert r["result"]["end"] == "2027-06-01"


def test_upcoming_says_when_it_trimmed_the_list():
    r = holidays("upcoming", **WB, start="2026-01-01", end="2026-12-31", n=3)
    assert r["result"]["truncated"] and r["result"]["count"] == 3
    assert any("raise 'n'" in w for w in r["warnings"])


# --- #94: the same dates across regions -------------------------------------------------


def test_two_states_are_compared_as_a_table():
    r = holidays("compare", region="IN", subdivs=["WB", "AS"], year=2026, month=10)
    assert r["ok"]
    rows = {row["date"]: row for row in r["result"]["dates"]}
    assert "2026-10-02" in rows and rows["2026-10-02"]["everywhere"] is True
    assert r["result"]["compared"] == ["WB", "AS"]


def test_a_date_observed_in_one_place_and_not_another_is_marked():
    r = holidays("compare", region="IN", subdivs=["WB", "TN"], year=2026, month=1, categories=["public", "optional"])
    differing = [row for row in r["result"]["dates"] if row["not_in"]]
    assert differing, "WB and TN do not observe an identical January"
    assert set(differing[0]["observed_in"]) | set(differing[0]["not_in"]) == {"WB", "TN"}


def test_comparing_across_countries_works_the_same_way():
    r = holidays("compare", regions=["IN", "US"], year=2026, month=1)
    assert r["ok"] and r["result"]["compared"] == ["IN", "US"]


def test_one_place_is_not_a_comparison():
    r = holidays("compare", region="IN", subdivs=["WB"], year=2026)
    assert not r["ok"] and "two or more" in r["message"]


# --- the coverage helper itself ---------------------------------------------------------


def test_coverage_reports_what_the_source_says_about_itself():
    india = coverage("IN")
    assert india["start_year"] == 1948 and india["end_year"] == 2100
    assert "hi" in india["languages"]


# --- #93: a calendar people can import, a table they can open ---------------------------


def test_a_holiday_list_can_come_back_as_icalendar():
    r = holidays("list", **WB, year=2026, month=10, format="ics")
    assert r["ok"] and r["result"]["media_type"] == "text/calendar"
    body = r["result"]["content"]
    assert body.startswith("BEGIN:VCALENDAR") and body.rstrip().endswith("END:VCALENDAR")
    assert body.count("BEGIN:VEVENT") == r["result"]["count"]


def test_an_all_day_event_ends_the_following_day():
    """DTEND is exclusive in RFC 5545. Getting it wrong makes every imported holiday a day
    short, which is the classic off-by-one in a calendar export."""
    body = holidays("list", region="IN", year=2026, month=1, format="ics")["result"]["content"]
    assert "DTSTART;VALUE=DATE:20260126" in body
    assert "DTEND;VALUE=DATE:20260127" in body


def test_the_event_ids_are_stable_so_a_re_export_updates_rather_than_duplicates():
    first = holidays("list", **WB, year=2026, month=10, format="ics")["result"]["content"]
    again = holidays("list", **WB, year=2026, month=10, format="ics")["result"]["content"]
    assert first == again
    assert "@leftbrain" in first


def test_special_characters_in_a_name_are_escaped():
    body = holidays("list", region="IN", year=2026, format="ics")["result"]["content"]
    for line in body.splitlines():
        if line.startswith("SUMMARY:") and ("," in line or ";" in line):
            assert r"\," in line or r"\;" in line, line


def test_csv_comes_back_through_the_writer_that_already_escapes_formulas():
    r = holidays("list", **WB, year=2026, month=10, format="csv")
    assert r["ok"] and r["result"]["media_type"] == "text/csv"
    assert r["result"]["content"].splitlines()[0] == "date,name,weekday"


def test_json_is_still_the_default_and_is_unchanged():
    r = holidays("list", **WB, year=2026, month=10)
    assert "holidays" in r["result"] and "content" not in r["result"]


def test_an_unknown_format_is_refused_with_the_ones_that_work():
    r = holidays("list", region="IN", year=2026, format="pdf")
    assert not r["ok"] and "json, ics, csv" in r["message"]


# --- #92: a festival can anchor date arithmetic -----------------------------------------


ANCHOR = {"festival": "Saptami", "year": 2026, "region": "IN", "subdiv": "WB"}


def test_three_days_before_saptami():
    """`datetime` did the arithmetic and `holidays` knew the dates; nothing joined them, so
    this meant the agent doing both by hand and hoping."""
    from leftbrain.core.datetimex import datetime_tool

    r = datetime_tool("add", value=ANCHOR, amount=-3, unit="days")
    assert r["ok"] and r["result"]["date"] == "2026-10-15"


def test_the_festival_it_resolved_to_is_stated():
    from leftbrain.core.datetimex import datetime_tool

    said = " ".join(datetime_tool("add", value=ANCHOR, amount=-3, unit="days")["assumptions"])
    assert "Dussehra (Saptami)" in said and "2026-10-18" in said


def test_a_festival_spanning_days_is_not_one_anchor():
    from leftbrain.core.datetimex import datetime_tool

    r = datetime_tool("add", value={**ANCHOR, "festival": "Durga Puja"}, amount=-3, unit="days")
    assert not r["ok"] and r["error"] == "ambiguous"
    assert "Dussehra (Saptami)" in r["needs"]["options"]


def test_a_festival_anchors_a_difference_too():
    from leftbrain.core.datetimex import datetime_tool

    r = datetime_tool("diff", start=ANCHOR, end="2026-12-25")
    assert r["ok"] and r["result"]["total"]["days"] == 68


def test_an_anchor_with_no_region_says_what_is_missing():
    from leftbrain.core.datetimex import datetime_tool

    r = datetime_tool("add", value={"festival": "Saptami", "year": 2026}, amount=1, unit="days")
    assert not r["ok"] and "region" in r["message"]


def test_an_unknown_festival_anchor_is_refused_with_near_misses():
    from leftbrain.core.datetimex import datetime_tool

    r = datetime_tool("add", value={**ANCHOR, "festival": "Jagadhatri"}, amount=1, unit="days")
    assert not r["ok"] and r["error"] == "ambiguous" and r["needs"]["options"]
