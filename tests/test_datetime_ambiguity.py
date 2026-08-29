"""#85, #82 and #81: a contradicted field, a refused working week, and a silent reading."""

import pytest

from leftbrain.core.datetimex import datetime_tool as dt

# --- #85 -------------------------------------------------------------------------------
#
# `2026-03-08 02:30` never happens in New York: the clocks jump 02:00 straight to 03:00. The
# warning said so and named the reading used - `03:30:00-04:00`. The `source` block reported
# `02:30:00-05:00`, `is_dst: false`. The instant was right throughout, so `unix` and the
# converted time were correct; only the representation disagreed with the warning beside it,
# and an agent reading fields rather than prose took the one that was wrong.

GAP = {"value": "2026-03-08 02:30", "from_tz": "America/New_York", "to_tz": "Asia/Kolkata"}


def test_the_source_is_a_clock_reading_that_actually_happened():
    src = dt("convert_tz", **GAP)["result"]["source"]
    assert src["iso"] == "2026-03-08T03:30:00-04:00"
    assert src["time"] == "03:30:00" and src["utc_offset"] == "-04:00" and src["is_dst"] is True


def test_the_source_and_the_warning_now_say_the_same_thing():
    r = dt("convert_tz", **GAP)
    assert "03:30:00" in r["warnings"][0]
    assert r["result"]["source"]["time"] in r["warnings"][0]


def test_the_instant_is_unchanged():
    """Both spellings denote 07:30 UTC, which is why only the representation was ever wrong."""
    r = dt("convert_tz", **GAP)["result"]
    assert r["source"]["unix"] == 1772955000 == r["converted"]["unix"]
    assert r["converted"]["iso"] == "2026-03-08T13:00:00+05:30"


def test_what_the_caller_wrote_is_still_visible():
    assert dt("convert_tz", **GAP)["result"]["requested_local"] == "2026-03-08 02:30:00"


def test_an_ordinary_conversion_gains_nothing():
    r = dt("convert_tz", value="2026-06-01 09:00", from_tz="America/New_York", to_tz="Asia/Kolkata")
    assert "requested_local" not in r["result"] and r["warnings"] == []


def test_the_ambiguous_hour_was_already_right_and_stays_right():
    """A fold reports the first occurrence and always agreed with its own warning."""
    r = dt("convert_tz", value="2026-11-01 01:30", from_tz="America/New_York", to_tz="Asia/Kolkata")
    assert r["result"]["source"]["utc_offset"] == "-04:00"
    assert "requested_local" not in r["result"]


# --- #82 -------------------------------------------------------------------------------


def people(days):
    return [
        {"tz": "Asia/Kolkata", "label": "A", "windows": [{"start": "09:00", "end": "18:00", "days": days}]},
        {"tz": "Asia/Kolkata", "label": "B", "windows": [{"start": "10:00", "end": "17:00", "days": days}]},
    ]


def slots(days):
    r = dt("free_slots", participants=people(days), duration=60, start="2026-09-01", end="2026-09-03")
    assert r["ok"], r
    return r["result"]["participants"][0]["windows"][0]["days"]


WORKING_WEEK = ["mon", "tue", "wed", "thu", "fri"]


@pytest.mark.parametrize("spec", ["mon-fri", "mon-friday", "monday-friday", "weekdays", "weekday", WORKING_WEEK])
def test_a_working_week_can_be_written_the_natural_way(spec):
    assert slots(spec) == WORKING_WEEK


@pytest.mark.parametrize(("spec", "expected"), [
    ("mon,wed,fri", ["mon", "wed", "fri"]),
    ("mon wed fri", ["mon", "wed", "fri"]),
    (["mon-wed", "fri"], ["mon", "tue", "wed", "fri"]),
    ("weekends", ["sat", "sun"]),
    ("sat-sun", ["sat", "sun"]),
    ("fri-mon", ["mon", "fri", "sat", "sun"]),
])
def test_the_other_shapes_an_agent_reaches_for(spec, expected):
    assert slots(spec) == expected


def test_an_unknown_weekday_names_what_would_have_worked():
    r = dt("free_slots", participants=people("funday"), duration=60, start="2026-09-01", end="2026-09-03")
    assert not r["ok"]
    for hint in ("mon-fri", "weekdays", "monday"):
        assert hint in r["message"], (hint, r["message"])


def test_the_computation_itself_is_untouched():
    """Verified by hand in the report: 13:00-16:30 UTC overlap is 210 minutes a day."""
    parts = [
        {"tz": "Asia/Kolkata", "label": "A", "windows": [{"start": "09:00", "end": "22:00", "days": "mon-fri"}]},
        {"tz": "America/New_York", "label": "B", "windows": [{"start": "09:00", "end": "17:00", "days": "mon-fri"}]},
    ]
    r = dt("free_slots", participants=parts, duration=60, start="2026-09-01", end="2026-09-02")
    assert r["ok"] and r["result"]["per_day"][0]["overlap_minutes"] == 210


# --- #81 -------------------------------------------------------------------------------


@pytest.mark.parametrize(("rule", "weeks"), [("every 2nd tuesday", 2), ("every second tuesday", 2), ("every 3rd friday", 3)])
def test_an_ordinal_weekday_phrase_is_refused_rather_than_guessed(rule, weeks):
    """Monthly and fortnightly are completely different schedules, and nothing said which was
    chosen: an agent scheduling a fortnightly standup got a monthly meeting series."""
    r = dt("recurrence", rule=rule, start="2026-09-01", count=4)
    assert not r["ok"] and r["error"] == "ambiguous"
    assert r["needs"]["field"] == "rule"
    assert any("of the month" in o for o in r["needs"]["options"])
    assert any(f"every {weeks} weeks" in o for o in r["needs"]["options"])


@pytest.mark.parametrize("rule", ["every 2nd tuesday", "every 3rd friday"])
def test_both_offered_readings_are_rules_the_tool_accepts(rule):
    """A `needs` an agent cannot act on is not much better than a guess."""
    for option in dt("recurrence", rule=rule, start="2026-09-01", count=4)["needs"]["options"]:
        assert dt("recurrence", rule=option, start="2026-09-01", count=4)["ok"], option


def test_the_two_readings_really_do_differ():
    monthly = dt("recurrence", rule="every 2nd tuesday of the month", start="2026-09-01", count=4)
    fortnightly = dt("recurrence", rule="every 2 weeks on tuesday", start="2026-09-01", count=4)
    assert monthly["result"]["occurrences"] == ["2026-09-08", "2026-10-13", "2026-11-10", "2026-12-08"]
    assert fortnightly["result"]["occurrences"] == ["2026-09-01", "2026-09-15", "2026-09-29", "2026-10-13"]


@pytest.mark.parametrize(("rule", "rrule"), [
    ("every other tuesday", "FREQ=WEEKLY;INTERVAL=2;BYDAY=TU"),
    ("every first monday", "FREQ=MONTHLY;BYDAY=MO;BYSETPOS=1"),
    ("every last friday", "FREQ=MONTHLY;BYDAY=FR;BYSETPOS=-1"),
    ("every 2nd tuesday of the month", "FREQ=MONTHLY;BYDAY=TU;BYSETPOS=2"),
])
def test_an_unambiguous_phrase_still_answers(rule, rrule):
    """`first` and `last` have no fortnightly reading, so they were never ambiguous."""
    r = dt("recurrence", rule=rule, start="2026-09-01", count=3)
    assert r["ok"] and r["result"]["rrule"].startswith(f"RRULE:{rrule}"), r
