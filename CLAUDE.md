# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
make dev          # full stack in Docker (Postgres + API on :8000, remote MCP on :8100)
make up           # alias of dev — the self-hosting spelling the marketing site advertises
make run          # API locally on SQLite, no Docker, with --reload
make seed         # load demo data through the public API against a running server (idempotent)
make test         # backend (pytest + coverage) then mcp tests — no Docker, no network
make test-fast    # same without coverage
make lint / fmt   # ruff check + format (backend and mcp)
make migration m="add foo"   # autogenerate an Alembic revision
make migrate      # alembic upgrade head
make deploy       # rsync + build + `docker stack deploy` on the homelab swarm
```

Single test / subset (from `backend/` or `mcp/`, both use `uv`):

```bash
cd backend && uv run pytest tests/integration/test_shopping_list.py::test_name -q
```

iOS (needs Xcode + XcodeGen; `Meals.xcodeproj` is generated, never hand-edited):

```bash
make ios-build    # xcodegen generate + xcodebuild for the iPhone simulator
make ios-test     # XCTest suite
make ios-screenshots  # App Store screenshot set: throwaway API + throwaway simulator
make ios-testflight   # archive, export, upload (App Store Connect API key in ~/.appstoreconnect)
```

## Architecture

Monorepo with one API and three clients. **The REST API is the only thing that
touches the database** — the iOS app and any AI use the same endpoints, which
is what keeps the API complete and the views consistent.

| Path | Role |
|---|---|
| `backend/` | FastAPI + async SQLAlchemy 2.0 + Alembic. Postgres in Docker; SQLite for `make run` and all tests |
| `web/` | Web app served by the backend at `/app` (StaticFiles mount in `app/main.py`). No build step: hand-written HTML/CSS + ES modules, same-origin with the API, no external requests |
| `ios/Meals/` | SwiftUI app (Swift 6, strict concurrency). Offline-first shopping list |
| `mcp/` | MCP server: a thin task-level wrapper over the REST API, no DB access. Its own image **and** a path dependency of the backend, which serves it at `/mcp` (`app/mcp_mount.py`) so one container is the whole product |
| `skill/` | `SKILL.md` + `prompt-pack.md` — served live by the backend at `/skill` and `/prompt-pack` |
| `integrations/` | Third-party glue that talks to the public API and ships nothing into the image. `node-red/` drains an Alexa shopping list into the list via `POST /shopping-list/items`. Published as a worked example, so it must stay free of anything host-specific — see its README before adding another |
| `backup/` | The nightly `pg_dump` sidecar (image + scripts) and the restore procedure. Shipped in `docker-compose.yml`, so the reference deployment backs itself up |
| `planning/` | The **decisions log** (`04-open-questions.md`) that code comments cite as Q1–Q23, kept live. The rest is the original plan, kept as history, not a roadmap |
| `docs/` | Public marketing site for **YAMP** (GitHub Pages: hand-written HTML + CSS plus real screenshots from `make ios-screenshots`, no build step, no external requests). Strategy in `planning/06-marketing.md`; public name is YAMP but code identifiers and the `X-Meals-Client` header never change |

Backend layering: `routers/` (HTTP + auth + commit boundaries) → `services/`
(domain logic, session-scoped, `flush` not `commit`) → `models/` (SQLAlchemy).
`serializers.py` is the single model→schema conversion layer; `schemas/` holds
Pydantic I/O only. Auth is `CurrentUser`/`DbSession` annotated dependencies
from `deps.py`; every query filters on `user.household_id`.

Logging is `app/observability.py` and nothing else: stdout only, JSON in
production, one access line per request (its middleware is registered **last**
in `main.py` so it wraps everything, and it is also the last-resort 500
handler — every response carries `X-Request-ID`). Don't add per-request log
lines elsewhere; for the handful of moments worth finding by name
(registration, deletion, ingest outcome…) call `log_event(...)` with ids and
enums as fields — never emails, tokens, URLs, or other personal data, which is
a promise `/privacy` makes. `/healthz` and `/metrics` 2xx/3xx are deliberately
not logged or counted (they exist to be polled).

Metrics are the same story's numbers (`app/metrics.py`): `log_event` already
increments `meals_events_total`, and the request middleware feeds the request
counter/histogram — with route *template* labels only, never raw paths.
`GET /metrics` serves the registry behind `METRICS_TOKEN` (404 when unset;
the endpoint shares the public host). Naming an event is all the
instrumentation a new feature usually needs.

### Domain invariants worth knowing before editing

- **Household scoping.** All data hangs off a `Household` (Q16), and since
  **Q19 a server holds many of them**: `POST /auth/register` creates a new,
  empty household, and joining an existing one needs a single-use invite code
  from `POST /auth/invites`. `household_id` is therefore the *only* thing
  standing between one family's shopping list and another's — a query that
  forgets it is a data breach, not a bug. There are no per-user permissions
  inside a household: everyone in it can do everything to the *food*. Who is in
  it is the lead's, and only that — see the next bullet.
  `REGISTRATION_ENABLED=false` blocks new *households* but still honours
  invites, so a closed server can admit the people it chose.
- **The lead** (Q23, amending Q19). `households.lead_user_id` names the member a
  household is billed to, and they are the only one who may invite, revoke an
  invite, remove a member or rename the household. That is the whole of the
  difference: they have no more power over recipes, plans, lists or ingredients
  than anyone else, and a new feature does **not** get gated on them unless
  money is involved. Every household has exactly one at all times — set at
  registration, handed on with `PATCH /auth/household`, and passed to the
  longest-standing member if a lead deletes their account. **Leaving is not
  gated**: `DELETE /auth/household/members/{id}` with your own id is anyone's,
  and it is the same endpoint the lead uses to remove somebody, because they are
  one act with two callers. Leaving, removal and `POST /auth/invites/redeem` all
  funnel into `move_user_to_household` in `services/accounts.py`, which moves
  `household_id` and collects the vacated household if nobody is left in it —
  only redeeming can reach that branch, which is why only it takes `force`.
- **Account lifecycle** (Q20, `services/accounts.py`). Deleting the last member
  of a household deletes the household's data; deleting anyone else deletes only
  them. The order of deletes is load-bearing — `household_id` columns carry no
  `ondelete`, so it is done explicitly in code rather than left to cascades, and
  it must behave the same on SQLite and Postgres. Password-reset tokens share the
  `auth_tokens` table under `kind="reset"` and **must never authenticate**:
  `deps.AUTHENTICATING_KINDS` is an allow-list, and a reset code hashes to the
  same value the bearer path computes once its separators are stripped.
- **Tests run with SQLite foreign keys ON** (`enforce_sqlite_foreign_keys`).
  SQLite ships with them off, which silently turned every `ondelete` into
  decoration while production enforced them. Don't build a test engine without it.
- **Plans are pools, not calendars** (Q1/Q4). A `Plan` is a labelled set of
  `PlanMeal`s with an optional `slot` ("dinner"). No days, no dates. Don't
  introduce per-day scheduling.
- **The shopping list knows *why*.** `ListItem` identity is
  `(list, ingredient, unit)` and its quantity is the **sum of its
  `ListItemSource` rows** — one per contributing plan-meal/recipe, or
  `plan_meal_id IS NULL` for ad-hoc adds. Adding a meal to a plan merges
  contributions; removing it deletes exactly its own rows and drops the line
  only when no source remains. Edits to a meal or recipe go through
  `resync_meal_contributions`. Never mutate `ListItem.quantity` — it's a
  derived property.
- **Unit convention (Q2), enforced in `services/units.py`.** Everything is
  metric (canonicalised to g/ml) or a count of a singularised natural unit
  ("tin", "clove"). Imperial and spoon/cup units are rejected for API clients
  with the exact conversion in the error; the backend's own JSON-LD ingestion
  converts instead (`INGEST_CONVERSIONS`). Merging only ever happens on an
  exact canonical-unit match.
- **Parse once, reuse forever** (Q3). `source_url` is unique per household and
  is the cache key: re-posting a known URL returns the stored recipe with 200,
  never a duplicate, and never clobbers a recipe with `edited=True`. Ingestion
  is free schema.org JSON-LD extraction only; a page without usable JSON-LD
  returns 422 telling the calling AI to parse it and `POST /recipes` with
  `parse_source="ai"`. **The backend never calls an LLM.**
- **Offline sync contract** (Q11). Clients may supply the `id` on ad-hoc adds;
  `ListItemSource.client_key` makes a replayed POST a no-op (returns 200, not
  201), and item ids are honoured unless already taken. iOS
  `ShoppingListStore` renders *server truth + queued `PendingOp`s*, persists
  both to disk, and replays ops in order — changes there must preserve
  idempotency and id-remapping.
- **Aisle order** (`services/aisles.py`) is the default shopping-list sort
  order and its emoji vocabulary is published in the skill. Keep the two in
  sync. Households can override the *order* (never the vocabulary) per store
  via `/supermarkets` (`services/supermarkets.py`): the active supermarket's
  order drives the list sort and `GET /aisles`, which is how iOS learns it
  without an app change. Orders saved before a new aisle existed gain it at
  the end — adding an aisle must never invalidate a saved supermarket.
- **Premium vs budget** (`services/values.py`, Q17). An ingredient's
  `value_tier` (`premium`/`budget`/`any`, plus a one-line `value_note`) is the
  household's own verdict — unlike an aisle it is **never guessed**, so no
  keyword table. It rides along on list items and recipe lines so it shows up
  at the shelf; the vocabulary is published in the skill.
- **Limits are config, and every one of them defaults to unlimited**
  (`app/limits.py`, `planning/08-freemium.md`). `LIMITS_PROFILE` is `unlimited`
  unless a deployment says otherwise, `households.tier` backfills to
  `unlimited`, and `enforce()` returns before it runs a single query when
  nothing is set — so a self-hosted instance sees no cap, no paywall, and no
  hint that a hosted tier exists. That is the whole feature, and three rules
  keep it that way. **Never bake a number into a default**: if one is tempting
  there, it has crossed the line into fencing off the tool. **Call `enforce`
  from the service layer, next to the insert** — a router holds no number and
  picks no status code; the module decides 402 (a tier cap a bigger tier would
  lift) or 403 (a fair-use ceiling, or a cap the top tier cannot lift), and one
  handler in `main.py` turns either into a response. And **never limit
  `/shopping-list*`**, in any tier: it is exempt from every billing block
  exactly as it is exempt from the client gate, because iOS drops any queued
  `PendingOp` the server refuses (Q11) — a cap there deletes what somebody
  typed in a supermarket. The one limit that is not a `COUNT` is URL ingests,
  which carries a monotonic per-month counter on the household because a count
  of rows would refund the quota every time a recipe was deleted. And a cap has
  to be one a household can get back under: **archived plans are not counted**,
  because there is no `DELETE /plans/{id}` (a plan's cooked history is why), so
  counting them would end the weekly loop at plan 21 with no way back.

### The web client (`web/`)

Ships inside the backend image (repo-root build context, like `skill/`) and is
mounted at `/app` with the same two-place directory lookup; browsers hitting
`/` with no `marketing_url` configured are redirected there. Rules that keep
it boring to operate:

- **No build step, no external requests.** Hand-written CSS + ES modules the
  browser loads directly; recipe `image_url`s are the one external fetch
  (allowed by the CSP in `index.html`). Don't introduce npm, bundlers, or CDN
  scripts.
- **Assets are served `Cache-Control: no-cache`** (`_RevalidatedStaticFiles`)
  because there are no hashed filenames — every load revalidates by ETag, so a
  deploy shows up on the next page load. Don't "optimise" this into long-lived
  caching without adding content hashes.
- It identifies as `X-Meals-Client: web/1.0 (1)`; only `ios/*` is ever gated,
  and the web app deploys *with* the server, so it can never be older than the
  API — no gate, no version ceremony.
- The XSS boundary is the `html` tagged template in `js/dom.js` (everything
  interpolated is escaped unless it is itself a template). Never build DOM
  strings outside it.
- Dialog code must not depend on the `close` *event* — some embedded browsers
  never deliver it; `openDialog` patches `close()` to also remove the element.
- The 4xx `detail` strings the API writes for AI clients are shown verbatim in
  toasts — another reason to keep them human sentences.

### Client/API compatibility

The skill and the MCP server ship with the API, so **iOS is the only client
that can be older than the server**, and once a build is on TestFlight it can't
be recalled. The contract is therefore **additive-only**:

- Never remove or rename a response field — deprecate by leaving it populated.
  Swift `Codable` ignores unknown keys, so *adding* fields is always safe.
- Never add a required request field, and never tighten validation on an
  existing one.
- New behaviour goes behind a new endpoint or a new optional query param.
- Never change the meaning of an existing value in place (re-canonicalising a
  unit, renumbering an enum) — that's the one class of change a tolerant
  decoder can't absorb.
- Keep the iOS models free of `String`-backed enums for server vocabularies
  (aisles, slots). A new aisle must never be a decode error.

`app/client_gate.py` is the escape hatch for the rare change that can't be
additive. The app sends `X-Meals-Client: ios/<version> (<build>)`; builds below
`min_ios_build` get a 426 and a blocking upgrade screen. Two rules:

- **Requests without that header are never gated** — curl, assistants and the
  MCP server must keep working.
- **The offline queue always drains.** `/shopping-list*` is exempt (and must
  stay exempt), because a blocked app that can't flush its queued `PendingOp`s
  destroys the user's data rather than merely showing them a stale UI (Q11).

Raising `MIN_IOS_BUILD` is a deploy-time decision that cuts off every install
below it, so it stays at `0` until a change genuinely forces it; the config
refuses a floor above `current_ios_build`. `GET /client-config` publishes both
numbers and the app checks it at launch and on every foreground.

**Bumping `CFBundleVersion` in `ios/Meals/project.yml` is four steps, not one**
— the full ritual is at the top of [ios/CHANGELOG.md](ios/CHANGELOG.md), which
is also the record of which builds actually reached TestFlight or the App Store.
In short: bump above the highest build *in App Store Connect* (uploads have come
from outside this repo), add a ledger row, upload, then move
`current_ios_build` to match, or nobody is ever told a newer build exists.
`CFBundleShortVersionString` must be **higher than the released App Store
version**, not equal to it: 1.0 is `READY_FOR_SALE`, so its pre-release train
is closed and an upload carrying 1.0 is a 409. Build 24 opened the `1.1` train,
which the upload itself created — no version record needed until something is
actually submitted for review. (Builds 1–15 went up as `0.1` and can never
attach to any of it.)

### AI-facing surfaces

Error strings, endpoint docstrings and MCP tool descriptions end up in an
agent's context — they are part of the product. Every 4xx should say what to
do instead (see the 409 in `routers/shopping.py` for the shape).

The MCP server (`mcp/meals_mcp/server.py`) runs in two modes: **stdio** with
`MEALS_API_TOKEN` from the env, and **http** (deployed at `/mcp`) which holds
no credentials and forwards each request's `Authorization` header to the API
verbatim — never add a server-side token fallback in http mode. That header
reaches the tools through the `_capture_caller_headers` middleware, which
publishes them in a contextvar for the life of the request: SDK 2.0 only hands
the request context to a handler that declares a `Context` parameter, and the
alternative is threading one through all 29 tools and their helpers. Keep the
middleware registered — without it every remote call silently drops to the
stdio env-token path.

http mode is served in **two places from one codebase**: its own container
(what the swarm routes `/mcp` to) and, since it is a path dependency of the
backend, the API process itself via `app/mcp_mount.py`. That second one is
what lets a single container be the whole product on hosts that only give you
one, so the constraints it adds are load-bearing:

- It is a Starlette `Route`, not `app.mount()`: mounting redirects `/mcp` to
  `/mcp/`, and a 307 on a POST is not something every MCP client follows.
- The session manager is entered once, in the API's lifespan, and the SDK
  refuses a second `run()`. Tests skip lifespan, so a test that reaches `/mcp`
  enters `mcp_mount.running()` itself and gets exactly one pass.
- The mounted server still calls the API **over HTTP** (loopback,
  `MCP_API_URL`), like any other client. It must never reach for the database
  or a service function; that boundary is the reason one wrapper serves both
  deployments.

`skill/` is shipped inside the backend image (hence `docker build` uses the
**repo root** as context, not `backend/` — the same reason `web/` and now
`mcp/` are in it) and served unauthenticated with
`{{API_URL}}` substituted from the request's forwarded-proto/host headers.
Keep `SKILL.md`, `prompt-pack.md` and the API in step when endpoints change.

`PRIVACY.md` and `SUPPORT.md` ship the same way and render at `/privacy` and
`/support` (`routers/pages.py`). **These are the App Store's privacy and support
URLs**, so a build that fails to COPY them takes down a live store listing —
which is why CI curls both against the built image. They're also exempt from the
client gate: someone stuck on the upgrade screen is exactly who needs them.

## Testing

`backend/tests/conftest.py` pins `DATABASE_URL` to in-memory SQLite and
disables rate limiting **before importing any app module** — keep new imports
below that block. Tests drive the ASGI app through `httpx.AsyncClient`
(`auth_client` is pre-registered); use the shared builders in conftest
(`create_recipe`, `create_meal`, `create_plan`, `get_list`) rather than
hand-rolled payloads. `asyncio_mode = "auto"`, so no `@pytest.mark.asyncio`.
External HTTP is stubbed with `respx` — tests never hit the network.

Model changes need an Alembic revision (`make migration m="..."`); tests
create tables from metadata and won't catch a missing migration.

`.github/workflows/ci.yml` runs `make lint` and `make test` on every push and
PR, plus the two things the local suite can't see: `alembic upgrade head` +
`alembic check` against real Postgres (the missing-migration and
Postgres-vs-SQLite gap above), and a `docker compose` smoke that boots both
images and repeats the deploy's checks — `/healthz`, `/client-config`,
`/skill` (asserting `{{API_URL}}` was substituted and the skill shipped in the
image), `/prompt-pack`, an MCP initialize handshake, then `app.seed` end to
end. There is no iOS job: macOS runners are billed per minute, so
`make ios-build` / `make ios-test` stay local.

## Deployment

Docker Swarm behind Traefik on `meals.marcuslab.uk` (api + mcp + Postgres).

**`deploy/` is gitignored and local-only** — it describes one specific set of
machines, so it's deliberately not in the public repo. It's on this machine and
backed up under `~/meals-local-deploy/`. Don't re-add it to git; if the deploy
needs changing, change it in place. `docker-compose.yml` is the public reference
deployment and the one CI boots.

Because it is untracked, `deploy/` is absent from every git worktree. `make
deploy` falls back to the main worktree's script and hands it the current tree
via `MEALS_REPO_ROOT`, which matters only in build mode below. Keep those two
in step if either moves.

**`deploy/deploy.sh` has two modes, and the default is the registry one:**

- **registry (default).** Pulls `ghcr.io/marco308/meals{,-mcp,-backup}:$MEALS_VERSION`
  (default `latest`) on zaphod, reads back each image's digest, and deploys
  *those digests*. Nothing is built, and the only thing synced is the stack
  file. The digest is what fixes the oldest wart here: it is part of the
  service spec, so `docker stack deploy` sees a real change and rolls it out
  by itself — no `service update --force`, and a deploy that changes nothing
  correctly does nothing. The script then asserts each service is running the
  digest it asked for, so "reported success and rolled nothing" is now a
  failure rather than a thing to notice later. It can only ship released code.
  **Verification waits for the rollout to converge** (`UpdateStatus.State`) and
  then checks the live `api_version` against the release it deployed: with
  start-first the outgoing container keeps serving, so checking early tests the
  image being replaced. The first digest deploy did exactly that and printed
  the old version while calling itself a pass.
- **build** (`MEALS_DEPLOY_BUILD=1`). The old path: rsync this tree to the
  node, build all three images there, force the services. Keep it. It is the
  only way to ship a branch that has no tag, which is exactly what you want
  when a fix is being proven before it is released.

Both then verify `/healthz`, `/client-config`, `/skill`, `/prompt-pack`, the
privacy and support pages, the web app and an MCP initialize handshake through
Traefik. In that script, keep every check a bare command — `curl … && echo` is
exempt from `set -e`.

The three images the stack runs are published by the release workflow, so
**a new service in the stack needs a new image in that matrix**, not a
`docker build` on the node.

**Rollouts are zero-downtime and it takes two settings, not one** (both in the
gitignored stack file, which is why they're restated here). api and mcp run one
replica each, and `make deploy` used to drop every request for a few seconds.
It needed:

1. `order: start-first` on api and mcp. Swarm's stop-first default killed the
   only task before starting its replacement.
2. `traefik.docker.lbswarm=true` on both. With start-first alone the deploy
   still failed requests for ~9.5s, because Traefik was picking task IPs
   itself: swarm reports a container Running the moment it starts, but api
   waits for Postgres and runs `alembic upgrade head` before uvicorn binds, so
   Traefik sent traffic to a port nothing was listening on (502) and to the
   retired task's overlay address after it vanished (hangs until timeout).
   Routing to the swarm VIP instead means ingress only reaches tasks that pass
   their healthcheck. Traefik here is v2; v3 renames the label to
   `traefik.swarm.lbswarm`.

Verified by polling `/healthz` every 200ms through Traefik across a full
`make deploy`: 468/468 requests 200, worst response 366ms.

Two things follow from the fix and they constrain what you may deploy:

- The container healthcheck is now the cutover gate *and* what admits a task
  to the VIP, so its interval bounds the rollout. It's 5s with a generous
  `start_period`, not the 30s Docker suggests. Don't raise it back.
- Old and new tasks overlap for a few seconds, and the new one runs
  `alembic upgrade head` **while the old code is still serving**. Additive
  migrations are safe; one that drops or renames something the old code still
  reads will break the outgoing task for that window, so split it
  expand/contract across two deploys. This is the same additive-only
  discipline the iOS client contract already demands, now applying to the
  schema during a rollout.

Postgres stays stop-first on purpose — one replica on one local volume can't
have two tasks at once.

### Releases

`git push origin v1.2.3` is the ritual, and the **version in the code has to
match the tag first** (`backend/pyproject.toml`, `mcp/pyproject.toml` and the
FastAPI `version=` in `backend/app/main.py`). A release job checks all three
and fails the release if any disagrees, because v1.0.1 shipped an API that
reported 1.0.0 and the deploy compares the live `api_version` against the
release it deployed. After that, `.github/workflows/release.yml`
builds all three images for amd64 and arm64, pushes them to GHCR
(`ghcr.io/marco308/meals` = the product, `…/meals-mcp` = the MCP server alone,
`…/meals-backup` = the pg_dump sidecar the swarm runs), runs the published API
image with **no arguments and no database service** to prove the one-container
story still holds, then cuts the GitHub release. A
failure in that verify job means the tag is public but the release is not, so
fix forward with a new tag rather than deleting one people may have pulled.

Both packages came out **public** and anonymously pullable, inherited from the
repo (checked against the GHCR API on the 1.0.0 tag, `linux/amd64` and
`linux/arm64`, tags `1.0.0`, `1.0` and `latest`). That is worth re-checking if
a release ever publishes under a different account or a private repo, because
package visibility is a package setting with no REST endpoint behind it: the
fix would be a manual flip in the package's settings. The release's verify job
logs in to GHCR so it passes either way.

Two properties of the image are part of the contract now, because a host may
depend on either and neither is visible from the API:

- **It runs as uid 1000** and writes to exactly one path, `/data`. A bind mount
  has to be `chown 1000:1000` first (named volumes inherit the ownership from
  the image). Don't add a step that writes anywhere else at runtime.
- **It defaults to SQLite under `/data`**, so `docker run` with nothing set
  works. `DATABASE_URL` overrides it, which is what every Postgres deployment
  here does. Migrations run on boot on both engines, so a migration that is
  Postgres-only breaks the default install rather than just CI.

### Backups

`backup/` is a sidecar in the same stack: nightly `pg_dump -Fc`, each dump read
back with `pg_restore --list` before it counts, 7 daily + 4 weekly, and a gpg
AES-256 copy pushed off the node with rclone (Google Drive here). Its one event
line per run is `logger=meals.backup` in the same JSON shape as
`app/observability.py` — that is what the freshness alert watches, so don't
change its `outcome=ok|error` fields without moving the alert with it.

Two rules constrain anything built on top:

- **Restore whole, never piecemeal.** `household_id` columns carry no
  `ondelete` and the deletion order lives in `services/accounts.py` (Q20), so
  cherry-picking tables out of a dump can leave rows the app would never have
  produced. `restore.sh` restores into a scratch database for exactly this
  reason; copy out of that if you need surgery.
- **The image tracks the db image.** `pg_dump` refuses to dump a server newer
  than itself, so `backup/Dockerfile`'s `FROM postgres:17-alpine` and the `db`
  service's tag move together.

Open items and deferred work live in `BACKLOG.md`.
