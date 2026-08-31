# Changelog

All notable changes to leftbrain are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **The scope grid says which tools reach the internet.** `weather`, `fx_rate`, `geo` and
  `url_check` carry a `network` pill with a title that says the call's input goes to a
  third-party service, sit under a "Reaches the internet" heading, and a **No network** button
  beside All / None / Expand all unticks the four and leaves everything else as it was. The
  same partial serves the create-key form, the key's scope editor and both consent pages, so
  a key that arrives with all four ticked - every key with no scope, including one minted by
  approving a consent screen - is never decided on a page that does not mention the internet.
  The fact lives with the data: `scopes.NETWORK_TOOLS` is the external half of the catalogue,
  and a test holds it equal to the tool reference's `network` flag. (#103)

### Changed

- **Docs.** The Claude Code client page says what "Disabled for this project" means and how
  to clear it; the quickstart's paste-in prompt tells the agent the same. (#104)
- **One endpoint. `/external/mcp` is retired; the network tools are on `/mcp`.** Adding
  leftbrain was two `mcp add` commands and, since OAuth, two sign-ins, for what every other
  layer - one key, one quota, one dashboard, one scope grid, one `/docs/tools` - already treated
  as one product. `weather`, `fx_rate`, `geo` and `url_check` are now published by the same
  server as the fourteen core tools, and the stdio `leftbrain` command carries them too.
  `LEFTBRAIN_SERVE_EXTERNAL=0` (or `--no-network`) leaves the four out of a process; per-key
  control over the same four is the scope grid, which marks them (#103). A request to
  `/external/mcp` answers `410` with `moved_to: "/mcp"` and a message saying what happened,
  with no key required, so a client still configured with it is told rather than shown a bare
  404. The root document lists one endpoint and a `network_tools` flag. `leftbrain-external`
  remains for a process that should carry only the four. **This flips the default from
  opt-in to opt-out for network access**; that was decided deliberately (#100).
- **Every parameter of `fx_rate` and `url_check` now carries its description** on the wire.
  The pass that copies the reference's parameter docs onto the published schema only handled
  tools with modes; the two mode-less tools were never on the described server before. (#100)

### Fixed

- **OAuth on `/external/mcp`.** The 401 challenge named `/mcp`'s protected-resource document
  whichever endpoint was asked for, and there was no document for the other mounts at all. A
  client that checks the document's `resource` against the URL it is connecting to - Claude
  Code does, as RFC 9728 requires - refused `/external/mcp` with "does not match expected"
  before any tool call. Every mounted endpoint now has its own document at
  `/.well-known/oauth-protected-resource<endpoint>`, and the challenge and the 401 body's
  `how_to_authorize` point at the one for the endpoint that was requested. A protected path
  that is not an MCP endpoint (`/keys/me`) is pointed at the core document. Static-key auth
  never read the challenge and is unchanged. (#101)
- **The 401 no longer sends an agent to a closed door.** Its message said "get one at
  POST /keys/signup" whenever a key store existed; that route answers 404 unless
  `LEFTBRAIN_OPEN_SIGNUP` is on, which it is not on the hosted server. The clause now names
  signup only when it is open and says "sign in at /login to create one" otherwise. (#104)
- **A headless agent is told to register before the device grant.** The 401's
  `if_you_have_no_browser` named `/oauth/device_authorization` alone, and calling it without a
  registered client answered a bare `invalid_client`. The 401 now names `POST /register` first,
  and the device endpoint's `invalid_client` carries an `error_description` that says the
  same and points at the agent guide. (#104)
- **A key minted through the device grant reads `· device`, not the approver's OS.** The
  device flow exists so the approval can happen on a different machine from the agent, so the
  approving browser's operating system says nothing about where the agent runs - the same
  reasoning that already names a cloud client `· web`. (#104)
- **The site links to its repository again, and "feedback is off" says where else to go.**
  `feedback.py`, the README and the report page all said the repository was private and that
  the feedback endpoint was therefore the only place to report a wrong answer. It is public, and
  on the hosted server the endpoint was not configured, so there was no route at all. The
  footer now links Source and Issues; the report page links the tracker whether or not filing
  is on; `POST /feedback` when off answers `unsupported` with the tracker in `message` and a
  `tracker` field; and the reason the endpoint exists is stated correctly - an agent mid-call
  holds a key, not a GitHub login. One source of truth: `leftbrain.__repo__` (and
  `Repository` / `Issues` in `pyproject.toml`), overridden by `LEFTBRAIN_FEEDBACK_REPO` when a
  self-hoster files reports into their own fork. The Northflank guide names the two feedback
  variables. (#102)
## [0.4.1] - 2026-08-30

### Added

- **Number-theory predicates in `math`** — `is_even`, `is_odd`, `is_negative`, `is_positive`,
  `is_integer`, `is_square`, `is_perfect`, `is_prime`, `is_coprime`. They live in the
  expression namespace so they compose, and they return booleans that coerce to 1 and 0, so
  `is_prime(11) + is_prime(12) + is_prime(13)` is 2 — which is the only batching `math` has,
  and is now documented rather than left to be rediscovered. A predicate applied to a free
  symbol names the symbol instead of answering `None`. (#57, #63)
- **Bounded ranges in `numbers.sequence`** — `kind=primes start=50 end=80` gives
  53, 59, 61, 67, 71, 73, 79. Same for `squares` and `fibonacci`. `end` is what asks for a
  range, so a `start` on its own is still ignored and still says so. (#57)
- **The physical constants in `math`** — `gravitational_constant` … `fine_structure_constant`,
  each under a long name everywhere and its conventional short name (`G`, `c`, `h`, `R`) only
  in the modes whose answer is a number. `c` and `G` are the most common single-letter
  variables in algebra, so `solve(["a + b = c"])` still treats `c` as an unknown, and a `vars`
  entry always wins. Values come from pint's CODATA registry, and every use is named in
  `assumptions` with its value and unit. (#55)
- **`holidays` modes `festival`, `upcoming` and `compare`.** `check` and `next` are date-first;
  these are name-first and window-first — "when is Durga Puja", "what is coming up in October",
  "which dates does West Bengal observe that Assam does not". A multi-day festival comes back
  as one thing with its days named, not as unrelated rows sharing a prefix, and common names
  resolve to whatever the dataset calls the same festival (Durga Puja is filed as `Dussehra`)
  with the substitution stated. A name it does not carry is refused with the near misses,
  because "not in this dataset" and "no such festival" are different claims. (#87, #88, #94)
- **`holidays.list` can return iCalendar or CSV** — `format="ics"` puts the calendar in a
  calendar, which is the thing people actually do with one. (#93)
- **Holiday names in other languages** — `language="hi"`, `"bn"`, `"ta"` and eight more for
  India, discoverable through mode `categories`. (#76)
- **A festival can anchor date arithmetic** — `{"festival": "Saptami", "year": 2026, "region":
  "IN", "subdiv": "WB"}` works wherever `datetime` takes a date, so "three days before Saptami"
  is one call rather than a lookup, some arithmetic and hoping both were right. (#92)
- **Somewhere to report a wrong answer.** `POST /feedback` for agents holding a key and a form
  at `/report` for signed-in people, both filing an issue on the tracker nobody outside can
  see. Off unless `LEFTBRAIN_FEEDBACK_TOKEN` and `LEFTBRAIN_FEEDBACK_REPO` are set. Anything
  key-shaped is blanked before filing, and the reporter is recorded — a key prefix or a GitHub
  login — never their email. (#53)
- **Every published parameter now says what it is for**, and `mode` lists what each tool can
  do. 0.4.0 gave them types; this gives them meanings, from the same text the documentation
  site already carries. (#64)

### Fixed

- **`17 % 5` returned 0.85.** `%` was read as a percentage wherever it appeared, so it became
  `(17/100) 5` — wrong by a factor of 20, with `% read as /100` as the only tell. Both are
  legitimate readings of `%`, so it now asks which was meant and takes
  `percent="modulus"|"percent"` as the answer, the way `angle` already works. `mod` is the
  spelling with one meaning and never asks. **This also uncovered a precedence bug in `mod`
  itself**: `2^10 mod 7` matched `10` as its left operand and answered 2^(10 mod 7) = 8
  instead of 1024 mod 7 = 2. (#97)
- **A SIP with no opening balance was refused.** "growth and SIPs" is advertised at tool level,
  but `principal` was required — so "5,000 a month for ten years", which is what a SIP
  normally is, came back as `'principal' is required`. The arithmetic was right all along.
  (#98)
- **A year the holiday source does not reach answered with an empty list.** India's tables run
  1948–2100; asking for 2200 returned nothing, which reads exactly like "no holidays that
  year". It is refused now, naming the window. Every date also carries its provenance — source,
  calendar, covered years, language and categories. (#90)
- **`holidays.check` could not say that a date was a festival.** `is_holiday` and `is_weekend`
  had to carry every question. It now answers with `is_observed`, `day_off` and
  `observances[]`, each classified in one vocabulary that means the same thing in every
  country. `is_holiday` keeps its old meaning. (#89, #95)
- **A rejected `math` function named only what failed.** `primepi` now comes back with the near
  misses and the accepted set, `factorint` returns real factors instead of a stringified dict,
  and the description no longer claims "ANY arithmetic" against a partial allowlist. (#63)

### Changed

- **`toolref` moved out of `leftbrain.web`** to `leftbrain.toolref`. It only ever needed
  `contract` and `core`, and `leftbrain.web.__init__` imports starlette — a `server` extra an
  MCP-only install does not have. This is internal; nothing about the published tools changed.

## [0.4.0] - 2026-08-30

Two batches of reported bugs, most of them the same shape: a parameter accepted and then
ignored, or a filter applied and never mentioned. Several answers that looked exact were
wrong, and this release makes each of them either right or refused.

**If you read fields rather than prose, four response shapes moved:**

- `geo_offline` — `coordinates` is now `zone_reference`, because that is what it always held.
  `tz_for_coords` gained a `coordinates` echoing the point asked about, and a
  `nearest_reference`.
- `holidays.countries` — 250 entries of `{code, name, aliases}` instead of ~500 bare strings
  mixing ISO-2, ISO-3 and the dataset's own abbreviations.
- `holidays.locale` — renamed `date_locale`, which is what it does. `locale` still works.
- `numbers.allocate` — `exact_unrounded` is a fraction (`1000/7`) where the decimal repeats.

**And three behaviours changed on purpose:**

- The daily quota now counts tool calls, not HTTP requests. A handshake and a refused call
  cost nothing; a `tools/call` that did work costs exactly one.
- `collections.sort_by` honours `order` when the key is given as `keys`. Anything relying on
  that silently sorting ascending will now sort descending, correctly.
- `math` refuses trigonometry without an `angle` even where the expression folds to a
  constant, and `datetime.recurrence` refuses "every 2nd tuesday" rather than picking one of
  its two readings.

### Added

- **OAuth 2.1 for `/mcp`**, so ChatGPT plugins and Claude's web connectors can connect at all —
  neither will send a static key, and ChatGPT's dialog has no field for one. Discovery
  (RFC 8414, RFC 9728), Client ID Metadata Documents, dynamic client registration (RFC 7591),
  PKCE `S256`, and refresh-token rotation. Needs `LEFTBRAIN_SECRET` and `LEFTBRAIN_BASE_URL`;
  without both, nothing is mounted. **`lblz_` keys are unaffected** — same responses, same quota
  headers, same `tools/list` filtering.
- **A consent screen at `/oauth/consent`**, which is not decoration: leftbrain uses a static
  GitHub client id against a third party that sets a consent cookie, and now allows dynamic
  registration — every precondition of the confused-deputy attack the MCP guidance describes.
  Consent is recorded per client and checked first, and no signed state is set until a human has
  approved.
- **The device grant (RFC 8628)** at `/oauth/device_authorization` and `/device`, for an agent on
  a machine with no browser: it shows its user a short code instead of asking them to paste a key
  into the conversation. No shipping MCP client drives this yet, so it is for agents calling the
  endpoints themselves; it will not make Claude Code connect.
- **A connector's key is an ordinary key.** Approving creates one named after the app and where it
  runs — `Claude Code · Windows`, `ChatGPT · web` — visible on the dashboard, revealable,
  re-scopable and revocable, counting against the same cap. Reconnecting the same app reuses its
  key rather than consuming a second slot.
- **Per-tool call counts in the scope editor**, so narrowing a key is a decision rather than a
  guess: a tool sitting at zero calls is one to untick.
- **`POST /keys/me/scope`**, letting a caller propose narrowing its own key. It returns `202` and
  a URL for the owner to approve; nothing changes until they do. Widening is refused outright.
- **`/docs/agents/auth`**, an authentication guide written for a model rather than a person, and
  linked from the 401 body and from RFC 9728's `resource_documentation`.

### Added

- **`holidays` mode `categories`**, mirroring `subdivisions`: the category values a country
  actually accepts. There is no single enum to publish — `optional` is valid for India and
  rejected outright for the United States — so the legal set has to be asked for per country
  rather than guessed at. (#75)

### Fixed

- **Degrees were discarded the moment a trig argument contained `pi`.** `sin(pi)` with
  `angle="deg"` returned `0` — the radian answer, as an exact integer, with nothing in
  `assumptions` to say the parameter had been dropped. SymPy folds `sin(pi)` to `0` while the
  expression is still being parsed, and the degree conversion ran afterwards, on a tree with no
  `sin` left in it to convert; a plain numeric argument survives parsing, which is why `sin(30)`
  was right the whole time. Degrees are now applied as the expression is built, so the fold
  happens on the already-converted argument: `sin(pi)` is `sin(π°) ≈ 0.0548`. `cos(pi)`,
  `sin(pi/2)`, `tan(pi/4)`, `cos(2·pi)` and `asin(1)` were all wrong the same way. (#66)
- **The mandatory `angle` refusal was skipped for exactly those expressions.** `math` refuses
  trigonometry without an `angle` rather than guessing between two readings that differ by a
  factor of 57 — but the check looked for trig in the *parsed* tree, so `sin(pi)` with no `angle`
  at all answered `0` instead of asking. Trigonometry is now detected in the source, which the
  parse cannot erase. (#69)
- **`text.count` ignored a `substring` it was given and answered a different question.** "how
  many r in blueberry" reaches the tool as `count(text="blueberry", substring="r")`; `substring`
  is in that mode's own accepted-parameter list, was supplied, and was dropped without a word —
  the reply was a dozen unrelated summary counts, and whichever one a model then picked was the
  wrong answer. A needle with no `what` now counts that needle and says so; a needle given
  alongside a `what` that cannot read it is reported rather than dropped. The `what` refusal also
  omitted `substring` — the spelling that actually worked — from the values it listed. (#65)
- **`numbers.sequence` ignored `start` in silence.** `end`, `step` and `ratio` were already
  reported when the kind did not read them; `start` was left off that list, so asking for the
  primes from 50 returned the primes from 2 with nothing said. (#56)
- **The daily quota billed HTTP requests, not tool calls — twice per call, and for calls it
  refused.** Metering ran in the auth middleware, before the tool and once per request, so a
  hosted connector that re-`initialize`s before each `tools/call` spent two units on every one, a
  connect spent four before any work, and a call refused for bad input had already been charged.
  Quota and rate limiting are now separate concerns: the per-minute limit still sees every
  request, because it is abuse protection, while a unit of the daily quota is spent once, after
  the tool has run, and only when it did work. `X-RateLimit-Remaining-Today` and
  `meta.quota.remaining_today` are the same number by construction, and both now count the call
  they ride on: an MCP `POST` holds its response start until there is a body to send with it,
  because a streamed reply would otherwise write its headers before the tool had run. (#62)
- **`simplify` removed a point from the domain without saying so.** `(x^2-1)/(x-1)` came back as
  `x + 1` with an empty `assumptions` list — but the input is undefined at `x = 1` and `x + 1` is
  2 there, so the two are not the same function. SymPy is behaving as documented; the silence was
  ours. Any answer that cancelled a factor now carries `restrictions` (`["x != 1"]`) and says in
  prose which points it gained. It covers every mode that can cancel, not just `simplify` —
  `eval` and `factor` do it too — and the denominators are read off an *unevaluated* parse,
  because `x*(x-2)/(x-2)` is already `x` by the time an evaluated tree exists. `expand`, which
  keeps the denominator, says nothing. (#68)
- **`math.convert_form` refused `value`, which the tool's own signature advertises.** The mode
  re-renders a quantity, so `value` is the word a caller reaches for, and the flat schema lists it
  because `stats` takes one; it is an alias for `expr` here, and giving both different values is
  refused rather than silently picking one. (#67)
- **Every string argument was uncallable on five parameters, and latent on 27 more.** A
  parameter annotated `Any` renders as `anyOf: [{}, {"type": "null"}]` — an empty schema with no
  type in it. An MCP client serialises its arguments against that schema, so with nothing to
  serialise against it emitted `{"text": abc}` unquoted: the call failed *inside the client*,
  never reached the server, and none of the contract's machinery applied — no envelope, no
  `needs`, and a retry produced the same malformed call. It made `encode`'s headline purpose
  unreachable for any text containing a letter, and digit-only values happening to work is what
  made it look intermittent. All 32 now carry concrete unions; a test sweeps for the shape so it
  cannot come back, and another pins that `"0.1"` still arrives as the exact decimal string
  rather than a float. (#71)
- **`collections.sort_by` ignored `order` whenever the key was given as `keys`** — which is the
  only way to sort on more than one field. "The top 3 departments by spend" took the first three
  rows of a silently *ascending* sort and returned the three lowest, in a well-formed response
  with nothing to contradict it. Anyone spot-checking with the singular `key` would have
  concluded `order` was fine. Mixed directions are now expressible too, as `{field, order}` or a
  `-field` prefix, and an order that cannot be applied is refused rather than dropped. (#84)
- **`holidays` applied a `categories: ["public"]` filter and never mentioned it.** `check`
  returned `is_holiday: false` for 2026-10-18 in West Bengal — the middle of Durga Puja — so an
  agent would tell its user that October 18 is not a holiday there. Omitting `subdiv` already
  produced an assumption; this was the one narrowing that stayed hidden. The default is now
  named on every call that used it, and `check` reports a date the dataset knows under another
  category as a near miss rather than a bare `false`. (#72, and the visible half of #80)
- **`geo_offline` reported the zone's reference city as `coordinates`**, whatever place was
  asked about: `tz_for_place("Mumbai")` and `("Chennai")` both returned Kolkata's, 1,650 and
  1,300 km out. `tz_for_coords` was visibly self-contradictory — those coordinates beside
  `distance_to_reference_km: 0.5`. The field is now `zone_reference`, a coordinate lookup echoes
  the point it was asked about, and `nearest_reference` shows which reference actually chose the
  zone. (#73)
- **`datetime.convert_tz` reported a wall clock that never existed.** Given a time inside a DST
  spring-forward gap it warned correctly and named the reading it used — then printed the other
  one in `source`, with `is_dst: false`. The instant was right throughout; only the
  machine-readable fields disagreed with the prose beside them, which is the wrong way round.
  (#85)
- **`datetime.recurrence` read "every 2nd tuesday" as monthly, silently.** "Every other tuesday"
  means the same thing to most speakers and produced a fortnightly schedule; `assumptions` was
  empty either way, so an agent scheduling a fortnightly standup got a monthly meeting series.
  It is refused now, the way `03/04/2025` already is, offering both readings as rules the tool
  accepts. (#81)
- **`datetime.free_slots` refused `days: "mon-fri"`** — the natural way to write a working week,
  and the case the feature exists for, with no format documented to guess from. Ranges, comma
  and space lists, and `weekdays`/`weekends` groups are accepted now. (#82)
- **`numbers.allocate` spent 8 KB of context on a 7-way split**, emitting ~1,100 digits of
  `142.857142…` per share while `truncated` reported false, because nothing had been cut. The
  exact value is `1000/7`. 9,516 bytes down to 1,151. (#74)
- **A defaulted `mode` was invisible, and its refusal spoke in the wrong vocabulary.** A call
  naming no mode gets the schema's default; `meta.mode` was absent, and a `validate` call the
  caller believed said `mode: "email"` was told it needed `rules`, which belongs to `assert`.
  The mode that ran is now always reported, and a refusal about that mode's own parameters says
  it was defaulted — but only such refusals, since a `forbidden` never ran the mode at all.
  (#79)
- **`holidays.locale` never localised anything.** It is the *date*-parsing locale, which is why
  `hi` was refused and a country code demanded. It is `date_locale` now; `locale` still works
  and says what it actually does. (#76)
- **An alias could resolve to a different country in silence.** `region: "BAH"` returns
  Bahrain — the upstream package's own abbreviation, not the IOC's, which gives BAH to the
  Bahamas — and the response echoed `region: "BH"`, which reads as normalisation rather than as
  a different country. Every alias now says what it resolved to, the two known collisions name
  the other reading and the code that reaches it, and `countries` returns 250 named entries
  instead of ~500 undifferentiated strings mixing three code systems. (#83)
- **`collections` called the same thing by different names in different modes** — records were
  `items` or `data`, the grouping key `key` or `by` — so learning one mode did not transfer to
  the next, and `group_by` cost three round-trips to get right. Both names are accepted wherever
  records are taken. (#78)
- **A zone listing an unexpected country said nothing about why.** `Asia/Tokyo` includes
  Australia, which is correct and upstream — Eyre Bird Observatory keeps UTC+9 — but the comment
  explaining it was being overwritten with an empty string, so correct data read as a bad
  reverse lookup. (#77)
- **A throttled key answered `500`, not `429`.** Both rate-limit paths passed the key record into
  `Verdict`'s `message` field positionally, and it is not serialisable — so every caller who hit
  their rate limit or exhausted their daily quota got a server error with no `Retry-After` and no
  explanation. `Verdict`'s optional fields are keyword-only now, so the shape cannot recur.
- **A key scoped to tools this build does not ship said the wrong thing.** It correctly allowed
  nothing and listed nothing, but refused calls with `allowed: files` — naming a tool nobody on
  that server can call. It now says the tools are not provided and points at the dashboard, and
  the key carries a warning on its row.

### Changed

- **The active-key cap rises from 3 to 5** (`LEFTBRAIN_MAX_KEYS_PER_EMAIL`), now that connecting
  an app consumes a slot. Three was sized for keys pasted into config files.

- **`numbers.compare`'s `percent_change_a_to_b` divides by `|a|`**, matching
  `finance.percent`: from -100 to -50 is +50%, not -50%. It is omitted, with an assumption
  saying why, when `a` is zero.
- **`color.contrast`'s `ratio` is rounded down**, and `ratio_exact` carries the unrounded
  figure. 2.99979 was displayed as `3.0` beside "does not meet 3:1".
- **Each mode of `convert` and `holidays` declares only the parameters it reads.** `decimals`
  on `convert.units`, `delta` on `convert.currency`, `n` on `holidays.check` and `date` on
  `holidays.list` were accepted and ignored; they are refused now.
  `convert.temperature` converts temperatures rather than whatever units it is handed.
- **`math.solve` filters solutions by `domain`.** `x^40 = 2` over the reals returned 26
  solutions, 24 of them complex. An identity reports `identity: true` and an inconsistent
  system reports no solutions, both `ok`.
- **`numbers.allocate` is capped at 2,000 parts**, which is what a 256 KB response can carry;
  10,000 passed the pre-check and then failed on size.
- **`numbers.semver` refuses a version given as a number**, which cannot tell `1.10` from
  `1.1`, and refuses a leading zero in a numeric part.
- **`encode.base64` decodes strictly**: data after the padding, padding in the middle and
  characters outside the alphabet are refused rather than quietly ignored.
- **`convert.units` refuses `S`, `H` and `T`** as ambiguous - siemens or seconds, henry or
  hours, tesla or tonnes.

### Fixed

- **`math` read a decimal literal as a binary float.** `exact` on `0.1 + 0.2 - 0.3` returned
  `277555756156289/5000000000000000000000000000000`: the IEEE-754 rounding error, faithfully
  rationalised. A decimal literal is now the rational it prints as, in every mode, so the sum
  is `0`, `0.1 * 3` is exactly `3/10` and `2^0.5` is `sqrt(2)`.
- **`math` read grouped digits as a tuple.** `17.5% of 8,45,000 + 12% of 1,20,000` came back as
  five numbers with no warning. Grouped digits are read as one number, with a line in
  `assumptions`, in the same two shapes `numbers.parse` accepts: Western threes (`1,234,567`)
  or Indian twos-then-a-three (`12,34,567`). A run that is neither — `3,14`, `1,2345`,
  `123,45,678` — is refused with those two spellings named, rather than evaluated as a tuple or
  stripped of its commas. Inside a call's brackets a comma still separates arguments.
- **A rational power ran to the deadline.** `(1+1/1000000)^1000000` is about 2.718 and also a
  seven-million-digit integer over another; the size estimate measured the value and let it
  through, and the hosted server built the rational until the 15 s kill. The estimate now
  measures the exact form too. `eval` takes the tree unevaluated and returns the decimal the
  caller asked for, with `warnings` saying why there is no `exact`; `exact` refuses as
  `too_large` in a millisecond and points at `eval`.
- **`ode` rejected the syntax its own docstring documents.** `y'' + 4y' + 4y = 0` failed the
  token guard with `disallowed token: "'"`, because the prime rewrite wanted a word boundary
  before `y` and there is none after a coefficient. The documented form now works, as does
  `4y` for `4*y(x)`.
- **`solve` reported "no real solutions" as a numeric failure.** `x^2 + 1 = 0` with
  `domain=real` answered `unsupported` — "no closed form exists and its roots could not be found
  numerically" — with `equations: ["False"]` leaking out. Neither half was true. It is now `ok`
  with an empty list, and `assumptions` says `no real solutions; 2 complex roots exist
  (domain='complex' to see them)`. The numeric fallback also respects the domain: the four
  complex roots of `x^4 - 2x^2 + 3` are no longer offered as real solutions.
- **`expand` never expanded trigonometry, and said nothing.** `sin(2*x)` came back untouched
  while `exp(x + y)` split. The multiple-angle and sum identities are applied now; `log(x*y)`
  is left alone, correctly, and `assumptions` says why. `expand`, `factor` and `simplify` all
  say so when they return the input unchanged, so "already in simplest form" and "this mode
  does not do that" can be told apart.
- **`factor` returned an integer unchanged.** `factor 12` is now `2**2*3`, with the primes and
  exponents in `factors` and `prime: true` for a prime.
- **`factor` on an equation surfaced CPython's parser.** `x^2 - 5*x + 6 = 0` answered
  `invalid syntax (<string>, line 1)`. A bare `=` where an expression is expected now says to
  drop the `= 0` and points at `solve`; no parse failure carries the `(<string>, line 1)`
  artefact any more, and unbalanced brackets are called that.
- **`math` cross-checks every integral it computes.** A definite integral is checked against
  numeric quadrature and the numeric value is returned when the two disagree, with a warning
  naming the closed form that was dropped; an antiderivative is differentiated back and the
  result carries `verified`. SymPy 1.14 answers `0` for the integral of `1/(x^8+1)` over
  `[0, 1]`. A divergent integral is refused, a range where the integrand is not real is
  flagged, and a step function is integrated numerically rather than erroring.
- **`math` limits that do not exist say so.** `1/x` at 0 and `sin(1/x)` at 0 returned
  `exists: true` beside `zoo` and `AccumBounds`. Both return `exists: false` now, with `left`
  and `right`, or `oscillates_between`.
- **`math.matrix`** refuses a right-hand side whose length does not match the matrix, reports
  an inconsistent system as `consistent: false` rather than "singular; general solution
  returned", names the shape when an operation needs a square matrix, names the row when the
  rows are ragged, reads a nested list written as a string, and estimates the digits of
  `A**n` before computing it.
- **`math.plot_points`** drops a sample that is a pole showing through (`tan(x)` beside a
  half-turn), falls back to SymPy where lambdify's `math` module has no such function
  (`zeta`), and refuses an expression that still has a second free symbol in it.
- **`math.convert_form`** honours `tolerance` on a value that is already exact, reports the
  argument of 0 as undefined rather than NaN, keeps the exact form of a value below 1e-308,
  and writes scientific notation through `Decimal` so `10^400` is not `inf`.
- **`math.stats`** refuses a z-score of one point and a covariance of one pair, returns a
  horizontal regression (slope 0, `r_squared: null`) instead of refusing it as "correlation
  undefined", and names the index and value of a data point that is not a number.
- **`numbers` computes at 1,200 significant digits.** Decimal's default context is 28, so
  `sequence` terms, `to_words` values and parsed numbers past 28 digits were rounded in
  silence and any `quantize` past them raised. A number that would round is refused as
  `too_large`.
- **`numbers.parse` resolves `.` and `,` as a whole.** `1.234,56` is 1234.56, `1,234.56` is
  1234.56 and `12,34,567.89` is 1234567.89; commas that group nothing (`1,2345`,
  `10,000,00`) are refused rather than stripped. Thin and non-breaking spaces group digits, a
  sign may come before a currency symbol, and a value past the float range carries the note
  that says so.
- **`finance` reads a rate written with its sign.** `rate="12%"` was 0.12%. `rate`, `tip`,
  `percent` and `discounts` take a percentage, and how any amount was read (`10L`, `1,10`)
  reaches `assumptions`.
- **`finance.npv_irr` finds every internal rate of return.** `[-100, 230, -132]` has one at
  10% and another at 20%, and bisection from the ends found neither. All roots are listed in
  `irrs_percent` when there is more than one, and the NPV is returned even when no IRR exists.
- **`finance.emi` stops the schedule when the balance reaches zero.** A rounded-up instalment
  clears a long loan early; the schedule ran on into negative balances and a negative final
  payment. `months_paid` says how many instalments there were.
- **`scale`** inverts an explicit `factor` in `inverse` mode, and scales to zero in `linear`
  mode instead of refusing with a message written for `inverse`.
- **`datetime` measures elapsed time in UTC.** `diff`, `duration_sum` and `overlap` across a
  daylight-saving change counted wall-clock hours, so a day over the spring-forward change
  was 24. A wall time in the gap is moved forward and reported by `parse`, `add`,
  `recurrence` and `cron_next`; an explicit offset settles a fold without the warning.
- **`datetime` reads what it is given.** A bare number is a Unix timestamp only with 9-13
  digits, so `2026` is refused rather than answered as 1970; a written date fills missing
  parts with the first of the period and reports each fill; `1/2` is as ambiguous as
  `1/2/2026`; a trailing zone abbreviation is resolved rather than dropped; `EST`, `MST`,
  `CET` and the rest resolve as abbreviations rather than as tzdata's fixed-offset zones;
  cron reads `7` as Sunday inside a range; `free_slots` reads `9am` as a time of day.
- **`holidays`** accepts country names and alpha-3 codes and returns the ISO-2 code, reads a
  month name, warns on `check` when the calendar has no data for that year, and declares only
  the parameters each mode reads.
- **`geo_offline`** resolves coordinates inside a one-zone country to that zone (New Delhi
  answered `Asia/Kathmandu`), refuses `Washington`, `Portland`, `Birmingham`, `Georgia` and
  `LA` as ambiguous, reads a zone name in any case and a backward-compatibility link such as
  `Asia/Calcutta`, matches country names regardless of accents, and no longer attaches a
  neighbouring territory's tzdata comment to a zone.
- **`text`'s regex guard covers four more shapes.** Bounded nesting (`(a{1,60}){1,60}`), an
  alternation whose branches overlap by character class, a nullable body under a fixed
  repeat, and a nested quantifier behind an `x`-mode comment all ran to a hang. With the text
  length known the guard also budgets the polynomial shapes (`a*a*a*b` over 100,000
  characters) and the slow-growing ones. `validate.assert`'s `matches` runs through it too.
- **`text` positions are in the caller's string.** A case-insensitive `count` or `find`
  reported offsets into the lower-cased text, which is a different length for a dotted I.
- **`collections`** matches a CSV number against the same JSON number, leaves rows without
  the key out of `set_ops` and `dedupe` rather than treating them as equal, keeps leading
  zeros as identifiers, reads `12,34` the same way in every mode, refuses a numeric
  comparison on a text column, and refuses a deep or huge structure up front instead of
  raising `RecursionError`.
- **`validate`** reports `EXEC`, a `DELETE` inside a CTE, `SELECT ... INTO` and `COPY` as
  writes; applies the PAN rule to a GSTIN's embedded PAN; rejects a card number of repeated
  digits; uses the same email check as mode `email` for `format: email`; reads a phone
  extension and rejects an unassigned country code; and refuses a URL with a control
  character, an out-of-range port or an empty `tel:`.
- **`encode`** compares a Base64 digest exactly (a case-mangled digest verified), refuses a
  `key_base64` key that is not Base64 rather than signing with an empty key, decodes Base64
  and hex strictly and names what is wrong, reads a JWT expiry given in milliseconds, and
  rejects `NaN` and `Infinity` when parsing JSON.
- **`convert`** rounds currency half-up as it says it does; keeps the case of a unit, so
  `Mb/s`, `mPa`, `Mm` and `mWh` mean what they say; reads `1,5` the way `numbers` does;
  reports `factor_exact` only when the factor is exactly representable; bounds `precision` to
  1-15; and tries the unit registry before reading three capitals as a currency.
- **`random`** refuses negative weights, a weighted unique draw larger than the list, a float
  range wider than a double, and a token request larger than the response can carry.
- **`color`** refuses `cmyk(0, 1, 1, 0)` as ambiguous - it is red as fractions and near-white
  as percentages - and rounds the contrast ratio down, so a displayed figure never reaches a
  WCAG threshold the colours do not meet.
- **Options are read as written across every tool.** `"false"` is false, a count must be a
  whole number, and no failure message carries a Python exception type or `invalid literal
  for int()`.

- **The loading skeleton replaces the page instead of covering it.** The outgoing page was faded
  to a quarter and the skeleton laid over the top, so both were on screen at once and the text
  showed through the bars. The content now leaves the flow while the next page is on its way and
  the skeleton stands in its place; the docs pages still replace only the article and keep the
  sidebar readable. Coming back with the browser's Back button no longer leaves a stranded row
  of skeleton lines above the restored page.

## [0.3.1] - 2026-08-27

### Added

- **Docs: connecting Claude on the web, and a ChatGPT section.** Which clients can carry a bearer
  key and which cannot, and why: a config file can set a header, a browser connector dialog
  cannot. ChatGPT gets its own section covering the Plugins rename, the developer-mode
  prerequisite and where the toggle lives per plan, and why no configuration works directly —
  OpenAI's documentation states ChatGPT cannot present custom API keys, and expects OAuth 2.1
  against the MCP authorization spec. Both blocked clients get something to do rather than a
  shrug: the desktop app for Claude, a header-injecting proxy for ChatGPT, with a Cloudflare
  Worker to copy and the trade stated — the proxy's URL becomes the credential.

### Changed

- **The per-key tool scope is a tree.** It was a three-column grid of blocks whose columns ran
  to different heights, so a tool's modes wrapped into a shape unrelated to the tool above it.
  Each tool is now one full-width row with a disclosure triangle, a `picked/total` count, and
  its modes as collapsible children under a guide line. A tool with some but not all of its
  modes shows an indeterminate checkbox; unticking the last mode turns the tool off and
  ticking one turns it back on. The header offers **All**, **None** and **Expand all**. Counts
  are rendered by the server, so the scope is readable with JavaScript off.

### Fixed

- **The loading skeleton now covers every page.** `site.js` looked for `.doc`, then `<main>`,
  then `document.body`, so only the docs pages showed it correctly: the dashboard got the
  markup with no CSS to position or dim it, and the landing, login and error pages prepended
  skeleton lines above the nav. Every template now renders inside one `#page` wrapper, which
  the script targets and the stylesheet positions and dims. The docs pages still dim only the
  article, so the sidebar stays readable.
- The site footer linked to the repository, which is private. It links to the tool reference
  instead.

## [0.3.0] - 2026-08-27

### Added

- **JSON tool reference.** `/docs/tools` and `/docs/tools/<name>` serve JSON to a client and
  HTML to a browser, by content negotiation on `Accept`. The index lists all eighteen tools
  with their modes, the version, and the contract's error codes; a tool gives each mode's
  purpose, description, parameters (type, default, required) and example arguments. Neither
  needs a key. `/` advertises the endpoint as `tools`. `tools/list` over `/mcp` is unchanged
  and remains the authenticated, scope-aware route.

- **Every response says what it cost** (#28 §6): a `meta` block with `tool`, `mode`,
  `latency_ms` (wall time in the server), `compute_ms` (time in the engine), `version`,
  `truncated` — lifted out of the result so a caller reading a list knows it is not the whole
  list — plus `request_id` and `quota` when the server has them. `X-Request-Id` and
  `X-Leftbrain-Latency-Ms` carry the first two as headers; a caller-supplied `X-Request-Id` is
  kept so one id spans both sides of a trace. `meta` never affects `ok`.
- **A 15-second compute ceiling** (#28 §1 step 3). `math`, `text`, `validate`, `collections`
  and `numbers` calls run in a worker process that is terminated when the deadline passes. A
  call that reaches it returns `timeout` with `stopped: "worker_terminated"`, the limit and
  the elapsed time; a caller-supplied `timeout` is clamped to the ceiling; a call that cannot
  get a worker within the queue deadline returns the retryable `busy`. Workers carry
  `RLIMIT_CPU` and `RLIMIT_AS`, use `forkserver` on POSIX, and are recycled every 200 calls.
  Configured with `LEFTBRAIN_COMPUTE_TIMEOUT`, `LEFTBRAIN_QUEUE_TIMEOUT`,
  `LEFTBRAIN_MAX_INFLIGHT` and friends — see the README for the full table and the ingress
  relationship. Needs `pebble`, added to the `server`, `all` and `dev` extras; without it the
  server logs that isolation is off and runs in-process. The library always runs in-process.
- **`retryable` on every failure, and four codes that say what was hit** (#28 §1, §4): the
  envelope's failure half is now `{ok: false, error, message, details?, retryable, hint?}`.
  `retryable` is `false` for everything except `busy` and `internal` — a client that reads only
  `ok: false` retries, and an identical retry of a call that hit a limit multiplies the load that
  caused it. New codes alongside `invalid_input` / `ambiguous` / `unsupported` / `timeout` /
  `forbidden` / `internal`: `too_large` (a pre-check refused it before any work started),
  `resource_exhausted` (a memory or CPU limit rather than the clock) and `busy` (saturated;
  nothing was computed). `contract.CODES` is the one list, and `TooLarge`, `ResourceExhausted`
  and `Busy` are raisable from a tool. Only `busy` is retryable by default: `internal` is the
  catch-all for an exception no mode anticipated, and most of those are a deterministic
  consequence of the input, so a site that knows its own failure was transient passes
  `retryable=True` at the raise instead.

- **Size caps: an input whose answer would be enormous is refused before it is computed**
  (#28 §2e/§2f/§2g). Each check costs microseconds and returns `too_large`, naming the limit
  and the parameter to change:
  - `math`: the expression is walked as an AST and the answer's size estimated in log10 space
    before anything is evaluated — SymPy computes `Integer ** Integer` while *parsing*, so
    `9^9^9^9`, `2^(2^100)`, `2^1000000`, `factorial(factorial(20))`, `gamma(10**10)` and
    `exp(10^20)` never start. The bound is `sys.get_int_max_str_digits()` (4 300 by default),
    which is the point beyond which CPython cannot render the integer at all. `precision` is
    capped at 5 000 digits and `series` `order` at 50 terms.
  - `numbers`: `sequence` caps the largest term at 1 000 digits (`geometric` ratio 2, n 10 000
    ended at 2^10000 and returned 15 MB); `allocate` caps `parts` at 10 000 (1 000 000 returned
    116 MB).
  - `text`: `regex_replace` refuses an output over 200 000 characters; `diff` caps each side at
    10 000 lines, words or characters (difflib is quadratic — 100 000 lines never returned).
  - `collections`: `pivot` refuses more than 200 distinct pivot columns.
  - `datetime`: `business_days` caps the range at 3 660 days; `now` and `convert_tz` cap the zone
    list at 50.
  - `holidays`: `next` caps `n` at 100.
  - A global 256 KB ceiling on any successful response backs all of them up, configurable with
    `LEFTBRAIN_MAX_RESPONSE_BYTES`. A mode with a knob should refuse earlier and say which knob;
    this only catches what slipped past one.

### Fixed

- **Extreme magnitudes are rendered rather than lost or refused.** Four behaviours for one
  class of input: `numbers.parse "1e400"` was `invalid_input` (`Decimal` holds it exactly —
  the parser's suffix regex simply never let it try); the same value as a JSON *number*
  arrived as `inf` and came back as a successful `"Infinity"` with nothing said;
  `convert "1e-400"` leaked `OverflowError: int too large to convert to float`; and
  `finance.compound principal=1e300` was `internal` plus a bare `InvalidOperation`.
  Now: scientific notation parses however large, a value that arrived as infinity says its
  magnitude was already lost before the tool saw it, and `convert` computes exactly at any
  size — only `value`, which is a JSON number, saturates to `Infinity`/`0`, with the exact
  answer in the new `value_exact` and the loss named in `warnings`. An ordinary conversion
  gains neither field. An amount too big to be money is `too_large`, not a crash.

- **The small list** (#28 §3.13): sixteen findings that were each wrong or silent rather than
  dangerous. `random.sample` with more groups than items left some empty and said nothing;
  `random.float decimals=100` ignored the parameter (a binary float has ~17 significant digits,
  so it is now 0..15 and anything larger is refused); `encode.base64 urlsafe=true` decoded
  standard-alphabet text anyway; `encode.url` left a malformed `%zz` escape untouched with no
  note; `validate.url` accepted embedded credentials and punycode/Cyrillic look-alike hosts
  silently, and now warns about both; `validate.ip` rejected the scoped IPv6 form
  `fe80::1%eth0` (RFC 4007), which is now valid with the `zone` reported;
  `validate.sql_parse` counted a trailing comment as a third statement;
  `numbers.parse` rejected the Unicode minus `−5`, which is what copy-paste from a document
  produces; `collections.find_duplicates` with `case_insensitive` did not also trim, so
  `"a@x.com "` and `"A@x.com"` were different; `collections.aggregate` had no `median`
  although `math.stats` did; `datetime.recurrence` refused `every other tuesday`, the short
  form people actually write; `holidays.list` for a year the calendar has no data for looked
  identical to a year with no holidays, and years past 2075 are now marked as estimated;
  `text.sort` sorted `éclair` after `Zebra` without saying it does no locale collation; and
  `text.count` now reports `graphemes` — what a reader sees, so a ZWJ family emoji is one
  character rather than five — and warns when text carries bidi controls or zero-width
  characters, which are invisible, count towards length, and are how a filename is made to
  read backwards.
- **Unknown parameters are refused instead of silently dropped** (#28 §2a). Every tool kept the
  keys it recognised and discarded the rest, so `datetime.age dob=… ref_date=…` returned the age
  as of *today* (the parameter is `on`) and `math.plot_points start=… end=…` plotted the default
  range — an answer computed from defaults after the caller's arguments were thrown away, with
  nothing in the response to tell it from a right one. Each of the fourteen core tools now
  declares `MODE_PARAMS`, what every one of its modes reads, and a call carrying anything else is
  `invalid_input` naming the mode, listing what it accepts and suggesting the closest match.
  `tests/test_mode_params.py` derives the same map from the source and fails if the two drift.
  Retired names (`from`/`to`) now say what replaced them instead of being ignored. `overlap` also
  honours a per-interval `tz`, which it used to drop — two windows 12½ hours apart were compared
  as wall clocks and reported as overlapping by 8 hours.
- **Mutually exclusive parameters say which one won** (#28 §2b): `numbers.round significant=2
  decimals=5` returned 120 with no word about `decimals`, and `finance.emi months=12 years=5`
  gave a one-year loan to a caller who described a five-year one. Both now record the choice in
  `assumptions`.
- **Eleven confidently wrong answers** (#28 §3.3–3.13). None of these failed; each returned
  `ok: true` and a value a caller would act on. `encode.json parse` reported a valid document
  as invalid when it arrived through MCP already decoded (`text` is typed `Any`, so
  `{"a": 1}` became Python's `{'a': 1}` repr); `encode.json stringify` emitted literal
  `Infinity` and `NaN`, which no strict JSON parser accepts, and now refuses;
  `datetime.parse` turned `01/02/50` into 2050 with no note — there is now a stated pivot
  (49 and below is 20xx) recorded in `assumptions`, because for a date of birth a silent
  century is a hundred years wrong; `datetime.recurrence` with `FREQ=MINUTELY` returned four
  copies of the same date, and a sub-daily frequency now keeps its times;
  `collections.pick_fields` silently destroyed a field when two renames collided;
  `collections.find_duplicates` with a key no record has reported every row as a duplicate of
  every other; `datetime.free_slots` refused two participants in the same timezone, which is
  two colleagues in one city, and now numbers them apart; `math.eval √2` produced a symbol
  called `sqrt2` because the sign only bound inside brackets; `collections.set_ops` conflated
  `1` and `true` (Python hashes them the same), and now compares by type as well as value;
  `finance.npv_irr` reported one IRR for a cashflow with three sign changes and now reports
  `sign_changes` and warns that Descartes' rule allows several.
- **Ranges that were never checked, and approximations that should have been refusals**
  (#28 §2c/§2d). Every one of these returned `ok: true` with a confident number:
  `geo_offline` accepted `lat=91` (returning `Arctic/Longyearbyen`, and a 10 118 km distance);
  `datetime.business_days` silently swapped a reversed range and now reports `sign` and
  `direction` like `diff` does; `convert.temperature` converted −500 °C to −226.85 K, and a
  *reading* below absolute zero is now refused while a `delta` difference still may be any
  sign; `convert.currency` accepted `rate=-83`; `holidays.list` accepted `month=13`, and the
  month filter now applies to `long_weekends` as well as `holidays`; `datetime.convert_tz`
  answered a wall time the clocks skipped or repeated with no note, and now warns naming which
  it is; `geo_offline.distance "Delhi" → "Mumbai"` returned **0 km** because both were
  approximated by Asia/Kolkata's reference city, and an unknown place is now `needs:
  coordinates` — a name whose reference city *is* the place (`Kolkata`, `London`) still
  resolves; `math.eval 1/0` and `tan(pi/2)` returned SymPy's complex infinity rendered as
  `nan + nani` and are now `invalid_input` pointing at `mode: limit`; `math.solve` on a
  degree-40 polynomial reported `no solutions found` where 40 roots exist, and now falls back
  to numeric roots saying so in `assumptions`.
- **Predictable inputs are phrased instead of raising Python exceptions** (#28 §4): five modes
  reported what broke inside leftbrain rather than what the caller did wrong.
  `collections.unflatten {"a": 1, "a.b": 2}` was `TypeError: 'int' object does not support item
  assignment` and now names the key that is both a value and a prefix; `scale` with `to_qty: 0`
  was `ZeroDivisionError: Fraction(1, 0)`; `datetime.add amount=1e9 unit=years` was
  `ValueError: year must be in 1..9999` and is now a stated 4 000 000 limit; `finance.compound
  years=1000000` was `internal` plus a bare `InvalidOperation` and is now `too_large` at 10 000
  years; `convert.units value=1e400` was `OverflowError: int too large to convert to float` and
  is now a stated 1e300 magnitude limit. Each carries `details` and a `hint`.
- **SSRF in `url_check`** (#28 §5): `http://169.254.169.254/latest/meta-data/` — the cloud
  metadata service, which answers with credentials — was actually fetched, as were `localhost`
  and RFC 1918 addresses. The host is now resolved and judged before the connection is opened and
  again after every redirect (a public host can answer `302` to a private address), and only
  public `http`/`https` addresses are fetched. `file:///etc/passwd` was being rewritten to
  `https://file:///etc/passwd` and attempted; any non-http(s) scheme is now refused instead. The
  connect budget drops from 15 s to 5 s.
- **CSV formula injection in `collections.to_csv`** (#28 §5): cells such as
  `=cmd|' /C calc'!A0`, `+1-2` and `@SUM(1)` were written verbatim, so opening the file in a
  spreadsheet ran them. Cells beginning `=`, `+`, `-`, `@`, tab or CR are now prefixed with an
  apostrophe, with the count in `assumptions` and `escaped_cells`; `escape_formulas: false`
  restores the old behaviour and warns. A negative number is left alone — `-12.5` is data.
- **A regular expression can no longer freeze the process** (#28 §1): `text.regex_match` with
  `(a+)+$` over `"a"*40 + "b"` never returned, and neither did `(a|aa)+` in `regex_replace` or a
  `pattern` inside a `validate.json_schema` schema. Once stdlib `re` starts backtracking nothing
  can stop it — `sre` is a C loop that never reaches a bytecode boundary, so no timeout, signal
  or thread kill is delivered until it finishes. The three shapes that cause it (a quantified
  group that is itself unbounded, one that can match nothing, and a quantified alternation whose
  branches overlap) are now recognised and refused with `unsupported` before the pattern is
  compiled, naming the shape and how to rewrite it. `validate.regex` judges rather than runs, so
  it keeps answering `valid: true` and reports the new `backtracking_risk` field with a warning.
- **Silent truncation now says so** (#28 §2f): `text.regex_match`'s `count` is the total number
  of matches rather than the number returned — an agent reading `count` off a truncated response
  got the limit and believed it was the answer. `returned` and `truncated` are new fields.
  `datetime.recurrence` with `count=1000000` returned 100 occurrences with nothing said about it,
  because a `count` larger than `limit` silenced the warning; it now sets `truncated` and warns.
  `holidays.next` warns when the two-year search window holds fewer holidays than were asked for.
- **A recursive JSON Schema is input, not a crash** (#28 §1): `validate.json_schema` with
  `{"$ref": "#"}` or 200-deep `allOf` raised `RecursionError`, which surfaced as `internal` with
  a traceback. Schemas nesting deeper than 50 levels are refused with `too_large`, and a `$ref`
  cycle is `invalid_input` explaining that validation cannot terminate.
- **`internal` errors no longer ship a stack trace to the caller** (#28 §4): the traceback named
  server file paths in every response. It is logged server-side now; set `LEFTBRAIN_DEBUG=1` to
  get the `trace` field back in the response as well.

### Changed

- **A call that fails the input schema now answers in the contract** (#28 §4): `convert` with no
  arguments, or `collections` with `where` as a string, used to come back as an MCP transport
  error carrying a pydantic dump and an `errors.pydantic.dev` link. Both now return
  `{"ok": false, "error": "invalid_input", …}` with the offending parameters under
  `details.parameters` and `needs.missing` naming anything required that was left out — the
  rejected values themselves are the caller's data and are not echoed back.

- **Per-key tool scopes** (#27): a key can be limited to chosen tools and, per tool, chosen
  modes — on the dashboard (a **Tools** disclosure on the create form, **Edit scope** on every
  key's row) or with `leftbrain-keys create|set … --tools "math,datetime,holidays:list+check"`
  (`--all-tools` lifts it). A scoped key's `tools/list` shows only the tools it may call, and a
  `tools/call` outside the scope returns the contract error
  `{"ok": false, "error": "forbidden", "message": "this key may not call holidays mode 'next'; allowed: list, check"}`
  as a result, not a transport error. `GET /keys/me` and `leftbrain-keys list` report `tools`
  (`null` for every tool). Existing keys have no scope and behave exactly as before; the
  `keys` table gains a nullable `scope` column on first start.

- `math`: `round(...)` over an expression with `vars` failed at parse time ("Cannot convert
  expression to float") because it evaluated its argument before the variables were
  substituted. It is now a deferred function that fires once the argument is numeric, and it
  rounds half-up on the decimal value, returning an exact rational (`round(2.675, 2)` →
  `67/25` = 2.68; `round(basic * 12 / 365 * unpaid_days, 2)` with vars → `5128.77`).
- `datetime`: a unix timestamp given as a digit string (`"1787232546"`, `"1787232546000"`) was
  read as a year and refused. Any 9–13 digit value, string or number, is now an epoch in every
  mode that takes a `value` (`parse`, `convert_tz`, `add`, `diff`, …), with the existing
  "unix timestamp read as UTC" / "read as milliseconds" assumptions; the MCP `value` parameter
  accepts numbers as well as strings, so `convert_tz` can take an epoch straight from another tool.

## [0.2.0] - 2026-08-27

Two new core tools — fourteen in all — and new modes on seven of the existing ones, plus a
dashboard that lets you delete old keys and a hosted free tier of 1,000 calls per key per day.
Everything is additive: every call that worked on 0.1.0 returns the same shape on 0.2.0.

### Added

#### New tools

- **`finance` tool** (13th core tool): `emi` (instalment, totals and amortisation schedule that
  reconciles to zero), `compound` (future value with optional per-period contributions and the
  effective annual rate), `cagr`, `npv_irr` (IRR by bisection), `gst` (inclusive/exclusive split
  into CGST/SGST or IGST, rounding difference reported) and `percent` (change vs percentage
  points, stacked vs additive discounts, exact bill splits). The rate's period and whether an
  amount is GST-inclusive are required, not guessed.
- **`color` tool** (14th core tool): `convert` between hex (3/4/6/8-digit), RGB, HSL, HSV/HSB,
  naive CMYK and Lab with alpha preserved; `describe` names the nearest of the 148 CSS colours
  by CIE76 ΔE and words the colour from fixed HSL bands; `swatch` returns a real PNG (stdlib
  zlib, 16–256 px, one colour or two side by side); `contrast` gives the WCAG 2.x ratio,
  AA/AAA for normal and large text and the smallest lightness change that passes; `mix`
  blends in sRGB or Lab; `harmony` rotates hue for complementary, analogous, triadic and
  split-complementary sets; `nearest` snaps to a caller's palette with the runner-up;
  `simulate` shows deuteranopia, protanopia and tritanopia (Viénot–Brettel–Mollon 1999);
  `grayscale` greys by rec709, rec601, lab, average or hsl with an optional ramp and strip.
  Bare triples such as `58, 26, 241` are refused with `needs.options` (rgb, hsl, hsv).

#### New modes and parameters

- `datetime` `now` takes a list as `tz` — zone names or `{tz, label}` objects — and answers with
  the shared instant as `utc` plus one full per-zone entry under `zones`, each carrying its
  `label` back. `convert_tz` accepts the same `{tz, label}` entries in `to_tz`. One round-trip
  for "what time is it in each of our offices right now"; the single-`tz` form is unchanged.
- `datetime` `free_slots`: common free slots for two or more participants in different time
  zones, from weekly windows (`09:00`–`17:00` on `mon`…`fri`) or one-off local ranges, intersected
  in UTC through `zoneinfo`. Each slot is shown in every participant's local time and in UTC,
  `per_day` totals the overlap per date, a window spanning a DST change is expanded to its real
  length with a note in `assumptions`, and no common time is `ok: true` with `slots: []` and a
  warning naming who never overlaps.
- `convert` `fuel_economy`: `mpg_us` / `mpg_uk` / `km_per_l` / `l_per_100km` with exact
  constants; a bare `mpg` is `ambiguous` (US or imperial gallon), and any conversion that crosses
  L/100 km states the inverse relation in `assumptions`.
- `convert` `cooking`: cups, tbsp, tsp, ml, fl oz ↔ g, kg, oz, lb. Mass ↔ volume needs
  `ingredient`, looked up in a built-in density table (18 staples; the grams-per-cup used are
  stated); a missing or unknown ingredient returns the table as `needs.options`. `cup` selects the
  US (240 ml, default and declared), metric, UK or Australian (20 ml tablespoon) system.
- `convert` `sizes`: `category=shoe` converts US men / US women / UK / EU / cm on a generic
  adult chart, snapping to the nearest half size with a warning; `category=clothing` maps XS–XXL
  to chest and waist cm bands for a chart chosen by `region` (`us` inch-based, `eu` EN 13402-3)
  and `gender`, both required. Every result warns that sizes are approximate and names the chart.
- `numbers` `semver`: compare or sort version strings as versions (`1.10` > `1.9`), with
  SemVer 2.0 pre-release precedence, build metadata carried but ignored for ordering, and a
  leading `v` / missing minor or patch tolerated and recorded in `assumptions`.
- `text` `similarity`: Levenshtein distance and a 0–1 ratio for a pair, or `text` + `items` to
  rank candidates and return the best match with its index — mapping typed input onto a menu,
  spotting near-duplicate names. Case-folded and whitespace-normalised by default, both stated.
- `collections`: every record input (`items`, `a`/`b`, `data`) also accepts CSV text — the
  delimiter is sniffed, the header row detected (first row with no numeric, date or boolean cell;
  `has_header` overrides), each field typed as number (exact `Decimal`, via `numbers.parse`),
  ISO date, boolean or text, and blank rows and `N/A`/`-`/`null` cells skipped and counted, all
  stated in `assumptions`; above 5,000 rows the call is refused. New table modes over the same
  input: `filter` (`where` predicates with the `validate.assert` vocabulary, compared in the
  field's type), `pivot` (`by` × `pivot_columns`, one aggregate of `column`, row and grand
  totals, empty cells `null`), `running` (cumulative total, restarting per `by`), `outliers`
  (Tukey hinges, 1.5×IQR, fences reported, at least 4 values), `summarize` (count/nulls/sum/avg/
  min/max/median for every numeric field, distinct counts for text, range for dates) and `to_csv`
  (`delimiter`, chosen `columns`). A lone numeric field is assumed for `running`/`outliers` and
  said so; several are `ambiguous`. `decimals` rounds computed values only; row-shaped results
  echo at most 500 rows with a warning, except `to_csv`.
- `validate` `cidr`: membership (`network` + `value` → `contains`, for an address or a smaller
  block) and overlap (a list of networks → every pair with `equal` / `a_contains_b` /
  `b_contains_a` / `disjoint`), plus a block's size, usable hosts, bounds and masks. Host bits
  set are read as the network and recorded in `assumptions`.
- `validate` `id` with `kind=isbn` returns both `isbn10` and `isbn13` for a valid number (a 979
  book has no ISBN-10 form).
- `encode` `hash` and `checksum` take `expected` and answer `matches` — compared in constant
  time against the hex, Base64 (or, for checksums, decimal) form, case- and whitespace-
  insensitive, with a `sha256sum`-style `<digest>  <file>` line accepted as-is. A mismatch is
  `ok: true, matches: false`: an answer, not an error. `hmac` shares the same comparison.
- `files` `file_hash`: sha256 (default) / sha1 / md5 / blake2b / crc32 and the other hashlib
  algorithms, streamed in 1 MiB chunks so a file's size is bounded only by `LEFTBRAIN_FILE_ROOTS`,
  with `bytes`, the algorithm used, and `expected` → `matches` for verifying a download.

#### Keys and dashboard

- `leftbrain-keys set --all --daily N [--rpm N] [--from-daily N]` changes limits on every key —
  or, with `--from-daily`, only on keys still at an old default — so a moved default can be
  migrated without a database shell or touching keys set by hand. Prints how many keys changed.
- Dashboard **Delete** for a revoked or expired key: removes the row and its usage history for
  good (with a confirm). A key that still works must be revoked first; deletion is owner- and
  CSRF-checked like the other key routes.

### Fixed

- Dashboard: keys issued before the server could show keys again (including the old
  `POST /keys/signup` rows, still named `self-serve signup`) are marked **legacy** and explained
  on the row instead of the bare "created before reveal was enabled". They no longer hold one of
  the owner's active-key slots, so a first sign-in that inherits three of them can still create a
  key. `/keys/me` output is unchanged.
- Demo endpoint: the 8 KB body cap now applies to chunked requests too, and deeply nested arguments return a contract `invalid_input` instead of a bare 500.
- Docker image build failed after the changelog was bundled into the wheel (`CHANGELOG.md` was not copied into the image).

### Changed

- Free tier is **1,000 calls per key per day** (was 5,000); `LEFTBRAIN_DEFAULT_DAILY_QUOTA` still overrides it. Every place the site quotes the limit — landing, sign-in, quickstart, the demo's 429 — now reads the configured value instead of a hard-coded number.
- Site shows loading skeletons on navigation, form submits and demo runs; the colour legend on docs pages was dropped.
- Docs key picker shows the key name only (prefix only for unnamed keys).
- Docs examples are labelled Request/Response and colour-coded: blue blocks are what you
  send, green blocks are what comes back, and a neutral block marks a setup command.

## [0.1.0] - 2026-08-26

First public release, and a **pre-release** — the package ships as
`Development Status :: 3 - Alpha`. Tool names, parameters and the response envelope may
still change before 1.0, so pin an exact version if you build on it.

### Added

- **12 core tools**, offline and free of API keys: `math`, `datetime`, `scale`, `convert`,
  `holidays`, `numbers`, `text`, `collections`, `validate`, `random`, `geo_offline`,
  `encode`. Each takes a `mode`, so a client pays for twelve tool descriptions rather than
  a hundred.
- **4 external tools** over key-less public APIs: `weather` (Open-Meteo), `fx_rate` (ECB
  reference rates via Frankfurter), `geo` (geocode, reverse, driving route), `url_check`.
- **Optional file tools** for agents that cannot open files themselves: `pdf_text`,
  `pdf_info`, `image_info`, `image_to_base64`, `base64_to_file`, `file_info`, `read_text`,
  `list_dir`, restricted to the paths in `LEFTBRAIN_FILE_ROOTS`.
- **One response contract** for every tool:
  `{"ok": true, "result": …, "assumptions": [], "warnings": []}`. Failures return
  `ok: false` with an `error` code and a message; an ambiguous input returns `needs` with
  the concrete options instead of guessing (`IST`, `03/04/2025`, `ton`, `KB`). `result` is
  never null, and exact and decimal forms are returned together so nothing is re-rounded.
- **MCP servers over stdio**: `leftbrain`, `leftbrain-external`, `leftbrain-files`.
- **MCP over Streamable HTTP**: `leftbrain-serve` puts every tool set in one stateless
  process — `/mcp`, `/external/mcp`, optionally `/files/mcp`, plus `GET /healthz` and a
  JSON service description at `/`. Dockerfile and compose file included.
- **Use from Python without MCP**: `import leftbrain as lb` gives `lb.math_tool(...)` and
  friends, and `lb.TOOLS` maps every tool name to its function.
- **Hosted service** at <https://leftbrain.idlesync.in>:
  - GitHub sign-in; keys belong to the account's verified primary email.
  - API keys prefixed `lblz_`, up to 3 per account, 5,000 requests/day and 60/minute, with
    quota headers on every response and `429` plus `Retry-After` when a limit is reached.
  - Dashboard: create a named key (shown once), reveal an existing one again, revoke, and
    see today's usage.
  - Docs with Windows PowerShell / macOS / Linux tabs — quickstart, MCP client setup, a
    custom-agents guide, and a copy-paste prompt that configures any coding agent. A
    signed-in reader's own key is filled into every example on the page.
  - A per-tool reference generated from the MCP servers themselves: parameters come from
    the published schemas and every example is executed while the page is built, so the
    docs cannot drift from the code.
  - A key-less live demo on the landing page (`numbers`, `convert`, `datetime`, `text`).
- **Per-user key store** on SQLite or Postgres, resolved from `LEFTBRAIN_KEYS_URL`,
  `DATABASE_URL` or `LEFTBRAIN_KEYS_DB`, with a `leftbrain-keys` admin CLI (create, list,
  enable, disable, revoke, set limits, usage, stats).
- **Key expiry.** Keys are created with a lifetime — 30, 90 (default) or 365 days, or never
  (the dashboard and CLI warn that a key which never expires is a liability if leaked).
  An expired key is refused with `403 {"error": "expired", "message": "key expired on <date>; create a new one at /dashboard"}`,
  drops out of the 3-active cap, and can no longer be shown again. The dashboard shows
  when each key expires and flags one within 7 days; the docs key picker skips expired
  keys and reminds you when the selected one is about to expire. `GET /keys/me` reports
  `expires_at` and `expired`; `leftbrain-keys create --expires 90d|never` and
  `set <prefix> --expires …` manage it from the CLI. Existing keys are migrated with no
  expiry.
- Deployment guide for a free Northflank sandbox in `docs/deploy-northflank.md`.

### Changed

- Range parameters use the name their domain already uses rather than `from`/`to`, which
  collided with the Python keyword and forced a `from_` spelling on the wire:
  `datetime diff`, `business_days` and `cron_next` take `start`/`end`; `math integrate`
  takes `lower`/`upper`; `math limit` takes `point`; `math convert_form` takes `form`;
  `geo_offline distance` and external `geo route` take `origin`/`destination`. The `from`,
  `from_` and bare `to` spellings are gone. `from_unit`/`to_unit`, `from_qty`/`to_qty` and
  `from_tz`/`to_tz` are unchanged.
- The MCP wrappers accept every parameter their core tool supports, so a tool is not less
  capable over MCP than it is from Python.

### Fixed

- The GitHub OAuth callback handles malformed or unexpected responses instead of failing
  with a server error.
- Docs `:::os` tab blocks are parsed fence-aware, so a `:::` inside a code sample no longer
  ends the block early.
- Static assets are stamped with a content hash, so a stylesheet or script change reaches
  browsers without waiting for a version bump.

### Security

- Security headers on every response: `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: strict-origin-when-cross-origin`, `X-Frame-Options: DENY` and a
  Content-Security-Policy with `frame-ancestors 'none'`.
- `X-Forwarded-For` is counted from the right using `LEFTBRAIN_TRUSTED_PROXY_HOPS`
  (default 1), and `X-Real-IP` / `CF-Connecting-IP` are ignored — a caller cannot forge the
  address a rate limit is keyed on.
- The demo endpoint runs behind an allow-list of tools, modes and argument names, an 8 KiB
  body cap and per-argument size caps, and never returns a traceback.
- Keys are authenticated by comparing a SHA-256 hash. The revealable copy is
  Fernet-encrypted under a key derived from `LEFTBRAIN_SECRET`; leave that unset and only
  the hash is stored.
- Anonymous signup is closed unless `LEFTBRAIN_OPEN_SIGNUP=1`. Sessions use a signed
  cookie, every mutation is CSRF-checked, and any page carrying a key is sent
  `Cache-Control: no-store`.

