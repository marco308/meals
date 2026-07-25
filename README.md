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
  with 21 task-level tools, and a skill/prompt pack the server publishes
  itself at `/skill` + `/prompt-pack`. The app ships **no built-in LLM** —
  bring your own.
- **Households** — a household is one recipe library, plan and shopping list,
  and it's the whole authorisation boundary. **Registering creates a household
  of your own**; the people you cook with join it with a single-use invite code
  (`XXXX-XXXX-XXXX`, short enough to read off one phone and type into another).
  Everyone in a household can do everything in it — being invited *is* the
  permission model.
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

Interactive API docs: <http://localhost:8000/docs>. The seed prints demo
credentials and an API token you can immediately use with `curl` or the MCP
server.

No Docker? `make run` starts the API locally on SQLite (zero services), and
`make test` runs the whole suite the same way.

```
make help    # everything else: logs, lint, migrate, fmt, down, nuke…
```

### Sending email (optional)

Only password reset sends any, and it's plain SMTP so any relay works. Leave it
unset and `POST /auth/password-reset` returns a 503 explaining what's missing;
everything else, including changing a password you *know*, works without it.

```bash
SMTP_HOST=smtp.example.com
SMTP_PORT=587           # default
SMTP_FROM=meals@example.com
SMTP_USERNAME=...       # if your relay authenticates
SMTP_PASSWORD=...
SMTP_START_TLS=true     # default
PASSWORD_RESET_TTL_MINUTES=30   # default
```

## Repo layout

| Directory | Contents |
|---|---|
| [`backend/`](backend/) | FastAPI + async SQLAlchemy + Alembic. Postgres in Docker, SQLite for local/tests |
| [`ios/`](ios/) | Native SwiftUI iPhone app: plan, recipe library + URL ingest, and an **offline-first shopping list** |
| [`mcp/`](mcp/) | MCP server wrapping the API with task-level tools (`ingest_recipe`, `get_shopping_list`, `check_off`, …) |
| [`skill/`](skill/) | The AI playbook: `SKILL.md` (Claude-family Agent Skill) + `prompt-pack.md` (portable, any assistant) — served live at `/skill` + `/prompt-pack` |
| [`planning/`](planning/) | Product plan and decisions log this POC implements |

### iOS app

`make ios-build` / `make ios-test` (needs Xcode + [XcodeGen](https://github.com/yonaskolb/XcodeGen)),
or open `ios/Meals/Meals.xcodeproj` after running `xcodegen generate` there.
Log in with the seed's demo account against `http://localhost:8000` (editable
on the login screen). Check-offs and quick adds work with no signal — the hard
requirement from decision Q11: interactions render instantly from a cached
list, queue to disk, survive relaunch, and replay in order (with idempotent
client ids and id-remapping for server-side merges) when connectivity returns.

## Trying the AI layer

> **`meals.marcuslab.uk` is my household's private instance, not a free public
> service.** Registration on it is closed. Self-hosting is the supported way to
> use this — it's AGPL, it costs nothing, and `make dev` gets you the whole
> stack. If you'd rather I hosted it for you, that's a paid arrangement: open an
> issue and ask. The `/skill` and `/prompt-pack` endpoints stay open to
> everyone, because they're documentation.

The MCP server ships with the deployment — any MCP-capable assistant connects
by URL, no local Python or repo checkout. Create a personal API token
(`POST /auth/tokens`, or use the seed's) and send it as a bearer header:

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

Self-hosting the remote mode: `MEALS_MCP_TRANSPORT=http` serves streamable
HTTP at `/mcp` on `0.0.0.0:8000` (`make dev` exposes it on
`http://localhost:8100/mcp`).

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
make test      # 272 backend tests (99% coverage) + 38 mcp tests — no Docker, no network
make ios-test  # 71 XCTest tests: API decoding against captured fixtures, the offline sync engine, error mapping
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

`make deploy` runs `deploy/deploy.sh`, which is **not in this repo** — a swarm
stack file is a description of somebody's specific hardware (node names, ingress
labels, host aliases, which box has the free disk), so it's kept out and
gitignored. [`docker-compose.yml`](docker-compose.yml) is the honest reference
for the shape of the deployment, and it's what CI boots and smoke-tests.

If you're deploying this yourself, the two things worth knowing, learned the
hard way:

- **Build the images where the tasks will run.** Locally built `:latest` images
  have no registry to pull from, so a task only starts on a node that already
  has the image.
- **Force the service update.** `docker stack deploy` won't roll out a rebuilt
  image that kept the same tag, so a deploy that looks clean can change nothing.

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
