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
| `mcp/` | MCP server: a thin task-level wrapper over the REST API, no DB access |
| `skill/` | `SKILL.md` + `prompt-pack.md` — served live by the backend at `/skill` and `/prompt-pack` |
| `planning/` | The **decisions log** (`04-open-questions.md`) that code comments cite as Q1–Q22, kept live. The rest is the original plan, kept as history, not a roadmap |
| `docs/` | Public marketing site for **YAMP** (GitHub Pages: hand-written HTML + CSS plus real screenshots from `make ios-screenshots`, no build step, no external requests). Strategy in `planning/06-marketing.md`; public name is YAMP but code identifiers and the `X-Meals-Client` header never change |

Backend layering: `routers/` (HTTP + auth + commit boundaries) → `services/`
(domain logic, session-scoped, `flush` not `commit`) → `models/` (SQLAlchemy).
`serializers.py` is the single model→schema conversion layer; `schemas/` holds
Pydantic I/O only. Auth is `CurrentUser`/`DbSession` annotated dependencies
from `deps.py`; every query filters on `user.household_id`.

### Domain invariants worth knowing before editing

- **Household scoping.** All data hangs off a `Household` (Q16), and since
  **Q19 a server holds many of them**: `POST /auth/register` creates a new,
  empty household, and joining an existing one needs a single-use invite code
  from `POST /auth/invites`. `household_id` is therefore the *only* thing
  standing between one family's shopping list and another's — a query that
  forgets it is a data breach, not a bug. There are no roles or permissions
  inside a household: everyone in it can do everything.
  `REGISTRATION_ENABLED=false` blocks new *households* but still honours
  invites, so a closed server can admit the people it chose.
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
`CFBundleShortVersionString` is `1.0` and must match the App Store version
record — builds 1–15 went up as `0.1` and can never attach to it.

### AI-facing surfaces

Error strings, endpoint docstrings and MCP tool descriptions end up in an
agent's context — they are part of the product. Every 4xx should say what to
do instead (see the 409 in `routers/shopping.py` for the shape).

The MCP server (`mcp/meals_mcp/server.py`) runs in two modes: **stdio** with
`MEALS_API_TOKEN` from the env, and **http** (deployed at `/mcp`) which holds
no credentials and forwards each request's `Authorization` header to the API
verbatim — never add a server-side token fallback in http mode.

`skill/` is shipped inside the backend image (hence `docker build` uses the
**repo root** as context, not `backend/`) and served unauthenticated with
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
via `MEALS_REPO_ROOT` — so a deploy from a worktree ships *that* branch, not
main. Keep those two in step if either moves.

`deploy/deploy.sh` syncs sources to the swarm manager, builds images on the node
that will run them (no registry, so a task only starts where its image already
exists), forces a service update (locally built `:latest` tags don't roll out
otherwise), and verifies `/healthz`, `/skill`, `/prompt-pack` and an MCP
initialize handshake through Traefik. In that script, keep every check a bare
command — `curl … && echo` is exempt from `set -e`.

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

Open items and deferred work live in `BACKLOG.md`.
