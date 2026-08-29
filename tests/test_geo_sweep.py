"""Adversarial sweep of `geo_offline`, in the failure classes #52 found in `math`."""

import pytest

from leftbrain.core.geo_offline import geo_offline as geo


@pytest.mark.parametrize(("lat", "lon", "zone"), [(28.6139, 77.209, "Asia/Kolkata"), (12.97, 77.59, "Asia/Kolkata"), (18.5, 73.9, "Asia/Kolkata"), (22.5726, 88.3639, "Asia/Kolkata"), (40.7128, -74.006, "America/New_York"), (39.9, 116.4, "Asia/Shanghai"), (-33.87, 151.21, "Australia/Sydney")])
def test_coordinates_inside_a_one_zone_country_get_that_zone(lat, lon, zone):
    """New Delhi answered Asia/Kathmandu: India's only reference city is Kolkata, 800 km away."""
    r = geo("tz_for_coords", lat=lat, lon=lon)
    assert r["ok"] and r["result"]["zone"] == zone, (lat, lon, r["result"].get("zone"))


def test_coordinates_must_be_numbers_and_come_in_pairs():
    r = geo("tz_for_coords", lat="north", lon=88.36)
    assert r["ok"] is False and "number" in r["message"] and "could not convert" not in r["message"], r
    r = geo("tz_for_coords", lat=22.5726)
    assert r["ok"] is False and "lon" in r["message"] and "NoneType" not in r["message"], r


def test_ambiguous_place_names_are_ambiguous():
    for place, expect in (("Washington", "America/Los_Angeles"), ("Georgia", "America/New_York"), ("LA", "America/Los_Angeles"), ("Portland", "America/New_York"), ("Birmingham", "America/Chicago")):
        r = geo("tz_for_place", place=place)
        assert r["ok"] is False and r["error"] == "ambiguous" and expect in r["needs"]["options"], (place, r)
    assert geo("tz_for_place", place="Washington DC")["result"]["zone"] == "America/New_York"


def test_zone_aliases_and_case():
    assert geo("tz_for_place", place="asia/kolkata")["result"]["zone"] == "Asia/Kolkata"
    r = geo("tz_for_place", place="Asia/Calcutta")
    assert r["ok"] and r["result"]["zone"] == "Asia/Kolkata" and any("Asia/Calcutta" in a and "link" in a for a in r["assumptions"]), r
    r = geo("zone_info", zone="Asia/Calcutta")
    assert r["ok"] and r["result"]["zone"] == "Asia/Kolkata" and r["result"]["countries"][0]["code"] == "IN"


def test_the_note_is_about_the_zone_asked_for():
    """Dubai's zone1970 line is shared with Réunion and Crozet; the comment described those."""
    assert geo("tz_for_place", place="Dubai")["result"]["note"] is None
    assert geo("tz_for_place", place="Tokyo")["result"]["note"] is None


def test_country_names_share_the_alias_table_and_ignore_accents():
    for name, code in (("England", "GB"), ("United Kingdom", "GB"), ("Reunion", "RE"), ("Réunion", "RE"), ("Curacao", "CW"), ("Timor-Leste", "TL"), ("Viet Nam", "VN"), ("Russian Federation", "RU")):
        r = geo("country", country=name)
        assert r["ok"] and r["result"]["code"] == code, (name, r)
    r = geo("country", country="Kosovo")
    assert r["ok"] and r["result"]["zone_count"] == 0 and any("XK" in w for w in r["warnings"]), r
