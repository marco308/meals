# Changelog

What has shipped, and what has only been written.

This repo ships two things on two different clocks, so they get two changelogs:

- **The server** (backend API, MCP server, skill and prompt pack) ships when
  `make deploy` runs. Its releases are the dated sections below, and "released"
  means *live on the deployment*, not *merged to main*.
- **The iOS app** ships one build at a time and can never be recalled once it
  reaches TestFlight, so every build gets a row in
  [ios/CHANGELOG.md](ios/CHANGELOG.md) recording where it got to.

Anything merged but not yet deployed lives under **Unreleased**. If you are
about to deploy, that section is the release note.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
The API contract is additive-only (see CLAUDE.md), so **Removed** and
**Changed** entries for response fields should be rare enough to be alarming.

## Unreleased

Nothing merged since the release below.

## 2026-09-03 — the freezer

Released as **1.5.0**. One additive migration, `a6c2e9f14b37` (a new
`freezer_items` table; nothing existing changes).

### Added

- **Freezer stock** (decision Q24, PR [#139](https://github.com/marco308/meals/pull/139)).
  A running tab of cooked portions waiting to be eaten, kept as one row per
  **batch** — a label, the portions left, the date it went in, a note — rather
  than a merged count per dish, because two batches of chilli a month apart are
  two things to eat oldest-first. A batch is named one way: a meal (it takes
  the meal's name), a recipe (the title), or free text for food that never came
  through the plan. The meal and recipe links are `SET NULL`, so tidying the
  library never empties a freezer. `GET /freezer` lists oldest first with a
  portion total; `POST /freezer` adds a batch, never merges; `POST
  /freezer/{id}/take` eats from one and deletes it at zero; `PATCH` recounts or
  renames; `DELETE` bins it. It touches neither the plan nor the shopping list.
- **The web app has a Freezer page** — the list oldest-first with a "been in a
  while" flag past 90 days, minus-one and plus-one, remove with confirmation,
  and an add dialog with meal, recipe and free-text tabs.
- **Three MCP tools** — `get_freezer`, `add_to_freezer` (resolves a name
  against meals, then recipes, and falls back to free text; `as_text` forces
  it) and `take_from_freezer` (oldest batch first, spilling into the next). The
  skill and prompt pack gain a freezer section, so this is **playbook v17**.
- **`freezer_items` joins the limits vocabulary**, counted in batches (hosted
  100 / 1,000 / 2,000; unlimited by default like everything else), and the
  household export gains a `freezer` section.
- **iOS 1.2 build 27** carries the freezer screen off the Plan tab and is on
  TestFlight; `current_ios_build` moves to 27 so installs are told.

### Fixed

- **Web: the `hidden` attribute now actually hides a form field.** `label.field
  { display: block }` was beating the browser's `[hidden]` rule, so anything
  toggled with `el.hidden` stayed on screen.

## 2026-08-24 — record the price, absorb the races

Released as **1.4.1**. No migrations.

### Fixed

- **A payment now records what was agreed.** `households.price_pence` was
  written by nothing that ships: the webhook granted without it and the
  operator command has no flag for it, so every household that actually paid
  had an empty price snapshot. §6's "founding price for life" is a promise
  stored on the row, `/privacy` says the server records "what it agreed to
  pay", the ops listing has a column for it and the web app has a sentence for
  it, and all four were describing a column nothing filled in. A grant now
  writes `BILLING_PRICE_PENCE` the first time a household pays, and
  deliberately never again: sending today's price on a renewal would make
  `entitlements.grant` refuse the renewal of anybody who bought before a rise,
  which is the promise failing in the most expensive direction.
- **Two deliveries of one webhook that overlap are a duplicate, not a 500.**
  Processors send duplicates and retry on any non-2xx, so both copies can pass
  the ledger check before either writes, and the second was answered with an
  unhandled `IntegrityError`. No second year was ever granted — the ledger's
  uniqueness is what refused it — but the response asked for a retry that would
  fail identically, and the outcome reached no counter, which is exactly the
  silent billing failure that module exists to not have. It now reads that
  refusal as the duplicate it is, and re-raises anything that is not the
  ledger's uniqueness.
- **A double-tapped "Create household" answers 409 rather than 500.**
  `POST /auth/register` checks the address and then inserts, so two overlapping
  registrations for one email raced to the unique index and the loser got an
  unhandled error instead of the sentence pointing at `POST /auth/login`.
  Nothing was left behind either way (the rollback takes the half-made
  household with it, which is why the instance ceilings are checked before any
  of it), and that is now covered by a test.

## 2026-08-24 — say what this is built on

Released as **1.4.0**. No migrations.

### Added

- **`/credits`**, served by every deployment from
  [CREDITS.md](CREDITS.md) on the same rails as `/privacy`, `/support` and
  `/terms`: what the server is built on, under which licences, with a note on
  each of the fifteen dependencies this project actually chose. Neither client
  ships third-party code, and the page says so rather than letting anyone
  assume otherwise. Nothing obliges it (the wheels carry their own licence
  files into the image, which is what MIT, BSD and Apache-2.0 ask of a
  distribution) beyond it being the decent thing to do.
- `backend/tests/unit/test_credits.py` resolves both `uv.lock` files down to
  what installs on Linux and fails when a shipped package is uncredited, when a
  credited one no longer ships, or when a row names no licence. A credits page
  nobody lints is a credits page that quietly stops being true.
- **An About card in the web app's Settings**, which is the first link anywhere
  in the web client to `/privacy`, `/support`, `/terms` and `/credits`. Linted
  against the pages router, so a fifth page fails CI until somebody decides
  where it belongs.
- **A Credits link in the iPhone app's Settings**, following the connected
  server like the privacy and support links do. Still no link to `/terms` from
  iOS, deliberately: that is the page with the price on it, and 3.1.3(f) is
  what the listing rests on. A lint in the Python suite keeps it that way,
  since there is no iOS job in CI.

## 2026-08-23 — pin the version that decides who is selling

Released as **1.3.1**. No migrations.

### Fixed

- **The Stripe API version is pinned on the checkout request** rather than
  inherited from the account's default. `managed_payments[enabled]` — the
  parameter that makes Stripe the merchant of record, and the whole reason §7
  chose one — exists only from `2025-03-31.basil`. An account on an older
  default would have rejected it and failed every checkout, and the setting that
  decides it is one this server cannot see. Found while setting up a sandbox:
  Stripe's own integration snippet sends the header and this code did not.

## 2026-08-23 — the web half of it

Released as **1.3.0** and deployed to `meals.marcuslab.uk` the same evening.
**No migrations**, so the rollout was an image swap.

Nothing here is switched on for that deployment either. It sets no
`LIMITS_PROFILE` and no `BILLING_API_KEY`, so the usage panel, the signup table
and the subscription card are all absent, `POST /billing/checkout` does not
exist, and `billing_enabled` is false. What changes for a user of it is one
button: their household's data, in one file, from Settings.

The web half of the money, and the web half of the limits. Two issues, both of
them the same shape: the freemium machinery existed and nothing a household
could see used it.

### Added

- **The web app shows a household what it is allowed**
  ([#120](https://github.com/marco308/meals/issues/120)). Settings gains a
  usage panel from `GET /limits`, the signup screen lists what an account here
  includes from the unauthenticated `GET /client-config`, and there is finally a
  button for `GET /household/export`, which had worked since #97 and been
  reachable only by somebody who knew the API existed. `limited` is the switch:
  on a server that has configured nothing, none of it appears.
- **A checkout can be started** ([#121](https://github.com/marco308/meals/issues/121)).
  `POST /billing/checkout` opens a hosted checkout with the household id where
  the webhook will look for it, and `GET /billing/subscription` says what a
  household has, until when, where it came from and where to manage it. Stripe,
  Paddle and Lemon Squeezy, as before. Web only: the iPhone app carries no price,
  no button and no link to one (`planning/08-freemium.md` §6).
- **`GET /client-config` publishes `billing_enabled`**, the single answer to
  whether this server sells anything at all — a different question from whether
  it limits anything, and one a client would otherwise have to infer from a 404.
- **`PRIVACY.md` names the processor**, which that section promised to do before
  any money moved: Stripe Managed Payments for the author's hosted service, with
  what is sent to them when a checkout starts (an email address and a household
  id, and nothing else).

### Notes for whoever deploys this

- **Still inert.** `BILLING_API_KEY` and `BILLING_PRICE_ID` are unset here, so
  `POST /billing/checkout` does not exist, and `billing_enabled` is false.
- **A server that sells must set `DEFAULT_HOUSEHOLD_TIER=free`** and it now
  refuses to boot otherwise: households starting on the top tier already have
  everything a subscription would buy, so every checkout would be refused with a
  409 that reads like a bug rather than a setting.
- No migrations.

## 2026-08-23 — the freemium backend, end to end

Deployed to `meals.marcuslab.uk` as **1.2.0**. **Two migrations**
(`74e2494dfabf`, `508d35134cdc`), both additive: five nullable columns on
`households` and one new `billing_events` table. Safe under the start-first
rollout, since neither takes anything away from the outgoing container still
reading those tables.

**Nothing in this release is switched on here.** The family instance sets no
`LIMITS_PROFILE`, no `MAX_HOUSEHOLDS`, no `BILLING_PROCESSOR` and no entitlement
on any household, so every limit is unlimited, the instance ceilings are
absent, `/billing/webhook` does not exist, and no household has an expiry to
lapse past. What actually changes for a user of this deployment is two new
endpoints that answer honestly about having no limits, and a `/terms` page that
says almost none of it applies to them. That is the whole point of §1: the
freemium machinery is invisible on a server that sells nothing, and this deploy
is the first real test of that claim against a live household.

This closes issues #95 through #100, which is the whole of
`planning/08-freemium.md` §8.

### Added

- **The billing webhook** ([#99](https://github.com/marco308/meals/issues/99)),
  and it **ships inert**: with `BILLING_PROCESSOR` unset the route does not
  exist at all (404, the posture `/metrics` takes without a token), because a
  self-hosted instance has no billing and must not be able to acquire one by
  accident. **One migration**, a new `billing_events` table.
- **Stripe Managed Payments, Paddle and Lemon Squeezy are all supported**,
  selected by `BILLING_PROCESSOR`. The requirement is a merchant of record, so
  that EU B2C digital-services VAT is the seller's to file rather than ours;
  that is not the same as "not Stripe", since **Managed Payments is Stripe
  acting as merchant of record** and runs on an existing Stripe account.
  Ordinary Stripe leaves the tax with you and is deliberately not what
  `stripe` means here. All three formats were read from the live
  documentation: Paddle signs `"{ts}:{body}"`, Lemon Squeezy signs the raw
  body, Stripe signs `"{ts}.{body}"`. Stripe's is the one a hand-rolled
  verifier gets wrong — several `v1` signatures can be live at once while an
  endpoint secret rolls, and the `v0` scheme it sends beside test events must
  be ignored rather than accepted.
- **No webhook can grant an entitlement with no end date.** A grant without an
  expiry never lapses, so it would quietly hand out a subscription nobody has
  to renew; an event carrying no billing period is refused and recorded
  instead.
- **A retry cannot grant a second year.** Every event is recorded once in a
  ledger keyed on `(processor, event_id)`; Lemon Squeezy sends no event id, so
  its key is a digest of the body, which makes an identical retry land on the
  same row. Processors retry on any non-2xx, so a blip between granting and
  answering 200 is the expected case rather than a rare one.
- **Nothing fails quietly**, which is the point of the whole design: every
  request ends as exactly one counted, logged, recorded outcome, including the
  ones that decide to do nothing. Deterministic failures — a duplicate, an event
  naming no household, one the entitlement layer refused — answer 200 so the
  processor stops retrying something that will fail identically, and are counted
  for the alert instead. Only genuinely transient failures 500 and are retried.
  Alert on `increase(meals_billing_webhooks_total{outcome=~"orphan|refused|bad_signature|unsigned|stale|unreadable"}[1h]) > 0`.
- A cancellation is deliberately **not** a revocation: Lemon Squeezy's
  `subscription_cancelled` starts a grace period that runs to the paid-through
  date, and cutting the entitlement short there would take away days somebody
  paid for. The entitlement expires on its own.

### Changed

- **The Apple review account is comped to the paid tier**
  ([#100](https://github.com/marco308/meals/issues/100),
  `planning/08-freemium.md` §6). `python -m app.provision` now puts the
  household it makes on a permanent comp through `services/entitlements.grant`,
  on creation and on every re-run, because an aged-out resubmission is exactly
  when a lapsed entitlement would bite. A reviewer who meets a cap sees broken
  functionality and files a 2.1 rejection rather than reading the reasoning. No
  expiry (a dated comp is a rejection scheduled for whenever it passes) and no
  price (nothing was paid, and a written price would make a later real purchase
  refuse itself). On a server with no limits it is a column nothing reads.
- **The App Store description no longer says "no subscription."** Not because
  it was a call to action, but because it stops being true the day the hosted
  tier opens, and a description contradicting the operator's own terms page is
  worse than a missing selling point. The replacement says the parts that stay
  true: the app is free, the software is free and open source, and you can run
  it yourself. A unit test now lints the fenced blocks that actually ship, since
  there is no iOS job in CI to catch it.
- **Guideline 3.1.3 re-read and recorded** (`planning/08-freemium.md` §6). The
  case is now numbered **3.1.3(f)** and names web hosting outright. The
  external-link rules did loosen, but only for the **United States storefront**;
  one binary and one set of metadata ship worldwide, so the strictest storefront
  sets the rule and the conservative wording stands unchanged.

### Added

- **Tests pinning that a cap can never become a wall.** `CommerceFreeTests`
  in the iOS suite asserts that 402, 403 and 503 arrive as ordinary
  `.server(status:detail:)` errors shown verbatim, and that **only** a 426 can
  put the app behind `UpgradeRequiredView`, with the 426 case as the control so
  the check cannot pass vacuously. This is also the answer to "what do the
  builds already in the wild do with a 402": the same thing, because nothing
  special-cases them and nothing ever did.

- **Entitlements: one row says what a household is on and until when**
  ([#99](https://github.com/marco308/meals/issues/99),
  `planning/08-freemium.md` §2 and §5). Beside the existing `tier` and price
  snapshot, a household now carries `paid_until`, where the entitlement came
  from, and a one-line note. **One migration**, every column nullable.
- **Lapsing is derived, never written back.** Past its expiry plus
  `ENTITLEMENT_GRACE_DAYS` (14), a household reads as `free` through
  `limits.effective_tier`, which is the only reader. Nothing rewrites `tier`,
  so nothing is deleted, everything already there stays readable, the plan
  stays usable, the shopping list keeps working, the export stays free, and a
  renewal is one column rather than a reconstruction. **A null `paid_until`
  never lapses**, which is every self-hosted household.
- **Comp tooling from day one**: `python -m app.entitlements` comps, extends,
  revokes and lists who is paid, ordered with whatever needs attention first.
  An operator command on the box, like `app/provision.py`, because a
  spreadsheet is not a source of truth. Extending an active year adds to its
  end so renewing early costs nothing; extending a lapsed one starts from
  today so nobody pays for the weeks they were locked out of growing.
- **The founding price is defended, not just stored.** §6 promises it for life,
  so `grant` refuses to overwrite an existing price and says why. Revoking
  keeps it, so coming back costs what it always did.
- **Dunning**: `python -m app.dunning` from cron sends one email before expiry
  and one after, through the SMTP password reset already uses. Each is marked
  once so there is never a third, both marks are cleared when the expiry moves
  so next year gets its own pair, and a relay failure marks nothing so the next
  run retries rather than swallowing somebody's only warning. A server with no
  SMTP does nothing and says so.

### Not included

- **The billing webhook**, deliberately. #99 says to choose a merchant of
  record before writing it and §7 has not: signature verification, event names
  and payload shape are all processor-specific, so writing it now would be
  guessing. Everything above is processor-independent and is what the webhook
  will call when there is one.

- **`/terms`, a terms and refunds page**
  ([#98](https://github.com/marco308/meals/issues/98)), shipped exactly the way
  `/privacy` and `/support` are: a markdown file COPYed into the image,
  rendered by `routers/pages.py`, exempt from the client gate, curled by CI
  against the built image, and advertised on the JSON landing at `/`.
- **It is written to be true on a server that sells nothing**, which is every
  server today. It opens by saying that almost none of it applies to a
  self-hosted instance, where the AGPL is the whole agreement, and it states
  plainly that nothing is on sale yet and nobody has been charged. What it
  commits to when that changes: £20 a year per household, a full no-questions
  refund inside 30 days, best effort rather than an SLA from a one-person
  operation, tested nightly backups, 90 days' notice and a pro-rata refund if
  the service ever ends, and nothing deleted on cancellation ever.
- **A billing section in `PRIVACY.md`**, because `/privacy` is a live App Store
  URL that promises this project holds no payment details. It now says so
  explicitly: no payment is taken anywhere today, and when it is, it goes
  through a third-party merchant of record whose name will appear there before
  the first charge, with card details never reaching this server or its author.

- **`GET /household/export` returns everything a household owns in one request**
  ([#97](https://github.com/marco308/meals/issues/97)) — recipes with their
  lines, the ingredient library, meals, plans, the cooked history, saved
  supermarkets and every shopping list including the archived ones, as one JSON
  document. Ids are kept so it is importable in principle; every reference
  carries the name beside the id so it is readable in practice, without joining
  anything.
- **It is free on every tier and always will be** (`planning/08-freemium.md`
  §1). Nothing in `app/limits.py` touches it: "take your data and go self-host"
  being one request is what makes hosting somebody's data defensible, and it is
  the same answer whether or not anyone is paying. It is equally useful with no
  hosting business at all — the thing to run before a migration, and the
  per-household complement to the whole-database `backup/` sidecar.
- **Streamed row by row**, so a 2,000-recipe household starts arriving
  immediately instead of being assembled in memory. Deterministic ordering
  throughout, so two exports of an unchanged household are the same bytes and
  can be diffed.
- **Credentials and bookkeeping stay behind**: no password hashes, API tokens
  or invite codes, and none of this server's own record *about* the household
  (its tier, any price, the ingest counter), which means nothing on the box it
  is moving to. The field lists are explicit rather than reflected off the
  table, and a test fails when a new column is neither exported nor written
  down as deliberately excluded.
- Import is **not** included, on purpose: id collisions, ingredient folding and
  unit canonicalisation make it a bigger design question, and it should not have
  held the export up.

- **`MAX_HOUSEHOLDS` and `MAX_USERS` bound the instance, not the household**
  ([#96](https://github.com/marco308/meals/issues/96),
  `planning/08-freemium.md` §3). The per-household caps say what one family
  costs and nothing about how many families the box can hold; these are that
  number, and the founding-cohort cap from `planning/06-marketing.md` §1b is now
  a setting rather than a note in a document. Unset by default, so a self-hosted
  server behaves exactly as it did.
- **A full server answers 503 with a waitlist sentence** rather than accepting
  somebody it has no room for. 503 and not 402/403 on purpose: no tier lifts
  this, the caller has done nothing wrong, and a waitlist means "later, yes",
  which is the one thing a status code can carry that 403 cannot. Being full
  stops new registrations and nothing else — everyone already here keeps
  writing, logging in and shopping.
- **The refusal depends on who is knocking.** A stranger starting a household of
  their own is offered the waitlist. Somebody registering against an invite is
  expected — a household here issued them a code — so they are told their code
  is unspent and still good once room appears, which it is: the check runs
  before anything is written, so a refusal consumes no invite and leaves no
  half-made household. `POST /auth/invites/redeem` is never refused by either
  ceiling, because moving an existing user between existing households adds no
  account and can only *lower* the household count.
- **Both ceilings ride on `/metrics`** as `meals_households_limit` and
  `meals_users_limit`, beside the counts they bound, so "nearly full" is a
  dashboard line rather than something learned from the first person turned
  away. Unset reports `+Inf` rather than 0, which would read as "this server
  allows no households" and make the ratio a division by zero.
- `REGISTRATION_ENABLED=false` is untouched and keeps its own 403: that server
  is closed rather than full, so the answer there is still an invite code.
- **`GET /limits` publishes what a household is allowed** and how much of it is
  left — every resource with its limit, usage and remainder, plus the tier the
  numbers came from ([#95](https://github.com/marco308/meals/issues/95),
  `planning/08-freemium.md` §4). An assistant about to import two hundred
  recipes can now ask before it starts rather than stopping on the fifty-first,
  which is worth more than any refusal sentence. The endpoint holds no numbers
  of its own: everything comes from the module that would refuse the write, so
  what is published and what is enforced cannot drift apart. `limit` is the
  number the household will actually meet, whichever of its tier's cap and the
  server's fair-use ceiling is lower, and `upgradable` carries the same
  judgement that picks 402 from 403.
- **It answers on a server that limits nothing**, reporting every resource as
  unlimited rather than 404ing, so no client has to special-case its absence —
  and it counts nothing there, because an unlimited allowance has nothing to be
  short of. The module's "no queries when nothing is configured" promise holds
  on the endpoint as well as on the write path.
- **`check_limits` MCP tool** and a golden rule in the skill and prompt pack
  telling an assistant to check before a bulk import, which is the only time
  ordinary use comes near a limit.
- **`free_tier_limits` on `GET /client-config`**, unauthenticated, so a signup
  page can show what an account costs nothing before anybody has one to log in
  with. Every value is null on a server that caps nothing, which says
  "unlimited" and names no hosted tier that does not exist. Additive, so older
  clients ignore it.

## 2026-08-22 — backups that recover on their own

Deployed to `meals.marcuslab.uk` as **1.1.1**, by digest and verified: the live
API reports 1.1.1, the rollout converged before that was checked, and each
service runs the digest the deploy asked for. **No migration.** The running
backup container was checked for the fix itself rather than just a new digest,
and reports healthy.

### Fixed

- **The backup sidecar can recover from a missed night again.** One failed run
  used to stop backups indefinitely: `freshness.sh` (which is the HEALTHCHECK)
  fails at 36h, swarm then kills the container roughly every 25 minutes, but
  `BACKUP_ON_START=auto` only took a dump when *no* dump existed at all. A
  stale-but-present dump therefore sent every restart straight back to sleeping
  until the scheduled time, which it was always killed long before reaching.
  One missed night on 2026-08-20 cost three days with no backup while the
  service looked merely restarty. `BACKUP_ON_START=auto` now asks
  `freshness.sh` instead of asking whether a file exists, so the check that
  kills the container is the same one that decides to dump on start, and a kill
  becomes the recovery rather than the trap.
- **A wedged backup can no longer stop the nightly loop.** `pg_dump` ran with no
  timeout of any kind, so a database that accepts a connection but never answers
  left it blocked forever: no dump, no `.part` file, and no event line, which is
  exactly what the 2026-08-20 miss looked like. `PGCONNECT_TIMEOUT` (15s) bounds
  getting a connection and `DUMP_TIMEOUT_S` (1h) bounds the dump itself, both
  failing loudly so `outcome=error` reaches the staleness alert. `RUN_TIMEOUT_S`
  (2h) is a backstop around the whole run, so a hang nobody predicted still
  leaves the loop running. Partial dumps are now cleaned up on failure instead
  of accumulating. Where `timeout` is unavailable the scripts run unbounded
  rather than not at all, since depending on a missing command would recreate
  the very bug these guard against.

## 2026-08-22 — hosted tier limits

Deployed to `meals.marcuslab.uk` as **1.1.0**. **One migration**,
`b1d73e5c9a24`, applied on rollout: six additive columns on `households`, and
both existing households backfilled to `tier = 'unlimited'`. No `LIMITS_*` is
set on this deployment, so nothing about it is capped and nothing behaves
differently — which is the promise the feature is built on, checked rather than
assumed.

### Added

- **Per-household limits, and every one of them defaults to unlimited** (issue
  [#94](https://github.com/marco308/meals/issues/94), `planning/08-freemium.md`).
  `app/limits.py` holds one `Limits` set of numbers per tier and one
  `enforce()` the service layer calls immediately before a row is inserted. A
  server that sets nothing has no caps, no paywall, and nothing anywhere that
  says a hosted tier exists — `enforce()` returns before it runs a single query,
  which is both the promise and the implementation of it. `LIMITS_PROFILE=hosted`
  runs the published table, `LIMITS_OVERRIDES` tunes any single number as JSON,
  and `DEFAULT_HOUSEHOLD_TIER` decides what a new registration starts on.
  Members, recipes, ingredients, meals, lines per meal, plans, meals per plan,
  supermarkets, API tokens and monthly URL ingests all have a boundary; every
  one is a plain `COUNT` with no locking, because two concurrent creates
  overshooting by one is cheaper to tolerate than to prevent.
- **Two refusals, because a caller has to act differently.** A tier cap a bigger
  tier would lift answers **402** and says so only in the status code; the
  sentence stays factual and points nowhere, so it reads the same on a
  self-hosted box and the iPhone app can render it verbatim without becoming a
  shop. A fair-use ceiling — or a cap the largest tier cannot lift — answers
  **403** and says that no tier goes further. Both name the limit, the tier and
  the number in use, and end with something to do instead, because an assistant
  bulk-importing will meet these and that sentence is the whole of what it can
  act on. Every block logs `limit.reached`; `outcome="ceiling"` on a paid
  household is the one worth alerting on.
- `households.tier`, plus the price snapshot the founding-price-for-life promise
  needs (`price_pence`, `price_currency`, `price_set_at`) and the URL-ingest
  counter (`ingest_period_started_at`, `ingests_used`). The ingest quota is the
  one limit that cannot be a `COUNT`: counting rows would refund it every time a
  recipe was deleted, which is exactly the loop it exists to stop. It is charged
  up front and committed before the page is fetched — the bandwidth is spent
  whether or not the page turns out to be readable — and `POST
  /recipes/{id}/reparse` costs the same allowance as `POST /recipes/ingest`,
  since it makes the same outbound request. A household whose library is already
  full is refused before either, so a full library never costs a fetch.
- **Archived plans do not count against the plans cap.** There is no way to
  delete a plan — its cooked history is the reason — so counting them would have
  made that cap a wall nothing could bring a household back under: the twentieth
  week planned and never another, with the iPhone app's "add to plan" dead
  behind it. Finishing a week is what frees the place for the next one.
- **Loo roll, toilet paper and water find their aisles**, and two guards keep
  the new "water" keyword honest: water chestnuts stay in tins and rose water
  stays with the baking. Keywords only — the aisle vocabulary the skill
  publishes is unchanged, so no client needs to know.
- **`integrations/node-red/`**: a worked example that drains an Alexa shopping
  list into the household's via `POST /shopping-list/items`. It talks to the
  public API and ships nothing into the image.

### Unchanged on purpose

- **`/shopping-list*` is exempt from every limit, in every tier**, exactly as it
  is exempt from the client gate. iOS drains its offline queue through those
  endpoints and drops any op the server refuses (Q11), so a cap there would
  delete what somebody typed in a supermarket rather than reduce their features.
  That is why an ad-hoc add can still create an ingredient after the ingredient
  allowance is spent, and why items-per-list and archived-shops carry no
  enforcement at all.
- PATs, `/mcp`, `/skill` and `/prompt-pack` are never gated by a tier. Being over
  a limit blocks only the writes that *grow* a household: nothing is deleted,
  nobody is ejected, and everything already there stays readable and usable.

## 2026-08-18 — household admin

Deployed to `meals.marcuslab.uk`. **One migration**, `a2f61d38c095`, applied on
rollout and backfilled: the existing household took its earliest user as lead.

### Added

- **A household knows who is in it, and you can leave one** (issue
  [#52](https://github.com/marco308/meals/issues/52), decision Q23).
  `GET /auth/household` returns the household with every member — name, email,
  when they joined and who admitted them — and `PATCH /auth/household` renames
  it or hands the lead to someone else. `DELETE /auth/household/members/{id}`
  removes a member, or leaves the household when the id is your own: the two are
  one endpoint because they are one act, moving a person into an empty household
  of their own. Nobody is deleted by it — account, password and every session
  and API token survive, and the recipes, plans and history stay with the
  household they always belonged to.
- **`POST /auth/invites/redeem`, so leaving isn't a one-way door.** An invite
  code could only ever be spent at registration, which meant a member who left
  had a working account and no way into any other household short of deleting
  it and signing up again. Redeeming while signed in changes which household the
  account reads and nothing else. It is also the only one of these that can
  destroy anything: leaving is refused when you are the last member, so it can
  never vacate a household, while redeeming out of a household of one that still
  holds recipes needs `{"force": true}` — the same idiom as a forced re-parse. A
  household nobody ever put anything in doesn't ask.
- **A lead**: `households.lead_user_id`, the member the household is billed to.
  Set to whoever registered it, backfilled on existing households to their
  earliest user, and passed to the longest-standing member automatically if a
  lead deletes their account. A lead who wants to leave hands over first. The
  iOS register screen can finally name the household it creates, which the API
  has accepted since Q19 and no client ever sent.

### Changed

- **Only the lead can invite or remove people** (Q23), which amends Q19's "no
  roles inside a household". Everything about the food stays equal — every
  member still adds, edits and deletes any recipe, plan or list — because the
  reason for the lead is billing and nothing else: hosted YAMP is priced per
  household, so the household needs one unambiguous answer to whose card it is.
  Reading `GET /auth/invites` and `GET /auth/household` stays open to everyone;
  who could walk into your house is not the lead's private business.
- `POST /auth/invites` and `DELETE /auth/invites/{id}` are therefore **the first
  non-additive change this API has made**. A non-lead on iOS build 16 or later
  still sees "Invite someone" and now gets a 403 — legibly, as the sentence the
  app prints under the button, naming the lead to ask. `MIN_IOS_BUILD` stays at
  `0`: a readable refusal is not worth cutting off every install below the next
  build.

### Migration

One column, `households.lead_user_id`, backfilled in the same revision
(`a2f61d38c095`). Verified on SQLite and Postgres, up and back down.

## 2026-08-17 (later the same day) — portions, re-parse, un-cook

Deployed to `meals.marcuslab.uk`. No migration: every one of these lands on
columns that already existed, which is why four features could go out together.

### Added

- **Un-cook** (issue [#51](https://github.com/marco308/meals/issues/51)).
  `DELETE /plans/{plan_id}/meals/{plan_meal_id}/cooked` takes back a mis-tap on
  the same path that recorded it, and `times_cooked` stops being a one-way
  ratchet on the counter that answers "what do we actually eat". `cooked_events`
  is otherwise append-only and deleting is still the right move here: the rows
  say "we ate this", a mis-tap means we didn't, and a compensating row would
  leave the history asserting both. Counters are recomputed from the events
  rather than decremented, so deleting this plan-meal's rows and re-deriving is
  the whole correction. The undo reads its meal and recipes off the **events**,
  not off the meal as it stands now, so a meal edited since cooking brings down
  the recipe actually cooked and leaves one added afterwards at zero.
- **Re-parse a recipe from its source page** (issue
  [#54](https://github.com/marco308/meals/issues/54)).
  `POST /recipes/{recipe_id}/reparse` is the deliberate exception to parse once,
  reuse forever (Q3): never automatic, only ever asked for. It updates in place,
  because the recipe's id is what meals and their shopping-list contributions
  point at, and re-parsing into a new row would strand every one of them. The
  id, the `source_url` cache key, the cooked history and the household's
  ingredient curation all survive; title, servings, times, image, instructions,
  tags and ingredients are replaced, and the active list re-syncs to them. A
  recipe marked `edited` is a 409 explaining what would be lost, overridable
  only with `{"force": true}`, which then clears the flag, since the stored
  recipe is the page again rather than someone's correction. Nothing is written
  unless the parse succeeds, so a dead page, or one that has lost its JSON-LD,
  leaves the recipe exactly as it was.
- **Cooking for a number of people** (issue
  [#53](https://github.com/marco308/meals/issues/53)). A meal's recipe line now
  takes `servings` as an alternative to `scale`; the server divides by the
  recipe's own figure and stores the same multiple it always stored, so the
  shopping list, the meal resync and the cooked history are untouched. The
  readback is **`scaled_servings`**, not `servings`: that name already means the
  recipe's own figure, and redefining it in place is exactly the meaning change
  the client contract forbids. Sending both for one recipe is a 422, as is
  asking for portions of a recipe that never said how many it serves, because
  guessing a serving count would silently change how much food someone buys.
- Two MCP tools, `undo_meal_cooked` and `reparse_recipe`, taking the server from
  27 to **29**. Both are there because "no, not that one" and "the page has been
  fixed since" are things people say in conversation rather than in a UI.

### Changed

- **Ingestion caps how much of a page it will read** (issue
  [#55](https://github.com/marco308/meals/issues/55)). `fetch_page` read the
  whole body into memory with only the fetch timeout bounding it, and the URL is
  the caller's, so a pathological page was a memory spike on the database's own
  machine: the last unbounded input on a path that already validates public
  addresses and pins redirects. The read now streams and stops at
  `recipe_fetch_max_bytes` (5 MB, overridable like the timeout beside it). A
  declared `Content-Length` over the ceiling is refused before a byte is read,
  but that is a courtesy rather than the guard, since the header is the remote
  server's own claim; the running total is what actually holds. Redirect hops
  stream too, so their bodies are abandoned rather than read in full on the way
  past. Streaming rules out `response.text`, which needs the whole body
  buffered, so the decode is now explicit: the charset the response declared
  else UTF-8, with undecodable bytes replaced rather than fatal, because a page
  with a few bad bytes can still carry perfectly good JSON-LD.
- The skill and prompt pack teach portions and re-parse, and retire the "there
  is no un-cook" line they had been telling assistants (playbook v13 to v15).

The iOS halves are on TestFlight rather than in this deploy: portions and
fractional scales in build 24, un-cook in build 25, both on the new 1.1 train.
Per-build detail is in [ios/CHANGELOG.md](ios/CHANGELOG.md).

## 2026-08-17 — nightly backups, live

Deployed to `meals.marcuslab.uk`. No migration, and no API change: this is a
new sidecar next to the database, plus the procedure for getting the data back.

### Added

- **The stack backs itself up.** [`backup/`](backup/) is a container that takes
  a nightly `pg_dump -Fc`, reads each dump back with `pg_restore --list` before
  it counts as one, keeps 7 daily and 4 weekly (the first dump of each ISO week,
  hard-linked so it costs no disk until the daily copy expires), and optionally
  uploads a gpg AES-256 encrypted copy anywhere rclone can reach. It is in
  [`docker-compose.yml`](docker-compose.yml), so a self-hosted stack is covered
  from the first `make up`. Closes
  [#9](https://github.com/marco308/meals/issues/9).
- **A restore script that has been rehearsed**, not just written:
  `restore.sh latest` restores into a scratch database and prints what came
  back. CI runs a dump-and-restore against seeded data on every push, and the
  drill was performed against production on 2026-08-17 — dump, restore, boot an
  API against the result, read the shopping list and cooked history back
  through the API. Steps and numbers in [`backup/README.md`](backup/README.md).
- **Noticing when it stops.** The container reports unhealthy once the newest
  dump passes 36h, and every run writes one event line in the same shape as the
  API's event log (`logger=meals.backup`, `outcome=ok|error`, and a `stage` when
  it failed). The deployment alerts on the absence of a successful one.

## 2026-08-13 (later the same day) — observability, metrics, MCP SDK 2.0

Deployed to `meals.marcuslab.uk`. No migration. Verified across the rollout by
polling `/healthz` through Traefik every 200ms: **1401/1401 requests 200**, no
failures, which is the `start-first` + `lbswarm=true` pair still holding.

### Added

- **Structured logging and request observability**, with no new dependencies.
  JSON lines in production and readable text for `make run` and tests
  (`LOG_FORMAT` / `LOG_LEVEL`). One access line per request carrying the matched
  route *template*, duration, the `X-Meals-Client` fields and — once
  authenticated — user and household ids; healthy `/healthz` polls stay quiet
  so the 5s container healthcheck doesn't drown the log. Every response now
  carries `X-Request-ID`, and an unhandled exception logs its traceback and
  answers with a 500 quoting that id, which is the difference between "a
  reviewer got an error" and the diagnosis in
  [ios/CHANGELOG.md](ios/CHANGELOG.md#the-21a-rejection).
- **Named domain events** via `log_event()`: registrations, invites, deletions,
  failed logins, rate-limit trips, client-gate rejections, and the recipe
  ingest funnel including the `ai_parsed` step that closes the 422 loop. Ids
  and enums only — never emails, tokens or full URLs, matching what `/privacy`
  promises.
- **`GET /metrics`**, Prometheus format, guarded by `METRICS_TOKEN`: unset (the
  default, and what a self-hoster gets) it 404s and the refresh task never
  starts. Request counters and latency histograms are labelled by route
  template with everything unmatched collapsed to `unmatched`, so a scanner
  probing random paths can't mint timeseries. `log_event()` also increments
  `meals_events_total{event, outcome}`, making every named event graphable
  without extra instrumentation. Process, platform and GC collectors ride
  along, plus whole-server usage gauges (households, users, recipes) refreshed
  once a minute by a lifespan task.

### Changed

- **The MCP server is ported to SDK 2.0.** `FastMCP` became `MCPServer`, the
  `settings.host` / `port` / `stateless_http` / `transport_security` knobs
  became per-app keyword arguments, and the `request_ctx` contextvar is gone —
  a handler only sees the request context if it declares a `Context` parameter.
  That last one is the one that mattered: http mode exists to forward the
  calling client's own `Authorization` header (Q15), and every tool reaches the
  API through helpers several frames below the handler. Rather than thread a
  `Context` through all 27 tools and their lookup helpers for no behaviour
  change, a server middleware publishes the request's headers in a contextvar
  of our own for the life of the request. Verified on the deployment: a full
  streamable-HTTP session (initialize, initialized, `tools/list`) returns all
  **27 tools**.

## 2026-08-13 — password reset, live

Deployed to `meals.marcuslab.uk`. No migration.

### Added

- `GET /client-config` now publishes `password_reset_enabled`, so a client can
  tell whether the server it is pointed at can send email at all rather than
  offering a "Forgot password?" link that always 503s. Self-hosted servers
  without SMTP are the normal case, not the broken one.

### Changed

- **Password reset works on the deployment.** `SMTP_*` is configured against
  Resend on the verified `marcuslab.uk` domain, so `POST /auth/password-reset`
  emails a code instead of returning 503. Closes
  [#7](https://github.com/marco308/meals/issues/7).

## 2026-08-06 — the web app, policy pages, and the 2.1(a) fix

Deployed to `meals.marcuslab.uk`. No migration. This is the deploy that App
Review's second look landed on, and the one that carried everything merged
since 25 July.

### Added

- **A web app**, served by the API itself at `/app` (`web/` in the repo) — the
  big-screen client. Same-origin with the endpoints it calls, no build step, no
  external requests; covers the plan, the shopping list (staples check,
  "already have it", finish-the-shop, previous shops), recipe library with URL
  ingest, meals, the ingredient catalogue (aisles, staples, value verdicts,
  duplicate merge), and settings (invites, API tokens, password, account
  deletion). Browsers at `/` on a deployment with no `marketing_url` configured
  now land there instead of `/docs`; the JSON landing advertises it as `app`.
  Assets are served `Cache-Control: no-cache` so a deploy shows up on the next
  page load.
- `GET /privacy` and `GET /support` — the App Store requires publicly reachable
  policy and support URLs, and the deployment is the only thing this project
  already hosts. Unauthenticated, exempt from the client gate, and rendered
  from `PRIVACY.md` / `SUPPORT.md` in the repo so the published page can't
  drift from the one GitHub shows.
- An in-app invite flow (Settings → Invite someone). `POST /auth/invites` has
  existed since Q19 but only through the API, which left "get your partner onto
  your server" as a curl command.
- App Store submission material under `ios/AppStore/`: listing copy, App Review
  notes, the App Privacy questionnaire answers, and a submission checklist.
- `make ios-screenshots` — boots a simulator against a locally seeded API and
  captures the App Store screenshot set through a UI test, so the shots can be
  regenerated instead of re-staged by hand.
- `python -m app.provision` — creates one account, in a **new and empty
  household**, on a server whose registration is closed. Written for the Apple
  Review account: a reviewer has to be able to sign in and use the app, and must
  not see a real household's data while doing it. An invite would have done the
  opposite.
- `app.seed` now honours `SEED_EMAIL` / `SEED_PASSWORD`, so the demo content can
  fill an account that already exists rather than only creating its own. When
  credentials are supplied it prints no password and mints no API token: that
  path is for real servers, and the first run against one put a live password
  in a terminal and left a token that had to be revoked. CodeQL caught the
  password half of it.
- CI now checks `/privacy` and `/support` against the built image. They render
  markdown that is COPYed into the image separately from `app/`, so forgetting
  them is a live App Store listing pointing at a 404.
- **Per-store aisle orders** (`/supermarkets`). A household can save a walking
  order per shop and pick the active one in settings; that order drives the
  shopping-list sort and `GET /aisles`, which is how iOS learns a new order
  without an app update. The emoji vocabulary itself stays central. An order
  saved before an aisle existed gains it at the end, so adding an aisle can
  never invalidate a saved supermarket.
- **❄️ Chilled**, walking between meat & fish and dairy: houmous, fresh dips
  and fresh pasta had no shelf and fell to ❓, or worse, mis-filed under dry
  goods on the bare "pasta" keyword. A data migration re-files ❓ rows with
  those exact names and never touches a tag a person set.
- **Ingredient rename** in both clients. A name is the identity key (every
  write finds-or-creates by its folded form, Q21), so this resolves what the
  typed name folds to rather than PATCHing the column: same row, existing row
  (a merge, which asks first), or a free name (created carrying this row's
  aisle, staple flag and verdict). References follow in every case, so recipes,
  meals and old shops never dangle. No API change; both clients orchestrate the
  lookup, POST and merge the skill already teaches.
- Sorting the ingredient catalogue by aisle or by value verdict, not just name.

### Changed

- **The duplicates dialog lets you pick the keeper** instead of fixing one per
  group: every spelling gets a radio with the suggested keeper pre-selected,
  per-row curation shown, and a warning when the pick isn't the canonical name
  (new writes would quietly recreate the row you just merged away). Each
  catalogue row also gains a manual "merge…" picker for the pairs the finder
  deliberately won't claim (Q21).
- 🥛 is now plain **Dairy**, not "Dairy & eggs". UK stores don't chill eggs.
- The staples check shows only staples, which is what it says on the button.
- The iOS app now defaults to `https://meals.marcuslab.uk` rather than
  `http://localhost:8000`. A public download that opens on a dead localhost URL
  looks broken; the field is still editable, which is the whole point of a
  self-hosted client.
- The login screen says what the server field is for and that self-hosting is
  the supported route, instead of presenting an unexplained URL box.
- Account settings (password, sign-out, deletion) moved out of the Plan tab's
  overflow menu into a Settings tab. App Review expects account deletion to be
  findable, and buried in a plan menu it was findable by neither them nor a user.
- iOS marketing version 0.1 → **1.0**, build 15 → **23** over the course of
  this window, and `current_ios_build` with it. A build can only attach to an
  App Store version record whose version string it matches, which makes every
  0.1 build TestFlight-only forever. Per-build detail is in
  [ios/CHANGELOG.md](ios/CHANGELOG.md); **1.0 carrying build 23 was approved
  2026-08-12** and is on the App Store.
- **Privacy policy** (13 Aug 2026): disclosed that the developer reads the
  aggregated, opt-in usage statistics and crash reports Apple provides through
  App Store Connect and Xcode. Nothing changed in the app: it still ships no
  analytics, crash-reporting or third-party code, and the App Store privacy
  questionnaire answers are unchanged. The policy previously implied the
  developer saw nothing at all, which would have stopped being true the moment
  those free Apple dashboards were opened.

### Fixed

- **The stale-connection 500 that got 1.0 rejected under guideline 2.1(a).**
  The engine was built with no `pool_pre_ping` and no `pool_recycle`, so the
  pool kept Postgres connections the swarm's overlay network had already
  dropped as idle, and the first request after a quiet spell got one. On a
  low-traffic server that is a real category of user, and one morning it was
  App Review. `/healthz` touches no database, so the healthcheck gating the
  zero-downtime rollout stayed green through four of these in 24 hours.
  Postgres only: recycling in-memory SQLite would discard the tests' schema.
  Full diagnosis at
  [ios/CHANGELOG.md](ios/CHANGELOG.md#the-21a-rejection).
- **A successful sign-in no longer spends the brute-force budget.** The other
  half of the same incident: the reviewer's retries after the 500 hit
  `auth_rate_limit_per_minute`, so they locked themselves out of their own
  account purely by trying again. Brute force is a stream of failures, so a
  success now refunds its attempt. Per-IP keying would not have helped, since
  one person retrying ten times exhausts their own bucket however precisely
  you identify them.
- **The app claimed to support iPad.** `TARGETED_DEVICE_FAMILY: "1"` was set at
  the project level in `ios/Meals/project.yml`, but xcodegen writes `"1,2"`
  onto every iOS target and a target setting beats a project one — so every
  build up to and including 16 shipped `UIDeviceFamily = [1, 2]` for a UI never
  designed or tested on an iPad. It surfaced as App Store Connect refusing the
  submission until 13" iPad screenshots were supplied, and it is also why
  validation used to insist on the `~ipad` orientation keys. Build 17 is the
  first that is really iPhone-only.

## 2026-07-25 — multi-household tenancy, account lifecycle

Deployed to `meals.marcuslab.uk`. Migration `d9e4b17c3a86`; the existing
account kept its household, and nothing was migrated between households.

### Added

- **Multiple households on one server** (decision Q19). `POST /auth/register`
  now creates a new, empty household; joining an existing one requires a
  single-use code from `POST /auth/invites`. `household_id` scoping was already
  enforced on every query, which is what made this a small change.
- **Password reset** (Q20): `POST /auth/password-reset` emails a typeable code,
  `POST /auth/password/reset-confirm` redeems it. Reset codes live in
  `auth_tokens` under `kind="reset"` and are barred from authenticating by the
  `deps.AUTHENTICATING_KINDS` allow-list.
- **Account deletion** (Q20): `DELETE /auth/me`. Deleting the last member of a
  household deletes that household's data; deleting anyone else deletes only
  them.
- **Remote MCP with per-caller auth**: streamable HTTP at `/mcp`, forwarding
  each request's own bearer token to the API. The MCP server holds no
  credentials of its own in http mode.
- **Client version gate** (`app/client_gate.py`): `GET /client-config`
  publishes `min_ios_build` and `current_ios_build`; builds below the floor get
  a 426. `/shopping-list*` is permanently exempt so a blocked app can still
  drain its offline queue.
- **Premium vs budget ingredient verdicts** (Q17), a 🧼 Toiletries aisle, recipe
  photos, recipe editing, per-meal scaling, and offline reads for plan and
  recipe views.
- CI (`.github/workflows/ci.yml`): lint, tests, Alembic against real Postgres,
  and a `docker compose` boot with an endpoint smoke test.

### Fixed

- Two Alembic heads that took the API down, plus a test that fails if it
  happens again.
- Orphaned plan rows when a meal was deleted.

### Security

- `REGISTRATION_ENABLED=false` set on the deployment. Invite codes are still
  honoured, so a closed server can still admit the people it chose.
- Relicensed AGPL-3.0 with an `ios/` carve-out for App Store distribution.

## 2026-07-24 — first deployment

`https://meals.marcuslab.uk` went live: swarm stack behind Traefik, Postgres
17, Cloudflare-resolved TLS. Moved to the `zaphod` worker node on 2026-07-25.

### Added

- The whole of F1–F4 and F6: recipe ingestion from schema.org JSON-LD, the
  recipe library, meal-options plans (pools, never calendars), the
  provenance-tracked aisle-sorted shopping list, per-user auth with PATs, the
  MCP server, and the published skill and prompt pack.
- Native SwiftUI iOS app with the offline-first shopping list (decision Q11),
  proven end to end with the API stopped.
