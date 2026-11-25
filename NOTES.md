# Notes: assumptions and design decisions

Technical notes for the synthetic BEO drafter. Review and decision history is kept outside this tree. No remotes or public repo. Agents do not log in to Fly or Sprites from this environment and do not handle the Ollama key.

## Scope

- This tree is a new implementation. It does not read or copy the author's separate product or other private employer code. BEO section names come from common banquet-order practice (overview, timeline, headcount, menu, dietary, room setup, AV, billing, notes), not from a private schema.
- Fixtures use invented venue and client names (see the name check below). Dates are in 2027. Every fixture is marked synthetic in its notes.
- The drafter does not compute billing totals, tax, or service-charge amounts. It flags seating and overage conflicts; it does not invent prices or extra seats.

## Runtime and clients

- Python 3 stdlib only, so a Sprite can run it without a package install. Assumed: a stock Sprite image has Python 3.11 or newer (local box is 3.13). If it does not, that is a later port risk, not a local-prep blocker.
- The default client is a deterministic mock. Same brief, same BEO. It derives a staffing plan from guarantee counts (1 server per 16, captains and bartenders at fixed thresholds) so the output is a draft, not a raw copy of the brief. When `staff_meals` is omitted, the same computed count (servers + captains + bartenders + 2) is written to `headcount.staff_meals` and into the plan string. An explicit `staff_meals` value, including 0, is kept.
- Timeline order is wrap-aware: times are sorted by minutes after load-in, and a clock time earlier than load-in is treated as the next day. Each row also carries `day_offset` and a calendar `date` (event date plus the offset), and Markdown prints that date so an overnight load-out is not implied only by sort order. The gala load-out is 2027-10-10 01:00.
- Markdown rendering walks lists nested inside dicts (menu courses, AV items) as bullets and never interpolates a Python list repr.
- The Ollama Cloud client now makes real HTTP calls with stdlib `urllib`, but only when that client is selected. It posts to `https://api.sprites.dev/v1/gateway/ollama/<OLLAMA_CONNECTION_ID>/api/chat` (or `OLLAMA_HOST` if set) with `stream: false` and no Authorization header. The Sprites gateway attaches the key. The client may still read `OLLAMA_API_KEY` so tests can prove it is never sent, logged, or shown in `repr`/`str`; do not set that variable on a Sprite. Tests inject a fake `urlopen` and never open a network socket. Ollama Cloud does not support structured outputs, so the client extracts a JSON object from the message content and validates the BEO schema; invented dishes are possible from the model and are a residual risk.
- A stdlib HTTP server (`python3 -m beo_drafter.server`) binds `127.0.0.1:8080` by default. Binding `0.0.0.0` requires `--bind 0.0.0.0` or `BEO_BIND` (needed on a Sprite, where the URL is routed to port 8080). The form and `POST /draft` are mock-only unless `DEMO_ALLOW_OLLAMA=1`. HTTP ignores client-supplied `out` / `receipts` paths and does not run `--approve` or write receipts; `--approve` stays on the CLI path. Model output is validated with a stricter `validate_beo` and is always returned with `overview.status: draft`.

## Receipts and status stamps

- The `--approve` flag and the receipt log are opt-in (`--approve` plus `--receipts`). Default draft writes files only, with `overview.status: draft` and an **UNAPPROVED DRAFT** banner.
- `--approve` sets `overview.status` to `approved` and rewrites the JSON and Markdown (with an **APPROVED** banner) before the receipt is hashed, so the files on disk match the hashed BEO.
- A receipt hash is sha256(previous_hash + canonical BEO JSON + canonical receipt body without `hash`). Genesis previous_hash is 64 zero hex characters. This is a thin, public-demo hash-chained receipt, and it is not a signature with a real key.
- After each append, a sibling anchor file (`<log>.anchor`) stores `{count, tip_hash}`. `verify-receipts` checks the chain and then the anchor, so tail truncation and a forged append that does not update the anchor fail. Mid-chain edit, delete, reorder, and insert already fail the hash chain.
- Limits, stated plainly: without the anchor, a prefix of a valid chain still verifies, so dropping the last receipt is undetectable. Anyone who can rewrite both the log and the anchor can still produce a chain that verifies. A real signature is later work.
- `append_receipt` takes an exclusive `fcntl` lock, re-reads the tip, appends, and updates the anchor before releasing the lock. If `fcntl` is missing (non-Unix), the same re-read-then-append path still runs, a warning is printed to stderr, and concurrent writers are not serialized; treat that fallback as single-writer. On this box and on a Linux Sprite, the lock is used. A 16-thread test asserts the chain does not fork.

## Conflicts (intentional in two fixtures)

- The wedding (expected 128, 12 rounds of 10 plus a sweetheart table = 122 seats) and the gala (expected 300 vs 280 seats, 5% overage cap = 14, expected 20 over guarantee) are left inconsistent on purpose so the drafter can flag them. Flags appear under `headcount.conflicts` and in the Markdown. Other fixtures have expected counts within seats and are not flagged.
- Seat capacity prefers an explicit `room_setup.seat_capacity` field. A conservative parse of "N rounds of M", "N tables of M", "sweetheart table" (+2), and "winners'/reserved table of N" is the fallback; the drafter does not invent seats when it cannot tell.

## Fixture name check (23 Nov 2026, public web search only)

Renamed after checking for obvious living brands:

- "Fieldnote Press" matches a real independent imprint (Amazon listings and a July 2026 title). Renamed to **Vellumfen Broadsheets** (no matching living imprint found).
- "Pebble & Oak Design Studio" is very close to Pebble + Oak, a real Bend, Oregon design studio. Renamed to **Shale & Nettle Studio**.
- "Lumen Arc Cooperative" is close to several real Lumen Arc / LumenArc businesses (productions, electrical, film tools). It was briefly called Fairglass Cooperative after an early name pass, then **Ninthwick Cooperative** (no matching living cooperative found in a follow-up search).
- "Riverlight Conservancy" is close to the real RiverLights development in Wilmington, NC. Renamed to **Wrenmarsh Conservancy**.
- "Harbor & Pine Event Hall" is close to Harbor Pine Hospitality + Events, a real Maine events business. Renamed to **Cinderwharf Hall**.

Left in place after search: Northglass Atrium, Cedarwick Civic Rooms, and Millpond Workshop Rooms had no matching real hall at the fictional addresses. Stonebroom Loft did not match a real loft venue; Stonebroom is a UK village name, noted here, not treated as a brand collision. Kim & Alvarez is a pairing of common surnames, not a company. Replacement names (Vellumfen Broadsheets, Ninthwick Cooperative, Shale & Nettle Studio, Wrenmarsh Conservancy, Cinderwharf Hall) had no obvious living-brand matches in the same search.

## Repo and secrets

- Local `git init` and commits are allowed. No remote, no push, no public GitHub repo.
- `.env.example` uses only placeholders (`replace-me`, the public gateway base URL). `.env` is gitignored. No live keys belong in this tree.
- Output directories (`out/`), `*.jsonl` logs, and `*.anchor` files are gitignored so generated BEOs and receipts stay local.


## HTTP bind, demo gate, and validation

- MED-1: the demo server now defaults to `127.0.0.1`. `0.0.0.0` is explicit (`--bind` or `BEO_BIND`). Ollama is not selectable over HTTP unless `DEMO_ALLOW_OLLAMA=1`.
- MED-2: `/draft` ignores caller `out` and `receipts` and does not approve over HTTP.
- LOW-1: `validate_beo` checks section types. The Ollama client and `draft()` call it with `force_draft=True`, so model output cannot come back as approved. A test sends a payload with missing sections so skipping `validate_beo` fails.
- DEPLOY.md step 7 is a later, optional step, with a spend warning. The public demo is mock-only by default. Do not set `DEMO_ALLOW_OLLAMA=1` on a public URL.


## Gateway URL, Markdown escaping, and request limits

- `GATEWAY_BASE` must `posixpath.normpath` to `/v1/gateway/ollama` and may not contain `.` or `..` path segments. Dots inside a connection_id (`a..b`, `conn.v1`) stay allowed.
- `render_markdown` HTML-escapes model-supplied text so CLI `.md` files do not carry raw tags. The HTTP form inserts that Markdown into `<pre>` without escaping it again, so `&` appears once as `&amp;` and `<script>` stays inert.
- The demo handler has a 15s wall-clock request deadline on top of the 15s idle timeout, so a slow trickle cannot hold a thread. `Transfer-Encoding` plus `Content-Length`, and duplicate `Content-Length` headers, return 400.

## Open risks (also reported upstream)

- Two request-limit fixes were merged into `8816eb1`, keeping the bind default, Ollama HTTP gate, ignored client paths, and `force_draft`. Missing Content-Length returns 400 so a bad length never hangs. No new env names.
- The Ollama connector is dashboard-only in the public docs; there is no `sprite connector` CLI. Availability can vary by org. The operator pastes the key.
- A live model may still invent dishes or miss schema fields; the client validates JSON shape but cannot guarantee dish fidelity the way the mock tests do.
- Crossing $5 on Fly's Free plan bills the whole month. Sprite compute pauses when idle; Ollama bills model usage separately. Docs disagree slightly on whether a sticky `services stop` prevents HTTP auto-start of an `--http-port` service; flip URL auth back to `sprite` and stop the service to cap surprise traffic.
- The personal org slug is often `personal` in the docs, but the operator must confirm `sprite org list` shows the personal org as current, not a work org.
- No public repo or demo post yet. Making the Sprite URL public puts the demo on the open internet; that is a demo-access step, not a GitHub publish.
- Confidence is high for local tests and the documented deploy steps; medium for a first-time Windows deploy until the pack has been run on the operator's machine, because some CLI success banners are not quoted in the docs.

## Fixture pass

- F1: the awards dinner "cap of 18 per person" is now a fictional per-person cap of `[CAP]`. No other dollar menu prices were in the fixtures. Left in place, and flagged as not menu prices: service-charge rates 18–23%, 5% overage caps, cost centers `NW-204` and `WF-118`, centerpiece heights (14 / 16 inches), and guest/fire-list/dietary counts.
- F2: wedding and gala now have a `menu.bar` plan (also copied into service notes, billing, and a last-call milestone). Cocktail, awards plated, awards buffet, brunch toast, and conference Day 1 also have bar plans. Breakfast, offsite lunch, day-meeting, and conference Day 2 have no bar.
- F3: plated fire-list counts in item strings now add up to the guarantee (wedding 68+38+14=120, gala 174+60+36+10=280 with a named vegan main, awards 72+7+11=90). Day-meeting lunch 24+10+8=42 and conference dinner/boxes 64+16=80 already added up. Dietary lines are specials with overlaps and must not exceed the guarantee. A test checks every fixture.
- F4: the awards timeline now includes staff meal, tech check, first-course drop, entrée fire, clear mains, dessert after the last award, and last call. Wedding and gala got the same service-beat pattern; gala load-out at 01:00 stays last.
- Existing briefs were patched in place for linen/floor plan `[ATTACHMENT]`, billing placeholders (`[RATE]`, `[DEPOSIT]`, `[SIGNER]`, `[PACKAGE]`, `[ROOM]`, `[CAP]`), cocktail-hour/dessert notes, welcome remarks (no officiant lavalier), severe-allergy first line, staff-meal times, and the lunch platter / staff-meal-3 explanation.
- New briefs: `cocktail.json`, `day-meeting.json`, `awards-buffet.json`, `conference-001a.json`, `conference-001b.json`, `brunch.json`. Conference days share `package_id` `synth-conference-001` in overview and notes; they remain two BEOs.

Q decisions:

- Q1: bar, linen, fire-list counts, and allergy detail stay in strings (and an optional `menu.bar` dict). `REQUIRED_SECTIONS` is unchanged.
- Q2: one BEO per function. Day-meeting is still one same-day BEO. Conference 001a/001b stay separate.
- Q3: `--approve` stamps `overview.status` and `overview.approved_at` only. Signature lines stay `[CLIENT SIGNATURE]` / `[MANAGER SIGNATURE]`.
- Q4: staffing ratio follows service style (cocktail 1:12, plated 1:16, buffet/family-style/brunch 1:20). Bartenders come from the bar plan (explicit count, else 1:60). No bartenders when there is no bar. An explicit `staff_meals` that differs from the computed floor-team total is kept and explained in the plan string.
- Q5: `billing.guarantee_due` is derived as noon, N hours or days before the event date, and printed under billing (wedding 72-hour → 2027-06-09 12:00).
- Q6: wedding and gala remain the only intentional seating/overage conflicts.

Name check (public web search, 23 Nov 2026, with a later pass the same week): Larkhollow Benefit Society, Woldfern Analytics, Slaghearth Cooperative, Aldersheath Botanical League, Marshthread Bindery, Fernmoss Assembly Rooms (renamed off Mossglen Assembly Rooms; Mossglen Limited looks like a real Essex firm), Ivorydene Meeting House, Quarrybell Pavilion, Tallowfield Commons, and Nettlewharf Orangery did not match living businesses near Orlando, or major local hospitality brands. Residual: "Slaghearth" appears as "Old Slaghearth cottages" in a Flintshire archive and is left as it is; "Yarrow" and "Tallowfield" occur as place or fantasy toponyms — the client on the Yarrowton venue is Aldersheath Botanical League. "Fernmoss Pet Supplies" is a real Whaley Bridge shop, not an assembly-rooms venue and not near Orlando; the fixture uses Fernmoss Assembly Rooms. Contacts and honorees remain invented.

The conference Day 2 boxed-lunch note was reworded from "gluten-free boxes (8)" to "eight gluten-free boxes" so the fire-list sum test does not treat a subset as a third entrée. Schema, HTML escaping, demo-server hardening, mock default, and network-free tests from r4/r5 are unchanged. DEPLOY.md was not edited; both copies stay as they were.

## Follow-up on guarantee dates, staffing, and fixtures

- `guarantee_due`: when `package_id` is set, the due date is derived from `package_start` (both conference briefs use 2027-07-15, so both print 2027-07-08 12:00). An explicit `billing.guarantee_due` in a brief overrides that derivation.
- Cocktail staffing is 1:20, not 1:12. Default bartender ratio is 1:50 when the bar plan includes spirits or a signature cocktail, 1:75 for beer and wine only, and 1:60 otherwise. An explicit `bar.bartenders` count still wins.
- MED-1: Conference Day 1 load-out is "F&B and reception-furniture load-out only; room held overnight".
- MED-2: Brunch bar hours are "during the 11:45 remarks", not "during the ceremony".
- MED-3: Conference Day 1 plated dinner now has first-course drop, entrée fire, and mains-cleared beats.
- MED-4: Mixed buffet-and-plated briefs (conference Day 1, day-meeting) print a staffing split by function (daytime buffet 1:20 and lunch or dinner plated 1:16) instead of 1:16 all day. Computed staff meals later count both crews, not the peak server count.
- LOW: Day 2 06:00 is "Overnight hold resumes; breakfast set in Meadow Hall", consistent with the overnight hold, and 15:00 is "Full load-out, including overnight hold". Cocktail timeline has a 17:30 tech check. Awards-buffet staffing includes 1 carving attendant. Gala load-out still lands at 2027-10-10 01:00 and Markdown now prints "(next day)".
- The fire-list test sums every `(N)` parenthetical in an item (so "chicken (80) and fish (40)" does not pass a guarantee of 80). Conference 001b keeps the subset-note wording "eight gluten-free boxes" (no parenthetical count) and still passes. Numbers later in the same parentheses, such as "(16, vegetarian; 6 vegan...)", are not extra fire-list counts.
- Mossglen Assembly Rooms was renamed to Fernmoss Assembly Rooms everywhere in this tree. Slaghearth is unchanged.
- The Scope line no longer names a private employer, and the receipt note now calls it a hash-chained receipt.

## Follow-up on package dates, mixed-shift meals, and plated mains

- Packaged briefs must set `package_start` or an explicit `billing.guarantee_due`; a missing or empty start raises `ValueError`. A `package_start` after this brief's event date is also rejected. The drafter only sees this brief, so "earliest event date" is this BEO's date.
- `full bar`, `hosted bar`, `cash bar`, and `open bar` default to the spirits 1:50 bartender ratio unless spirits are negated. A guarantee of 51 on an open or hosted bar plans 2 bartenders. "Hosted beer and wine" is still beer-and-wine, not "hosted bar".
- Mixed buffet-and-plated days count both crews in staff meals (buffet servers plus plated servers, plus captains, bartenders, carving, and 2). Conference Day 1 computes 15 unless the brief override of 14 is kept. Day-meeting computes 9 unless its override of 8 is kept.
- Cocktail `staff_meals` is 16, matching the 1:20 computed floor-team total (including the carving attendant for the carved-lamb station).
- The automatic "Event end" row is omitted when a milestone already sits at the event end time, so conference Day 1 and Day 2 keep their specific end wording only.
- Wedding, awards-dinner, and gala mains now name a starch, a vegetable, and a sauce in the item text. Counts and placeholders are unchanged; no prices were added.

## Prompt, provenance, and residual bar and CLI details

This pass bundled a typed Ollama prompt, the trace-to-sentence demo feature, and residual bar and CLI fixes. Mock stays the default. Tests inject a fake urlopen and never open a network socket. No live model calls from this environment.

### Ollama prompt and coercion

Live smoke on the Sprite (gemma4:31b and gpt-oss:120b) failed `validate_beo` with `BEO.timeline must be a list`. `SYSTEM_PROMPT` now spells out types: timeline and dietary are JSON arrays (timeline rows match the mock shape: `time`, `item`, optional `date`, `day_offset`, `room`, `function`); notes is a string; overview, headcount, menu, room_setup, av, and billing are objects. A compact skeleton derived from the mock output shape is in the prompt.

Decision, logged: if a model returns `timeline` as an object mapping a clock time to a label (or to a row dict), `coerce_model_beo` converts it to a list of `{time, item}` rows and records that in `beo.coercions`. That was the live failure and is a narrow, unambiguous fix. Other wrong types are still rejected by `validate_beo`.

### Provenance (demo loop)

Each mock-drafted field has an entry in `beo.provenance`: `{source: brief|computed|default, ref, text}`. `computed` and `default` are guesses. Markdown marks guesses with a `[guess]` tag. The HTTP page renders a traced HTML view with class `guess` (red, `#b00020`) and the source sentence or field in the `title` (hover). Values are escaped once. Existing r3/r4 server limits are unchanged (127.0.0.1 default, mock-only HTTP unless `DEMO_ALLOW_OLLAMA=1`, ignored client paths, body cap, deadlines).

The demo form can overlay a pasted JSON brief or a short event email onto the selected fixture, and can set a guarantee override and a vegan count (the wedding fixture is 120, so 180 plus vegan is the scripted loop). Email/text parsing is deterministic and labeled-line only:

- `Event:`, `Event name:`, or `Subject:`
- `Date: YYYY-MM-DD`
- `Guarantee:` and `Expected:` (integers)
- `Vegan:`, `Vegan guests:`, or `Vegan count:`
- `Client:` and `Venue:`

Other lines are ignored. A `{` starts JSON and must parse as a brief object. A pasted email without a selected fixture is rejected. No NLP.

Assumptions: provenance is a parallel top-level map, not a wrapper around each value, so `validate_beo` and existing tests keep their shapes. Timeline times come from the brief; default labels such as "Load-out" are guesses unless the brief supplied `load_in_item` / `load_out_item`. HTML titles are the provenance `source — ref — text` string, not a second copy of the BEO. Notes provenance is split (`notes.copied` and `notes.signoff`), as described below.

### Bar, CLI, and package-date details

- Any spirits bar is 1:50, including bourbon, whiskey/whisky, tequila, vodka, gin, rum, and a plain or full liquor bar.
- `no bar` in style or inclusions yields 0 bartenders (unless an explicit `bartenders` count is set).
- `spirit-free` / `spirits-free` is treated as a negation, not as spirits.
- `cash bar`, `open bar`, `full bar`, and `hosted bar` are read from style, inclusions, and `signature_drink` only, not from notes, so a "cash bar" mention in notes does not change the ratio.
- `cli.py` `main` catches `ValueError`, `OSError`, and `RuntimeError`, prints only the message to stderr (absolute paths replaced with `[path]`, no traceback), and returns 1.
- Event-end hiding normalizes `21:00`, `21:00:00`, and trailing spaces to `HH:MM`.
- Whitespace-only `package_start` is treated as missing and raises the packaged-brief message, not an isoformat error.

Known limits, left as they are: mixed-shift over-count; Day-1-only cross-day `package_start` error.

## Staffing, notes provenance, and input limits (24 Nov 2026)

- Any spirits bar is 1:50. That includes vodka, gin, rum, and a plain "liquor" bar (plus "full liquor"), alongside bourbon, whiskey/whisky, and tequila. Only beer and wine stays 1:75. The spirit-free / spirits-free negation applies to all of those spirit words (so "spirit-free bourbon bar" is not 1:50). Phrases of the form `no <spirit>` or `without <spirit>` also do not count that spirit, so "no vodka", "no gin", and "beer and wine, no vodka" are not 1:50. Phrase matching stays on style, inclusions, and `signature_drink` only, not notes.
- A pasted event email still overlays a selected fixture. It does not build a standalone brief.
- Sign-off placeholders that were not in the brief (the drafter-added sign-off block, including `[CLIENT SIGNATURE]` / `[MANAGER SIGNATURE]` and similar `[SIGNER]`-style placeholders the drafter appends) render as a red guess with source `default`. Note text copied from the brief stays `brief`. Provenance is split (`notes.copied` and `notes.signoff`) and both Markdown (`[guess]`) and HTML (`class="guess"`) trace the two parts separately. A brief that already includes `[CLIENT SIGNATURE]` is left as brief text and is not appended to.

Also:

- Guarantee and vegan overrides must be integers from 0 to 10000; otherwise the override is rejected with that clean message.
- Timeline coercion keeps only known row keys (`time`, `item`, `date`, `day_offset`, `room`, `function`). Scalar non-string items are stringified; lists, objects, and booleans are rejected.
- CLI `_safe_error` also replaces `file:` URLs with `[path]`.
- `paste.py` rejects input over 64 KiB, independent of the HTTP body cap.
- Docs were redacted for a later public snapshot: no private product names, work-org slugs, local machine paths, operator names, or internal decision history.
- Labeled `Expected:`, `Guarantee:`, and `Vegan:` values that are not plain integers in range (including `-1`, `1e3`, and `12.5`) are rejected with a clean message instead of being ignored.

### Known issues (carried, not changed)

- Cross-day `package_start` is still checked against this brief's event date only (Day-1-only).
- Mixed-shift staff-meal over-count remains acceptable-as-is (Day 1 still computes 15 unless the brief keeps an override of 14).
## Output stems and demo HTTP limits

- `write_beo` accepts only a stem matching `^[A-Za-z0-9._-]{1,128}$`, and also rejects `.` and `..`. The JSON and Markdown paths are resolved and must stay under the resolved out directory. The CLI prints a short message and exits 1 if the stem is rejected.
- The demo HTTP server allows at most 8 in-flight requests; further requests get `503`. Handler errors use a generic message unless the validation text is already safe. Responses include `Content-Security-Policy` (`default-src 'none'; style-src 'unsafe-inline'` plus `base-uri`, `form-action`, and `frame-ancestors`), `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, and `X-Frame-Options: DENY`, and do not send a Server version banner.

