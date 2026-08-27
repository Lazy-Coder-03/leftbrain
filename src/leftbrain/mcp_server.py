"""leftbrain MCP server - exposes the core tools over stdio (or HTTP).

Run: ``leftbrain`` (after ``pip install leftbrain[mcp]``) or ``python -m leftbrain.mcp_server``.

Tool descriptions are written to say *when* to call the tool, because the
usual failure is the model not calling it, not the tool being wrong.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

try:
    from .mcp_contract import ContractMCPServer
except ImportError:  # pragma: no cover
    print("leftbrain MCP server needs the 'mcp' package: pip install 'leftbrain[mcp]'", file=sys.stderr)
    raise

from . import __version__
from .core import (
    collections_,
    datetimex,
    geo_offline,
    holidays_,
    mathx,
    random_,
)
from .core import (
    color as color_mod,
)
from .core import (
    convert as convert_mod,
)
from .core import (
    encode as encode_mod,
)
from .core import (
    finance as finance_mod,
)
from .core import (
    numbers as numbers_mod,
)
from .core import (
    scale as scale_mod,
)
from .core import (
    text as text_mod,
)
from .core import (
    validate as validate_mod,
)
from .scopes import enforce

INSTRUCTIONS = """leftbrain provides exact, deterministic answers for things language models get wrong.
Call these tools BEFORE stating any number, date, conversion, count, ordering, or validation result -
even when the answer seems obvious. Every response has the shape
{ok, result, assumptions[], warnings[]} or {ok:false, error, message, needs?}.
When ok is false and 'needs' is present, the input was ambiguous: pick one of needs.options and call again.
Read 'assumptions' - they say how ambiguous input was interpreted."""

server = ContractMCPServer(
    "leftbrain",
    title="leftbrain",
    instructions=INSTRUCTIONS,
    version=__version__,
    website_url="https://github.com/Lazy-Coder-03/leftbrain",
)


def _clean(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


@server.tool(name="math")
@enforce("math")
def math(
    mode: str = "eval",
    expr: str | None = None,
    angle: str | None = None,
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
    value: Any | None = None,
    predict: float | None = None,
    range: list[float] | None = None,
    tolerance: float | None = None,
    significant: int | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Use for ANY arithmetic or mathematics before stating a number - percentages, fractions,
    powers, roots, trigonometry, complex numbers, algebra, calculus, matrices, statistics.

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
    params = _clean(dict(expr=expr, angle=angle, precision=precision, var=var, vars=vars, equations=equations, domain=domain, order=order, at=at, lower=lower, upper=upper, point=point, form=form, side=side, equation=equation, func=func, ics=ics, op=op, A=A, B=B, b=b, n=n, data=data, y=y, weights=weights, percentile=percentile, value=value, predict=predict, range=range, tolerance=tolerance, significant=significant, timeout=timeout))
    return mathx.math(mode, **params)


@server.tool(name="datetime")
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


@server.tool(name="scale")
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


@server.tool(name="convert")
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


@server.tool(name="holidays")
@enforce("holidays")
def holidays(
    mode: str = "list",
    region: str | None = None,
    year: int | None = None,
    years: list[int] | None = None,
    month: int | None = None,
    subdiv: str | None = None,
    date: str | None = None,
    n: int | None = None,
    categories: list[str] | None = None,
    locale: str | None = None,
) -> dict[str, Any]:
    """Use for public holidays of any country/state (region="IN", subdiv="WB").
    mode: list (year/years, month) | check (date) | next (date, n) | countries | subdivisions.
    The model's holiday knowledge is stale; this dataset is current.
    """
    return holidays_.holidays(mode, **_clean(dict(region=region, year=year, years=years, month=month, subdiv=subdiv, date=date, n=n, categories=categories, locale=locale)))


@server.tool(name="numbers")
@enforce("numbers")
def numbers(
    mode: str = "compare",
    values: list[Any] | None = None,
    a: Any | None = None,
    b: Any | None = None,
    value: Any | None = None,
    decimals: int | None = None,
    significant: int | None = None,
    nearest: float | None = None,
    rounding: str | None = None,
    locale: str | None = None,
    style: str | None = None,
    currency: str | None = None,
    accounting: bool | None = None,
    total: Any | None = None,
    parts: int | None = None,
    weights: list[Any] | dict[str, Any] | None = None,
    percentages: list[Any] | None = None,
    labels: list[str] | None = None,
    method: str | None = None,
    kind: str | None = None,
    start: Any | None = None,
    step: Any | None = None,
    ratio: Any | None = None,
    end: Any | None = None,
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
    return numbers_mod.numbers(mode, **_clean(dict(values=values, a=a, b=b, value=value, decimals=decimals, significant=significant, nearest=nearest, rounding=rounding, locale=locale, style=style, currency=currency, accounting=accounting, total=total, parts=parts, weights=weights, percentages=percentages, labels=labels, method=method, kind=kind, start=start, step=step, ratio=ratio, end=end, n=n, system=system, suffix_only=suffix_only)))


@server.tool(name="finance")
@enforce("finance")
def finance(
    mode: str = "emi",
    principal: Any | None = None,
    rate: Any | None = None,
    rate_period: str | None = None,
    months: Any | None = None,
    years: Any | None = None,
    schedule: bool | None = None,
    decimals: int | None = None,
    rounding: str | None = None,
    compounding: str | None = None,
    contribution: Any | None = None,
    contribution_timing: str | None = None,
    start_value: Any | None = None,
    end_value: Any | None = None,
    cashflows: list[Any] | None = None,
    amount: Any | None = None,
    amount_is: str | None = None,
    supply: str | None = None,
    op: str | None = None,
    a: Any | None = None,
    b: Any | None = None,
    percent: Any | None = None,
    value: Any | None = None,
    price: Any | None = None,
    discounts: list[Any] | None = None,
    total: Any | None = None,
    tip: Any | None = None,
    people: int | None = None,
) -> dict[str, Any]:
    """Use for any money arithmetic: loan EMIs with a full amortisation schedule, compound
    growth and SIPs, CAGR, NPV and IRR of a cash-flow series, GST inclusive/exclusive splits
    with CGST/SGST/IGST, and percentages (change vs percentage points, stacked discounts,
    bill splits). Exact decimals; the rate's period (annual|monthly) and whether an amount is
    GST-inclusive are never guessed.
    mode: emi | compound | cagr | npv_irr | gst | percent
    """
    return finance_mod.finance(mode, **_clean(dict(principal=principal, rate=rate, rate_period=rate_period, months=months, years=years, schedule=schedule, decimals=decimals, rounding=rounding, compounding=compounding, contribution=contribution, contribution_timing=contribution_timing, start_value=start_value, end_value=end_value, cashflows=cashflows, amount=amount, amount_is=amount_is, supply=supply, op=op, a=a, b=b, percent=percent, value=value, price=price, discounts=discounts, total=total, tip=tip, people=people)))


@server.tool(name="text")
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
    return text_mod.text(mode, **_clean(dict(text=text, what=what, substring=substring, case_sensitive=case_sensitive, pattern=pattern, flags=flags, replacement=replacement, count=count, a=a, b=b, granularity=granularity, items=items, key=key, order=order, natural=natural, case_insensitive=case_insensitive, unique=unique, limit=limit, context=context, normalize_whitespace=normalize_whitespace, overlapping=overlapping)))


@server.tool(name="collections")
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
    data: Any | None = None,
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
    return collections_.collections(mode, **_clean(dict(items=items, a=a, b=b, op=op, key=key, keys=keys, fields=fields, field=field, agg=agg, agg_field=agg_field, ops=ops, data=data, depth=depth, separator=separator, page=page, per_page=per_page, size=size, n=n, case_insensitive=case_insensitive, include_items=include_items, order=order, rename=rename, short_names=short_names, flatten_lists=flatten_lists, where=where, by=by, pivot_columns=pivot_columns, column=column, columns=columns, delimiter=delimiter, has_header=has_header, decimals=decimals)))


@server.tool(name="validate")
@enforce("validate")
def validate(
    mode: str = "assert",
    data: Any | None = None,
    rules: list[dict[str, Any]] | dict[str, Any] | None = None,
    schema: dict[str, Any] | None = None,
    kind: str | None = None,
    value: Any | None = None,
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
    return validate_mod.validate(mode, **_clean(dict(data=data, rules=rules, schema=schema, kind=kind, value=value, region=region, sql=sql, dialect=dialect, pattern=pattern, network=network)))


@server.tool(name="random")
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


@server.tool(name="geo_offline")
@enforce("geo_offline")
def geo(
    mode: str = "tz_for_place",
    place: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    origin: Any | None = None,
    destination: Any | None = None,
    country: str | None = None,
    zone: str | None = None,
    all: bool | None = None,
    point: Any | None = None,
) -> dict[str, Any]:
    """Use to get the IANA timezone for a city/country ("Mumbai" -> Asia/Kolkata) before any
    timezone conversion, the zone nearest to coordinates, great-circle distance and bearing
    between two places or coordinates, or a country's zones. Fully offline.
    mode: tz_for_place | tz_for_coords | distance (origin, destination) | country | zone_info
    """
    params = _clean(dict(place=place, lat=lat, lon=lon, origin=origin, destination=destination, country=country, zone=zone, all=all, point=point))
    return geo_offline.geo_offline(mode, **params)


@server.tool(name="encode")
@enforce("encode")
def encode(
    mode: str = "hash",
    text: Any | None = None,
    algo: str | None = None,
    key: str | None = None,
    expected: str | None = None,
    action: str | None = None,
    urlsafe: bool | None = None,
    token: str | None = None,
    data: Any | None = None,
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


@server.tool(name="color")
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


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="leftbrain", description="leftbrain MCP server")
    ap.add_argument("--transport", choices=["stdio", "streamable-http", "sse"], default="stdio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--version", action="version", version=f"leftbrain {__version__}")
    args = ap.parse_args(argv)
    if args.transport == "stdio":
        server.run(transport="stdio")
    else:
        server.settings.host = args.host
        server.settings.port = args.port
        server.run(transport=args.transport)


if __name__ == "__main__":
    main()
