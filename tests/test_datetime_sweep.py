"""Adversarial sweep of `datetime`, in the failure classes #52 found in `math`."""

import time

import pytest

from leftbrain.core.datetimex import datetime_tool as dt

NY = "America/New_York"

# --- H/A. elapsed time across a DST change was wall-clock -------------------


def test_diff_across_a_dst_change_is_elapsed_time_not_wall_clock():
    """Two aware datetimes in one ZoneInfo subtract as naive wall clocks in Python: 23 real hours came back as 24."""
    r = dt("diff", start="2026-03-07 12:00", end="2026-03-08 12:00", tz=NY, unit="hours")
    assert r["ok"] and r["result"]["value"] == 23.0, r["result"].get("value")
    r = dt("diff", start="2026-10-31 12:00", end="2026-11-01 12:00", tz=NY, unit="hours")
    assert r["result"]["value"] == 25.0
    r = dt("duration_sum", ranges=[{"start": "2026-03-08 00:00", "end": "2026-03-08 06:00", "tz": NY}])
    assert r["result"]["total"]["hours"] == 5.0, r["result"]["total"]
    o = dt("overlap", a={"start": "2026-03-08 00:00", "end": "2026-03-08 06:00", "tz": NY}, b={"start": "2026-03-08 01:00", "end": "2026-03-08 09:00", "tz": NY})
    assert o["result"]["overlap"]["hours"] == 4.0


def test_duration_sum_is_microsecond_exact():
    r = dt("duration_sum", ranges=[{"start": "2026-08-29 09:00", "end": "2026-08-29 09:00:00.1"}] * 3)
    assert r["result"]["total"]["seconds"] == 0.3


# --- B. abbreviations ---------------------------------------------------------


def test_legacy_fixed_offset_zones_do_not_bypass_the_abbreviation_refusal():
    """tzdata ships `EST` as a fixed -05:00 zone, so `tz="EST"` got August in New York an hour wrong with nothing said."""
    r = dt("now", tz="EST")
    assert r["ok"] is False and r["error"] == "ambiguous" and NY in r["needs"]["options"], r
    r = dt("convert_tz", value="2026-08-29 10:00", from_tz=NY, to_tz="CET")
    assert r["ok"] is False and r["error"] == "ambiguous"
    r = dt("now", tz="HST")
    assert r["ok"] and r["result"]["tz"] == "Pacific/Honolulu"


def test_an_abbreviation_inside_the_value_is_not_dropped():
    r = dt("parse", value="2026-08-29 10:00 EST", ref_date="2026-08-29")
    assert r["ok"] is False and r["error"] == "ambiguous", r
    r = dt("parse", value="2026-08-29 10:00 JST", ref_date="2026-08-29")
    assert r["ok"] and r["result"]["utc_offset"] == "+09:00" and any("JST" in a for a in r["assumptions"]), r
    r = dt("convert_tz", value="2026-08-29 10:00 IST", to_tz="UTC")
    assert r["ok"] is False and r["error"] == "ambiguous" and "IST" in r["message"]


# --- G/B. a small integer is not an epoch --------------------------------------


@pytest.mark.parametrize("call", [lambda: dt("parse", value=2026), lambda: dt("age", dob=2000, on="2026-08-29"), lambda: dt("fiscal", value=2026, region="IN"), lambda: dt("parse", value=20260101)])
def test_a_short_integer_is_refused_not_read_as_1970(call):
    r = call()
    assert r["ok"] is False and r["error"] == "invalid_input", r
    assert "1970" not in str(r.get("result")) and "timestamp" in r["message"] and r["hint"], r["message"]


def test_a_real_epoch_still_works():
    assert dt("parse", value=1787232546, tz="Asia/Kolkata")["result"]["date"] == "2026-08-20"


# --- B/E. incomplete dates are completed openly, not from today ------------


def test_missing_fields_are_the_first_of_the_period_and_said_so():
    """`March` came back as March 29th - the day silently taken from the reference date."""
    r = dt("parse", value="March", ref_date="2026-08-29")
    assert r["result"]["date"] == "2026-03-01" and any("day" in a for a in r["assumptions"]), r
    r = dt("parse", value="2026", ref_date="2026-08-29")
    assert r["result"]["date"] == "2026-01-01" and any("first" in a for a in r["assumptions"]), r
    r = dt("parse", value="March 2026", ref_date="2026-08-29")
    assert r["result"]["date"] == "2026-03-01"
    r = dt("parse", value="Feb 3", ref_date="2026-08-29")
    assert r["result"]["date"] == "2026-02-03" and any("year" in a for a in r["assumptions"]), r
    r = dt("parse", value="at 10", ref_date="2026-08-29")
    assert r["result"]["iso"] == "2026-08-29T10:00:00" and any("date" in a for a in r["assumptions"]), r


def test_a_dayless_numeric_pair_is_as_ambiguous_as_the_dated_one():
    r = dt("parse", value="1/2", ref_date="2026-08-29")
    assert r["ok"] is False and r["error"] == "ambiguous", r
    assert dt("parse", value="1/2", ref_date="2026-08-29", locale="US")["result"]["date"] == "2026-01-02"


# --- H. wall times that do not exist ------------------------------------------


def test_a_wall_time_in_the_dst_gap_is_moved_and_said():
    r = dt("add", value="2026-03-07 02:30", amount=1, unit="day", tz=NY)
    assert r["ok"] and r["result"]["iso"] == "2026-03-08T03:30:00-04:00", r
    assert any("does not exist" in w for w in r["warnings"]), r["warnings"]
    r = dt("parse", value="2026-03-08 02:30", tz=NY)
    assert r["result"]["iso"] == "2026-03-08T03:30:00-04:00" and r["warnings"]


def test_hourly_recurrence_skips_the_missing_hour():
    r = dt("recurrence", rule="FREQ=HOURLY", start="2026-03-08 00:30", count=4, tz=NY)
    assert r["result"]["occurrences"] == ["2026-03-08T00:30:00-05:00", "2026-03-08T01:30:00-05:00", "2026-03-08T03:30:00-04:00", "2026-03-08T04:30:00-04:00"], r
    assert r["warnings"]


def test_cron_skips_a_day_whose_time_does_not_exist():
    r = dt("cron_next", expr="30 2 * * *", start="2026-03-07T12:00", n=2, tz=NY)
    assert [x["date"] for x in r["result"]["next"]] == ["2026-03-09", "2026-03-10"], r
    assert r["warnings"]


def test_an_explicit_offset_settles_a_fold_without_a_warning():
    r = dt("convert_tz", value="2026-11-01 01:30-05:00", from_tz=NY, to_tz="UTC")
    assert r["result"]["converted"]["iso"] == "2026-11-01T06:30:00+00:00" and r["warnings"] == [], r["warnings"]
    r = dt("convert_tz", value="2026-11-01 01:30", from_tz=NY, to_tz="UTC")
    assert r["warnings"]


# --- A. fractional days --------------------------------------------------------


def test_a_fractional_day_keeps_its_hours():
    r = dt("add", value="2026-08-29", amount=1.5, unit="days")
    assert r["result"]["iso"] == "2026-08-30T12:00:00", r
    r = dt("add", value="2026-08-29", amount=1.5, unit="months")
    assert r["ok"] is False and "whole" in r["message"]


# --- C. raw exception text --------------------------------------------------------


@pytest.mark.parametrize(
    ("call", "want"),
    [
        (lambda: dt("add", value="2026-08-29", amount=10000, unit="years"), "9999"),
        (lambda: dt("add", value="2026-08-29", amount=3000000, unit="days"), "9999"),
        (lambda: dt("add", value="2026-08-29", amount=1, unit=5), "unit"),
        (lambda: dt("add", value="2026-08-29", amount="one", unit="days"), "amount"),
        (lambda: dt("business_days", start="2026-08-24", end="2026-08-30", weekend=[7, 13]), "0"),
        (lambda: dt("recurrence", rule="FREQ=DAILY", start="2026-08-29", count="three"), "whole number"),
        (lambda: dt("cron_next", expr="0 9 * * *", start="2026-08-29", n="five"), "whole number"),
        (lambda: dt("duration_sum", ranges=[{"start": "2026-08-29 09:00", "end": "2026-08-29 12:30", "tz": "Asia/Kolkata"}, {"start": "2026-08-29 13:00", "end": "2026-08-29 17:00"}]), "timezone"),
        (lambda: dt("age", dob="0001-01-01", on="9999-12-31"), "9999"),
    ],
)
def test_bad_inputs_are_refused_in_words(call, want):
    r = call()
    assert r["ok"] is False and r["error"] == "invalid_input", r
    assert want in r["message"], r["message"]
    for leak in ("ValueError", "TypeError", "OverflowError", "AttributeError", "invalid literal", "could not convert"):
        assert leak not in r["message"], r["message"]


def test_words_where_numbers_were_expected():
    assert dt("nth_weekday", year=2026, month=8, weekday="monday", n="fifth")["result"]["date"] == "2026-08-31"
    assert dt("fiscal", value="2026-08-29", fy_start_month="April")["result"]["fy_start_month"] == 4
    r = dt("business_days", start="2026-08-10", end="2026-08-20", extra_holidays="2026-08-11")
    assert r["ok"] and r["result"]["holidays_skipped"][0]["date"] == "2026-08-11"


def test_a_business_day_walk_past_the_calendar_is_refused_up_front():
    started = time.monotonic()
    r = dt("add", value="2026-08-29", amount=3999999, unit="business_days")
    assert time.monotonic() - started < 1.0 and r["ok"] is False and r["error"] == "too_large", r
    r = dt("diff", start="0001-01-01", end="9999-12-31", unit="business_days")
    assert r["ok"] is False and r["error"] == "too_large"
    r = dt("duration_sum", ranges=[{"start": "2026-08-29 09:00", "end": "2026-08-29 10:00"}] * 20000)
    assert r["ok"] is False and r["error"] == "too_large"


# --- G/E. flags and reversed ranges --------------------------------------------


def test_business_days_flags_read_the_word_false_and_stick_to_the_callers_ends():
    r = dt("business_days", start="2026-08-24", end="2026-08-28", include_start="false")
    assert r["result"]["business_days"] == 4, r
    r = dt("business_days", start="2026-08-24", end="2026-08-28", include_start="maybe")
    assert r["ok"] is False and "include_start" in r["message"]
    r = dt("business_days", start="2026-08-28", end="2026-08-24", include_start=False)
    assert r["result"]["dates"] == ["2026-08-24", "2026-08-25", "2026-08-26", "2026-08-27"], r["result"]["dates"]


def test_identical_instants_are_the_same_instant():
    r = dt("diff", start="2026-08-29", end="2026-08-29")
    assert r["result"]["direction"] == "same instant" and r["result"]["sign"] == 0


# --- D/E. dropped parameters and silent fallbacks ------------------------------


def test_fiscal_refuses_an_unknown_region_rather_than_assuming_january():
    r = dt("fiscal", value="2026-08-29", region="India")
    assert r["ok"] is False and "region" in r["message"] and "IN" in r["hint"], r
    assert dt("fiscal", value="2026-08-29", region="IN")["result"]["fiscal_year"] == "FY2026-27"


def test_region_and_weekend_only_apply_to_business_day_units():
    r = dt("diff", start="2026-08-14", end="2026-08-17", unit="days", region="IN", weekend="sun")
    assert r["ok"] and any("business" in a for a in r["assumptions"]), r["assumptions"]
    r = dt("add", value="2026-08-13", amount=1, unit="day", region="IN")
    assert any("business" in a for a in r["assumptions"])


def test_recurrence_edges():
    r = dt("recurrence", rule="FREQ=DAILY", start="2026-08-29 10:00", until="2026-08-30", tz="Asia/Kolkata", dates_only=False)
    assert r["result"]["count"] == 2, r
    r = dt("recurrence", rule="every 0 days", start="2026-08-29", count=3)
    assert r["ok"] is False and "interval" in r["message"].lower()
    r = dt("recurrence", rule="FREQ=DAILY", start="2026-08-29", limit=-1)
    assert r["ok"] is False and "limit" in r["message"]
    r = dt("recurrence", rule="FREQ=DAILY", start="2026-08-29", until="2026-09-02", count=2)
    assert r["ok"] and any("'until'" in a and "'count'" in a for a in r["assumptions"]), r["assumptions"]


def test_cron_sunday_as_7_inside_a_range():
    r = dt("cron_next", expr="0 9 * * 5-7", start="2026-08-29T10:00", n=3)
    assert r["ok"], r
    assert [x["weekday"] for x in r["result"]["next"]] == ["Sunday", "Friday", "Saturday"]


def test_free_slots_reads_am_pm_clock_times():
    r = dt(
        "free_slots",
        participants=[
            {"tz": "Asia/Kolkata", "label": "A", "windows": [{"start": "09:00", "end": "17:00", "days": ["mon", "tue", "wed", "thu", "fri"]}]},
            {"tz": "Europe/London", "label": "C", "windows": [{"start": "9am", "end": "5pm"}]},
        ],
        start="2026-08-31",
        end="2026-09-04",
    )
    assert r["ok"] and r["result"]["total_slots"] > 0, r["warnings"]
