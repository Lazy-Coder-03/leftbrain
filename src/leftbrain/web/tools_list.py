"""Shared tool metadata: (name, one-line description, modes) for each of the 14 tools.

Used by both the landing page's tool grid and the generated docs "Tools" page.
Lives in its own module so `docs.py` can import it without creating a circular
import with `views.py` (which also renders the landing page).
"""

from __future__ import annotations

TOOLS: list[tuple[str, str, str]] = [
    ("math", "Exact arithmetic & symbolic algebra (SymPy)", "eval · exact · simplify · expand · factor · solve …"),
    ("datetime", "Dates, durations, time zones, business days", "now · convert_tz · parse · add · diff · weekday …"),
    ("scale", "Scale numbers and recipes proportionally", "linear · inverse"),
    ("convert", "Units, currencies, fuel economy, cooking measures, sizes", "units · temperature · currency · fuel_economy · cooking · sizes · auto"),
    ("holidays", "Public holidays by country/region", "list · check · next · countries · subdivisions"),
    ("numbers", "Compare, round, format, allocate exactly", "compare · round · format · allocate · sequence · parse …"),
    ("finance", "EMI, compound growth, CAGR, NPV/IRR, GST", "emi · compound · cagr · npv_irr · gst · percent"),
    ("text", "Count, slice, case, diff — by codepoint", "count · regex_match · regex_replace · diff · sort · dedupe …"),
    ("collections", "Sort, group, filter, pivot — records or CSV", "set_ops · group_by · sort_by · filter · pivot · summarize · to_csv …"),
    ("validate", "Assert rules over JSON, emails, IBANs, schemas", "json_schema · assert · id · email · url · phone …"),
    ("random", "Seeded, reproducible randomness", "uuid · int · float · pick · shuffle · token …"),
    ("geo_offline", "Distance, bearing, bounding boxes", "tz_for_place · tz_for_coords · distance · country · zone_info"),
    ("encode", "Hashes, base64, URL, hex", "hash · hmac · checksum · base64 · hex · url · html …"),
    ("color", "Convert, name, contrast, mix, harmonise, swatch", "convert · describe · swatch · contrast · mix · harmony · nearest …"),
]
