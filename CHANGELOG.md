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

Nothing merged since the last deploy.

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
