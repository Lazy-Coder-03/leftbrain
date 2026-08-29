"""#73 and #77: what a geo answer is *about*.

#73: `coordinates` was the zone's reference city, whatever place had been asked for. So
`tz_for_place("Mumbai")` and `tz_for_place("Chennai")` both came back holding Kolkata's
coordinates — 1,650 and 1,300 km out — under a field name that reads as "where this is". The
`tz_for_coords` response was visibly self-contradictory: Kolkata's coordinates beside
`distance_to_reference_km: 0.5`, with alternatives correctly computed for Mumbai.

#77: `Asia/Tokyo` listing Australia is not a bad reverse lookup. tzdata's `zone1970.tab` really
reads `JP,AU  ... Asia/Tokyo  Eyre Bird Observatory` — a Western Australian station that keeps
UTC+9. What was missing was any explanation, so correct data read as a mapping error.
"""

import pytest

from leftbrain.core.geo_offline import geo_offline as geo

MUMBAI = (19.076, 72.8777)


def test_the_coordinates_are_the_point_that_was_asked_about():
    r = geo("tz_for_coords", lat=MUMBAI[0], lon=MUMBAI[1])
    assert r["ok"]
    assert r["result"]["coordinates"] == {"lat": MUMBAI[0], "lon": MUMBAI[1]}


def test_the_zone_reference_is_named_for_what_it_is():
    r = geo("tz_for_coords", lat=MUMBAI[0], lon=MUMBAI[1])["result"]
    assert r["zone"] == "Asia/Kolkata"
    assert round(r["zone_reference"]["lat"]) == 23 and round(r["zone_reference"]["lon"]) == 88
    assert "coordinates" in r and r["coordinates"] != r["zone_reference"]


def test_the_distance_can_be_checked_against_the_point_it_was_measured_from():
    """0.5 km beside a reference city 1,650 km away is what made the response look wrong."""
    r = geo("tz_for_coords", lat=MUMBAI[0], lon=MUMBAI[1])["result"]
    near = r["nearest_reference"]
    assert near["km"] == r["distance_to_reference_km"]
    assert abs(near["lat"] - MUMBAI[0]) < 1 and abs(near["lon"] - MUMBAI[1]) < 1


def test_a_place_lookup_does_not_claim_to_know_where_the_place_is():
    """The old field said Kolkata for every non-namesake city in the zone."""
    for place in ("Mumbai", "Chennai"):
        r = geo("tz_for_place", place=place)
        assert r["ok"] and "coordinates" not in r["result"], (place, r["result"])
        assert r["result"]["zone_reference"] is not None


def test_a_zone_whose_namesake_was_asked_for_is_unchanged():
    r = geo("tz_for_place", place="Sydney")["result"]
    assert r["zone"] == "Australia/Sydney"
    assert round(r["zone_reference"]["lat"]) == -34


def test_distance_by_place_name_still_refuses_rather_than_measuring_the_zone_city():
    """Already correct before this change, and the reason the bug was not worse: the refusal
    names the exact trap. Pinned so it cannot regress into silently answering ~0 km."""
    r = geo("distance", origin="Mumbai", destination="Chennai")
    assert not r["ok"] and r["error"] == "ambiguous"
    assert "reference city" in r["message"]


# --- #77 -------------------------------------------------------------------------------


def test_a_zone_listing_an_unexpected_country_says_why():
    r = geo("zone_info", zone="Asia/Tokyo")["result"]
    assert [c["code"] for c in r["countries"]] == ["JP", "AU"]
    assert "Eyre Bird Observatory" in r["countries_note"]
    assert "JP, AU" in r["countries_note"]


def test_the_shared_note_is_quoted_not_claimed_to_explain_every_country():
    """Dubai's shared entry says only "Crozet", which accounts for TF and not for Oman."""
    note = geo("zone_info", zone="Asia/Dubai")["result"]["countries_note"]
    assert "Crozet" in note and "AE, OM, RE, SC, TF" in note
    assert "reads" in note  # quoted from upstream rather than asserted as an explanation


@pytest.mark.parametrize("zone", ["Australia/Sydney", "Asia/Kolkata", "Europe/London"])
def test_a_single_country_zone_says_nothing_extra(zone):
    assert geo("zone_info", zone=zone)["result"]["countries_note"] is None


def test_the_zones_own_note_still_belongs_to_the_country_asked_about():
    """Blanking it is deliberate: "Crozet" is not an answer about Dubai."""
    assert geo("tz_for_place", place="Dubai")["result"]["note"] is None
    assert geo("zone_info", zone="Australia/Sydney")["result"]["note"] == "New South Wales (most areas)"
