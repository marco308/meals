# Meals

[![CI](https://github.com/marco308/meals/actions/workflows/ci.yml/badge.svg)](https://github.com/marco308/meals/actions/workflows/ci.yml)

A meal **options** planner (not a rigid Mon–Sun grid) with a recipe library and
an aisle-sorted shopping list, exposed through an AI-friendly API so anyone can
drive it with their own AI assistant. POC implementation of the plan in
[`planning/`](planning/).

## What it does

- **Recipes** — submit a URL once and it's parsed (free, from the page's
  schema.org JSON-LD) and cached forever; pages without structured data are
  handed to *your* AI to parse and submit back. Manual family recipes work too,
  and anything can be deleted from the app.
- **Meals** — the unit of planning: recipes + loose ingredients ("cottage pie
  *with peas and carrots*" needs no carrot recipe). Editable in place — add a
  recipe, drop a side — and the shopping list follows the change without
  un-ticking what's already in the trolley.
- **Cooked history** — marking a meal cooked is recorded permanently, per
  recipe as well as per meal, and survives deleting the plan or the meal. The
  library shows "cooked 12× · last May 2026" and sorts by *most cooked* or
  *not had in a while*.
- **Plans** — weekly-ish pools of meal options grouped by slot (dinners,
  lunches). No days, no dates: an unexpected trip breaks nothing.
- **Shopping list** — auto-populated from the plan with full provenance (every
  item knows which meals need it), exact-unit merging, ad-hoc items, staples
  check, "already have it", and store-walking aisle order (🥬 → 🍞 → 🥩 → …).
- **AI access layer** — the headline: a documented REST API, an MCP server
  with 29 task-level tools, and a skill/prompt pack the server publishes
  itself at `/skill` + `/prompt-pack`. The app ships **no built-in LLM** —
  bring your own.
- **Households** — a household is one recipe library, plan and shopping list,
  and it's the whole authorisation boundary. **Registering creates a household
  of your own**; the people you cook with join it with a single-use invite code
  (`XXXX-XXXX-XXXX`, short enough to read off one phone and type into another),
  either when they sign up or from inside an account they already have.
  Everyone in a household can do everything *to the food* — there is no
  read-only member and no per-recipe permission. The one exception is the guest
  list: each household has a **lead** (the member it would be billed to) who
  invites and removes people. Leaving is always your own to do, and nobody is
  deleted by being removed — they land in an empty household of their own,
  account and tokens intact.
- **Auth** — real per-user accounts (bcrypt + opaque bearer tokens), plus
  per-user API tokens (PATs) for AI clients. Users can change their own password
  (`POST /auth/password`, or from the app's menu): it revokes every session token
  and issues a fresh one, so the device doing the change stays logged in and the
  others don't. **Forgotten** passwords reset by emailed code, and accounts can be
  **deleted** from inside the app — the last member of a household takes its data
  with them, anyone else takes only themselves.

## Quickstart

```bash
make dev     # full stack in Docker: Postgres + API on http://localhost:8000
make seed    # demo user, recipes, a plan, and a ready-made shopping list
```

The **web app** is at <http://localhost:8000/app/> (a browser at the bare
address is redirected there). Interactive API docs: <http://localhost:8000/docs>. The seed's demo account is
`demo@example.com` / `demo-password-123`, and it prints an API token you can
use immediately with `curl` or the MCP server. (The password lives here rather
than in the seed's output: `make seed` also fills real accounts on real servers
— `SEED_EMAIL` / `SEED_PASSWORD` — and a password echoed to a terminal outlives
the run.)

No Docker? `make run` starts the API locally on SQLite (zero services), and
`make test` runs the whole suite the same way.

```
make help    # everything else: logs, lint, migrate, fmt, down, nuke…
```

### One container, no checkout

Every release publishes an image that is the whole product: API, web client,
the published skill, and the MCP endpoint at `/mcp`, defaulting to SQLite
under `/data`. It runs as uid 1000 and needs no arguments, which is what makes
it work on hosts that give you exactly one container:

```bash
docker run -d -p 8000:8000 -v yamp-data:/data ghcr.io/marco308/meals:latest
```

Point `DATABASE_URL` at Postgres when you outgrow that
(`postgresql+asyncpg://user:pass@host:5432/meals`); the schema migrates itself
on boot either way. With a *bind* mount rather than a named volume, `chown
1000:1000` the directory first, since Docker only copies ownership into empty
named volumes. `ghcr.io/marco308/meals-mcp` is the MCP server on its own, for
deployments that want it on a separate host.

### Sending email (optional)

Only password reset sends any, and it's plain SMTP so any relay works. Leave it
unset and `POST /auth/password-reset` returns a 503 explaining what's missing;
everything else, including changing a password you *know*, works without it.
`GET /client-config` reports which of the two you are as `password_reset_enabled`,
so a client can hide the option rather than offer a door that doesn't open.

```bash
SMTP_HOST=smtp.example.com
SMTP_PORT=587           # default
SMTP_FROM=meals@example.com
SMTP_USERNAME=...       # if your relay authenticates
SMTP_PASSWORD=...
SMTP_START_TLS=true     # default
PASSWORD_RESET_TTL_MINUTES=30   # default
```

Two relays that need no server of your own. **Resend** — verify your domain,
then the username is literally `resend` and the password is the API key:

```bash
SMTP_HOST=smtp.resend.com
SMTP_USERNAME=resend
SMTP_PASSWORD=re_...
SMTP_FROM=meals@your-domain
```

**Gmail** — needs 2FA, then an app password from Google Account → Security → App
passwords. `SMTP_FROM` must be the account itself; Google rejects any other From.

```bash
SMTP_HOST=smtp.gmail.com
SMTP_USERNAME=you@gmail.com
SMTP_PASSWORD=<16-character app password>
SMTP_FROM=you@gmail.com
```

### Logs

Everything goes to stdout, one line per record: an access line per request
(with a request id, echoed as `X-Request-ID` and quoted in any 500), and named
events for the moments worth finding later — registrations, deletions, ingest
outcomes. In production (`ENVIRONMENT=production`) lines are JSON so `docker
logs` pipes straight into a log shipper; anywhere else they're plain text.
`LOG_FORMAT=json|text` overrides, `LOG_LEVEL=INFO` is the default. Logs carry
ids, never emails, tokens, or what anyone is cooking.

### Metrics

Set `METRICS_TOKEN` and `GET /metrics` serves Prometheus text format to
`Authorization: Bearer <that token>`; unset (the default), the endpoint 404s
and no metrics work runs at all. You get request counts and latency
histograms by route template, a counter per named event from the log, process
metrics, and slow-moving usage gauges (households, users, recipes) refreshed
once a minute. Scrapes and healthchecks don't count themselves, so a quiet
family server's graphs show the family, not the monitoring.

### Backups

The stack backs itself up: a sidecar takes a nightly `pg_dump`, checks it can
be read back before counting it, and keeps 7 daily and 4 weekly. Set
`RCLONE_REMOTE` and a passphrase and it also puts a gpg-encrypted copy wherever
[rclone](https://rclone.org) can reach — Drive, S3, another box in the house —
because a dump next to the database survives `DROP TABLE` and nothing else.

```bash
docker compose exec backup backup.sh                                   # now, not tonight
docker compose exec backup restore.sh --target meals_check --drop latest   # prove it
```

The container goes unhealthy if the newest dump ages past 36h, and CI restores
a dump into a scratch database on every push, because an untested backup is a
hypothesis. Details, and how to actually recover, in
[`backup/README.md`](backup/README.md).

## Repo layout

| Directory | Contents |
|---|---|
| [`backend/`](backend/) | FastAPI + async SQLAlchemy + Alembic. Postgres in Docker, SQLite for local/tests |
| [`web/`](web/) | Web app, served by the API itself at `/app` — the big-screen client. Plain HTML/CSS/ES modules, no build step |
| [`ios/`](ios/) | Native SwiftUI iPhone app: plan, recipe library + URL ingest, and an **offline-first shopping list** |
| [`mcp/`](mcp/) | MCP server wrapping the API with task-level tools (`ingest_recipe`, `get_shopping_list`, `check_off`, …) |
| [`skill/`](skill/) | The AI playbook: `SKILL.md` (Claude-family Agent Skill) + `prompt-pack.md` (portable, any assistant) — served live at `/skill` + `/prompt-pack` |
| [`backup/`](backup/) | The nightly `pg_dump` sidecar, and the restore script you want to have rehearsed |
| [`planning/`](planning/) | Product plan and decisions log this POC implements |

### Web app

The big-screen client, served by the API itself at `/app` — same origin as the
endpoints it calls, so there's no second host, no CORS, nothing extra to
deploy: if the server is up, the web app is too. Plain HTML/CSS/ES modules
with no build step and no external requests, in the same Sunday-market skin as
the site (dark mode included). It covers the whole loop: the plan, the
shopping list (staples check, "already have it", finish-the-shop), recipe
library with URL ingest, meals, the ingredient catalogue (aisles, staples,
⭐/💷 verdicts, duplicate merge — the tidy-up screens the phone doesn't have),
and settings (invites, API tokens, password, account deletion).

### iOS app

`make ios-build` / `make ios-test` (needs Xcode + [XcodeGen](https://github.com/yonaskolb/XcodeGen)),
or open `ios/Meals/Meals.xcodeproj` after running `xcodegen generate` there.
The app ships pointing at `https://meals.marcuslab.uk`; put your own address in
the Server field on the sign-in screen, or launch the simulator build with
`-serverURL http://localhost:8000` and use the seed's demo account. Check-offs
and quick adds work with no signal — the hard requirement from decision Q11:
interactions render instantly from a cached list, queue to disk, survive
relaunch, and replay in order (with idempotent client ids and id-remapping for
server-side merges) when connectivity returns.

`make ios-screenshots` regenerates the App Store screenshot set against a
throwaway seeded API and a throwaway simulator
([how](ios/screenshots/README.md)). What's shipped, and what is only built,
is tracked in [ios/CHANGELOG.md](ios/CHANGELOG.md); submission material lives
in [ios/AppStore/](ios/AppStore/).

## Trying the AI layer

> **`meals.marcuslab.uk` is my household's private instance, not a free public
> service.** Registration on it is closed. Self-hosting is the supported way to
> use this — it's AGPL, it costs nothing, and `make dev` gets you the whole
> stack. If you'd rather I hosted it for you, that's a paid arrangement: open an
> issue and ask. The `/skill` and `/prompt-pack` endpoints stay open to
> everyone, because they're documentation.

The MCP server ships with the deployment — any MCP-capable assistant connects
by URL, no local Python or repo checkout. **The API serves it at `/mcp` on its
own origin**, so a single container is enough; the separate `mcp` image exists
for deployments that would rather run it apart (this one does, see
[`docker-compose.yml`](docker-compose.yml)), and `MCP_ENABLED=false` turns the
built-in one off. Either way it holds no credentials.

Create a personal API token (`POST /auth/tokens`, or use the seed's) and send
it as a bearer header:

```bash
claude mcp add --transport http meals https://your-meals-server.example/mcp \
  --header "Authorization: Bearer meals_…"
```

The remote server holds no credentials of its own: each request's bearer
token is forwarded to the API, so every connecting client acts as themselves.
Any MCP client that can send a custom header works the same way (claude.ai
custom connectors need OAuth, which the server doesn't speak yet — see
[BACKLOG.md](BACKLOG.md)).

**Local fallback (stdio)** — no deployment needed, runs from the repo:

```json
{
  "mcpServers": {
    "meals": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/meals/mcp", "python", "-m", "meals_mcp.server"],
      "env": {
        "MEALS_API_URL": "http://localhost:8000",
        "MEALS_API_TOKEN": "meals_…"
      }
    }
  }
}
```

Running it as its own service: `MEALS_MCP_TRANSPORT=http` serves streamable
HTTP at `/mcp` on `0.0.0.0:8000` (`make dev` exposes that container on
`http://localhost:8100/mcp`, and the same endpoint from the API itself on
`http://localhost:8000/mcp`).

### The skill & prompt pack

The server publishes its own operating manual — grab it from the deployment,
not a repo checkout, so it always matches the endpoints it describes:

- **`/skill`** — `SKILL.md`, installable as a Claude-family Agent Skill.
- **`/prompt-pack`** — portable instructions for any assistant, served with
  that deployment's base URL already filled in: paste into custom instructions,
  add your API token, and the REST API alone is enough (no MCP needed).

Both are unauthenticated on any instance, so you can read mine to see the shape
of them — <https://meals.marcuslab.uk/skill> and
<https://meals.marcuslab.uk/prompt-pack> — but point your assistant at your own
server, since the pack embeds the base URL it was served from.

Both are unauthenticated, ship inside the backend image, and are advertised
from the API root (`GET /` returns a JSON landing for non-browser clients).
The repo copies in [`skill/`](skill/) are the sources.

**Version stamp.** An installed skill or a pasted prompt pack is a snapshot
that never updates itself, so both carry a `<!-- playbook-version: N -->` marker
and the live surfaces publish the current number: `GET /skill/version` (also on
the root landing as `playbook_version`) and the MCP server's connection
instructions, which every client re-reads on connect. An assistant that sees a
higher number than its own copy knows to re-fetch and say so.

When the playbook's guidance changes, bump all four together — the stamps in
[`skill/SKILL.md`](skill/SKILL.md) and [`skill/prompt-pack.md`](skill/prompt-pack.md)
(the `<!-- playbook-version: N -->` marker *and* the "playbook vN" line each file
states in its prose), `PLAYBOOK_VERSION` in
[`mcp/meals_mcp/server.py`](mcp/meals_mcp/server.py), and the version + content
digest pinned in
[`backend/tests/integration/test_misc.py`](backend/tests/integration/test_misc.py).

Tests fail if they drift — and the pinned digest is what makes that mean
something. A stamp only helps if it moves when the guidance does: without the
pin, new tools and new advice can ship under an unchanged number, so a stale
copy compares v1 to v1, sees no drift, and never learns what it is missing. The
digest hashes both documents with the version references normalised out, so
editing what the playbook *says* fails
`test_guidance_changes_are_announced_by_a_version_bump` until the version is
bumped; the failure message prints the new digest to paste in.

### The quantity convention (decision Q2)

Every quantity is **metric** (g/kg/ml/l) or a **count of a natural unit**
("2 tins", "3 cloves"). No cups/oz/tbsp — clients convert first, and the API
rejects violations with the exact conversion to apply. The backend merges
exact-matching canonical units only.

## Tests

```bash
make test      # 620 backend tests (97% coverage) + 62 mcp tests — no Docker, no network
make ios-test  # 141 XCTest tests: API decoding against captured fixtures, the offline sync engine, error mapping
```

The suite covers the unit convention, JSON-LD extraction (incl. `@graph`,
HowToSections, malformed scripts), ingredient-line parsing, auth + PATs +
rate limiting, password change + session revocation, recipe caching semantics,
and the full shopping-list engine
(merging, provenance, decrement-on-removal, ad-hoc survival, staples,
check-off/uncheck, archive, resync on meal/recipe edits).

### CI

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every push to
`main` and every PR: `make lint`, `make test`, then the two things the local
suite can't see — `alembic upgrade head` + `alembic check` against real
Postgres (the suite builds its schema from metadata, so a missing or
SQLite-only migration is invisible to it), and a full `docker compose` smoke
that boots both images and re-runs the deploy's own checks (`/healthz`,
`/client-config`, `/skill` with `{{API_URL}}` substituted, `/prompt-pack`, an
MCP initialize handshake) before seeding demo data through the public API.

There is no iOS job — macOS runners are billed per minute even on public
repos, so `make ios-build` / `make ios-test` stay local for now.

## Deployment notes

The stack is an API container + a remote-MCP container + Postgres, deployed here
on Docker Swarm behind Traefik with Let's Encrypt (see
[`docker-compose.yml`](docker-compose.yml) for the shape). The MCP container is
routed at `/mcp` on the same host and
authenticates nothing itself — it forwards each caller's bearer token to the
API, which stays the single auth gate. Being internet-facing, auth is
mandatory everywhere except `/healthz`, and auth endpoints are rate-limited.
Registering creates a *new* household, so an open server never exposes an
existing one; `REGISTRATION_ENABLED=false` additionally stops new households
being created while still honouring invite codes, so closing a server doesn't
lock out your own family.

Per-household limits exist and are **off**: `LIMITS_PROFILE` is `unlimited`
unless you change it, every household's `tier` is `unlimited`, and the
enforcement returns before it runs a query, so an install that sets nothing has
no caps and no idea any exist. If you *want* them — a server you let friends
onto, a box you'd rather not have somebody fill — `LIMITS_PROFILE=hosted` runs a
published table of numbers, `LIMITS_OVERRIDES` tunes any single one as JSON, and
`DEFAULT_HOUSEHOLD_TIER` says what a new registration starts on. Being over a
limit blocks only the writes that grow a household; nothing is deleted, nothing
becomes unreadable, and the shopping list is exempt in every case because the
iPhone app drains its offline queue through it.

`make deploy` runs `deploy/deploy.sh`, which is **not in this repo** — a swarm
stack file is a description of somebody's specific hardware (node names, ingress
labels, host aliases, which box has the free disk), so it's kept out and
gitignored. [`docker-compose.yml`](docker-compose.yml) is the honest reference
for the shape of the deployment, and it's what CI boots and smoke-tests.

Being untracked, `deploy/` only exists in the checkout you put it in, so from a
`git worktree` there is nothing to run. `make deploy` therefore falls back to
the main worktree's copy of the script and passes the *current* tree as
`MEALS_REPO_ROOT`, which matters when that script is building rather than
pulling.

That deployment pulls the released images and deploys them **by digest**, which
is worth copying if you run something similar:

- **Deploy a digest, not a tag.** A rebuilt `:latest` leaves the service spec
  identical, so `docker stack deploy` reports success and rolls nothing. Resolve
  the tag to `repo@sha256:…` and the spec changes exactly when the image does.
- **Pull on the node that will run it,** before deploying. Locally built images
  have no registry to pull from at all, so a task only starts where the image
  already is; pre-pulling also means a start-first rollout has nothing to wait
  for.
- **Then check what is actually running** (`docker service inspect … Image`)
  rather than trusting that the deploy did anything.

## Contributing

Issues and pull requests are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).
The [decisions log](planning/04-open-questions.md) records why things are the way
they are (Q1–Q19), and code comments cite those numbers. Vulnerabilities go
through [SECURITY.md](SECURITY.md), not a public issue.

## Licence

Copyright © 2026 Marcus Williams.

- **Everything except `ios/`** — [GNU AGPL-3.0](LICENSE). Self-host it, modify it,
  run it for your household, free and without asking; if you run a modified
  version as a network service, your users are entitled to its source.
- **`ios/`** — [source-available, not open source](ios/LICENSE). You may read it,
  build it and run it on your own devices, and contribute back, but not
  redistribute it or ship it to an app store.

The carve-out exists because the App Store's terms and the GPL family conflict,
which is what makes an AGPL iOS app undistributable there. Keeping `ios/` under
its own licence is what lets the same code be published here and shipped through
TestFlight. Contributions to `ios/` therefore need a CLA; everything else doesn't.
