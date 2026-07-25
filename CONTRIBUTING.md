# Contributing

Thanks for looking. This is a personal project that got built properly, and
issues and pull requests are welcome.

## Before you start

- **Open an issue first** for anything beyond a bug fix or a typo. The
  [decisions log](planning/04-open-questions.md) records *why* things are the
  way they are — Q1–Q19 — and a change that contradicts a decision needs the
  decision revisited first, not worked around. Code comments cite those numbers.
- **Read [CLAUDE.md](CLAUDE.md).** It's written for AI assistants but it's the
  real architecture guide: the domain invariants there (household scoping,
  plans-are-pools, derived shopping-list quantities, the metric-or-count unit
  convention, additive-only API compatibility) are the constraints a PR is
  reviewed against.
- **[BACKLOG.md](BACKLOG.md)** is the list of things known to be missing, in
  rough priority order. Anything there is fair game.

## Two licences, and why it matters to you

| Path | Licence | What you're granting |
|---|---|---|
| everything except `ios/` | [AGPL-3.0](LICENSE) | The usual inbound=outbound: your contribution is licensed to the project under the AGPL too. Nothing to sign. |
| `ios/` | [source-available](ios/LICENSE) | You must sign a CLA before it can be merged. |

The `ios/` carve-out exists because the App Store's terms and the GPL family
conflict, so the app can only be shipped there if one person holds the rights to
all of it. That means **contributions to `ios/` need a contributor licence
agreement**: you keep your copyright, and you grant an irrevocable licence to
use your contribution in the App Store build, including in a commercially
licensed version.

There is no automated CLA bot yet. If you want to contribute to `ios/`, say so
in the issue and it'll get sorted out by email before you spend time on code.
Contributions to the backend, MCP server or skill need none of this.

## Running things

```bash
make dev       # Postgres + API on :8000, remote MCP on :8100
make test      # backend + mcp, no Docker, no network
make lint fmt  # ruff, before you push
```

The full command list is in [README.md](README.md) and `make help`. CI runs
`make lint` and `make test`, plus migrations against real Postgres and a
`docker compose` boot smoke — see
[`.github/workflows/ci.yml`](.github/workflows/ci.yml). There's no iOS job;
`make ios-build` / `make ios-test` are local-only.

## What a good PR looks like

- **Tests.** Use the shared builders in
  [`backend/tests/conftest.py`](backend/tests/conftest.py) (`create_recipe`,
  `create_meal`, `create_plan`, `get_list`) rather than hand-rolled payloads.
  `asyncio_mode = "auto"`, so no `@pytest.mark.asyncio`. External HTTP is
  stubbed with `respx` — tests never hit the network.
- **A migration if you touched a model.** `make migration m="..."`. Tests build
  tables from metadata and will not catch a missing revision; CI will.
- **API changes that are additive.** iOS is the only client that can be older
  than the server, and a TestFlight build can't be recalled. Never remove or
  rename a response field, never add a required request field, never change what
  an existing value means. New behaviour goes behind a new endpoint or a new
  optional parameter. CLAUDE.md explains the escape hatch for the rare change
  that can't be additive.
- **Error strings written for the reader.** Every 4xx ends up in some
  assistant's context window, so it should say what to do instead — see the 409
  in `backend/app/routers/shopping.py` for the shape.
- **No LLM calls in the backend.** That's decision Q13, and it's the reason the
  thing costs nothing to run. Parsing that needs a model is the *client's* job.

## Security

Please don't open a public issue for a vulnerability — see
[SECURITY.md](SECURITY.md).
