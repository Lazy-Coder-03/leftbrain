from leftbrain import datetime_tool as dt


def test_now_reports_offset():
    r = dt("now", tz="Asia/Kolkata")
    assert r["ok"] and r["result"]["utc_offset"] == "+05:30"


def test_abbreviation_refused():
    r = dt("now", tz="IST")
    assert not r["ok"] and r["error"] == "ambiguous" and "Asia/Kolkata" in r["needs"]["options"]


def test_single_option_abbreviation_accepted_with_assumption():
    r = dt("now", tz="JST")
    assert r["ok"] and any("JST" in a for a in r["assumptions"])


def test_city_name_resolves():
    r = dt("now", tz="Mumbai")
    assert r["ok"] and r["result"]["tz"] == "Asia/Kolkata"


def test_now_in_several_zones_at_once():
    r = dt("now", tz=["Asia/Kolkata", {"tz": "Asia/Dubai", "label": "Acme FZ-LLC"}, "UTC+05:30"])
    assert r["ok"]
    zones = r["result"]["zones"]
    assert [z["tz"] for z in zones] == ["Asia/Kolkata", "Asia/Dubai", "UTC+05:30"]
    assert "label" not in zones[0] and zones[1]["label"] == "Acme FZ-LLC"
    assert zones[0]["utc_offset"] == "+05:30" and zones[1]["utc_offset"] == "+04:00"
    # every entry is the full single-zone shape, and all of them are the same instant
    for z in zones:
        assert {"iso", "date", "weekday", "time", "utc_offset", "tz", "unix", "is_dst", "iso_week", "day_of_year"} <= set(z)
    assert len({z["unix"] for z in zones}) == 1 and r["result"]["utc"].endswith("+00:00")
    assert "utc" not in zones[0]  # the instant is stated once, at the top
    assert any("fixed offset" in a for a in r["assumptions"])
    # a one-element list is still a list: the caller asked for a fan-out
    one = dt("now", tz=["Asia/Tokyo"])["result"]
    assert isinstance(one["zones"], list) and one["zones"][0]["tz"] == "Asia/Tokyo"
    # the single-string form is unchanged
    single = dt("now", tz="Asia/Tokyo")["result"]
    assert "zones" not in single and single["tz"] == "Asia/Tokyo" and "utc" in single


def test_now_zone_list_refuses_bad_entries():
    assert dt("now", tz=[])["error"] == "invalid_input"
    r = dt("now", tz=[{"label": "no zone here"}])
    assert not r["ok"] and "tz" in r["message"]
    r = dt("now", tz=["Asia/Kolkata", "IST"])
    assert not r["ok"] and r["error"] == "ambiguous"  # one bad zone fails the whole call, as everywhere
    r = dt("now", tz=[{"tz": "Asia/Kolkata", "label": 7}])
    assert not r["ok"] and "label" in r["message"]


def test_convert_tz_accepts_labelled_targets():
    r = dt("convert_tz", value="2025-11-04T18:00:00", from_tz="Europe/London", to_tz=[{"tz": "Asia/Kolkata", "label": "Acme India"}, "Australia/Sydney"])
    conv = r["result"]["converted"]
    assert conv[0]["label"] == "Acme India" and conv[0]["tz"] == "Asia/Kolkata" and conv[0]["day_shift"] == 0
    assert "label" not in conv[1] and conv[1]["day_shift"] == 1
    one = dt("convert_tz", value="2025-11-04T18:00:00+00:00", to_tz={"tz": "Asia/Tokyo", "label": "Tokyo office"})["result"]["converted"]
    assert one["label"] == "Tokyo office" and one["iso"] == "2025-11-05T03:00:00+09:00"


def test_convert_tz_dst_edge():
    # 8 March 2026 09:30 IST is 23:00 on 7 March in New York (still EST; DST starts later that day)
    r = dt("convert_tz", value="2026-03-08 09:30", from_tz="Asia/Kolkata", to_tz="America/New_York")
    c = r["result"]["converted"]
    assert c["iso"] == "2026-03-07T23:00:00-05:00" and c["day_shift"] == -1


def test_parse_numeric_ambiguity():
    r = dt("parse", value="03/04/2025")
    assert not r["ok"] and r["error"] == "ambiguous"
    assert dt("parse", value="03/04/2025", locale="IN")["result"]["date"] == "2025-04-03"
    assert dt("parse", value="03/04/2025", locale="US")["result"]["date"] == "2025-03-04"
    assert dt("parse", value="13/04/2025")["result"]["date"] == "2025-04-13"  # unambiguous


def test_parse_relative():
    r = dt("parse", value="next friday at 5pm", ref_date="2026-08-26", tz="Asia/Kolkata")
    assert r["result"]["iso"] == "2026-08-28T17:00:00+05:30"
    assert dt("parse", value="in 3 days", ref_date="2026-08-26")["result"]["date"] == "2026-08-29"
    assert dt("parse", value="end of month", ref_date="2026-02-10")["result"]["date"] == "2026-02-28"
    assert dt("parse", value="2 weeks ago", ref_date="2026-08-26")["result"]["date"] == "2026-08-12"


def test_add_month_end_clamp():
    r = dt("add", value="2026-01-31", amount=1, unit="month")
    assert r["result"]["date"] == "2026-02-28" and r["warnings"]


def test_add_business_days_skips_holidays():
    r = dt("add", value="2026-10-01", amount=1, unit="business_days", region="IN")
    assert r["result"]["date"] == "2026-10-05"  # 2 Oct is Gandhi Jayanti, then weekend


def test_diff_calendar_and_totals():
    r = dt("diff", start="2024-02-29", end="2026-08-26")["result"]
    assert r["calendar"]["years"] == 2 and r["calendar"]["months"] == 5 and r["calendar"]["days"] == 28
    assert r["total"]["days"] == 909


def test_the_retired_from_to_names_are_not_accepted():
    """Ranges are `start`/`end` everywhere; `from`/`to` are not read and the error says so."""
    r = dt("diff", **{"from": "2026-08-26", "to": "2026-12-25"})
    assert not r["ok"] and r["error"] == "invalid_input" and "'start'" in r["message"]
    r = dt("business_days", **{"from": "2026-10-01", "to": "2026-10-31"})
    assert not r["ok"] and r["error"] == "invalid_input" and "'start' and 'end'" in r["message"]
    r = dt("cron_next", expr="0 9 * * 1-5", tz="Asia/Kolkata", n=1, **{"from": "2026-08-28T10:00"})
    assert not r["ok"] and "'start'" in r["message"]  # was: silently started from now


def test_business_days_inclusive():
    r = dt("business_days", region="IN", start="2026-10-01", end="2026-10-31")["result"]
    assert r["business_days"] == 20 and {h["date"] for h in r["holidays_skipped"]} == {"2026-10-02", "2026-10-20"}


def test_nth_weekday():
    assert dt("nth_weekday", year=2026, month=9, weekday="tuesday", n=2)["result"]["date"] == "2026-09-08"
    assert dt("nth_weekday", year=2026, month=9, weekday="friday", n=-1)["result"]["date"] == "2026-09-25"


def test_overlap_and_duration_sum():
    o = dt("overlap", a={"start": "2026-08-26T09:00", "end": "2026-08-26T11:00"}, b={"start": "2026-08-26T10:30", "end": "2026-08-26T12:00"})
    assert o["result"]["overlaps"] and o["result"]["overlap"]["hours"] == 0.5
    s = dt("duration_sum", ranges=[{"start": "2026-08-26T09:00", "end": "2026-08-26T13:00"}, {"start": "2026-08-26T14:00", "end": "2026-08-26T18:30"}])
    assert s["result"]["total"]["hhmm"] == "08:30"


def test_recurrence_phrase():
    r = dt("recurrence", rule="every 2nd tuesday", start="2026-09-01", count=3)
    assert r["result"]["occurrences"] == ["2026-09-08", "2026-10-13", "2026-11-10"]


def test_cron_next():
    r = dt("cron_next", expr="0 9 * * 1-5", tz="Asia/Kolkata", n=2, start="2026-08-28T10:00")
    assert [x["date"] for x in r["result"]["next"]] == ["2026-08-31", "2026-09-01"]


def test_age_and_fiscal():
    a = dt("age", dob="1995-06-15", on="2026-08-26")["result"]
    assert (a["years"], a["months"], a["days"]) == (31, 2, 11)
    f = dt("fiscal", value="2026-08-26", region="IN")["result"]
    assert f["fiscal_year"] == "FY2026-27" and f["quarter"] == "Q2"
    assert dt("fiscal", value="2026-08-26", region="US")["result"]["fiscal_year"] == "FY2026"


# --------------------------------------------------------------------------- #
# free_slots
# --------------------------------------------------------------------------- #

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri"]
TRIO = [
    {"tz": "Asia/Kolkata", "label": "Kolkata", "windows": [{"start": "09:00", "end": "20:00", "days": WEEKDAYS}]},
    {"tz": "Europe/London", "label": "London", "windows": [{"start": "08:00", "end": "16:00", "days": WEEKDAYS}]},
    {"tz": "America/New_York", "label": "New York", "windows": [{"start": "07:00", "end": "11:00", "days": WEEKDAYS}]},
]


def test_free_slots_three_zones_weekly():
    # Kolkata 09-20 IST = 03:30-14:30 UTC; London 08-16 BST = 07:00-15:00 UTC; New York 07-11 EDT = 11:00-15:00 UTC
    # -> 11:00-14:30 UTC every weekday: 210 minutes, seven 30-minute slots a day, 35 over Mon 7 - Fri 11 Sep 2026
    r = dt("free_slots", participants=TRIO, start="2026-09-07", end="2026-09-11", limit=50)
    assert r["ok"], r
    res = r["result"]
    assert res["total_slots"] == 35 and len(res["slots"]) == 35
    first = res["slots"][0]
    assert first["utc"] == {"start": "2026-09-07T11:00:00+00:00", "end": "2026-09-07T11:30:00+00:00"} and first["minutes"] == 30
    assert [x["start"] for x in first["local"]] == ["2026-09-07T16:30:00+05:30", "2026-09-07T12:00:00+01:00", "2026-09-07T07:00:00-04:00"]
    assert [x["label"] for x in first["local"]] == ["Kolkata", "London", "New York"]
    assert [x["tz"] for x in first["local"]] == ["Asia/Kolkata", "Europe/London", "America/New_York"]
    assert all(x["weekday"] == "Monday" for x in first["local"])
    monday = [s["utc"]["start"][11:16] for s in res["slots"] if s["utc"]["start"].startswith("2026-09-07")]
    assert monday == ["11:00", "11:30", "12:00", "12:30", "13:00", "13:30", "14:00"]
    assert res["per_day"] == [{"date": f"2026-09-{d:02d}", "weekday": w, "overlap_minutes": 210} for d, w in [(7, "Monday"), (8, "Tuesday"), (9, "Wednesday"), (10, "Thursday"), (11, "Friday")]]
    assert res["total_overlap_minutes"] == 1050
    assert res["range"] == {"start": "2026-09-07", "end": "2026-09-11", "tz": "UTC"}
    assert [p["label"] for p in res["participants"]] == ["Kolkata", "London", "New York"]
    assert res["participants"][0]["windows"] == [{"start": "09:00", "end": "20:00", "days": WEEKDAYS}]
    assert r["warnings"] == []
    # earliest UTC first, strictly increasing
    starts = [s["utc"]["start"] for s in res["slots"]]
    assert starts == sorted(starts) and len(set(starts)) == len(starts)


def test_free_slots_limit_and_duration():
    r = dt("free_slots", participants=TRIO, start="2026-09-07", end="2026-09-11")
    assert r["ok"] and len(r["result"]["slots"]) == 20 and r["result"]["total_slots"] == 35
    assert any("15 more" in w and "limit" in w for w in r["warnings"])
    # a 60-minute meeting at 30-minute granularity: 11:00 .. 13:30 starts, six a day
    r = dt("free_slots", participants=TRIO, start="2026-09-07", end="2026-09-07", duration=60)
    assert [s["utc"]["start"][11:16] for s in r["result"]["slots"]] == ["11:00", "11:30", "12:00", "12:30", "13:00", "13:30"]
    assert all(s["minutes"] == 60 for s in r["result"]["slots"])
    # granularity sets the step and the default duration
    r = dt("free_slots", participants=TRIO, start="2026-09-07", end="2026-09-07", granularity=60)
    assert [s["utc"]["start"][11:16] for s in r["result"]["slots"]] == ["11:00", "12:00", "13:00"] and r["result"]["slots"][0]["minutes"] == 60
    # a window with no `days` applies every day, so the weekend appears too
    everyday = [{"tz": "Asia/Kolkata", "windows": [{"start": "09:00", "end": "20:00"}]}, {"tz": "Europe/London", "windows": [{"start": "08:00", "end": "16:00"}]}]
    r = dt("free_slots", participants=everyday, start="2026-09-12", end="2026-09-13", granularity=120)
    assert r["ok"] and {d["weekday"] for d in r["result"]["per_day"]} == {"Saturday", "Sunday"}
    assert r["result"]["slots"][0]["local"][0]["label"] == "Asia/Kolkata"  # no label: the zone stands in


def test_free_slots_single_absolute_window():
    # Kolkata 09:00-12:00 IST = 03:30-06:30 UTC; London 05:00-08:00 BST = 04:00-07:00 UTC -> 04:00-06:30 UTC
    two = [
        {"tz": "Asia/Kolkata", "label": "Asha", "windows": [{"start": "2026-09-01T09:00", "end": "2026-09-01T12:00"}]},
        {"tz": "Europe/London", "label": "Ben", "windows": [{"start": "2026-09-01T05:00", "end": "2026-09-01T08:00"}]},
    ]
    r = dt("free_slots", participants=two, start="2026-09-01", end="2026-09-01", duration=60)
    assert r["ok"], r
    assert [s["utc"]["start"][11:16] for s in r["result"]["slots"]] == ["04:00", "04:30", "05:00", "05:30"]
    assert r["result"]["slots"][-1]["utc"]["end"] == "2026-09-01T06:30:00+00:00"
    assert r["result"]["slots"][0]["local"] == [
        {"label": "Asha", "tz": "Asia/Kolkata", "start": "2026-09-01T09:30:00+05:30", "end": "2026-09-01T10:30:00+05:30", "weekday": "Tuesday"},
        {"label": "Ben", "tz": "Europe/London", "start": "2026-09-01T05:00:00+01:00", "end": "2026-09-01T06:00:00+01:00", "weekday": "Tuesday"},
    ]
    assert r["result"]["per_day"] == [{"date": "2026-09-01", "weekday": "Tuesday", "overlap_minutes": 150}]
    # the search range defaults to seven days from `start`, and says so
    r = dt("free_slots", participants=two, start="2026-09-01")
    assert r["ok"] and r["result"]["range"]["end"] == "2026-09-08" and any("no 'end' given" in a for a in r["assumptions"])
    # an absolute window outside the range contributes nothing
    r = dt("free_slots", participants=two, start="2026-09-02", end="2026-09-03")
    assert r["ok"] and r["result"]["slots"] == [] and r["result"]["per_day"] == []


def test_free_slots_no_overlap_names_the_odd_one_out():
    # Kolkata 09-18 IST = 03:30-12:30 UTC; London 09-17 BST = 08-16 UTC; New York 09-17 EDT = 13-21 UTC
    office_hours = [
        {"tz": "Asia/Kolkata", "label": "Kolkata", "windows": [{"start": "09:00", "end": "18:00", "days": WEEKDAYS}]},
        {"tz": "Europe/London", "label": "London", "windows": [{"start": "09:00", "end": "17:00", "days": WEEKDAYS}]},
        {"tz": "America/New_York", "label": "New York", "windows": [{"start": "09:00", "end": "17:00", "days": WEEKDAYS}]},
    ]
    r = dt("free_slots", participants=office_hours, start="2026-09-07", end="2026-09-11")
    assert r["ok"] and r["result"]["slots"] == [] and r["result"]["per_day"] == [] and r["result"]["total_slots"] == 0
    assert any("Kolkata" in w and "New York" in w and "never overlap" in w for w in r["warnings"])
    assert not any("London" in w for w in r["warnings"])  # London meets both; it is not the problem
    # common time exists but is shorter than the meeting: still ok, and the warning says how long the best stretch is
    r = dt("free_slots", participants=TRIO, start="2026-09-07", end="2026-09-07", duration=240)
    assert r["ok"] and r["result"]["slots"] == [] and r["result"]["per_day"][0]["overlap_minutes"] == 210
    assert any("210" in w and "240" in w for w in r["warnings"])


def test_free_slots_dst_crossing_is_expanded_and_noted():
    # New York springs forward at 02:00 on Sunday 8 March 2026: 00:00-05:00 local is only four hours, 05:00-09:00 UTC
    pair = [
        {"tz": "America/New_York", "label": "NY", "windows": [{"start": "00:00", "end": "05:00", "days": ["sun"]}]},
        {"tz": "Europe/London", "label": "LDN", "windows": [{"start": "05:00", "end": "10:00", "days": ["sunday"]}]},
    ]
    r = dt("free_slots", participants=pair, start="2026-03-08", end="2026-03-08", granularity=60)
    assert r["ok"], r
    assert r["result"]["per_day"] == [{"date": "2026-03-08", "weekday": "Sunday", "overlap_minutes": 240}]
    assert [s["utc"]["start"][11:16] for s in r["result"]["slots"]] == ["05:00", "06:00", "07:00", "08:00"]
    assert any("NY" in a and "2026-03-08" in a and "DST" in a and "-05:00" in a and "-04:00" in a for a in r["assumptions"])
    # the local rendering follows the offset in force at that instant
    last = r["result"]["slots"][-1]["local"][0]
    assert last["start"] == "2026-03-08T04:00:00-04:00" and last["end"] == "2026-03-08T05:00:00-04:00"
    # a window that does not cross a transition gets no such note
    r = dt("free_slots", participants=pair, start="2026-03-15", end="2026-03-15", granularity=60)
    assert r["ok"] and not any("DST" in a for a in r["assumptions"])


def test_free_slots_abbreviations_and_errors():
    r = dt("free_slots", participants=[{"tz": "IST", "windows": [{"start": "09:00", "end": "17:00"}]}, TRIO[1]], start="2026-09-07")
    assert not r["ok"] and r["error"] == "ambiguous" and "Asia/Kolkata" in r["needs"]["options"]
    win = [{"start": "09:00", "end": "17:00"}]

    def bad(**kw):
        r = dt("free_slots", **kw)
        assert not r["ok"] and r["error"] == "invalid_input", r
        return r["message"]

    assert "two" in bad(participants=[TRIO[0]], start="2026-09-07")
    assert "two" in bad(start="2026-09-07")
    assert "tz" in bad(participants=[{"windows": win}, TRIO[1]], start="2026-09-07")
    assert "windows" in bad(participants=[{"tz": "Asia/Kolkata"}, TRIO[1]], start="2026-09-07")
    assert "start" in bad(participants=[{"tz": "Asia/Kolkata", "windows": [{"end": "17:00"}]}, TRIO[1]], start="2026-09-07")
    assert "after" in bad(participants=[{"tz": "Asia/Kolkata", "windows": [{"start": "17:00", "end": "09:00"}]}, TRIO[1]], start="2026-09-07")
    assert "after" in bad(participants=[{"tz": "Asia/Kolkata", "windows": [{"start": "2026-09-07T17:00", "end": "2026-09-07T17:00"}]}, TRIO[1]], start="2026-09-07")
    assert "days" in bad(participants=[{"tz": "Asia/Kolkata", "windows": [{"start": "09:00", "end": "17:00", "days": ["funday"]}]}, TRIO[1]], start="2026-09-07")
    assert "granularity" in bad(participants=TRIO, start="2026-09-07", granularity=0)
    assert "duration" in bad(participants=TRIO, start="2026-09-07", duration=-30)
    assert "before" in bad(participants=TRIO, start="2026-09-07", end="2026-09-01")
    assert "92" in bad(participants=TRIO, start="2026-01-01", end="2026-12-31")
    # a weekly window needs a time of day, an absolute one a full timestamp on both ends
    assert "time" in bad(participants=[{"tz": "Asia/Kolkata", "windows": [{"start": "09:00", "end": "2026-09-07T17:00"}]}, TRIO[1]], start="2026-09-07")


def test_unix_timestamps_as_digit_strings():
    n = dt("parse", value=1787232546, tz="Asia/Kolkata")["result"]
    s = dt("parse", value="1787232546", tz="Asia/Kolkata")
    assert s["ok"], s
    assert s["result"]["unix"] == n["unix"] == 1787232546 and s["result"]["iso"] == n["iso"]
    assert "unix timestamp read as UTC" in s["assumptions"]
    ms = dt("parse", value="1787232546000", tz="Asia/Kolkata")
    assert ms["ok"] and ms["result"]["unix"] == 1787232546 and "timestamp read as milliseconds" in ms["assumptions"]
    assert dt("parse", value=" -1000000000 ")["result"]["iso"] == "1938-04-24T22:13:20+00:00"  # negative epochs, surrounding whitespace
    c = dt("convert_tz", value="1787232546", to_tz="Asia/Kolkata")
    assert c["ok"] and c["result"]["converted"]["unix"] == 1787232546 and c["result"]["converted"]["utc_offset"] == "+05:30"
    assert dt("add", value="1787232546", amount=1, unit="days")["result"]["unix"] == 1787232546 + 86400
    # digit strings that are not timestamps keep their old readings
    assert dt("parse", value="2026")["result"]["date"].startswith("2026-")  # a 4-digit string is still a year
    assert dt("parse", value="20260827")["result"]["date"] == "2026-08-27"
