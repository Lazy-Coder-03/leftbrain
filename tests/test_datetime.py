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
    assert r["ok"] and "no 'start' given; started from now" in r["assumptions"]


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
