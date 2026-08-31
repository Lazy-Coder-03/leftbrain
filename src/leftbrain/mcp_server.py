"""leftbrain MCP server - exposes the core tools over stdio (or HTTP).

Run: ``leftbrain`` (after ``pip install leftbrain[mcp]``) or ``python -m leftbrain.mcp_server``.

Tool descriptions are written to say *when* to call the tool, because the
usual failure is the model not calling it, not the tool being wrong.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

try:
    from .mcp_contract import ContractMCPServer
except ImportError:  # pragma: no cover
    print("leftbrain MCP server needs the 'mcp' package: pip install 'leftbrain[mcp]'", file=sys.stderr)
    raise

from . import __version__
from .core import (
    color as color_mod,
)
from .core import (
    convert as convert_mod,
)
from .core import (
    datetimex,
    geo_offline,
    random_,
)
from .core import (
    encode as encode_mod,
)
from .core import (
    finance as finance_mod,
)
from .core import (
    scale as scale_mod,
)
from .runner import run_guarded
from .scopes import enforce

INSTRUCTIONS = """leftbrain provides exact, deterministic answers for things language models get wrong.
Call these tools BEFORE stating any number, date, conversion, count, ordering, or validation result -
even when the answer seems obvious. Every response has the shape
{ok, result, assumptions[], warnings[]} or {ok:false, error, message, needs?}.
When ok is false and 'needs' is present, the input was ambiguous: pick one of needs.options and call again.
Read 'assumptions' - they say how ambiguous input was interpreted."""

#: Appended to the instructions when the network tools are on the server, because the
#: opening line promises determinism and these four are the exception to it.
NETWORK_NOTE = "weather, fx_rate, geo and url_check reach the internet; their answers are as-of the moment of the call."


def _clean(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


#: A quantity the caller may write as a number or as text - "1.2 Cr", "2.5k", "12%", "1/3".
#:
#: Spelled out rather than `Any` because an MCP client serialises its arguments *against this
#: schema*, and `Any` renders as `anyOf: [{}, {"type": "null"}]` - an empty schema, carrying no
#: type at all. A client with nothing to serialise against emitted `{"text": abc}` with the
#: string unquoted, so the call failed inside the client and never reached the server: no
#: envelope, no `needs`, nothing for an agent to recover from, and a retry produced the same
#: malformed call (#71). Pydantic's smart union keeps each input as the type it arrived as, so
#: "0.1" stays the exact decimal string leftbrain parses and 5 stays an int.
Quantity = str | float | int | None

#: A whole JSON document rather than a scalar: objects and arrays are accepted here too.
#: `encode.text` really does take a dict - a JSON document arriving already parsed - so this
#: cannot be narrowed to a string.
Document = str | float | int | bool | list[Any] | dict[str, Any] | None

#: A place: a name, "lat,lon", [lat, lon], or {"lat": .., "lon": ..}.
Place = str | list[Any] | dict[str, Any] | None

@enforce("math")
def math(
    mode: str = "eval",
    expr: str | None = None,
    angle: str | None = None,
    percent: str | None = None,
    precision: int | None = None,
    var: str | None = None,
    vars: dict[str, Any] | list[str] | None = None,
    equations: list[str] | None = None,
    domain: str | None = None,
    order: int | None = None,
    at: str | float | None = None,
    lower: str | float | None = None,
    upper: str | float | None = None,
    point: str | float | None = None,
    form: str | None = None,
    side: str | None = None,
    equation: str | None = None,
    func: str | None = None,
    ics: dict[str, Any] | None = None,
    op: str | None = None,
    A: list[list[Any]] | str | None = None,
    B: list[list[Any]] | None = None,
    b: list[Any] | None = None,
    n: int | None = None,
    data: list[Any] | None = None,
    y: list[Any] | None = None,
    weights: list[Any] | None = None,
    percentile: float | None = None,
    value: Quantity = None,
    predict: float | None = None,
    range: list[float] | None = None,
    tolerance: float | None = None,
    significant: int | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Use for arithmetic and mathematics rather than working it out: percentages, fractions,
    powers, roots, trigonometry, complex numbers, algebra, calculus, matrices, statistics -
    each answered in exact form and decimal form together. Functions come from a named
    allowlist (isprime and factorint are in, primepi is not) and a rejected name is answered
    with the accepted set. Predicates return booleans that add up, so
    is_prime(11)+is_prime(12) counts how many hold in one call.

    mode: eval | exact | simplify | expand | factor | solve | diff | integrate | limit | series | ode | matrix | stats | convert_form | plot_points
    - eval/exact: expr (e.g. "15% of 200", "(3+4i)*(1-2i)", "sin(30)" with angle="deg")
    - solve: equations=["x^2+1=0"] (+ vars, domain=real|complex|integer|positive)
    - diff/integrate/limit/series: expr + var (+ order / lower,upper / point,side / at,order)
    - ode: equation="y'' + y = 0", func="y(x)", ics={"y(0)":1,"y'(0)":0}
    - matrix: op=det|inv|transpose|rank|trace|rref|eig|solve|mul|add|sub|pow, A=[[...]], B/b
    - stats: op=describe|mean|median|stdev|pstdev|variance|percentile|quartiles|corr|regress|..., data=[...]
    - convert_form: form=polar|rect|latex|decimal|fraction|scientific|percent
    angle is REQUIRED ('rad' or 'deg') whenever the expression contains trigonometry.
    Returns exact form, decimal form and LaTeX together.
    """
    params = _clean(dict(expr=expr, angle=angle, percent=percent, precision=precision, var=var, vars=vars, equations=equations, domain=domain, order=order, at=at, lower=lower, upper=upper, point=point, form=form, side=side, equation=equation, func=func, ics=ics, op=op, A=A, B=B, b=b, n=n, data=data, y=y, weights=weights, percentile=percentile, value=value, predict=predict, range=range, tolerance=tolerance, significant=significant, timeout=timeout))
    return run_guarded("math", mode, params, timeout=timeout)


@enforce("datetime")
def datetime(
    mode: str = "now",
    value: str | int | float | None = None,
    tz: str | list[str | dict[str, str]] | None = None,
    from_tz: str | None = None,
    to_tz: str | dict[str, str] | list[str | dict[str, str]] | None = None,
    locale: str | None = None,
    ref_date: str | None = None,
    amount: float | None = None,
    unit: str | None = None,
    region: str | None = None,
    subdiv: str | None = None,
    weekend: list[str] | None = None,
    extra_holidays: list[Any] | None = None,
    include_start: bool | None = None,
    include_end: bool | None = None,
    year: int | None = None,
    month: int | str | None = None,
    weekday: str | None = None,
    n: int | None = None,
    a: dict[str, str] | None = None,
    b: dict[str, str] | None = None,
    ranges: list[dict[str, Any]] | None = None,
    rule: str | None = None,
    start: str | None = None,
    end: str | None = None,
    count: int | None = None,
    until: str | None = None,
    limit: int | None = None,
    expr: str | None = None,
    dob: str | None = None,
    on: str | None = None,
    fy_start_month: int | None = None,
    dates_only: bool | None = None,
    participants: list[dict[str, Any]] | None = None,
    duration: int | None = None,
    granularity: int | None = None,
) -> dict[str, Any]:
    """Use for ANYTHING involving dates, times, timezones, durations, weekdays, deadlines,
    business days, recurring schedules, cron, ages or fiscal periods. Never compute these yourself.

    mode:
    - now (tz) - the current instant; the model has no clock without this. tz may be a list of zones (or [{tz, label}]) for several at once
    - convert_tz (value, from_tz, to_tz) - IANA names only ("Asia/Kolkata"); abbreviations like IST are refused as ambiguous; to_tz takes a list, entries may be {tz, label}
    - parse (value, locale, ref_date) - ISO, written, or relative ("next friday 5pm"); 03/04/2025 needs locale
    - add (value, amount, unit) - unit: days|weeks|months|years|hours|minutes|business_days (+ region)
    - diff (start, end, unit) - calendar breakdown + totals; unit=business_days for working days
    - weekday (value) | nth_weekday (year, month, weekday, n; n=-1 = last)
    - business_days (start, end, region, subdiv, weekend, extra_holidays)
    - overlap (a={start,end}, b={start,end}) | duration_sum (ranges=[{start,end}])
    - free_slots (participants=[{tz, label, windows=[{start, end, days}]}], duration, granularity, start, end, limit) - common free slots for 2+ people in different zones, each in everyone's local time and UTC; DST-aware
    - recurrence (rule="every 2nd tuesday" or RRULE, start, count/until)
    - cron_next (expr="0 9 * * 1-5", tz, start, n)
    - age (dob, on) | fiscal (value, region or fy_start_month)
    """
    params = _clean(dict(value=value, tz=tz, from_tz=from_tz, to_tz=to_tz, locale=locale, ref_date=ref_date, amount=amount, unit=unit, region=region, subdiv=subdiv, weekend=weekend, extra_holidays=extra_holidays, include_start=include_start, include_end=include_end, year=year, month=month, weekday=weekday, n=n, a=a, b=b, ranges=ranges, rule=rule, start=start, end=end, count=count, until=until, limit=limit, expr=expr, dob=dob, on=on, fy_start_month=fy_start_month, dates_only=dates_only, participants=participants, duration=duration, granularity=granularity))
    return datetimex.datetime_tool(mode, **params)


@enforce("scale")
def scale(
    from_qty: float | str | None = None,
    to_qty: float | str | None = None,
    from_unit: str | None = None,
    to_unit: str | None = None,
    entities: list[dict[str, Any]] | dict[str, Any] | None = None,
    mode: str = "linear",
    factor: float | str | None = None,
    precision: int = 6,
    assume: str | None = None,
) -> dict[str, Any]:
    """Use when one quantity changes and everything proportional to it must change too:
    recipes (4 -> 7 servings), price per kg -> per 250 g, batch sizes, headcount vs. days
    ratios held constant. entities=[{name, qty, unit?, integer?}].
    mode: linear (direct proportion - more servings, more of everything) | inverse (more workers,
    fewer days)
    Returns the factor and every entity scaled, in exact and decimal form.
    """
    return scale_mod.scale(**_clean(dict(from_qty=from_qty, to_qty=to_qty, from_unit=from_unit, to_unit=to_unit, entities=entities, mode=mode, factor=factor, precision=precision, assume=assume)))


@enforce("convert")
def convert(
    value: float | str,
    from_unit: str,
    to_unit: str,
    mode: str = "auto",
    assume: str | None = None,
    delta: bool | None = None,
    rate: float | None = None,
    rates: dict[str, float] | None = None,
    base: str | None = None,
    precision: int | None = None,
    decimals: int | None = None,
    date: str | None = None,
    ingredient: str | None = None,
    cup: str | None = None,
    category: str | None = None,
    region: str | None = None,
    gender: str | None = None,
) -> dict[str, Any]:
    """Use for ANY unit, temperature, currency, fuel-economy, cooking or size conversion (km->mi,
    sqft->sqm, C->F, kWh->J, GB->GiB, USD->INR, mpg->L/100km, cups of flour->g, US 9->EU shoe).
    Ambiguous units (ton, gallon, oz, cup, KB, mpg) are refused with options unless assume="common".
    Currency needs rate= or rates= (fetch via the external fx_rate tool).
    mode: units (any physical or digital unit) | temperature (C/F/K, delta= for a difference) |
    currency (rate or rates) | fuel_economy (mpg_us/mpg_uk/km_per_l/l_per_100km) |
    cooking (cup/tbsp/tsp/ml/fl_oz <-> g/kg/oz_weight/lb, ingredient= for mass<->volume, cup=us|metric|uk|au) |
    sizes (category=shoe: us_men/us_women/uk/eu/cm; category=clothing: alpha/chest_cm/waist_cm with region= and gender=) |
    auto (pick units or currency from the arguments)
    """
    return convert_mod.convert(mode, **_clean(dict(value=value, from_unit=from_unit, to_unit=to_unit, assume=assume, delta=delta, rate=rate, rates=rates, base=base, precision=precision, decimals=decimals, date=date, ingredient=ingredient, cup=cup, category=category, region=region, gender=gender)))


@enforce("numbers")
def numbers(
    mode: str = "compare",
    values: list[Any] | None = None,
    a: Quantity = None,
    b: Quantity = None,
    value: Quantity = None,
    decimals: int | None = None,
    significant: int | None = None,
    nearest: float | None = None,
    rounding: str | None = None,
    locale: str | None = None,
    style: str | None = None,
    currency: str | None = None,
    accounting: bool | None = None,
    total: Quantity = None,
    parts: int | None = None,
    weights: list[Any] | dict[str, Any] | None = None,
    percentages: list[Any] | None = None,
    labels: list[str] | None = None,
    method: str | None = None,
    kind: str | None = None,
    start: Quantity = None,
    step: Quantity = None,
    ratio: Quantity = None,
    end: Quantity = None,
    n: int | None = None,
    system: str | None = None,
    suffix_only: bool | None = None,
) -> dict[str, Any]:
    """Use to compare numbers (9.11 vs 9.9), round with a stated rule, format for a locale
    (Indian 12,34,567 / currency / percent / compact), split an amount so shares sum exactly
    (allocate: total, parts or weights), generate sequences, parse "₹1.2 Cr"/"2.5k"/"12%",
    or spell an amount in words (to_words: system=indian|international, currency=INR), or order
    version strings (semver: 1.10 > 1.9, pre-releases per SemVer 2.0).
    mode: compare | round | format | allocate | sequence | parse | to_words | semver
    """
    return run_guarded("numbers", mode, _clean(dict(values=values, a=a, b=b, value=value, decimals=decimals, significant=significant, nearest=nearest, rounding=rounding, locale=locale, style=style, currency=currency, accounting=accounting, total=total, parts=parts, weights=weights, percentages=percentages, labels=labels, method=method, kind=kind, start=start, step=step, ratio=ratio, end=end, n=n, system=system, suffix_only=suffix_only)))


@enforce("finance")
def finance(
    mode: str = "emi",
    principal: Quantity = None,
    rate: Quantity = None,
    rate_period: str | None = None,
    months: Quantity = None,
    years: Quantity = None,
    schedule: bool | None = None,
    decimals: int | None = None,
    rounding: str | None = None,
    compounding: str | None = None,
    contribution: Quantity = None,
    contribution_timing: str | None = None,
    start_value: Quantity = None,
    end_value: Quantity = None,
    cashflows: list[Any] | None = None,
    amount: Quantity = None,
    amount_is: str | None = None,
    supply: str | None = None,
    op: str | None = None,
    a: Quantity = None,
    b: Quantity = None,
    percent: Quantity = None,
    value: Quantity = None,
    price: Quantity = None,
    discounts: list[Any] | None = None,
    total: Quantity = None,
    tip: Quantity = None,
    people: int | None = None,
) -> dict[str, Any]:
    """Use for any money arithmetic: loan EMIs with a full amortisation schedule, compound
    growth and SIPs (mode='compound' with 'contribution'; leave 'principal' out for a SIP
    starting from zero), CAGR, NPV and IRR of a cash-flow series, GST inclusive/exclusive splits
    with CGST/SGST/IGST, and percentages (change vs percentage points, stacked discounts,
    bill splits). Exact decimals; the rate's period (annual|monthly) and whether an amount is
    GST-inclusive are never guessed.
    mode: emi | compound | cagr | npv_irr | gst | percent
    """
    return finance_mod.finance(mode, **_clean(dict(principal=principal, rate=rate, rate_period=rate_period, months=months, years=years, schedule=schedule, decimals=decimals, rounding=rounding, compounding=compounding, contribution=contribution, contribution_timing=contribution_timing, start_value=start_value, end_value=end_value, cashflows=cashflows, amount=amount, amount_is=amount_is, supply=supply, op=op, a=a, b=b, percent=percent, value=value, price=price, discounts=discounts, total=total, tip=tip, people=people)))


@enforce("text")
def text(
    mode: str = "count",
    text: str | None = None,
    what: str | list[str] | None = None,
    substring: str | None = None,
    case_sensitive: bool | None = None,
    pattern: str | None = None,
    flags: str | None = None,
    replacement: str | None = None,
    count: int | None = None,
    a: str | None = None,
    b: str | None = None,
    granularity: str | None = None,
    items: list[Any] | None = None,
    key: str | None = None,
    order: str | None = None,
    natural: bool | None = None,
    case_insensitive: bool | None = None,
    unique: bool | None = None,
    limit: int | None = None,
    context: int | None = None,
    normalize_whitespace: bool | None = None,
    overlapping: bool | None = None,
) -> dict[str, Any]:
    """Use to count characters/words/occurrences ("how many r in strawberry"), run or test a
    regex, produce an exact diff between two texts, sort strings (natural order), remove
    duplicates, find positions, extract emails/phones/urls/dates/ids from text, or measure how
    alike two strings are and pick the best match from a list (similarity: Levenshtein).
    mode: count | regex_match | regex_replace | diff | sort | dedupe | extract | find | similarity
    """
    return run_guarded("text", mode, _clean(dict(text=text, what=what, substring=substring, case_sensitive=case_sensitive, pattern=pattern, flags=flags, replacement=replacement, count=count, a=a, b=b, granularity=granularity, items=items, key=key, order=order, natural=natural, case_insensitive=case_insensitive, unique=unique, limit=limit, context=context, normalize_whitespace=normalize_whitespace, overlapping=overlapping)))


@enforce("collections")
def collections(
    mode: str = "set_ops",
    items: list[Any] | str | None = None,
    a: list[Any] | str | None = None,
    b: list[Any] | str | None = None,
    op: str | None = None,
    key: str | None = None,
    keys: list[Any] | None = None,
    fields: list[str] | None = None,
    field: str | None = None,
    agg: list[str] | str | None = None,
    agg_field: str | None = None,
    ops: list[str] | None = None,
    data: Document = None,
    depth: int | None = None,
    separator: str | None = None,
    page: int | None = None,
    per_page: int | None = None,
    size: int | None = None,
    n: int | None = None,
    case_insensitive: bool | None = None,
    include_items: bool | None = None,
    order: str | None = None,
    rename: dict[str, str] | None = None,
    short_names: bool | None = None,
    flatten_lists: bool | None = None,
    where: list[dict[str, Any]] | None = None,
    by: list[str] | str | None = None,
    pivot_columns: str | None = None,
    column: str | None = None,
    columns: list[str] | None = None,
    delimiter: str | None = None,
    has_header: bool | None = None,
    escape_formulas: bool | None = None,
    decimals: int | None = None,
) -> dict[str, Any]:
    """Use for exact list/record logic that models get wrong past ~20 items: compare two lists
    (what's missing where), union/intersection/difference, group records by a field with
    sum/avg/count, multi-key sorting, duplicates, pagination, chunking, flatten/unflatten JSON,
    and table arithmetic — filter rows, pivot, running totals, IQR outliers, a per-field summary,
    CSV out. Records may be given as JSON objects or as CSV text: the delimiter, header row and
    field types (number, date, boolean, text) are detected and stated in `assumptions`.
    mode: set_ops | group_by | aggregate | pick_fields | flatten | unflatten | paginate | find_duplicates | sort_by | chunk |
    filter | pivot | running | outliers | summarize | to_csv
    Paths use dotted syntax: "user.address.city", "items[0].sku".
    """
    return run_guarded("collections", mode, _clean(dict(items=items, a=a, b=b, op=op, key=key, keys=keys, fields=fields, field=field, agg=agg, agg_field=agg_field, ops=ops, data=data, depth=depth, separator=separator, page=page, per_page=per_page, size=size, n=n, case_insensitive=case_insensitive, include_items=include_items, order=order, rename=rename, short_names=short_names, flatten_lists=flatten_lists, where=where, by=by, pivot_columns=pivot_columns, column=column, columns=columns, delimiter=delimiter, has_header=has_header, escape_formulas=escape_formulas, decimals=decimals)))


@enforce("validate")
def validate(
    mode: str = "assert",
    data: Document = None,
    rules: list[dict[str, Any]] | dict[str, Any] | None = None,
    schema: dict[str, Any] | None = None,
    kind: str | None = None,
    value: Quantity = None,
    region: str | None = None,
    sql: str | None = None,
    dialect: str | None = None,
    pattern: str | None = None,
    network: str | list[str] | None = None,
) -> dict[str, Any]:
    """Use to CHECK instead of judge: evaluate policy rules over a JSON document
    (assert: rules=[{path:"leave.days", op:"lte", value:2}] -> pass/fail + score), validate
    against a JSON Schema, verify identifiers by checksum (kind=card|iban|gstin|pan|aadhaar|isbn|
    ean|ifsc|vin|uuid|upi; isbn returns both forms), check email/url/phone/ip syntax, test CIDR
    membership and overlap (cidr: network, value), or parse SQL before running it
    (sql_parse flags writes, DELETE/UPDATE without WHERE, tables touched).
    mode: json_schema | assert | id | email | url | phone | ip | sql_parse | regex | cidr
    assert ops: eq ne gt gte lt lte between in not_in contains starts_with ends_with matches
    exists missing empty not_empty type len_eq len_gt len_lt before after is_email is_url is_date unique sum_eq each
    """
    return run_guarded("validate", mode, _clean(dict(data=data, rules=rules, schema=schema, kind=kind, value=value, region=region, sql=sql, dialect=dialect, pattern=pattern, network=network)))


@enforce("random")
def random(
    mode: str = "uuid",
    n: int | None = None,
    min: float | None = None,
    max: float | None = None,
    unique: bool | None = None,
    seed: str | int | None = None,
    items: list[Any] | None = None,
    weights: list[float] | None = None,
    kind: str | None = None,
    length: int | None = None,
    version: int | None = None,
    decimals: int | None = None,
    p: float | None = None,
    groups: int | list[str] | None = None,
    k: int | None = None,
    format: str | None = None,
) -> dict[str, Any]:
    """Use whenever randomness is needed - the model cannot generate it. UUIDs (v4/v7), random
    integers/floats (seed= for reproducibility), pick/shuffle/sample from a list, A/B group
    assignment, secure tokens/passwords/OTPs (kind=hex|alnum|urlsafe|password|otp|readable).
    mode: uuid | int | float | pick | shuffle | token | bool | sample
    """
    return random_.random_tool(mode, **_clean(dict(n=n, min=min, max=max, unique=unique, seed=seed, items=items, weights=weights, kind=kind, length=length, version=version, decimals=decimals, p=p, groups=groups, k=k, format=format)))


@enforce("geo_offline")
def geo(
    mode: str = "tz_for_place",
    place: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    origin: Place = None,
    destination: Place = None,
    country: str | None = None,
    zone: str | None = None,
    all: bool | None = None,
    point: Place = None,
) -> dict[str, Any]:
    """Use to get the IANA timezone for a city/country ("Mumbai" -> Asia/Kolkata) before any
    timezone conversion, the zone nearest to coordinates, great-circle distance and bearing
    between two places or coordinates, or a country's zones. Fully offline.
    mode: tz_for_place | tz_for_coords | distance (origin, destination) | country | zone_info
    """
    params = _clean(dict(place=place, lat=lat, lon=lon, origin=origin, destination=destination, country=country, zone=zone, all=all, point=point))
    return geo_offline.geo_offline(mode, **params)


@enforce("encode")
def encode(
    mode: str = "hash",
    text: Document = None,
    algo: str | None = None,
    key: str | None = None,
    expected: str | None = None,
    action: str | None = None,
    urlsafe: bool | None = None,
    token: str | None = None,
    data: Document = None,
    indent: int | None = None,
    bytes_base64: str | None = None,
    bytes_hex: str | None = None,
    encoding: str | None = None,
    key_base64: bool | None = None,
    strip_padding: bool | None = None,
    plus: bool | None = None,
    safe: str | None = None,
    quote: bool | None = None,
    sort_keys: bool | None = None,
) -> dict[str, Any]:
    """Use for any hash, HMAC, checksum or encoding - models hallucinate these every time.
    mode: hash (algo=sha256|md5|sha1|sha512|blake2b) | hmac (key) | checksum (crc32|adler32) |
    base64 (action=encode|decode) | hex | url | html | jwt_decode (unverified claims) | json (parse|format|minify)
    """
    return encode_mod.encode(mode, **_clean(dict(text=text, algo=algo, key=key, expected=expected, action=action, urlsafe=urlsafe, token=token, data=data, indent=indent, bytes_base64=bytes_base64, bytes_hex=bytes_hex, encoding=encoding, key_base64=key_base64, strip_padding=strip_padding, plus=plus, safe=safe, quote=quote, sort_keys=sort_keys)))


@enforce("color")
def color(
    mode: str = "convert",
    value: str | None = None,
    spaces: str | list[str] | None = None,
    other: str | None = None,
    ratio: float | None = None,
    space: str | None = None,
    kind: str | None = None,
    palette: list[str] | None = None,
    size: int | None = None,
    method: str | None = None,
    ramp: int | None = None,
    image: bool | None = None,
    level: str | None = None,
    decimals: int | None = None,
) -> dict[str, Any]:
    """Use for any colour question - conversions, the nearest name, WCAG contrast, blends, harmonies, colour-blind views, greys, a PNG swatch to look at. Colour is arithmetic; never guess it.
    mode: convert (spaces) | describe | swatch (other, size) | contrast (other, level) | mix (other, ratio, space) |
    harmony (kind) | nearest (palette) | simulate (kind, image, size) | grayscale (method, ramp, image, size)
    - convert (value, spaces) - hex (3/4/6/8 digits), rgb, hsl, hsv/hsb, cmyk and Lab in every direction, alpha kept
    - describe (value) - nearest of the 148 CSS colour names by Lab ΔE plus a fixed-wording description such as "vivid red-orange, medium-light"
    - swatch (value, other, size) - a solid PNG of the colour, or two side by side, as Base64 (16 to 256 px)
    - contrast (value, other, level) - WCAG 2.x ratio, AA/AAA for normal and large text, and the smallest lightness change that passes
    - mix (value, other, ratio, space) - blend two colours in sRGB or Lab
    - harmony (value, kind) - complementary, analogous, triadic or split_complementary by hue rotation
    - nearest (value, palette) - snap to the closest colour of a palette by Lab ΔE, with the runner-up
    - simulate (value, kind, image) - the colour under deuteranopia, protanopia or tritanopia
    - grayscale (value, method, ramp, image) - the grey under rec709, rec601, lab, average or hsl, with an optional ramp
    """
    return color_mod.color(mode, **_clean(dict(value=value, spaces=spaces, other=other, ratio=ratio, space=space, kind=kind, palette=palette, size=size, method=method, ramp=ramp, image=image, level=level, decimals=decimals)))


#: Name and function, in the order the tools are published. Registration is a loop over
#: this rather than a decorator per function so that a server can be built more than once -
#: one per process for stdio, one per app for HTTP, with or without the network tools (#100).
CORE_TOOLS: tuple[tuple[str, Any], ...] = (
    ("math", math),
    ("datetime", datetime),
    ("scale", scale),
    ("convert", convert),
    ("numbers", numbers),
    ("finance", finance),
    ("text", text),
    ("collections", collections),
    ("validate", validate),
    ("random", random),
    ("geo_offline", geo),  # the function is `geo`; `geo_offline` is the module it wraps
    ("encode", encode),
    ("color", color),
)


def _describe_parameters(server: ContractMCPServer) -> int:
    """Copy the reference's parameter docs onto the published JSON schema of ``server``.

    A client that defers tool schemas shows a preview when deciding whether to load the full
    definition, and ours read as `A?: any, B?: any, angle?: any...` - thirty untyped optionals
    with nothing to choose between them, next to rival tools showing real descriptions (#64).
    #71 gave them types; this gives them meanings.

    The text comes from `toolref`, which already has to describe every parameter for the
    documentation site, so there is one source rather than two that drift. A parameter
    documented per mode gets each mode's wording, because the schema is flat across modes and
    a caller reading it cannot see which mode a sentence belongs to.
    """
    from . import toolref

    described = 0
    for doc in (*toolref.CATALOGUE, *toolref.EXTERNAL_CATALOGUE):
        tool = server._tool_manager._tools.get(doc.name)
        if tool is None:  # a tool this build does not ship
            continue
        properties = (tool.parameters or {}).get("properties", {})
        per_name: dict[str, list[str]] = {}
        # a tool without modes (fx_rate, url_check) documents its parameters on the tool itself
        documented = [(mode.name, mode.params) for mode in doc.modes] or [(None, doc.params)]
        for mode_name, params in documented:
            for param in params:
                if param.name in properties and param.doc:
                    per_name.setdefault(param.name, [])
                    line = f"{mode_name}: {param.doc}" if len(doc.modes) > 1 else param.doc
                    if line not in per_name[param.name]:
                        per_name[param.name].append(line)
        for name, lines in per_name.items():
            properties[name]["description"] = " ".join(lines) if len(lines) > 1 else lines[0]
            described += 1
        if "mode" in properties:
            properties["mode"]["description"] = "What this call does: " + " | ".join(m.name for m in doc.modes)
            described += 1
    return described


def build_server(*, network: bool = True) -> ContractMCPServer:
    """One leftbrain server: the thirteen core tools, and the four network tools unless told not to.

    ``network=False`` is the offline build - ``LEFTBRAIN_SERVE_EXTERNAL=0`` or ``--no-network``.
    It leaves the four out of ``tools/list`` altogether rather than serving them from somewhere
    else: there is no somewhere else any more (#100). Per-key control over the same four is the
    scope grid, which marks them (#103).
    """
    srv = ContractMCPServer(
        "leftbrain",
        title="leftbrain",
        instructions=INSTRUCTIONS + ("\n" + NETWORK_NOTE if network else ""),
        version=__version__,
        website_url="https://leftbrain.idlesync.in",
    )
    for name, fn in CORE_TOOLS:
        srv.tool(name=name)(fn)
    if network:
        from .external.mcp_server import register as register_network_tools

        register_network_tools(srv)
    try:  # descriptions are worth having, never worth failing to start over
        _describe_parameters(srv)
    except Exception as e:  # noqa: BLE001 - a tool with no descriptions still works
        import logging

        logging.getLogger("leftbrain").warning("parameter descriptions unavailable: %s", e)
    return srv


def network_wanted() -> bool:
    """``LEFTBRAIN_SERVE_EXTERNAL`` - the one switch, read the same way by stdio and HTTP."""
    return os.environ.get("LEFTBRAIN_SERVE_EXTERNAL", "1") != "0"


#: The server as a module-level object, for anything that introspects it (the docs build, the
#: schema tests). It carries every tool; ``build_server`` is what a process should run.
server = build_server()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="leftbrain", description="leftbrain MCP server")
    ap.add_argument("--transport", choices=["stdio", "streamable-http", "sse"], default="stdio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-network", action="store_true", help="leave out weather, fx_rate, geo and url_check (or set LEFTBRAIN_SERVE_EXTERNAL=0)")
    ap.add_argument("--version", action="version", version=f"leftbrain {__version__}")
    args = ap.parse_args(argv)
    srv = build_server(network=network_wanted() and not args.no_network)
    if args.transport == "stdio":
        srv.run(transport="stdio")
    else:
        srv.settings.host = args.host
        srv.settings.port = args.port
        srv.run(transport=args.transport)


if __name__ == "__main__":
    main()
